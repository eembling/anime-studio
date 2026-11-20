"""Audio participates in captured ComfyUI inputs and in the generated take itself."""
import array
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app

@unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
class ClipAudioTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.st=app.Studio(self.temp.name);self.addCleanup(self.st.db.conn.close)
        self.addCleanup(self.st.shot_previews.shutdown)
        self.api=app.API(self.st);self.worker=app.Worker(self.st)
        self.st.production.http=lambda *args,**kwargs:{node['class_type']:{'input':{}} for presets in app.STARTERS.values() for preset in presets.values() for node in preset['wf'].values()}
        self.sid=self.api.shot_save(dict(kind='motion',scene='Test',duration=4,motion_prompt='Speak naturally'))['id']
        key=self.st.data/'shots/key.png';app.ffmpeg(['-f','lavfi','-i','color=blue:s=128x72','-frames:v','1',str(key)])
        tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','shots/key.png')",(self.sid,))
        self.st.db.x("UPDATE shots SET keyframe_take=?,audio='voices/voice.wav',audio_mode='file' WHERE id=?",(tid,self.sid))
        self.voice=self.st.data/'voices/voice.wav'
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=440:duration=6',str(self.voice)])
        self.movie=self.st.data/'shots/raw.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=red:s=128x72:r=16:d=2','-c:v','libx264',str(self.movie)])

    def queue(self,stage='animate',**kwargs):
        result=self.api.generate(dict(stage=stage,shot_ids=[self.sid],**kwargs));self.assertTrue(result['ok'],result)
        self.assertEqual(result['queued'],1,result)
        return result,self.st.db.one('SELECT * FROM jobs ORDER BY id DESC LIMIT 1')

    def rms(self,file,start=0):
        pcm=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-t','0.2','-i',str(file),'-vn','-ac','1','-ar','48000','-f','f32le','-'])
        values=array.array('f');values.frombytes(pcm)
        return (sum(x*x for x in values)/max(1,len(values)))**.5

    def test_animate_automatically_captures_voice_and_uses_ready_s2v(self):
        result,job=self.queue();meta=self.st.snapshots.meta(job)
        self.assertEqual(meta['key'],'lipsync');self.assertTrue(meta['audio_conditioned'])
        self.assertEqual(result['audio_included'],1);self.assertEqual(result['audio_conditioned'],1)
        key,params=self.worker.compose(job)
        self.assertGreater(self.rms(params['files']['audio'],.5),.01)
        params['images']={slot:Path(file).name for slot,file in params['files'].items()}
        graph=app.build_workflow(meta['workflow'],key,params)
        loader=next(node for node in graph.values() if node['class_type']=='LoadAudio')
        self.assertEqual(loader['inputs']['audio'],params['images']['audio'])

    def test_queued_voice_survives_selection_and_source_changes(self):
        _,job=self.queue();meta=self.st.snapshots.meta(job);captured=self.st.abs(meta['clip_audio']['path']).read_bytes()
        app.ffmpeg(['-f','lavfi','-i','anullsrc=r=48000:cl=mono:d=6',str(self.voice)])
        self.st.db.x("UPDATE shots SET audio=NULL WHERE id=?",(self.sid,))
        self.assertEqual(self.st.abs(meta['clip_audio']['path']).read_bytes(),captured)
        self.assertGreater(self.rms(self.worker.compose(job)[1]['files']['audio'],.5),.01)
        repeated=self.st.snapshots.repeat(self._complete(job)['id'])
        self.assertEqual(self.st.snapshots.meta(self.st.db.one('SELECT * FROM jobs WHERE id=?',(repeated,)))['clip_audio'],meta['clip_audio'])

    def _complete(self,job):
        self.st.db.x("UPDATE jobs SET status='done' WHERE id=?",(job['id'],));return job

    def test_generated_take_contains_voice_without_retiming_video(self):
        _,job=self.queue();source=self.st.data/'jobs/result.mp4';shutil.copy2(self.movie,source)
        self.worker.finish(job,'lipsync',[source])
        take=self.st.db.one("SELECT * FROM takes WHERE job_id=?",(job['id'],));output=self.st.abs(take['path'])
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(output)]))
        self.assertEqual({row['codec_type'] for row in info['streams']},{'audio','video'})
        self.assertEqual(next(row for row in info['streams'] if row['codec_type']=='video')['avg_frame_rate'],'16/1')
        self.assertAlmostEqual(float(info['format']['duration']),2,delta=.05);self.assertGreater(self.rms(output,.5),.01)

    def test_unavailable_audio_model_keeps_voice_in_clip_and_reports_fallback(self):
        self.st.db.x("DELETE FROM workflows WHERE stage='lipsync'")
        result,job=self.queue();meta=self.st.snapshots.meta(job)
        self.assertEqual(meta['key'],'animate');self.assertFalse(meta['audio_conditioned']);self.assertTrue(meta['clip_audio'])
        self.assertIn('audio-driven animation is unavailable',result['audio_notes'][0])
        source=self.st.data/'jobs/result.mp4';shutil.copy2(self.movie,source);self.worker.finish(job,'animate',[source])
        take=self.st.db.one('SELECT * FROM takes WHERE job_id=?',(job['id'],));self.assertGreater(self.rms(self.st.abs(take['path']),.5),.01)

    def test_dialogue_offset_is_applied_to_model_input_and_generated_take(self):
        self.st.db.x('UPDATE shots SET dialogue_offset=.5 WHERE id=?',(self.sid,))
        _,job=self.queue();params=self.worker.compose(job)[1]
        self.assertLess(self.rms(params['files']['audio']),.001);self.assertGreater(self.rms(params['files']['audio'],.7),.01)
        source=self.st.data/'jobs/result.mp4';shutil.copy2(self.movie,source);self.worker.finish(job,'lipsync',[source])
        take=self.st.db.one('SELECT * FROM takes WHERE job_id=?',(job['id'],));file=self.st.abs(take['path'])
        self.assertLess(self.rms(file),.001);self.assertGreater(self.rms(file,.7),.01)

    def test_continuation_uses_its_voice_section_and_audio_model_capacity(self):
        root=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/raw.mp4',16,32)",(self.sid,))
        self.st.db.x('UPDATE shots SET clip_take=?,duration=7 WHERE id=?',(root,self.sid))
        self.st.sections.action(dict(action='save',shot_id=self.sid,segment=0,prompt='Finish the sentence'))
        details=self.st.sections.details(self.sid);self.assertEqual(details['key'],'lipsync')
        _,job=self.queue('continue');meta=self.st.snapshots.meta(job)
        self.assertAlmostEqual(meta['clip_audio']['source_offset'],1.9375)
        self.assertEqual(meta['key'],'lipsync');self.assertLessEqual(meta['params']['length'],77)
        self.assertGreater(self.rms(self.worker.compose(job)[1]['files']['audio'],.5),.01)

    def test_no_selected_voice_retains_image_to_video_generation(self):
        self.st.db.x('UPDATE shots SET audio=NULL WHERE id=?',(self.sid,))
        _,job=self.queue();meta=self.st.snapshots.meta(job)
        self.assertEqual(meta['key'],'animate');self.assertIsNone(meta['clip_audio']);self.assertNotIn('audio',meta['params']['files'])

    def test_missing_selected_voice_blocks_queue_instead_of_ignoring_it(self):
        self.voice.unlink()
        with self.assertRaisesRegex(ValueError,'selected shot dialogue is missing'):self.api.generate(dict(stage='animate',shot_ids=[self.sid]))
        self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])

    def test_unconnected_audio_loader_is_not_used_instead_of_model_input(self):
        import copy
        workflow=copy.deepcopy(app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        workflow['0']={'class_type':'LoadAudio','inputs':{'audio':'unused.wav'}}
        self.assertEqual(app.detect_slots(workflow,'lipsync')['audio_in'],['11'])
        self.assertEqual(app.detect_slots(workflow,'animate')['audio_in'],['11'])

    def test_finished_voice_change_marks_animation_and_continuations_outdated(self):
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.sid,))
        before=self.st.production.signature(shot,'animate')
        self.st.db.x('UPDATE shots SET dialogue_offset=.25 WHERE id=?',(self.sid,))
        changed=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.sid,))
        self.assertNotEqual(before,self.st.production.signature(changed,'animate'))

    def test_ready_audio_model_can_generate_when_image_only_model_is_unavailable(self):
        self.st.production.http=lambda *args,**kwargs:{node['class_type']:{'input':{}} for node in app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'].values()}
        result,_=self.queue();self.assertEqual(result['audio_conditioned'],1)

    def test_s2v_minimum_padding_does_not_extend_the_saved_take(self):
        self.st.db.x('UPDATE shots SET duration=1 WHERE id=?',(self.sid,))
        _,job=self.queue();meta=self.st.snapshots.meta(job)
        source=self.st.data/'jobs/padded.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=red:s=128x72:r=16','-frames:v','73','-c:v','libx264',str(source)])
        self.worker.finish(job,'lipsync',[source])
        take=self.st.db.one('SELECT * FROM takes WHERE job_id=?',(job['id'],));output=self.st.abs(take['path'])
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(output)]))
        video=next(stream for stream in info['streams'] if stream['codec_type']=='video')
        self.assertEqual(int(video['nb_frames']),meta['params']['length'])
        self.assertAlmostEqual(float(video['duration']),meta['params']['length']/16,delta=.001)

if __name__=='__main__':unittest.main()
