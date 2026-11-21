import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker
from script_dialogue import script_blocks


SCRIPT = """# Episode 1 — Lantern
**Episode conflict:** Get home in the rain.
### ACT A
**EXT. PIER — DUSK** *(1:00 – 2:00)*
Lighting: Violet skylight and amber lamps.

### Shot 01 — First look
**Keyframe:** Aiko at the far end, gripping a red umbrella.
**Action:** The umbrella tilts. Aiko turns toward the sea.
**Camera:** Medium close-up, low angle, slow push-in.
**Direction:** Hold the silhouette for half a beat, then turn sharply.
**Cast:** Aiko, Kenji
**Duration:** 3 seconds
**Sound:** Footsteps on wet wood.

**AIKO (under her breath):** Where are you?
**KENJI (O.S.):** Here.

### Shot 02 — The pier
Wide shot: Waves lap against the wet pilings.
Duration: unknown

INT. SHED - NIGHT

PIPPA
(whispering)
Keep the lantern low.
We are almost there.
"""


class ScriptPlanningTests(unittest.TestCase):
    def test_adjacent_screenplay_cues_and_scene_boundaries(self):
        text = """INT. HALL — NIGHT
Shot 1
Camera: Close-up
EXT. GATE — DAWN
AIKO
Are you coming?
KENJI (V.O.)
Keep going.
"""
        shot, = script_blocks(text)
        self.assertEqual(shot['dialogue'], 'AIKO: Are you coming?\nKENJI: Keep going.')
        self.assertEqual(shot['camera'], 'Medium close-up, steady camera')
        self.assertNotIn('KENJI', shot['keyframe_prompt'])
        with tempfile.TemporaryDirectory() as folder:
            studio = Studio(folder)
            try:
                API(studio).script_draft({'script': text})
                saved, = studio.ordered_shots()
                lines = studio.shot_lines(saved)
                self.assertEqual(json.loads(saved['asset_ids']), [lines[0]['character_id']])
                self.assertIsNotNone(lines[1]['character_id'])
                self.assertEqual(lines[1]['direction'], 'V.O.')
            finally:
                studio.db.conn.close()

    def test_screenplay_and_labeled_shot_fields(self):
        first, wide, spoken = script_blocks(SCRIPT, 5)
        self.assertEqual(first['scene'], 'EXT. PIER — DUSK')
        self.assertIn('gripping a red umbrella', first['keyframe_prompt'])
        self.assertIn('Violet skylight', first['keyframe_prompt'])
        self.assertIn('turn sharply', first['motion_prompt'])
        self.assertIn('umbrella tilts', first['motion_prompt'])
        self.assertNotIn('Where are you?', first['motion_prompt'])
        self.assertNotIn('KENJI speaks', first['motion_prompt'])
        self.assertEqual(first['camera'], 'Medium close-up, low angle, slow push-in')
        self.assertEqual(first['cast'], ['Aiko', 'Kenji'])
        self.assertEqual(first['duration'], 3)
        self.assertIn('Footsteps on wet wood', first['notes'])
        self.assertEqual([row['direction'] for row in first['entries']], ['under her breath', 'O.S.'])
        self.assertEqual(wide['camera'], 'Wide shot')
        self.assertEqual(wide['duration'], 5)
        self.assertTrue(wide['warnings'])
        self.assertIn('Violet skylight', wide['keyframe_prompt'])
        self.assertNotIn('Violet skylight', spoken['keyframe_prompt'])
        self.assertEqual(spoken['dialogue'], 'PIPPA: Keep the lantern low. We are almost there.')
        self.assertEqual(spoken['entries'][0]['direction'], 'whispering')

    def test_preview_is_read_only_and_selected_prompts_reach_render_composition(self):
        with tempfile.TemporaryDirectory() as folder:
            studio = Studio(folder)
            try:
                api = API(studio)
                preview = api.script_draft({'script':SCRIPT,'preview':True})
                self.assertEqual(studio.ordered_shots(), [])
                self.assertEqual(studio.db.q('SELECT * FROM assets'), [])
                with self.assertRaisesRegex(ValueError, 'changed'):
                    api.script_draft({'script':SCRIPT.replace('red','green'), 'token':preview['token']})
                self.assertEqual(api.script_draft({'script':SCRIPT,'token':preview['token'],'indices':[0]})['made'],1)
                shot = studio.ordered_shots()[0]
                self.assertEqual(len(json.loads(shot['asset_ids'])), 2)
                studio.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','shots/reference.png')", (shot['id'],))
                studio.abs('shots/reference.png').touch()
                studio.db.x("UPDATE shots SET keyframe_take=(SELECT id FROM takes) WHERE id=?", (shot['id'],))
                worker = Worker(studio)
                _, image = worker.compose({'id':1,'stage':'keyframe','shot_id':shot['id'],'seed':42})
                _, video = worker.compose({'id':2,'stage':'animate','shot_id':shot['id'],'seed':42})
                self.assertIn('Violet skylight', image['prompt'])
                self.assertIn('slow push-in', video['prompt'])
                self.assertIn('turn sharply', video['prompt'])
                self.assertNotIn('Where are you?', video['prompt'])
            finally:
                studio.db.conn.close()

    def test_revision_applies_changed_directions_and_preserves_manual_camera(self):
        with tempfile.TemporaryDirectory() as folder:
            studio = Studio(folder)
            try:
                api = API(studio)
                api.script_draft({'script':SCRIPT})
                old = studio.ordered_shots()[0]
                api.shot_save({'id':old['id'],'camera':'Manual locked overhead'})
                text = SCRIPT.replace('turn sharply','turn slowly').replace('under her breath','calmly')
                preview = api.script_revision({'script':text})
                self.assertEqual(preview['rows'][0]['change'],'changed')
                self.assertEqual(preview['rows'][0]['shot_id'],old['id'])
                api.script_revision({'script':text,'token':preview['token'],'apply':True,
                                     'selections':[{'index':0,'shot_id':old['id'],'apply':True}]})
                shot = studio.ordered_shots()[0]
                self.assertEqual(shot['camera'],'Manual locked overhead')
                self.assertIn('turn slowly', shot['motion_prompt'])
                self.assertEqual(studio.shot_lines(shot)[0]['direction'],'calmly')
            finally:
                studio.db.conn.close()
