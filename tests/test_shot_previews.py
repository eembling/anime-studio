"""Exercise the actual cached MP4's streams, audible samples and source timing."""
import base64
import array
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class ShotPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.st=app.Studio(self.temp.name);self.addCleanup(self.st.db.conn.close)
        self.addCleanup(self.st.shot_previews.shutdown)
        self.api=app.API(self.st)
        self.sid=self.api.shot_save({'scene':'Test','kind':'motion','duration':4})['id']
        self.video=self.st.data/'shots/video.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=red:s=128x72:r=16:d=2','-c:v','libx264',str(self.video)])
        self.tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate',?,16,32)",(self.sid,self.st.rel(self.video)))
        self.st.db.x('UPDATE shots SET clip_take=? WHERE id=?',(self.tid,self.sid))
        voice=self.st.data/'voices/voice.wav'
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=440:duration=4','-af','volume=2',str(voice)])
        self.st.db.x("UPDATE shots SET audio='voices/voice.wav' WHERE id=?",(self.sid,))

    def preview(self,tid=None):
        body=dict(shot_id=self.sid,take_id=tid or self.tid)
        for _ in range(200):
            result=self.st.shot_previews.request(body)
            if result['status']=='ready':return result,self.st.abs(result['url'].removeprefix('/media/'))
            if result['status']=='error':self.fail(result['error'])
            time.sleep(.02)
        self.fail('Preview did not finish')

    def rms(self,path,start=0,duration=.2):
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-t',str(duration),'-i',str(path),'-vn','-f','f32le','-ac','1','-ar','48000','-'])
        samples=array.array('f');samples.frombytes(raw)
        return (sum(x*x for x in samples)/max(1,len(samples)))**.5

    def test_one_file_has_audible_audio_original_fps_and_duration(self):
        result,path=self.preview()
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]))
        self.assertEqual({s['codec_type'] for s in info['streams']},{'audio','video'})
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        self.assertEqual(video['avg_frame_rate'],'16/1')
        self.assertAlmostEqual(float(info['format']['duration']),2,delta=.05)
        self.assertGreater(self.rms(path,.5),.02)
        self.assertEqual(result['audio_label'],'Dialogue')
        cached,_=self.preview();self.assertEqual(cached['url'],result['url'])

    def test_positive_offset_silences_only_the_intro(self):
        self.st.db.x('UPDATE shots SET dialogue_offset=.5 WHERE id=?',(self.sid,))
        _,path=self.preview();self.assertLess(self.rms(path,0),.001);self.assertGreater(self.rms(path,.7),.02)

    def test_continuation_uses_later_dialogue_and_does_not_speed_up(self):
        # Distinct silence/tone regions reveal an incorrect source offset.
        voice=self.st.data/'voices/later.wav'
        app.ffmpeg(['-f','lavfi','-i','anullsrc=r=48000:cl=mono:d=2','-f','lavfi','-i','sine=frequency=880:duration=2',
                    '-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]',str(voice)])
        self.st.db.x("UPDATE shots SET audio='voices/later.wav' WHERE id=?",(self.sid,))
        tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames,segment) VALUES(?,'continue',?,16,32,0)",(self.sid,self.st.rel(self.video)))
        result,path=self.preview(tid);self.assertAlmostEqual(result['source_offset'],1.9375)
        self.assertGreater(self.rms(path,.5),.01)

    def test_missing_dialogue_is_an_error_instead_of_silent_fallback(self):
        self.st.db.x("UPDATE shots SET audio='voices/missing.wav' WHERE id=?",(self.sid,))
        body=dict(shot_id=self.sid,take_id=self.tid)
        for _ in range(100):
            result=self.st.shot_previews.request(body)
            if result['status']=='error':break
            time.sleep(.02)
        self.assertEqual(result['status'],'error')

    def test_wrong_shot_take_is_rejected(self):
        other=self.api.shot_save({'kind':'motion','duration':2})['id']
        with self.assertRaises(ValueError):self.st.shot_previews.request(dict(shot_id=other,take_id=self.tid))

    def test_dialogue_edit_invalidates_cached_media(self):
        first,_=self.preview();self.st.db.x('UPDATE shots SET dialogue_offset=.2 WHERE id=?',(self.sid,))
        second,_=self.preview();self.assertNotEqual(first['url'],second['url'])

    def test_timed_sound_is_in_combined_asset(self):
        self.st.db.x("UPDATE shots SET audio='' WHERE id=?",(self.sid,))
        self.api.audio_cue_save(dict(name='Tone',kind='sfx',file=dict(name='tone.wav',data=base64.b64encode((self.st.data/'voices/voice.wav').read_bytes()).decode()),start=.5,offset=0,duration=1,volume=1))
        result,path=self.preview();self.assertLess(self.rms(path,0),.001);self.assertGreater(self.rms(path,.7),.02)
        self.assertIn('timed sounds',result['audio_label'])

    def test_embedded_clip_audio_is_preserved_without_assigned_dialogue(self):
        clip=self.st.data/'shots/with-audio.mp4'
        app.ffmpeg(['-i',str(self.video),'-i',str(self.st.data/'voices/voice.wav'),'-map','0:v','-map','1:a',
                    '-c:v','copy','-c:a','aac','-t','2',str(clip)])
        self.st.db.x('UPDATE takes SET path=? WHERE id=?',(self.st.rel(clip),self.tid))
        self.st.db.x("UPDATE shots SET audio='' WHERE id=?",(self.sid,))
        result,path=self.preview();self.assertEqual(result['audio_label'],'Clip audio');self.assertGreater(self.rms(path,.5),.02)

    def test_muted_dialogue_is_explicit_and_does_not_silently_ignore_mixer(self):
        mixer=self.st.finishing.mixer();mixer['dialogue']['mute']=True
        self.st.db.x("INSERT OR REPLACE INTO settings VALUES('audio_mixer',?)",(json.dumps(mixer),))
        result,path=self.preview();self.assertTrue(result['mixer_silenced']);self.assertLess(self.rms(path,.5),.001)

    def test_same_path_voice_replacement_invalidates_cache(self):
        first,_=self.preview()
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=880:duration=4',str(self.st.data/'voices/voice.wav')])
        second,_=self.preview();self.assertNotEqual(first['url'],second['url'])

    def test_cleared_cache_rebuilds_instead_of_returning_missing_media(self):
        first,path=self.preview();path.unlink()
        second,new_path=self.preview();self.assertEqual(first['url'],second['url']);self.assertTrue(new_path.is_file())

if __name__=='__main__':unittest.main()
