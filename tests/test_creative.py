"""Behavior and FFmpeg checks for the five creative-production features."""
import base64
import copy
import io
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import wave
import zlib
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app
from creative import digest


def png(alpha=255):
    def chunk(tag, data):
        return struct.pack('>I', len(data))+tag+data+struct.pack('>I', zlib.crc32(tag+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',32,32,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([200,80,40,alpha])*32)*32))+chunk(b'IEND',b'')


class CreativeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=app.Studio(self.tmp.name);self.addCleanup(self.st.db.conn.close)
        self.st.worker=app.Worker(self.st)
        self.api=app.API(self.st);self.c=self.st.creative
        self.st.db.x('DELETE FROM instances')
        self.api.instance_save({'name':'Fixture GPU','url':'http://fixture:8188','roles':list(app.JOB_STAGES),'enabled':True})
        self.nodes={n['class_type']:{'input':{'required':{}}} for choices in app.STARTERS.values() for spec in choices.values() for n in spec['wf'].values()}
        self.nodes['ImageCompositeMasked']={'input':{'required':{}}}
        self.nodes['LoadImage']['input']['required']['image']=[['unrelated-server-image.png']]
        self.st.production.http=lambda *a,**kw:copy.deepcopy(self.nodes)
        self.aid=self.api.asset_save({'name':'Tobi','kind':'character','description':'Blue coat'})['id']
        self.ref='uploads/reference.png';self.st.abs(self.ref).write_bytes(png())
        self.st.db.x('UPDATE assets SET image=? WHERE id=?',(self.ref,self.aid))
        self.sid=self.api.shot_save({'scene':'Bridge','kind':'motion','duration':2,'asset_ids':[self.aid],'keyframe_prompt':'Tobi by the river','dialogue':'Tobi: Tobi waits here.'})['id']
        self.kid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,created) VALUES(?,'keyframe',?,?)",(self.sid,self.ref,time.time()))
        self.api.approve({'take_id':self.kid})

    def shot(self):return self.c.shot(self.sid)
    def line(self):return self.st.shot_lines(self.shot())[0]
    def recipe(self,stage='keyframe',**body):
        wf=json.loads(self.st.wf_row(stage)['json']);controls=[]
        if stage in ('keyframe','animate'):
            node=next(k for k,n in wf.items() if isinstance(n['inputs'].get('cfg'),(int,float)))
            controls=[{'node':node,'field':'cfg','label':'Guidance'}]
        ident=self.c.recipe_save({'name':'Fixture '+stage,'stage':stage,'controls':controls,**body})['id']
        return self.c.recipe(ident)
    def assign(self,recipe):return self.c.recipe_assign({'shot_id':self.sid,'stage':recipe['stage'],'recipe_id':recipe['id'],'values':{}})
    def audio(self):
        path=self.st.abs('voices/base.wav')
        with wave.open(str(path),'wb') as w:
            w.setparams((1,2,16000,0,'NONE','not compressed'))
            import math
            w.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*440*math.tau/16000))) for i in range(16000)))
        self.st.db.x('INSERT INTO voice_clips(asset_id,path,name,is_ref,transcript) VALUES(?,?,?,1,?)',(self.aid,'voices/base.wav','Reference','We are home.'))
        lines=self.st.shot_lines(self.shot());lines[0].update(audio='voices/base.wav',src='upload');self.st.save_lines(self.sid,lines)
        return path
    def job(self,jid):return self.st.db.one('SELECT * FROM jobs WHERE id=?',(jid,))
    def finish(self,job,path,special=False):
        self.st.db.x("UPDATE jobs SET status='running',started=?,instance_id=1 WHERE id=?",(time.time()-1,job['id']))
        job=self.job(job['id'])
        if not self.c.finish_special(job,self.c.meta(job)['key'],[path]):app.Worker(self.st).finish(job,self.c.meta(job)['key'],[path])
        self.st.db.x("UPDATE jobs SET status='done',finished=? WHERE id=?",(time.time(),job['id']))
        self.c.completed(self.job(job['id']),'done')
        return self.c.meta(job)['run_id']

    def test_reference_versions_are_immutable_and_scene_scoped(self):
        first=self.c.look_save({'asset_id':self.aid,'name':'Travel coat','notes':'Brass buttons'})
        self.st.db.x("UPDATE assets SET description='Red shirt' WHERE id=?",(self.aid,))
        second=self.c.look_save({'asset_id':self.aid,'name':'Travel coat','notes':'No buttons'})
        self.assertEqual((first['version'],second['version']),(1,2))
        self.c.look_bind({'scene':'Bridge','asset_id':self.aid,'look_id':first['id'],'view_index':0})
        _,p=app.Worker(self.st).compose({'stage':'keyframe','shot_id':self.sid,'seed':1,'id':99})
        self.assertIn('Blue coat',p['prompt']);self.assertIn('Brass buttons',p['prompt']);self.assertNotIn('Red shirt',p['prompt'])
        self.assertEqual(json.loads(self.st.db.one('SELECT json FROM asset_looks WHERE id=?',(first['id'],))['json'])['notes'],'Brass buttons')
        self.assertEqual(self.st.db.one('SELECT image FROM assets WHERE id=?',(self.aid,))['image'],self.ref)

    def test_continuity_review_invalidates_after_reference_or_picture_change(self):
        token=self.c.review_signature(self.shot())
        self.c.review({'shot_id':self.sid,'signature':token,'notes':'Costume matches'})
        self.assertTrue(self.c.state()['reviews'][0]['current'])
        self.api.shot_save({'id':self.sid,'keyframe_prompt':'New lighting'})
        self.assertFalse(self.c.state()['reviews'][0]['current'])
        with self.assertRaisesRegex(ValueError,'changed'):self.c.review({'shot_id':self.sid,'signature':token})

    def test_recipe_versions_and_queued_workflow_inputs_are_frozen(self):
        recipe=self.recipe();self.assign(recipe)
        result=self.api.generate({'stage':'keyframe','shot_ids':[self.sid],'takes':1,'seed':12})
        job=self.job(result['job_ids'][0]);key,p=app.Worker(self.st).compose(job)
        self.assertEqual(p['seed'],12);self.assertIn('Tobi by the river',p['prompt'])
        self.api.shot_save({'id':self.sid,'keyframe_prompt':'Different shot'})
        self.st.db.x("UPDATE workflows SET json='{}' WHERE stage='keyframe'")
        _,again=app.Worker(self.st).compose(job)
        self.assertEqual(p,again);self.assertEqual(json.loads(self.c.workflow(job)['json']),recipe['workflow'])
        next_version=self.c.recipe_save({'base_id':recipe['id'],'name':recipe['name'],'stage':'keyframe'})
        self.assertEqual(next_version['version'],2)
        self.assertEqual(self.c.recipe(recipe['id'])['workflow'],recipe['workflow'])

    def test_comparison_variants_have_distinct_controls_and_do_not_assign(self):
        recipe=self.recipe();control=recipe['controls'][0];key=control['node']+'.'+control['field']
        result=self.c.experiment({'recipe_id':recipe['id'],'shot_id':self.sid,'seed':71,'vary_seed':False,'variants':[{key:3},{key:5}]})
        self.assertEqual(result['queued'],2);self.assertIsNone(self.c.assignment(self.sid,'keyframe'))
        jobs=[self.job(i) for i in result['job_ids']]
        self.assertEqual([j['seed'] for j in jobs],[71,71])
        for job,value in zip(jobs,[3,5]):
            wf=self.c.patch_controls(job,json.loads(self.c.workflow(job)['json']))
            self.assertEqual(wf[control['node']]['inputs'][control['field']],value)
            self.assertTrue(self.c.meta(job)['manual'])

    def test_invalid_comparison_is_not_partially_queued(self):
        recipe=self.recipe();key=recipe['controls'][0]['node']+'.cfg'
        with self.assertRaises(ValueError):self.c.experiment({'recipe_id':recipe['id'],'shot_id':self.sid,'variants':[{key:3},{key:float('nan')}]})
        self.assertEqual(self.st.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)
        with self.assertRaisesRegex(ValueError,'managed'):self.c.recipe_save({'name':'Bad','stage':'keyframe','controls':[{'node':'10','field':'image'}]})

    def test_runtime_input_enums_are_not_model_requirements(self):
        recipe=self.recipe();self.assertTrue(self.c.recipe_check({'recipe_id':recipe['id']})['ready'])
        self.nodes['UNETLoader']={'input':{'required':{'unet_name':[['different.safetensors']]}}}
        self.c.readiness={}
        self.assertFalse(self.c.recipe_check({'recipe_id':recipe['id']})['ready'])

    def test_director_conditioning_requires_connected_explicit_slots(self):
        file={'name':'pose.png','data':base64.b64encode(png()).decode()}
        self.c.direction_save({'shot_id':self.sid,'boxes':[],'joints':[[.5,.5]],'conditioning':True,'pose_file':file,'notes':'Eye level'})
        self.assertFalse(self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})['ok'])
        recipe=self.recipe();self.assign(recipe)
        with self.assertRaisesRegex(ValueError,'connected.*POSE'):self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})
        self.assertEqual(self.st.db.one('SELECT COUNT(*) n FROM jobs')['n'],0)
        wf=copy.deepcopy(recipe['workflow']);wf['9900']={'class_type':'LoadImage','inputs':{'image':'pose.png'},'_meta':{'title':'POSE'}}
        prompt=app.detect_slots(wf,'keyframe')['prompt'][0];wf[prompt]['inputs']['image3']=['9900',0]
        connected=self.recipe(workflow=wf);self.assign(connected)
        result=self.api.generate({'stage':'keyframe','shot_ids':[self.sid]});job=self.job(result['job_ids'][0]);key,p=app.Worker(self.st).compose(job)
        self.assertIn('creative_POSE',p['files']);self.assertIn('Eye level',p['prompt'])
        p['images']={k:'uploaded-'+k+'.png' for k in p['files']}
        built=app.build_workflow(json.loads(self.c.workflow(job)['json']),key,p)
        self.assertEqual(built['9900']['inputs']['image'],'uploaded-creative_POSE.png')

    def test_custom_recipe_dispatch_does_not_require_global_workflow_readiness(self):
        recipe=self.recipe();self.assign(recipe)
        job_id=self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})['job_ids'][0]
        inst=self.st.db.one('SELECT * FROM instances');self.st.health[inst['id']]={'ok':True}
        self.st.production.readiness={'checked':time.time(),'servers':[{'id':inst['id'],'stages':{'keyframe':{'ready':False}}}]}
        with patch('threading.Thread.start'):app.Worker(self.st).dispatch()
        self.assertEqual(self.job(job_id)['status'],'running')

    def test_masked_repair_keeps_source_and_requires_selection(self):
        recipe=next(r for r in self.c.state()['recipes'] if r['purpose']=='image_repair')
        token=self.c.state()['source_tokens'][self.kid]
        result=self.c.repair({'shot_id':self.sid,'take_id':self.kid,'recipe_id':recipe['id'],'source_token':token,'prompt':'Fix hand','mask':{'data':base64.b64encode(png(0)).decode()}})
        job=self.job(result['job_ids'][0]);key,p=app.Worker(self.st).compose(job)
        self.assertEqual(key,'keyframe');self.assertIn('creative_MASK',p['files'])
        p['images']={k:k+'.png' for k in p['files']}
        built=app.build_workflow(json.loads(self.c.workflow(job)['json']),key,p)
        self.assertEqual(built['9002']['inputs']['image'],'creative_MASK.png')
        candidate=self.st.data/'cache/candidate.png';candidate.write_bytes(png())
        run_id=self.finish(job,candidate)
        self.assertEqual(self.shot()['keyframe_take'],self.kid)
        take=self.st.db.one('SELECT * FROM takes WHERE job_id=?',(job['id'],))
        self.c.use_result({'run_id':run_id,'take_id':take['id']})
        self.assertEqual(self.shot()['keyframe_take'],take['id']);self.assertTrue(self.st.abs(self.ref).is_file())

    def test_deleted_objects_do_not_leave_look_or_direction_bindings_for_reused_ids(self):
        look=self.c.look_save({'asset_id':self.aid,'name':'Coat'})
        self.c.look_bind({'scene':'Bridge','asset_id':self.aid,'look_id':look['id']})
        self.c.direction_save({'shot_id':self.sid,'notes':'Direction'})
        self.api.shot_delete({'ids':[self.sid]});self.assertEqual(self.st.db.q('SELECT * FROM shot_direction'),[])
        self.api.asset_delete({'id':self.aid});self.assertEqual(self.st.db.q('SELECT * FROM scene_looks'),[])
        self.assertEqual(self.st.db.q('SELECT * FROM asset_looks'),[])

    def test_old_checkpoints_clear_new_episode_specific_data(self):
        original=self.st.editor.snapshot()
        self.c.direction_save({'shot_id':self.sid,'notes':'Later direction'})
        for key in ['shot_direction','shot_recipes','scene_looks','continuity_reviews','line_performances','performance_takes','creative_runs']:original.pop(key,None)
        self.st.editor.apply(original)
        self.assertEqual(self.c.direction(self.sid),{})

    def test_cancel_and_clear_preserve_run_status_and_timing_history(self):
        recipe=self.recipe();self.assign(recipe)
        jid=self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})['job_ids'][0]
        self.api.job_action({'action':'cancel','ids':[jid]});self.api.job_action({'action':'clear'})
        run=self.c.state()['runs'][0];self.assertEqual(run['status'],'cancelled');self.assertEqual(run['takes'],[])

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_word_timing_saves_pauses_and_rejects_stale_audio(self):
        self.audio();line=self.line();r=self.c.performance({'shot_id':self.sid,'line_id':line['line_id']})
        self.assertEqual(r['source'],'estimated');self.assertTrue(r['peaks'])
        saved=self.c.performance_save({'shot_id':self.sid,'line_id':line['line_id'],'token':r['token'],'words':r['words'],'source':'edited','lead_in':.2,'pause_after':.5})
        self.assertNotEqual(saved['token'],r['token']);self.assertAlmostEqual(app.media_seconds(self.st.abs(self.shot()['audio'])),1.2,delta=.03)
        rows=self.st.editor.subtitles({})['rows'];self.assertAlmostEqual(rows[0]['start'],.2,delta=.01)
        with self.assertRaisesRegex(ValueError,'changed'):self.c.performance_save({'shot_id':self.sid,'line_id':line['line_id'],'token':r['token'],'words':r['words']})

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_pronunciation_changes_synthesis_not_script_and_is_applied_once(self):
        self.audio();self.c.pronunciations({'asset_id':self.aid,'terms':{'Tobi':'Toh bee','Toh':'different'}})
        recipe=self.recipe('voice');self.assign(recipe)
        jid=self.api.generate({'stage':'voice','lines':[{'shot_id':self.sid,'index':0}],'takes':1})['job_ids'][0]
        job=self.job(jid);_,p=app.Worker(self.st).compose(job)
        self.assertEqual(p['prompt'],'Toh bee waits here.');self.assertEqual(job['note'],'Tobi waits here.')
        self.assertEqual(self.line()['text'],'Tobi waits here.')

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_phrase_retake_is_explicit_and_produces_a_full_line_take(self):
        self.audio();line=self.line();r=self.c.performance({'shot_id':self.sid,'line_id':line['line_id']})
        saved=self.c.performance_save({'shot_id':self.sid,'line_id':line['line_id'],'token':r['token'],'words':r['words'],'source':'edited'})
        result=self.c.phrase_generate({'shot_id':self.sid,'line_id':line['line_id'],'token':saved['token'],'first':1,'last':1})
        job=self.job(result['job_ids'][0]);_,p=app.Worker(self.st).compose(job)
        self.assertEqual(p['prompt'],'waits');self.assertEqual(job['note'],line['text'])
        candidate=self.st.data/'cache/phrase.wav';shutil.copyfile(self.st.abs(line['audio']),candidate)
        self.finish(job,candidate)
        self.assertEqual(self.line()['audio'],line['audio']);take=self.st.db.one('SELECT * FROM performance_takes')
        result=self.c.performance_apply({'shot_id':self.sid,'line_id':line['line_id'],'take_id':take['id'],'fit':True})
        full=self.st.db.one('SELECT * FROM voice_takes WHERE id=?',(result['take_id'],))
        self.assertEqual(full['text'],line['text']);self.assertEqual(full['line_id'],line['line_id'])
        self.assertAlmostEqual(app.media_seconds(self.st.abs(full['path'])),1,delta=.03)
        with self.assertRaisesRegex(ValueError,'changed'):self.c.performance_apply({'shot_id':self.sid,'line_id':line['line_id'],'take_id':take['id']})

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_camera_preview_and_video_interval_repair_preserve_duration_and_outside_frames(self):
        self.c.direction_save({'shot_id':self.sid,'camera':{'start':{'x':.5,'y':.5,'zoom':1},'end':{'x':.7,'y':.5,'zoom':1.3}}})
        preview=self.c.direction_preview({'shot_id':self.sid});self.assertTrue(self.st.abs(preview['url'].removeprefix('/media/')).is_file())
        source=self.st.data/'shots/source.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=red:s=64x64:r=8:d=2','-f','lavfi','-i','color=blue:s=64x64:r=8:d=2','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/source.mp4',8,32)",(self.sid,))
        self.api.approve({'take_id':tid});recipe=next(r for r in self.c.state()['recipes'] if r['purpose']=='video_repair')
        result=self.c.repair({'shot_id':self.sid,'take_id':tid,'recipe_id':recipe['id'],'source_token':self.c.state()['source_tokens'][tid],'prompt':'Green motion','start':1,'end':2})
        candidate=self.st.data/'cache/green.mp4';app.ffmpeg(['-f','lavfi','-i','color=green:s=64x64:r=16:d=1','-c:v','libx264','-pix_fmt','yuv420p',str(candidate)])
        job=self.job(result['job_ids'][0]);_,p=app.Worker(self.st).compose(job);self.assertLessEqual(p['length'],21)
        self.finish(job,candidate);repaired=self.st.db.one('SELECT * FROM takes WHERE job_id=?',(job['id'],))
        self.assertEqual(self.shot()['clip_take'],tid);self.assertAlmostEqual(app.media_seconds(self.st.abs(repaired['path'])),4,delta=.15)
        def rgb(at):return subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',str(self.st.abs(repaired['path'])),'-frames:v','1','-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'])
        before,middle,after=rgb(.5),rgb(1.5),rgb(2.5)
        self.assertGreater(before[0],before[2]+80);self.assertGreater(middle[1],middle[0]+50);self.assertGreater(after[2],after[0]+80)

    def test_global_workflow_changes_do_not_invalidate_a_pinned_recipe(self):
        recipe=self.recipe();self.assign(recipe)
        before=self.st.production.signature(self.shot(),'keyframe')
        self.st.db.x("UPDATE workflows SET json='{}' WHERE stage='keyframe'")
        self.assertEqual(before,self.st.production.signature(self.shot(),'keyframe'))

    def test_timeline_split_preserves_recipe_and_splits_camera_move(self):
        recipe=self.recipe();self.assign(recipe)
        self.c.direction_save({'shot_id':self.sid,'camera':{'start':{'x':.5,'y':.5,'zoom':1},'end':{'x':.7,'y':.5,'zoom':1.4}}})
        self.st.editor.timeline({'action':'split','id':self.sid,'at':1})
        new=next(s for s in self.st.ordered_shots() if s['id']!=self.sid)
        self.assertEqual(self.c.assignment(new['id'],'keyframe')['recipe_id'],recipe['id'])
        self.assertAlmostEqual(self.c.direction(self.sid)['camera']['end']['zoom'],1.2)
        self.assertAlmostEqual(self.c.direction(new['id'])['camera']['start']['zoom'],1.2)
        self.assertAlmostEqual(self.c.direction(new['id'])['camera']['end']['zoom'],1.4)

    def test_continuity_lighting_edit_keeps_an_unchanged_collection_binding(self):
        look=self.c.look_save({'asset_id':self.aid,'name':'Coat'})
        self.c.look_bind({'scene':'Bridge','asset_id':self.aid,'look_id':look['id']})
        context=self.st.editor.context('Bridge')
        self.st.editor.continuity({'scene':'Bridge','lighting':'Moonlight','variants':context['variants']})
        self.assertEqual(self.st.db.one('SELECT look_id FROM scene_looks')['look_id'],look['id'])

    def test_missing_recipe_from_an_old_episode_does_not_break_state(self):
        recipe=self.recipe();self.assign(recipe)
        self.st.db.x('DELETE FROM generation_recipes WHERE id=?',(recipe['id'],))
        self.assertEqual(self.api.state()['version'],'2.10')
        with self.assertRaisesRegex(ValueError,'not found'):self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})

    @unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required')
    def test_failed_performance_save_rolls_back_timing_and_pronunciation(self):
        self.audio();line=self.line();perf=self.c.performance({'shot_id':self.sid,'line_id':line['line_id']})
        with patch.object(self.st,'rebuild_audio',side_effect=RuntimeError('Encoder failed')):
            with self.assertRaisesRegex(RuntimeError,'Encoder failed'):
                self.c.performance_save({'shot_id':self.sid,'line_id':line['line_id'],'token':perf['token'],'words':perf['words'],'terms':{'Tobi':'Toh bee'}})
        self.assertEqual(self.st.db.q('SELECT * FROM character_pronunciations'),[])
        self.assertEqual(self.st.db.q('SELECT * FROM line_performances'),[])

    def test_archive_import_keeps_collection_and_director_media_resolvable(self):
        look=self.c.look_save({'asset_id':self.aid,'name':'Coat'})
        self.c.look_bind({'scene':'Bridge','asset_id':self.aid,'look_id':look['id']})
        self.c.direction_save({'shot_id':self.sid,'pose_file':{'data':base64.b64encode(png()).decode()}})
        backup=self.st.editor.backup({});data=self.st.abs(backup['path']).read_bytes()
        self.st.editor.restore_backup({'file':{'name':'project.zip','data':base64.b64encode(data).decode()}})
        restored=self.c.state()['looks'][0]
        self.assertTrue(self.c.path(restored['references'][0]['path']).is_file())
        self.assertTrue(self.c.path(self.c.direction(self.sid)['pose']).is_file())

    def test_local_camera_edits_do_not_make_the_source_keyframe_outdated(self):
        before=self.st.production.signature(self.shot(),'keyframe')
        self.c.direction_save({'shot_id':self.sid,'camera':{'start':{'x':.5,'y':.5,'zoom':1},'end':{'x':.7,'y':.5,'zoom':1.4}}})
        self.assertEqual(before,self.st.production.signature(self.shot(),'keyframe'))

if __name__=='__main__':unittest.main()
