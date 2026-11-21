import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio


class KeyframeReviewTests(unittest.TestCase):
    def test_reference_priority_and_snapshot_match_the_review(self):
        with TemporaryDirectory() as root:
            studio = Studio(root)
            self.addCleanup(studio.db.conn.close)
            self.addCleanup(studio.shot_previews.shutdown)
            studio.production.ready = lambda *args: True
            api = API(studio)
            assets = []
            for index in range(5):
                relative = f'cast/{index}.png'
                studio.abs(relative).write_bytes(bytes([index]))
                assets.append(studio.db.x('INSERT INTO assets(name,image,lora,lora_strength) VALUES(?,?,?,?)',
                                          (f'Cast {index}', relative, f'cast{index}.safetensors', 0.8)))
            previous_id = api.shot_save({'kind':'motion'})['id']
            keyframe = studio.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe',?)",
                                   (previous_id, 'cast/0.png'))
            studio.db.x('UPDATE shots SET keyframe_take=? WHERE id=?', (keyframe, previous_id))
            sid = api.shot_save({'kind':'motion', 'keyframe_prompt':'Close-up at dusk'})['id']
            studio.db.x('UPDATE shots SET asset_ids=?,ref_prev=1 WHERE id=?', (json.dumps(assets), sid))
            initial = api.keyframe_review({'shot_id':sid})
            self.assertFalse(next(row for row in initial['references'] if row['id']=='previous')['applied'])
            self.assertIn('cast4.safetensors', initial['omitted_loras'])
            review = api.keyframe_review({'shot_id':sid,'reference_order':['previous']})
            self.assertIn('Picture 1 is the previous shot', review['positive'][0])
            args = {'stage':'keyframe','shot_ids':[sid],'reference_order':['previous'],'keyframe_review_token':review['token']}
            studio.db.x("UPDATE shots SET camera='Low angle' WHERE id=?", (sid,))
            with self.assertRaisesRegex(ValueError, 'inputs changed'):
                api.generate(args)
            review = api.keyframe_review({'shot_id':sid,'reference_order':['previous']})
            result = api.generate(dict(args, keyframe_review_token=review['token']))
            self.assertEqual(result['queued'],1)
            raw = studio.db.one('SELECT snapshot_json FROM jobs')['snapshot_json']
            captured = json.loads(raw)['params']
            self.assertEqual(captured['prompt'],review['positive'][0])
            self.assertEqual(studio.abs(captured['files']['ref1']).read_bytes(), bytes([0]))
