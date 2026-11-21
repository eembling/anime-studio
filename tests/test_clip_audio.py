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

    def test_s2v_cpu_override_is_changed_to_gpu_for_new_jobs(self):
        row=self.st.wf_row('lipsync');workflow=json.loads(row['json'])
        workflow['2']['inputs']['device']='cpu'
        self.st.db.x("UPDATE workflows SET json=? WHERE stage='lipsync'",(json.dumps(workflow),))
        _,job=self.queue();meta=self.st.snapshots.meta(job)
        self.assertEqual(meta['workflow']['2']['inputs']['device'],'default')
        self.assertEqual(json.loads(self.st.wf_row('lipsync')['json'])['2']['inputs']['device'],'cpu')

    def test_gpu_preference_does_not_mutate_captured_retry_inputs(self):
        _,job=self.queue();meta=self.st.snapshots.meta(job)
        meta['workflow']['2']['inputs']['device']='cpu'
        self.st.db.x('UPDATE jobs SET snapshot_json=? WHERE id=?',(json.dumps(meta),job['id']))
        self._complete(job);repeat=self.st.snapshots.repeat(job['id'])
        saved=self.st.snapshots.meta(self.st.db.one('SELECT * FROM jobs WHERE id=?',(repeat,)))
        self.assertEqual(saved['workflow']['2']['inputs']['device'],'cpu')

    def test_s2v_recipe_cpu_control_cannot_reapply_cpu_after_gpu_capture(self):
        import copy
        recipe=self.st.creative.insert_recipe('GPU voice','animate',dict(workflow=copy.deepcopy(app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf']),
            fps=16,max_frames=77,purpose='generation',requirements='',controls=[dict(node='2',field='device',type='text',default='cpu',label='Device')]))
        self.st.creative.recipe_assign(dict(shot_id=self.sid,stage='animate',recipe_id=recipe['id'],values={'2.device':'cpu'}))
        _,job=self.queue();meta=self.st.snapshots.meta(job)
        key,params=self.worker.compose(job);params['images']={slot:Path(file).name for slot,file in params['files'].items()}
        graph=self.st.snapshots.patch(job,app.build_workflow(meta['workflow'],key,params))
        self.assertEqual(graph['2']['inputs']['device'],'default')

    def test_sampling_comparison_pins_same_seed_and_different_steps(self):
        _,first=self.queue(seed=123,voice_quality='standard')
        _,second=self.queue(seed=123,voice_quality='fine')
        a=self.st.snapshots.meta(first);b=self.st.snapshots.meta(second)
        self.assertEqual(first['seed'],second['seed'])
        self.assertEqual(a['workflow']['30']['inputs']['steps'],20)
        self.assertEqual(b['workflow']['30']['inputs']['steps'],30)
        self.assertEqual(a['params']['files'].keys(),b['params']['files'].keys())
        self.assertIn('clear mouth articulation',a['params']['prompt'])
        self.api.shot_save(dict(id=self.sid,voice_quality='fine',voice_direction='Smile while speaking'))
        self.assertEqual(self.st.snapshots.meta(first),a)

    def test_native_continuation_captures_bounded_motion_video(self):
        root=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/raw.mp4',16,32)",(self.sid,))
        self.st.db.x('UPDATE shots SET clip_take=?,duration=7 WHERE id=?',(root,self.sid))
        self.st.sections.action(dict(action='save',shot_id=self.sid,segment=0,prompt='Keep speaking'))
        self.st.production.http=lambda *args,**kwargs:{**{node['class_type']:{'input':{}} for presets in app.STARTERS.values() for preset in presets.values() for node in preset['wf'].values()},'LoadVideo':{'input':{}},'GetVideoComponents':{'input':{}}}
        _,job=self.queue('continue');meta=self.st.snapshots.meta(job)
        key,params=self.worker.compose(job)
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(params['files']['video'])]))
        self.assertEqual(len(info['streams']),1)
        self.assertLessEqual(int(info['streams'][0]['nb_frames']),73)
        self.assertEqual(info['streams'][0]['r_frame_rate'],'16/1')
        params['images']={slot:Path(file).name for slot,file in params['files'].items()}
        graph=app.build_workflow(meta['workflow'],key,params)
        component=graph[graph['22']['inputs']['ref_motion'][0]]
        loader=graph[component['inputs']['video'][0]]
        self.assertEqual(loader['inputs']['file'],params['images']['video'])
        self.assertAlmostEqual(meta['clip_audio']['source_offset'],1.9375)
        captured=params['files']['video'].read_bytes();self.movie.unlink()
        self.assertEqual(self.worker.compose(job)[1]['files']['video'].read_bytes(),captured)

    def test_existing_clip_request_never_regenerates_from_image(self):
        self.api.shot_save(dict(id=self.sid,lip_sync_mode='existing'))
        with self.assertRaisesRegex(ValueError,'tested video-conditioned workflow'):
            self.api.generate(dict(stage='lipsync',shot_ids=[self.sid]))
        self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])

    def test_explicit_voice_animation_rejects_mux_only_fallback(self):
        self.st.db.x("DELETE FROM workflows WHERE stage='lipsync'")
        with self.assertRaisesRegex(ValueError,'Voice-driven animation is unavailable'):
            self.api.generate(dict(stage='animate',shot_ids=[self.sid],require_voice_animation=True))
        self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])

    def test_existing_video_workflow_captures_video_and_voice_without_keyframe(self):
        import copy
        workflow=copy.deepcopy(app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        del workflow['10'];del workflow['22']['inputs']['ref_image']
        workflow['60']={'class_type':'LoadVideo','inputs':{'file':'source.mp4'},'_meta':{'title':'VIDEO_IN'}}
        workflow['61']={'class_type':'GetVideoComponents','inputs':{'video':['60',0]}}
        workflow['22']['inputs']['control_video']=['61',0]
        self.st.db.x("UPDATE workflows SET json=?,name='tested-video-workflow' WHERE stage='lipsync'",(json.dumps(workflow),))
        root=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/raw.mp4',16,32)",(self.sid,))
        self.st.db.x("UPDATE shots SET keyframe_take=NULL,clip_take=?,duration=2,lip_sync_mode='existing' WHERE id=?",(root,self.sid))
        self.assertEqual(app.check_workflow(workflow,'lipsync'),[])
        _,job=self.queue('lipsync');meta=self.st.snapshots.meta(job)
        key,params=self.worker.compose(job)
        self.assertEqual(params['files']['video'].read_bytes(),self.movie.read_bytes())
        self.assertNotIn('start',params['files'])
        params['images']={slot:Path(file).name for slot,file in params['files'].items()}
        self.assertEqual(app.build_workflow(meta['workflow'],key,params)['60']['inputs']['file'],params['images']['video'])

    def test_existing_clip_can_use_assigned_recipe_without_replacing_animation_starter(self):
        import copy
        workflow=copy.deepcopy(app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        workflow['60']={'class_type':'LoadVideo','inputs':{'file':'source.mp4'},'_meta':{'title':'VIDEO_IN'}}
        workflow['61']={'class_type':'GetVideoComponents','inputs':{'video':['60',0]}}
        workflow['22']['inputs']['control_video']=['61',0]
        recipe=self.st.creative.insert_recipe('Video conditioning','lipsync',dict(workflow=workflow,fps=16,max_frames=77,purpose='generation',requirements='',controls=[]))
        self.st.creative.recipe_assign(dict(shot_id=self.sid,stage='lipsync',recipe_id=recipe['id'],values={}))
        root=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/raw.mp4',16,32)",(self.sid,))
        self.st.db.x("UPDATE shots SET clip_take=?,duration=2,lip_sync_mode='existing' WHERE id=?",(root,self.sid))
        _,job=self.queue('lipsync');meta=self.st.snapshots.meta(job)
        self.assertIn('video',meta['params']['files'])
        self.assertEqual(self.st.wf_row('lipsync')['name'],'wan22_s2v_14b_fp8')
        self.assertEqual(self.st.creative.meta(job)['recipe']['id'],recipe['id'])
        self.st.db.x("DELETE FROM shot_recipes WHERE shot_id=? AND stage='lipsync'",(self.sid,))
        result=self.api.generate(dict(stage='lipsync',shot_ids=[self.sid],recipe_id=recipe['id'],seed=123))
        self.assertEqual(result['queued'],1)

    def test_closeup_is_captured_and_does_not_modify_original(self):
        original=(self.st.data/'shots/key.png').read_bytes()
        self.api.shot_save(dict(id=self.sid,voice_framing='closeup'))
        _,job=self.queue();params=self.worker.compose(job)[1]
        self.assertNotEqual(params['files']['start'].read_bytes(),original)
        self.assertEqual((self.st.data/'shots/key.png').read_bytes(),original)
        self.assertIn('dialogue close-up',params['prompt'])

    def test_custom_crop_is_positioned_and_captured(self):
        key = self.st.data/'shots/key.png'
        app.ffmpeg(['-f', 'lavfi', '-i', 'color=blue:s=128x72',
                    '-vf', 'drawbox=x=0:y=0:w=64:h=72:color=red:t=fill', '-frames:v', '1', str(key)])
        self.api.shot_save(dict(id=self.sid, voice_framing='closeup', voice_crop={'x':0,'y':50,'size':40}))
        _, job = self.queue()
        crop = self.worker.compose(job)[1]['files']['start']
        pixels = subprocess.check_output(['ffmpeg','-v','error','-i',str(crop),'-f','rawvideo','-pix_fmt','rgb24','-'])
        self.assertGreater(pixels[0], 200)
        self.assertLess(pixels[2], 15)
        self.api.shot_save(dict(id=self.sid, voice_crop={'x':100,'y':50,'size':40}))
        self.assertEqual(self.worker.compose(job)[1]['files']['start'].read_bytes(), crop.read_bytes())

    def test_native_s2v_rejects_incorrect_model_cadence(self):
        self.st.db.x("UPDATE workflows SET fps=24 WHERE stage='lipsync'")
        with self.assertRaisesRegex(ValueError,'requires 16 fps'):
            self.api.generate(dict(stage='animate',shot_ids=[self.sid]))
        self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])

    def test_invalid_quality_cannot_enter_project_or_queue(self):
        with self.assertRaisesRegex(ValueError,'Invalid voice quality'):
            self.api.shot_save(dict(id=self.sid,voice_quality='fastest'))
        with self.assertRaisesRegex(ValueError,'Invalid voice quality'):
            self.api.generate(dict(stage='animate',shot_ids=[self.sid],voice_quality='fastest'))

    def test_custom_sampling_controls_survive_motion_and_direction_changes(self):
        import copy
        workflow=copy.deepcopy(app.STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        recipe=self.st.creative.insert_recipe('Pinned performance','animate',dict(workflow=workflow,fps=16,max_frames=77,purpose='generation',requirements='',controls=[dict(node='30',field='steps',type='number',default=27,label='Steps',min=1,max=100)]))
        self.st.creative.recipe_assign(dict(shot_id=self.sid,stage='animate',recipe_id=recipe['id'],values={'30.steps':27}))
        _,job=self.queue(voice_quality='fine');meta=self.st.snapshots.meta(job)
        self.assertEqual(meta['controls']['30.steps'],27)
        key,params=self.worker.compose(job);params['images']={slot:Path(file).name for slot,file in params['files'].items()}
        graph=self.st.snapshots.patch(job,app.build_workflow(meta['workflow'],key,params))
        self.assertEqual(graph['30']['inputs']['steps'],27)

if __name__=='__main__':unittest.main()
