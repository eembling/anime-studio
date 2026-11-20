import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.st=app.Studio(self.tmp.name)
        self.addCleanup(self.st.db.conn.close)
        self.api=app.API(self.st)
        self.editor=self.st.editor
        self.a=self.api.shot_save({'scene':'A','keyframe_prompt':'Script action','dialogue':'','duration':4})['id']
        self.b=self.api.shot_save({'scene':'B','keyframe_prompt':'Other scene','duration':4})['id']

    def add_take(self,shot,stage):
        path=f'shots/{stage}-{shot}.mp4';self.st.abs(path).write_bytes(b'original media')
        return self.st.db.x('INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,?,?,?,?)',(shot,stage,path,16,65))

    def test_deleting_selected_clip_clears_dependent_selections_and_is_recoverable(self):
        key=self.add_take(self.a,'keyframe');clip=self.add_take(self.a,'animate')
        cont=self.add_take(self.a,'continue');final=self.add_take(self.a,'upscale')
        other=self.add_take(self.b,'animate')
        self.st.db.x('UPDATE shots SET keyframe_take=?,clip_take=?,final_take=?,cont_takes=? WHERE id=?',
                     (key,clip,final,json.dumps([cont]),self.a))
        history=self.editor.checkpoint('Before clip deletion')
        self.api.take_delete({'take_id':clip})
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,))
        self.assertIsNone(shot['clip_take']);self.assertIsNone(shot['final_take'])
        self.assertEqual(json.loads(shot['cont_takes']),[])
        self.assertEqual(shot['keyframe_take'],key)
        self.assertIsNone(self.st.take(clip))
        for tid in (key,cont,final,other):self.assertIsNotNone(self.st.take(tid))
        self.assertTrue(self.st.abs(f'shots/animate-{self.a}.mp4').is_file())
        self.editor.history({'action':'restore','id':history})
        self.assertEqual(self.st.chain(self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,)))[0]['id'],clip)

    def test_deleting_unused_clip_preserves_selected_chain(self):
        selected=self.add_take(self.a,'animate');unused=self.add_take(self.a,'lipsync')
        cont=self.add_take(self.a,'continue');final=self.add_take(self.a,'upscale')
        self.st.db.x('UPDATE shots SET clip_take=?,final_take=?,cont_takes=? WHERE id=?',
                     (selected,final,json.dumps([cont]),self.a))
        self.api.take_delete({'take_id':unused})
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,))
        self.assertEqual(shot['clip_take'],selected);self.assertEqual(shot['final_take'],final)
        self.assertEqual(json.loads(shot['cont_takes']),[cont])

    def test_deleting_selected_continuation_truncates_chain_and_clears_upscale(self):
        clip=self.add_take(self.a,'animate');first=self.add_take(self.a,'continue')
        second=self.add_take(self.a,'continue');third=self.add_take(self.a,'continue')
        final=self.add_take(self.a,'upscale')
        self.st.db.x('UPDATE shots SET clip_take=?,final_take=?,cont_takes=? WHERE id=?',
                     (clip,final,json.dumps([first,second,third]),self.a))
        self.api.take_delete({'take_id':second})
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,))
        self.assertEqual(json.loads(shot['cont_takes']),[first]);self.assertIsNone(shot['final_take'])
        self.assertEqual(shot['clip_take'],clip);self.assertIsNotNone(self.st.take(third))

    def test_deleting_upscale_keeps_source_clip_and_repeated_delete_succeeds(self):
        clip=self.add_take(self.a,'animate');final=self.add_take(self.a,'upscale')
        self.st.db.x('UPDATE shots SET clip_take=?,final_take=? WHERE id=?',(clip,final,self.a))
        self.api.take_delete({'take_id':final})
        self.assertTrue(self.api.take_delete({'take_id':final})['ok'])
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,))
        self.assertIsNone(shot['final_take']);self.assertEqual(shot['clip_take'],clip)

    def test_scene_clip_cleanup_is_scoped_and_recoverable(self):
        k=self.add_take(self.a,'keyframe');v=self.add_take(self.a,'animate');other=self.add_take(self.b,'animate')
        self.st.db.x('UPDATE shots SET keyframe_take=?,clip_take=? WHERE id=?',(k,v,self.a))
        preview=self.editor.scene({'scene':'A','mode':'clips'})
        self.editor.scene({'scene':'A','mode':'clips','apply':True,'token':preview['token']})
        self.assertIsNone(self.st.take(v));self.assertIsNotNone(self.st.take(k));self.assertIsNotNone(self.st.take(other))
        self.assertTrue(self.st.abs(f'shots/animate-{self.a}.mp4').is_file())
        history=self.editor.state()['history'][0]
        self.editor.history({'action':'restore','id':history['id']})
        self.assertEqual(self.st.db.one('SELECT clip_take FROM shots WHERE id=?',(self.a,))['clip_take'],v)

    def test_reset_keeps_shots_dialogue_and_saved_script(self):
        self.st.db.x("INSERT OR REPLACE INTO settings VALUES('script','Full script')")
        self.st.db.x("UPDATE shots SET dialogue='AIKO: Hello',camera='pan',motion_prompt='wave',duration=8,notes='edit',ref_prev=1 WHERE id=?",(self.a,))
        self.add_take(self.a,'keyframe')
        preview=self.editor.scene({'scene':'A','mode':'reset'})
        self.editor.scene({'scene':'A','mode':'reset','apply':True,'token':preview['token']})
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,))
        self.assertEqual(shot['dialogue'],'AIKO: Hello');self.assertEqual(self.st.settings()['script'],'Full script')
        self.assertEqual(shot['duration'],4);self.assertEqual(shot['keyframe_prompt'],'');self.assertEqual(shot['camera'],'')
        self.assertEqual(len(self.st.ordered_shots()),2)
        self.assertEqual(self.st.db.one('SELECT keyframe_prompt FROM shots WHERE id=?',(self.b,))['keyframe_prompt'],'Other scene')

    def test_stale_scene_preview_and_active_jobs_block_cleanup(self):
        preview=self.editor.scene({'scene':'A','mode':'clips'})
        self.add_take(self.a,'animate')
        with self.assertRaisesRegex(ValueError,'changed'):
            self.editor.scene({'scene':'A','mode':'clips','apply':True,'token':preview['token']})
        self.st.db.x("INSERT INTO jobs(shot_id,stage,status) VALUES(?,'animate','running')",(self.a,))
        preview=self.editor.scene({'scene':'A','mode':'clips'})
        with self.assertRaisesRegex(ValueError,'renders'):
            self.editor.scene({'scene':'A','mode':'clips','apply':True,'token':preview['token']})

    def test_missing_scene_never_clears_other_scenes(self):
        with self.assertRaisesRegex(ValueError,'not found'):
            self.editor.scene({'scene':'missing','mode':'reset'})
        self.assertEqual(len(self.st.ordered_shots()),2)

    def test_anchored_audio_ripples_and_absolute_audio_does_not(self):
        path='uploads/cue.wav';self.st.abs(path).write_bytes(b'fake')
        db=self.st.db
        db.x("INSERT INTO audio_cues(name,kind,path,start,offset,duration,volume,fade_in,fade_out,enabled,anchor_shot,anchor_offset) VALUES('cue','sfx',?,0,0,1,1,0,0,1,?,.5)",(path,self.b))
        self.assertEqual(self.st.soundtrack.cues()[0]['start'],4.5)
        self.editor.timeline({'action':'trim','id':self.a,'duration':6,'trim_in':1})
        self.assertEqual(self.st.soundtrack.cues()[0]['start'],6.5)
        self.editor.timeline({'action':'reorder','ids':[self.b,self.a]})
        self.assertEqual(self.st.soundtrack.cues()[0]['start'],.5)

    def test_reorder_validates_complete_set(self):
        with self.assertRaises(ValueError):self.editor.timeline({'action':'reorder','ids':[self.a,self.a]})
        self.assertEqual([s['id'] for s in self.st.ordered_shots()],[self.a,self.b])

    def test_split_preserves_source_and_remaps_chain(self):
        root=self.add_take(self.a,'animate');cont=self.add_take(self.a,'continue')
        self.st.db.x('UPDATE shots SET clip_take=?,cont_takes=? WHERE id=?',(root,json.dumps([cont]),self.a))
        self.editor.timeline({'action':'split','id':self.a,'at':1.5})
        left,right,_=self.st.ordered_shots()
        self.assertEqual(left['duration'],1.5);self.assertEqual(right['duration'],2.5);self.assertEqual(right['trim_in'],1.5)
        self.assertNotEqual(right['clip_take'],root)
        self.assertEqual(self.st.chain(right)[-1]['shot_id'],right['id'])
        self.assertEqual(self.st.take(right['clip_take'])['path'],self.st.take(root)['path'])

    def test_episode_switch_preserves_independent_shots_and_shared_cast(self):
        cast=self.api.asset_save({'name':'Aiko','kind':'character'})['id']
        self.editor.library({'action':'save'})
        original=self.st.settings()['active_episode']
        self.editor.library({'action':'new','title':'Episode 2'})
        self.assertEqual(self.st.ordered_shots(),[])
        self.assertIsNotNone(self.st.db.one('SELECT id FROM assets WHERE id=?',(cast,)))
        self.api.shot_save({'scene':'New','keyframe_prompt':'New script'})
        self.editor.library({'action':'open','id':original})
        self.assertEqual([s['scene'] for s in self.st.ordered_shots()],['A','B'])

    def test_history_restore_preserves_newer_state_for_redo(self):
        ident=self.editor.checkpoint('Original')
        self.st.db.x("UPDATE shots SET notes='changed' WHERE id=?",(self.a,))
        self.editor.history({'action':'restore','id':ident})
        self.assertEqual(self.st.db.one('SELECT notes FROM shots WHERE id=?',(self.a,))['notes'],'')
        before=next(r for r in self.editor.state()['history'] if r['label']=='Before restoring history')
        self.editor.history({'action':'restore','id':before['id']})
        self.assertEqual(self.st.db.one('SELECT notes FROM shots WHERE id=?',(self.a,))['notes'],'changed')

    def test_continuity_changes_prompt_and_invalidates_signature(self):
        self.st.db.x("UPDATE shots SET asset_ids='[]' WHERE id=?",(self.a,))
        before=self.st.production.signature(self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,)),'keyframe')
        self.editor.continuity({'scene':'A','lighting':'sunset','notes':'red coat','variants':[]})
        after=self.st.production.signature(self.st.db.one('SELECT * FROM shots WHERE id=?',(self.a,)),'keyframe')
        self.assertNotEqual(before,after)
        _,params=app.Worker(self.st).compose({'id':1,'stage':'keyframe','shot_id':self.a,'seed':1})
        self.assertIn('sunset',params['prompt']);self.assertIn('red coat',params['prompt'])

    def test_portable_backup_round_trip(self):
        self.add_take(self.a,'keyframe')
        archive=self.editor.backup({})
        data=self.st.abs(archive['path']).read_bytes()
        self.st.db.x("UPDATE shots SET keyframe_prompt='changed' WHERE id=?",(self.a,))
        import base64
        self.editor.restore_backup({'file':{'name':'backup.zip','data':'data:application/zip;base64,'+base64.b64encode(data).decode()}})
        self.assertEqual(self.st.ordered_shots()[0]['keyframe_prompt'],'Script action')
        take=self.st.db.one('SELECT path FROM takes')
        self.assertTrue(self.st.abs(take['path']).exists())

    def test_subtitle_edit_export_and_quality(self):
        self.st.db.x("UPDATE shots SET dialogue='AIKO: Hello there' WHERE id=?",(self.a,))
        rows=self.editor.subtitles({})['rows'];self.assertEqual(rows[0]['text'],'Hello there')
        result=self.editor.subtitles({'rows':[{'start':0,'end':1,'text':'Edited caption'}],'save':True,'export':True})
        self.assertIn('Edited caption',self.st.abs(result['url'].removeprefix('/media/')).read_text())
        self.assertEqual(self.editor.subtitles({})['rows'][0]['text'],'Edited caption')
        self.assertTrue(self.editor.qc(self.api.state()['shots']))

    def test_reconnection_collects_existing_remote_output_without_resubmitting(self):
        jid=self.st.db.x("INSERT INTO jobs(shot_id,stage,status,prompt_id,instance_id,seed) VALUES(?,'keyframe','running','existing-prompt',1,7)",(self.a,))
        entry={'status':{'completed':True,'status_str':'success'},'outputs':{'1':{'images':[{'filename':'result.png','subfolder':'','type':'output'}]}}}
        calls=[]
        def request(method,url,*args,**kwargs):
            calls.append((method,url))
            if method=='POST':self.fail('Reconnection must not resubmit a prompt')
            return {'existing-prompt':entry}
        with patch.object(app,'http_json',side_effect=request), patch.object(app,'http',return_value=b'old render image'), patch('comfy_events.ProgressEvents.start',return_value=None):
            app.Worker(self.st).execute(jid,{'id':1,'url':'http://mock:8188'})
        self.assertEqual(self.st.db.one('SELECT status FROM jobs WHERE id=?',(jid,))['status'],'done')
        self.assertEqual(self.st.db.one('SELECT COUNT(*) n FROM takes WHERE job_id=?',(jid,))['n'],1)
        self.assertTrue(all(method=='GET' for method,url in calls))

    def test_render_file_names_remain_unique_when_job_ids_are_reused(self):
        worker=app.Worker(self.st)
        job={'id':1,'shot_id':self.a,'asset_id':None,'stage':'keyframe','seed':7,'segment':None,'draft':0}
        paths=[]
        for content in (b'first render',b'new render'):
            file=self.st.data/'jobs'/'result.png';file.write_bytes(content)
            worker.finish(job,'keyframe',[file])
            paths.append(self.st.db.q('SELECT path FROM takes ORDER BY id DESC')[0]['path'])
        self.assertNotEqual(*paths)
        self.assertEqual(self.st.abs(paths[0]).read_bytes(),b'first render')

    def test_archive_metadata_cannot_reference_files_outside_project(self):
        snapshot=self.editor.snapshot()
        snapshot['shots'][0]['audio']='/etc/passwd'
        with self.assertRaisesRegex(ValueError,'inside the project'):
            self.editor.apply(snapshot)
        self.assertEqual(self.st.ordered_shots()[0]['audio'],None)


    def test_restart_keeps_remote_identity(self):
        self.st.db.x("INSERT INTO jobs(shot_id,stage,status,prompt_id,instance_id) VALUES(?,'animate','running','remote-123',1)",(self.a,))
        restarted=app.Studio(self.tmp.name)
        try:
            job=restarted.db.one('SELECT * FROM jobs')
            self.assertEqual(job['status'],'pending');self.assertEqual(job['prompt_id'],'remote-123');self.assertEqual(job['instance_id'],1)
        finally:restarted.db.conn.close()


if __name__=='__main__':unittest.main()
