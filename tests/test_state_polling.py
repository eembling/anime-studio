import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker


class StatePollingTests(unittest.TestCase):
    def test_sampler_updates_invalidate_only_the_queue_view(self):
        with tempfile.TemporaryDirectory() as folder:
            studio = Studio(folder)
            self.addCleanup(studio.db.conn.close)
            self.addCleanup(studio.shot_previews.shutdown)
            api = API(studio)
            jid = studio.db.x("INSERT INTO jobs(stage,status,snapshot_json) VALUES('animate','running','{}')")
            with patch('anime_studio.time.time', return_value=120):
                project_revision = api.state_etag()
                queue_revision = api.state_etag(queue_only=True)
                Worker(studio)._progress(jid, 'Sampler 1/20')
                self.assertEqual(api.state_etag(), project_revision)
                self.assertNotEqual(api.state_etag(queue_only=True), queue_revision)
                payload = api.queue_poll()
                self.assertNotIn('shots', payload)
                self.assertNotIn('jobs', payload)
                self.assertIs(payload['render_queue']['active'][0]['snapshot_json'], True)
                self.assertEqual(payload['render_queue']['active'][0]['progress'], 'Sampler 1/20')
                studio.db.x("UPDATE jobs SET status='done' WHERE id=?", (jid,))
                self.assertNotEqual(api.state_etag(), project_revision)
