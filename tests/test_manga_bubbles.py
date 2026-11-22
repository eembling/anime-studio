import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio
import manga


class MangaBubbleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.studio = Studio(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.studio.db.conn.close)
        self.addCleanup(self.studio.shot_previews.shutdown)
        self.studio.production.ready = lambda *args: True
        self.api = API(self.studio)
        db = self.studio.db
        for name in ('aiko', 'kenji', 'roof'):
            self.studio.abs(f'cast/{name}.png').write_bytes(name.encode())
        self.aiko = db.x("INSERT INTO assets(kind,name,description,image) VALUES('character','AIKO','short black hair, red scarf','cast/aiko.png')")
        self.kenji = db.x("INSERT INTO assets(kind,name,description,image) VALUES('character','KENJI','tall, school uniform','cast/kenji.png')")
        self.roof = db.x("INSERT INTO assets(kind,name,description,image) VALUES('location','Rooftop','chain-link fence at sunset','cast/roof.png')")
        self.mina = db.x("INSERT INTO assets(kind,name,description) VALUES('character','MINA','')")
        self.sid = self.api.shot_save({'kind': 'still', 'keyframe_prompt': 'Two students face each other',
                                       'asset_ids': [self.aiko, self.roof],
                                       'dialogue': 'AIKO: You came after all.\nKENJI: I promised.'})['id']

    def prompt(self):
        return self.api.keyframe_review({'shot_id': self.sid})['positive'][0]

    def test_references_name_kind_and_dialogue_drives_acting(self):
        text = self.prompt()
        self.assertIn('Picture 1 is the character AIKO (short black hair, red scarf).', text)
        self.assertIn('Picture 2 is the location Rooftop', text)
        self.assertIn('Setting: Rooftop.', text)
        self.assertIn('AIKO is speaking', text)
        # KENJI speaks but is not tagged in the shot: he is heard, not drawn.
        self.assertIn('KENJI speaks from outside the frame', text)
        self.assertNotIn('Do not draw any text', text)   # lettering is off by default

    def test_tagging_bubbles_adds_speaker_and_validates(self):
        with self.assertRaisesRegex(ValueError, 'choose who says it'):
            self.api.shot_save({'id': self.sid, 'bubbles': [{'kind': 'speech', 'text': 'Hi'}]})
        with self.assertRaisesRegex(ValueError, 'not a location'):
            self.api.shot_save({'id': self.sid, 'bubbles': [{'kind': 'speech', 'text': 'Hi', 'character_id': self.roof}]})
        self.api.shot_save({'id': self.sid, 'bubbles': [
            {'kind': 'shout', 'character_id': self.kenji, 'text': 'Watch out!', 'emotion': 'Panicked'},
            {'kind': 'narration', 'text': 'That evening...'},
            {'kind': 'speech', 'character_id': self.mina, 'text': 'Over here!', 'off_panel': True}]})
        shot = self.studio.db.one('SELECT * FROM shots WHERE id=?', (self.sid,))
        self.assertIn(self.kenji, json.loads(shot['asset_ids']))      # speaker was tagged into the shot
        self.assertNotIn(self.mina, json.loads(shot['asset_ids']))    # off-panel speaker was not
        state = next(s for s in self.api.state()['shots'] if s['id'] == self.sid)
        self.assertEqual([b['kind'] for b in state['bubbles']], ['shout', 'narration', 'speech'])
        text = self.prompt()
        self.assertIn('KENJI is shouting, panicked', text)
        self.assertIn('AIKO listens', text)
        self.assertIn('MINA speaks from outside the frame', text)

    def test_lettering_modes(self):
        self.api.shot_save({'id': self.sid, 'bubbles': [{'kind': 'speech', 'character_id': self.aiko, 'text': 'You came "after" all.'},
                                                          {'kind': 'sfx', 'text': 'BAM', 'position': 'bottom'}]})
        self.api.settings({'settings': {'lettering_mode': 'space'}})
        text = self.prompt()
        self.assertIn('Do not draw any text', text)
        self.assertIn('upper right', text)        # first balloon enters at the upper right (right-to-left reading)
        self.api.settings({'settings': {'lettering_mode': 'render', 'reading_direction': 'ltr'}})
        text = self.prompt()
        self.assertIn('containing the text "You came \'after\' all."', text)
        self.assertIn('tail pointing to AIKO', text)
        self.assertIn('sound effect lettering reading "BAM" in the bottom centre', text)
        with self.assertRaisesRegex(ValueError, 'Choose one of'):
            self.api.settings({'settings': {'lettering_mode': 'loud'}})

    def test_composition_boxes_become_placement_and_anchor_balloons(self):
        self.studio.db.x('INSERT OR REPLACE INTO shot_direction VALUES(?,?)', (self.sid, json.dumps(
            {'boxes': [{'asset_id': self.aiko, 'x': .2, 'y': .55, 'width': .2, 'height': .8}]})))
        self.api.settings({'settings': {'lettering_mode': 'render'}})
        self.api.shot_save({'id': self.sid, 'bubbles': [{'kind': 'speech', 'character_id': self.aiko, 'text': 'Hey.'}]})
        text = self.prompt()
        self.assertIn('Placement: AIKO on the left, close to the camera', text)
        self.assertIn('in the upper left, its tail pointing to AIKO', text)

    def test_old_shots_keep_their_signature(self):
        shot = self.studio.db.one('SELECT * FROM shots WHERE id=?', (self.sid,))
        self.assertEqual(manga.signature(self.studio, shot), {})
        before = self.studio.production.signature(shot, 'keyframe')
        self.api.shot_save({'id': self.sid, 'bubbles': [{'kind': 'speech', 'character_id': self.aiko, 'text': 'Hey.'}]})
        shot = self.studio.db.one('SELECT * FROM shots WHERE id=?', (self.sid,))
        self.assertNotEqual(before, self.studio.production.signature(shot, 'keyframe'))

    def test_animation_names_who_talks(self):
        self.assertEqual(manga.motion_acting(self.studio, self.studio.db.one('SELECT * FROM shots WHERE id=?', (self.sid,))),
                         'AIKO talks, lips moving naturally')   # KENJI is heard off-panel, so his lips are not animated


if __name__ == '__main__':
    unittest.main()
