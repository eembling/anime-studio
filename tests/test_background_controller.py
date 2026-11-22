import hashlib
import importlib.util
import shutil
import json
from pathlib import Path
import socket
import subprocess
import sys
from tempfile import TemporaryDirectory
import time
import unittest
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


@unittest.skipUnless(shutil.which('ffmpeg') and importlib.util.find_spec('PIL'), 'Requires FFmpeg and Pillow')
class BackgroundControllerTests(unittest.TestCase):
    def test_detached_controller_completes_queue_once(self):
        with TemporaryDirectory() as folder:
            project = Path(folder).resolve()/'project'
            project.mkdir()
            (project/'.keep-rendering').touch()
            port, mock_port = free_port(), free_port()
            base = f'http://127.0.0.1:{port}'
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def api(route, payload=None):
                data = json.dumps(payload).encode() if payload is not None else None
                with opener.open(urllib.request.Request(base+route, data=data, headers={'Content-Type':'application/json'}), timeout=12) as response:
                    return json.load(response)

            mock = subprocess.Popen([sys.executable, str(ROOT/'tests/mock_comfy.py'), str(mock_port),
                                     str(project/'mock'), 'virtual'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            args = [sys.executable, str(ROOT/'app/anime_studio.py'), '--port', str(port), '--data', str(project)]
            launch = """import subprocess,sys
log=open(sys.argv[1], 'w')
p=subprocess.Popen(sys.argv[2:],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
print(p.pid)
"""
            launcher = subprocess.Popen([sys.executable, '-c', launch, str(project/'test.log'), *args],
                                        stdout=subprocess.PIPE)
            try:
                for _ in range(40):
                    try:
                        ping = api('/api/ping')
                        break
                    except OSError:
                        time.sleep(.15)
                else:
                    self.fail((project/'test.log').read_text())
                self.assertEqual(ping['project'], hashlib.sha256(str(project).encode()).hexdigest())
                state = api('/api/state')
                instance = state['instances'][0]['id']
                api('/api/instance/save', {'id':instance,'name':'Mock','url':f'127.0.0.1:{mock_port}','roles':['keyframe'],'enabled':True})
                api('/api/instance/check', {})
                shot = api('/api/shot/save', {'kind':'motion','keyframe_prompt':'A rainy street'})['id']
                queued = api('/api/generate', {'stage':'keyframe','shot_ids':[shot],'takes':2})
                self.assertEqual(queued['queued'],2, queued)
                launcher.communicate(timeout=5)
                self.assertEqual(launcher.returncode,0)
                duplicate = subprocess.run([*args, '--port', str(free_port())], capture_output=True, text=True, timeout=5)
                self.assertIn('already has a render controller', duplicate.stderr)
                for _ in range(80):
                    state=api('/api/state')
                    if state['job_counts'].get('done') == 2 or state['job_counts'].get('error'):
                        break
                    time.sleep(.25)
                self.assertEqual(state['job_counts'], {'done':2}, [(j['id'],j.get('error')) for j in state['jobs']])
                self.assertEqual(len(state['shots'][0]['takes']),2)
            finally:
                if launcher.stdout and not launcher.stdout.closed:
                    launcher.communicate(timeout=5)
                try: api('/api/quit', {})
                except OSError: pass
                mock.terminate()
                mock.wait(timeout=5)
                for _ in range(40):
                    try: api('/api/ping')
                    except OSError: break
                    time.sleep(.1)
