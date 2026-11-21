"""Native 16 fps project timing and migration of legacy defaults."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app
from timeline import upgrade_legacy_fps

class FrameRateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.st=app.Studio(self.temp.name);self.addCleanup(self.st.db.conn.close)
        self.addCleanup(self.st.shot_previews.shutdown)

    def test_default_is_16_and_migration_preserves_explicit_later_choice(self):
        self.assertEqual(self.st.settings()['out_fps'],'16')
        self.assertEqual(upgrade_legacy_fps({'out_fps':'24'})['out_fps'],'16')
        self.assertEqual(upgrade_legacy_fps({'out_fps':'24','studio_16fps_v1':'1'})['out_fps'],'24')
        self.assertEqual(upgrade_legacy_fps({'out_fps':'30'})['out_fps'],'30')

    def test_existing_project_legacy_default_is_upgraded_on_start(self):
        self.st.db.x("UPDATE settings SET value='24' WHERE key='out_fps'")
        self.st.db.x("DELETE FROM settings WHERE key='studio_16fps_v1'")
        reopened=app.Studio(self.temp.name)
        try:self.assertEqual(reopened.settings()['out_fps'],'16')
        finally:reopened.shot_previews.shutdown();reopened.db.conn.close()

    def test_captured_export_worker_does_not_migrate_its_frozen_timing(self):
        self.st.db.x("UPDATE settings SET value='24' WHERE key='out_fps'")
        self.st.db.x("DELETE FROM settings WHERE key='studio_16fps_v1'")
        clone=app.Studio(self.temp.name,export_worker=True)
        try:self.assertEqual(clone.settings()['out_fps'],'24')
        finally:clone.shot_previews.shutdown();clone.db.conn.close()

    def test_old_episode_or_checkpoint_cannot_restore_legacy_24_fps(self):
        snapshot=self.st.editor.snapshot()
        snapshot['settings']=[dict(row,value='24') if row['key']=='out_fps' else row for row in snapshot['settings'] if row['key']!='studio_16fps_v1']
        self.st.editor.apply(snapshot)
        self.assertEqual(self.st.settings()['out_fps'],'16')
        self.assertEqual(self.st.settings()['studio_16fps_v1'],'1')

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_episode_and_animatic_are_both_encoded_at_16_fps(self):
        st=self.st;api=app.API(st)
        for key,value in [('out_width','64'),('out_height','64')]:st.db.x('UPDATE settings SET value=? WHERE key=?',(value,key))
        sid=api.shot_save(dict(kind='motion',duration=1.5))['id']
        clip=st.data/'shots/clip.mp4';voice=st.data/'voices/dialogue.wav'
        app.ffmpeg(['-f','lavfi','-i','color=blue:s=64x64:r=16:d=1.5','-c:v','libx264',str(clip)])
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=440:duration=1.5',str(voice)])
        tid=st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/clip.mp4',16,24)",(sid,))
        st.db.x("UPDATE shots SET clip_take=?,audio='voices/dialogue.wav' WHERE id=?",(tid,sid))
        for preview in [False,True]:
            with self.subTest(preview=preview):
                app.assemble(st,False,0,preview=preview)
                self.assertIsNotNone(st.assembly['output'],st.assembly['log'])
                info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(st.abs(st.assembly['output']))]))
                video=next(row for row in info['streams'] if row['codec_type']=='video')
                self.assertEqual(video['avg_frame_rate'],'16/1')
                self.assertAlmostEqual(float(info['format']['duration']),1.5,delta=.05)
                self.assertTrue(any(row['codec_type']=='audio' for row in info['streams']))

if __name__=='__main__':unittest.main()
