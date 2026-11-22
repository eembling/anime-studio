import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker
import keyframe_prompt


class KeyframePromptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.studio = Studio(self.tmp.name)
        self.addCleanup(self.studio.db.conn.close)
        self.addCleanup(self.studio.shot_previews.shutdown)
        self.studio.production.ready = lambda *args: True
        self.api = API(self.studio)
        db = self.studio.db
        for name in ('aiko', 'roof'):
            self.studio.abs(f'cast/{name}.png').write_bytes(name.encode())
        self.aiko = db.x("INSERT INTO assets(kind,name,description,image) VALUES('character','AIKO','short black hair, red scarf','cast/aiko.png')")
        self.kenji = db.x("INSERT INTO assets(kind,name,description) VALUES('character','KENJI','tall, school uniform')")
        self.roof = db.x("INSERT INTO assets(kind,name,description,image) VALUES('location','Rooftop','chain-link fence at sunset','cast/roof.png')")
        self.sid = self.api.shot_save({'kind': 'motion', 'keyframe_prompt': 'Two students face each other', 'camera': 'Low angle',
                                       'asset_ids': [self.aiko, self.kenji, self.roof],
                                       'dialogue': 'AIKO (angry): You came after all.\nKENJI: I promised.'})['id']

    def prompt(self):
        return self.api.keyframe_review({'shot_id': self.sid})['positive'][0]

    def test_sections_name_kinds_speakers_and_listeners(self):
        text = self.prompt()
        lines = text.split('\n')
        self.assertEqual(lines[0], 'Picture 1 is the character AIKO (short black hair, red scarf).')
        self.assertEqual(lines[1], 'Picture 2 is the location Rooftop (chain-link fence at sunset).')
        self.assertIn('Also in this shot: the character KENJI (tall, school uniform).', lines)
        self.assertIn('Setting: Rooftop.', lines)
        self.assertIn('Acting: AIKO is speaking, angry, mouth open mid-word; KENJI is speaking, mouth open mid-word.', lines)
        self.assertIn('Camera: Low angle.', lines)

    def test_direction_boxes_become_placement(self):
        self.studio.db.x('INSERT OR REPLACE INTO shot_direction VALUES(?,?)', (self.sid, json.dumps(
            {'boxes': [{'asset_id': self.aiko, 'x': .2, 'y': .55, 'width': .2, 'height': .8},
                       {'asset_id': self.kenji, 'x': .8, 'y': .5, 'width': .2, 'height': .3}]})))
        self.assertIn('Placement: AIKO on the left, close to the camera, large in frame; KENJI on the right, in the background, small in frame.',
                      self.prompt())

    def test_negative_folded_into_positive_only_when_cfg_ignores_it(self):
        self.api.settings({'settings': {'keyframe_negative': 'text, watermark'}})
        self.assertIn('Avoid: text, watermark.', self.prompt())    # starter graph samples at CFG 1
        self.assertFalse(keyframe_prompt.ignores_negative({'1': {'inputs': {'cfg': 4.0}}}))
        self.assertFalse(keyframe_prompt.ignores_negative({}))

    def test_animation_names_who_talks(self):
        take = self.studio.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','cast/aiko.png')", (self.sid,))
        self.studio.db.x('UPDATE shots SET keyframe_take=? WHERE id=?', (take, self.sid))
        _, params = Worker(self.studio).compose({'id': 1, 'shot_id': self.sid, 'seed': 1, 'stage': 'animate'})
        self.assertIn('AIKO and KENJI talk in turn, lips moving naturally', params['prompt'])
        self.studio.db.x('UPDATE shots SET asset_ids=? WHERE id=?', (json.dumps([self.aiko]), self.sid))
        _, params = Worker(self.studio).compose({'id': 2, 'shot_id': self.sid, 'seed': 1, 'stage': 'animate'})
        self.assertIn('AIKO talks, lips', params['prompt'])
        self.assertNotIn('KENJI', params['prompt'])


if __name__ == '__main__':
    unittest.main()
