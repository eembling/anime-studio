from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker


class SubmissionRecoveryTests(unittest.TestCase):
    def test_lost_reply_recovers_once_or_remains_uncertain(self):
        for accepted in (True, False):
            with self.subTest(accepted=accepted), TemporaryDirectory() as folder:
                studio = Studio(folder)
                worker = Worker(studio)
                shot_id = API(studio).shot_save({'kind': 'motion'})['id']
                db = studio.db
                jid = db.x("INSERT INTO jobs(shot_id,stage,status,seed,instance_id) VALUES(?,'keyframe','running',1,1)", (shot_id,))
                instance = db.one('SELECT * FROM instances WHERE id=1')
                submissions = []
                result = {'status': {'completed': True}, 'outputs': {'60': {'images': [{'filename': 'take.png'}]}}}

                def request(method, url, obj=None, timeout=None):
                    route = url.removeprefix(instance['url'])
                    if method == 'POST' and route == '/prompt':
                        submissions.append(obj['client_id'])
                        self.assertEqual(db.one('SELECT submission_client_id FROM jobs WHERE id=?', (jid,))['submission_client_id'], submissions[0])
                        raise ConnectionResetError('Disconnected before acknowledgement')
                    if route == '/queue':
                        return {'queue_running': [[0, 'remote-id', {}, {'client_id': submissions[0]}]] if accepted else []}
                    if route.startswith('/history?'):
                        return {}
                    if route == '/history/remote-id':
                        return {'remote-id': result}
                    self.fail(f'Unexpected request {method} {route}')

                with patch('anime_studio.http_json', side_effect=request), patch('anime_studio.http', return_value=b'fixture'), \
                     patch('comfy_events.ProgressEvents'), patch.object(worker, 'finish') as finish, patch('anime_studio.traceback.print_exc'):
                    worker.execute(jid, instance)
                    self.assertIn('Submission unconfirmed', db.one('SELECT error FROM jobs WHERE id=?', (jid,))['error'])
                    db.x("UPDATE jobs SET status='running' WHERE id=?", (jid,))
                    worker.execute(jid, instance)
                    self.assertEqual(len(submissions), 1)
                    self.assertEqual(finish.call_count, int(accepted))
                    self.assertEqual(db.one('SELECT status FROM jobs WHERE id=?', (jid,))['status'], 'done' if accepted else 'error')
                db.conn.close()
