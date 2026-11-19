#!/usr/bin/env python3
"""Behavior checks for production planning, revisions, provenance and readiness."""
import copy
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import anime_studio as app


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.st = app.Studio(self.tmp.name)
        self.st.worker = app.Worker(self.st)
        self.api = app.API(self.st)
        self.prod = self.st.production
        self.st.db.x('DELETE FROM instances')
        self.api.instance_save({'name': 'Test GPU', 'url': 'http://test:8188', 'roles': list(app.JOB_STAGES), 'enabled': True})
        self.nodes = {n['class_type']: {'input': {'required': {}}} for variants in app.STARTERS.values()
                      for starter in variants.values() for n in starter['wf'].values()}
        self.prod.http = lambda *args, **kwargs: copy.deepcopy(self.nodes)

    def tearDown(self):
        self.st.db.conn.close()
        self.tmp.cleanup()

    def shot(self, **values):
        return self.api.shot_save(dict(scene='Pier', kind='motion', keyframe_prompt='A quiet sea', motion_prompt='Waves', **values))['id']

    def test_readiness_explains_missing_nodes_and_models(self):
        self.assertTrue(self.prod.ready('voice_design'))
        del self.nodes['FB_Qwen3TTSVoiceDesign']
        checked = self.prod.inspect(True)['servers'][0]['stages']['voice_design']
        self.assertFalse(checked['ready'])
        self.assertIn('FB_Qwen3TTSVoiceDesign', checked['problems'][0])
        self.nodes['UpscaleModelLoader'] = {'input': {'required': {'model_name': [['a-different-model.pth']]}}}
        checked = self.prod.inspect(True)['servers'][0]['stages']['upscale']
        self.assertFalse(checked['ready'])
        self.assertTrue(any('model_name' in p for p in checked['problems']))
        self.assertFalse(self.api.generate({'stage': 'voice_design', 'asset_ids': []})['ok'])

    def test_lipsync_preset_populates_inputs_and_pads_short_chunks(self):
        tpl = app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf']
        self.assertEqual(app.check_workflow(tpl, 'lipsync'), [])
        wf = app.build_workflow(tpl, 'lipsync', {'prompt': 'Aiko speaks', 'negative': 'blur', 'seed': 42,
                'width': 848, 'height': 480, 'length': 33, 'prefix': 'test/lipsync',
                'images': {'start': 'uploaded.png', 'audio': 'spoken.wav'}})
        self.assertEqual(wf['10']['inputs']['image'], 'uploaded.png')
        self.assertEqual(wf['11']['inputs']['audio'], 'spoken.wav')
        self.assertEqual(wf['22']['inputs']['length'], 73)
        self.assertEqual(wf['22']['inputs']['ref_image'], ['10', 0])
        self.assertEqual(wf['30']['inputs']['seed'], 42)
        self.assertEqual(wf['20']['inputs']['text'], 'Aiko speaks')

    def test_lipsync_checks_diffusion_and_audio_encoder_files(self):
        self.nodes['UNETLoader']['input']['required']['unet_name'] = [['other.safetensors']]
        self.nodes['AudioEncoderLoader']['input']['required']['audio_encoder_name'] = [['other.safetensors']]
        r = self.prod.inspect(True)['servers'][0]['stages']['lipsync']
        self.assertFalse(r['ready'])
        self.assertTrue(any('wan2.2_s2v' in p for p in r['problems']))
        self.assertTrue(any('wav2vec2' in p for p in r['problems']))

    def test_lipsync_rejects_long_shots_without_queuing(self):
        sid = self.shot(duration=8)
        (Path(self.tmp.name) / 'keyframe.png').write_bytes(b'test')
        tid = self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','keyframe.png')", (sid,))
        self.api.approve({'take_id': tid})
        r = self.api.generate({'stage': 'lipsync', 'shot_ids': [sid]})
        self.assertEqual(r['queued'], 0)
        self.assertTrue(any('Split the shot' in p for p in r['skipped']))

    def test_dispatch_skips_incompatible_first_last_frame_jobs(self):
        sid = self.shot(end_next=True)
        del self.nodes['WanFirstLastFrameToVideo']
        self.prod.inspect(True)
        self.assertFalse(self.prod.ready('animate', [sid]))
        self.assertTrue(self.prod.ready('keyframe', [sid]))
        blocked = self.st.db.x("INSERT INTO jobs(shot_id,stage,status,priority) VALUES(?,'animate','pending',10)", (sid,))
        ready = self.st.db.x("INSERT INTO jobs(shot_id,stage,status,priority) VALUES(?,'keyframe','pending',0)", (sid,))
        inst = self.st.db.one('SELECT id FROM instances')['id']
        self.st.health[inst] = {'ok': True}
        done, picked = threading.Event(), []
        def execute(jid, server):
            picked.append(jid)
            done.set()
        self.st.worker.execute = execute
        self.st.worker.dispatch()
        self.assertTrue(done.wait(1))
        self.assertEqual(picked, [ready])
        self.assertEqual(self.st.db.one('SELECT status FROM jobs WHERE id=?', (blocked,))['status'], 'pending')

    def test_revision_retains_takes_and_requires_fresh_preview(self):
        self.api.script_draft({'script': '# Pier\n\nAIKO: Welcome home.\n\nThe sea sparkles.'})
        old = self.st.ordered_shots()
        text = '# Pier\n\nAIKO: Welcome back.\n\nThe sea sparkles.\n\nA gull takes flight.'
        preview = self.api.script_revision({'script': text})
        selections = [{'index': r['index'], 'shot_id': r['shot_id'], 'apply': r['change'] != 'unchanged'} for r in preview['rows']]
        self.api.script_revision({'script': text, 'token': preview['token'], 'apply': True, 'selections': selections})
        new = self.st.ordered_shots()
        self.assertEqual(len(new), 3)
        self.assertEqual(new[0]['id'], old[0]['id'])
        self.assertEqual(new[1]['id'], old[1]['id'])
        self.assertEqual(new[0]['dialogue'], 'AIKO: Welcome back.')
        with self.assertRaisesRegex(RuntimeError, 'changed since'):
            self.api.script_revision({'script': text, 'token': preview['token'], 'apply': True, 'selections': selections})

    def test_revision_can_reorder_without_duplicate_targets(self):
        self.api.script_draft({'script': '# Pier\n\nThe sea sparkles.\n\nA gull takes flight.'})
        old = self.st.ordered_shots()
        text = '# Pier\n\nA gull takes flight.\n\nThe sea sparkles.'
        preview = self.api.script_revision({'script': text})
        self.assertEqual([r['shot_id'] for r in preview['rows']], [old[1]['id'], old[0]['id']])
        self.api.script_revision({'script': text, 'token': preview['token'], 'apply': True,
                                 'selections': [dict(index=r['index'], shot_id=r['shot_id'], apply=False) for r in preview['rows']]})
        self.assertEqual([s['id'] for s in self.st.ordered_shots()], [old[1]['id'], old[0]['id']])

    def test_invalid_revision_is_rejected_before_any_mutation(self):
        sid = self.shot()
        preview = self.api.script_revision({'script': 'Changed.\n\nAnother.'})
        before = self.st.ordered_shots()
        with self.assertRaisesRegex(RuntimeError, 'only once'):
            self.api.script_revision({'script': 'Changed.\n\nAnother.', 'token': preview['token'], 'apply': True,
                                     'selections': [dict(index=i, shot_id=sid, apply=True) for i in (0, 1)]})
        self.assertEqual(self.st.ordered_shots(), before)

    def test_provenance_flags_changes_without_discarding_takes(self):
        sid = self.shot()
        s = self.st.ordered_shots()[0]
        sig = self.prod.signature(s, 'keyframe')
        tid = self.st.db.x("INSERT INTO takes(shot_id,stage,path,job_id,input_signature) VALUES(?,'keyframe','test.png',1,?)", (sid, sig))
        self.api.approve({'take_id': tid})
        self.assertTrue(self.api.state()['shots'][0]['keyframe_approved'])
        self.assertEqual(self.api.state()['shots'][0]['outdated'], [])
        self.api.shot_save({'id': sid, 'keyframe_prompt': 'Stormy sea'})
        changed = self.api.state()['shots'][0]
        self.assertIn('keyframe', changed['outdated'])
        self.assertFalse(changed['keyframe_approved'])
        self.assertEqual(changed['keyframe_take'], tid)
        self.assertEqual(self.st.db.one('SELECT COUNT(*) n FROM takes')['n'], 1)
        self.st.db.x('UPDATE takes SET input_signature=NULL')
        self.assertEqual(self.api.state()['shots'][0]['takes'][0]['provenance'], 'unknown')

    def test_lipsync_does_not_invalidate_itself_on_selection(self):
        sid = self.shot()
        s = self.st.ordered_shots()[0]
        sig = self.prod.signature(s, 'lipsync')
        tid = self.st.db.x("INSERT INTO takes(shot_id,stage,path,job_id,input_signature) VALUES(?,'lipsync','video.mp4',1,?)", (sid, sig))
        self.api.approve({'take_id': tid})
        self.assertNotIn('lipsync', self.api.state()['shots'][0]['outdated'])

    def test_scene_plan_persists_and_stops_for_approval(self):
        sid = self.shot()
        plan = self.prod.plan({'scene': 'Pier', 'priority': 8})['id']
        self.prod.tick()  # no voices -> keyframes
        self.prod.tick()  # queue keyframe
        p = self.st.db.one('SELECT * FROM production_plans WHERE id=?', (plan,))
        self.assertEqual(p['phase'], 'approval')
        ids = json.loads(p['jobs'])
        self.assertEqual(len(ids), 2)
        self.assertEqual(self.st.db.one('SELECT priority FROM jobs WHERE id=?', (ids[0],))['priority'], 8)
        self.st.db.x("UPDATE jobs SET status='done'")
        self.prod.tick()
        p = self.st.db.one('SELECT * FROM production_plans WHERE id=?', (plan,))
        self.assertEqual(p['phase'], 'approval')
        self.assertIn('approve', p['detail'])
        self.prod.plan({'action': 'cancel', 'id': plan})
        self.assertEqual(self.st.db.one('SELECT status FROM production_plans WHERE id=?', (plan,))['status'], 'cancelled')

    def test_deleting_shot_removes_saved_approval(self):
        sid = self.shot()
        self.st.db.x('INSERT INTO production_approvals VALUES(?,?)', (sid, 'old approval'))
        self.api.shot_delete({'ids': [sid]})
        self.assertIsNone(self.st.db.one('SELECT * FROM production_approvals WHERE shot_id=?', (sid,)))

    def test_queue_priority_only_changes_pending_jobs(self):
        jid = self.st.db.x("INSERT INTO jobs(stage,status) VALUES('keyframe','pending')")
        self.api.job_action({'action': 'priority', 'ids': [jid], 'priority': 99})
        self.assertEqual(self.st.db.one('SELECT priority FROM jobs WHERE id=?', (jid,))['priority'], 10)
        self.st.db.x("UPDATE jobs SET status='running' WHERE id=?", (jid,))
        self.api.job_action({'action': 'priority', 'ids': [jid], 'priority': -5})
        self.assertEqual(self.st.db.one('SELECT priority FROM jobs WHERE id=?', (jid,))['priority'], 10)


if __name__ == '__main__':
    unittest.main()
