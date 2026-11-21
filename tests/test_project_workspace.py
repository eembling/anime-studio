import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'app'))
from anime_studio import API, DEFAULT_INSTANCES, Studio
from project_workspace import ProjectWorkspace, identity
from render_queue import eligible_server


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


class SharedConfigurationTests(unittest.TestCase):
    def test_initial_setup_recovers_servers_from_another_project(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = Studio(root/'original')
            current = Studio(root/'recent')
            try:
                api = API(source)
                api.instance_save({'name':'Audio GPU','url':'http://audio.local:8188','roles':['voice'],'enabled':False})
                api.settings({'settings':{'tts_model':'0.6B','title':'Original title'}})
                config = root/'config'
                config.mkdir()
                (config/'projects.json').write_text(json.dumps({
                    identity(source.data): {'name':'Original','path':str(source.data)},
                }))
                current.projects = ProjectWorkspace(current, config, [], DEFAULT_INSTANCES)
                audio = next(row for row in current.instances() if row['name'] == 'Audio GPU')
                self.assertEqual(audio['enabled'], 0)
                self.assertEqual(current.settings()['tts_model'], '0.6B')
                self.assertEqual(current.settings()['title'], 'My Anime Episode')

                current.db.x("INSERT INTO jobs(stage,status,instance_id,prompt_id) VALUES('voice','pending',?,'remote-id')", (audio['id'],))
                API(current).instance_delete({'id':audio['id']})
                source.projects = ProjectWorkspace(source, config, [], DEFAULT_INSTANCES)
                self.assertFalse(any(row['name'] == 'Audio GPU' for row in source.instances()))
                resumable = next(server for server in current.render_instances() if server['id'] == audio['id'])
                self.assertEqual(resumable['url'], 'http://audio.local:8188')
                self.assertTrue(eligible_server({'instance_id':audio['id'],'prompt_id':'remote-id'}, resumable, {}, None))
                self.assertFalse(eligible_server({'stage':'voice'}, resumable, {}, None))
            finally:
                current.db.conn.close()
                source.db.conn.close()


@unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('PIL'), 'Requires FFmpeg and Pillow')
class ProjectWorkspaceTests(unittest.TestCase):
    def test_create_switch_and_reopen_with_two_independent_queues(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            config = root/'config'
            first = root/'Existing Show'
            port, mock_port = free_port(), free_port()
            env = dict(os.environ, ANIME_STUDIO_CONFIG=str(config))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            ports = [port]

            def request(port, route, body=None, headers=None):
                data = json.dumps(body).encode() if body is not None else None
                headers = {'Content-Type':'application/json', **(headers or {})}
                req = urllib.request.Request(f'http://127.0.0.1:{port}'+route, data=data, headers=headers)
                with opener.open(req, timeout=15) as response:
                    return json.load(response)

            def wait_for(check, timeout=30):
                stop = time.monotonic()+timeout
                result = None
                while time.monotonic()<stop:
                    try:
                        result = check()
                        if result:
                            return result
                    except OSError:
                        pass
                    time.sleep(.1)
                self.fail(f'Project controller did not become ready. Last result: {result}')

            def start_project():
                return subprocess.Popen([sys.executable, str(ROOT/'app/anime_studio.py'), '--projects',
                                         '--port', str(port), '--data', str(first)],
                                        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            mock = subprocess.Popen([sys.executable, str(ROOT/'tests/mock_comfy.py'), str(mock_port),
                                     str(root/'mock'), 'Mock GPU'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            controller = start_project()
            try:
                wait_for(lambda: request(port, '/api/ping').get('projects'))
                wait_for(lambda: request(mock_port, '/system_stats'))
                with self.assertRaises(urllib.error.HTTPError) as rejected:
                    request(port, '/api/projects', {'action':'create','name':'Cross origin'},
                            {'Origin':'https://example.com'})
                self.assertEqual(rejected.exception.code, 403)

                def queue_on(target, label):
                    state = request(target, '/api/state')
                    instance = state['instances'][0]['id']
                    request(target, '/api/instance/save', {'id':instance,'name':'Mock','url':f'http://127.0.0.1:{mock_port}',
                                                          'roles':['keyframe'],'enabled':True})
                    shot = request(target, '/api/shot/save', {'kind':'motion','keyframe_prompt':label})['id']
                    request(target, '/api/asset/save', {'kind':'character','name':label})
                    queued = request(target, '/api/generate', {'stage':'keyframe','shot_ids':[shot],'takes':2})
                    self.assertEqual(queued.get('queued'), 2, queued)

                queue_on(port, 'Existing cast')
                request(port, '/api/instance/save', {'name':'Spare ComfyUI','url':'http://127.0.0.1:1','roles':['voice'],'enabled':False})
                request(port, '/api/settings', {'settings':{'kf_width':1024,'tts_model':'0.6B','title':'Original title'}})
                request(port, '/api/workflow/meta', {'stage':'animate','fps':16,'max_frames':65})
                self.assertTrue(request(port, '/api/ping')['job_counts'])
                created = request(port, '/api/projects', {'action':'create','name':'Second Show'})
                self.assertTrue(created['ok'], created)
                second_port = created['port']
                self.assertNotEqual(port,second_port)
                ports.append(second_port)
                second_state = request(second_port, '/api/state')
                self.assertEqual(second_state['project_name'], 'Second Show')
                self.assertEqual((second_state['assets'],second_state['shots'],second_state['job_counts']), ([],[],{}))
                self.assertEqual([(s['name'],s['enabled']) for s in second_state['instances']], [('Mock',1),('Spare ComfyUI',0)])
                self.assertEqual(second_state['settings']['kf_width'], '1024')
                self.assertEqual(second_state['settings']['tts_model'], '0.6B')
                self.assertEqual(second_state['settings']['title'], 'My Anime Episode')
                self.assertEqual(second_state['workflows']['animate']['max_frames'], 65)

                queue_on(second_port, 'Second cast')
                request(second_port, '/api/settings', {'settings':{'kf_width':1280}})
                request(second_port, '/api/workflow', {'stage':'lipsync','remove':True})
                first_state = request(port, '/api/state')
                self.assertEqual(first_state['settings']['kf_width'], '1280')
                self.assertIsNone(first_state['workflows']['lipsync'])
                opened = request(second_port, '/api/projects', {'action':'open','path':str(first)})
                self.assertEqual(opened.get('port'), port, opened)
                self.assertEqual(Path((config/'data-folder').read_text(encoding='utf-8')), first)
                projects = request(port, '/api/projects')['projects']
                switch = request(port, '/api/projects', {'action':'switch','id':next(p['id'] for p in projects if p['name']=='Second Show')})
                self.assertEqual(switch.get('port'), second_port, switch)

                for target, label in [(port,'Existing cast'), (second_port,'Second cast')]:
                    def completed():
                        state = request(target, '/api/state')
                        self.assertFalse(state['job_counts'].get('error'), state['jobs'])
                        return state if state['job_counts'].get('done') == 2 else None
                    state = wait_for(completed)
                    self.assertEqual([asset['name'] for asset in state['assets']], [label])
                    self.assertEqual(len(state['shots'][0]['takes']), 2)
            finally:
                for target in reversed(ports):
                    try:
                        request(target, '/api/quit', {})
                        def stopped():
                            try:
                                request(target, '/api/ping')
                                return False
                            except OSError:
                                return True
                        wait_for(stopped, timeout=5)
                    except OSError:
                        pass
                if controller.poll() is None:
                    controller.terminate()
                controller.wait(timeout=5)
                mock.terminate()
                mock.wait(timeout=5)
