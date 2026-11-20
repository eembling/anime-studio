"""Render reproducibility, speech timing, storage safeguards, waveform and cut inspection."""
import json,sys,tempfile,unittest,subprocess,shutil,wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app
from alignment import match_words
from render_diagnostics import diagnose

class ImprovedWorkspaceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.st=app.Studio(self.tmp.name);self.api=app.API(self.st)
  self.sid=self.api.shot_save({'scene':'Test','keyframe_prompt':'Aiko at the door','duration':4})['id']
  self.st.production.ready=lambda *a:True
 def tearDown(self):self.st.db.conn.close();self.tmp.cleanup()
 def queue(self):return self.api.generate({'stage':'keyframe','shot_ids':[self.sid]})
 def test_ordinary_job_freezes_workflow_and_settings_at_queue_time(self):
  self.queue();job=self.st.db.one('SELECT * FROM jobs');key,params=app.Worker(self.st).compose(job);graph=self.st.snapshots.workflow(job)
  self.api.settings({'settings':{'kf_width':'640','style_prompt':'Different style'}});self.st.db.x("UPDATE workflows SET json='{}' WHERE stage='keyframe'")
  self.assertEqual(app.Worker(self.st).compose(job),(key,params));self.assertEqual(self.st.snapshots.workflow(job),graph)
 def test_reference_file_copy_survives_replacement_of_the_original(self):
  path=self.st.data/'uploads/reference.png';path.write_bytes(b'original reference');aid=self.api.asset_save({'name':'Aiko','kind':'character','description':'Blue coat'})['id']
  self.st.db.x("UPDATE assets SET image='uploads/reference.png' WHERE id=?",(aid,));self.st.db.x('UPDATE shots SET asset_ids=? WHERE id=?',(json.dumps([aid]),self.sid));self.queue()
  job=self.st.db.one('SELECT * FROM jobs');frozen=app.Worker(self.st).compose(job)[1]['files']['ref1'];path.write_bytes(b'changed')
  self.assertNotEqual(path,frozen);self.assertEqual(frozen.read_bytes(),b'original reference')
 def test_failed_capture_rolls_back_the_batch_before_dispatch(self):
  with patch.object(self.st.snapshots,'capture',side_effect=ValueError('Cannot snapshot')):
   with self.assertRaises(ValueError):self.queue()
  self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])
 def test_repeat_uses_original_seed_and_inputs_as_a_separate_job(self):
  self.queue();old=self.st.db.one('SELECT * FROM jobs');self.st.db.x("UPDATE jobs SET status='done' WHERE id=?",(old['id'],));self.api.settings({'settings':{'style_prompt':'Different'}})
  jid=self.st.snapshots.repeat(old['id']);new=self.st.db.one('SELECT * FROM jobs WHERE id=?',(jid,));a=app.Worker(self.st).compose(old)[1];b=app.Worker(self.st).compose(new)[1]
  self.assertEqual(old['seed'],new['seed']);self.assertEqual(a['prompt'],b['prompt']);self.assertNotEqual(a['prefix'],b['prefix']);self.assertIsNone(new['prompt_id']);self.assertNotEqual(old['id'],jid)
 def test_upscale_keeps_the_captured_source_framerate(self):
  video=self.st.data/'shots/clip.mp4';video.write_bytes(b'video')
  tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps) VALUES(?,'animate','shots/clip.mp4',24)",(self.sid,));self.st.db.x('UPDATE shots SET clip_take=? WHERE id=?',(tid,self.sid))
  self.api.generate({'stage':'upscale','shot_ids':[self.sid]});job=self.st.db.one("SELECT * FROM jobs WHERE stage='upscale'")
  self.st.db.x('UPDATE takes SET fps=16 WHERE id=?',(tid,));self.assertEqual(self.st.snapshots.workflow(job)['fps'],24)
 def test_legacy_jobs_cannot_claim_a_reproducible_repeat(self):
  jid=self.st.db.x("INSERT INTO jobs(shot_id,stage,status) VALUES(?,'keyframe','done')",(self.sid,))
  with self.assertRaisesRegex(ValueError,'older render'):self.st.snapshots.repeat(jid)
 def test_storage_protects_selected_media_history_exports_and_job_inputs(self):
  for folder,name in [('shots','selected.mp4'),('history','checkpoint.json'),('exports','episode.mp4'),('cache','unused.mp4'),('uploads','unused.png')]:
   (self.st.data/folder/name).write_bytes(b'test')
  self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'animate','shots/selected.mp4')",(self.sid,))
  self.queue();snapshot=self.st.db.one('SELECT snapshot_json FROM jobs')['snapshot_json']
  self.st.db.x("UPDATE jobs SET status='done'");scan=self.st.storage.scan();paths={f['path'] for f in scan['candidates']}
  self.assertIn('cache/unused.mp4',paths);self.assertIn('uploads/unused.png',paths);self.assertNotIn('shots/selected.mp4',paths);self.assertFalse(any(p.startswith(('history/','exports/')) for p in paths))
 def test_history_reference_protects_deleted_take_file(self):
  p=self.st.data/'shots/old.mp4';p.write_bytes(b'old');tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'animate','shots/old.mp4')",(self.sid,));self.st.editor.checkpoint('Keep old take');self.api.take_delete({'take_id':tid})
  self.assertNotIn('shots/old.mp4',{f['path'] for f in self.st.storage.scan()['candidates']})
 def trash(self):
  (self.st.data/'cache/unused.bin').write_bytes(b'cache');scan=self.st.storage.scan();return self.st.storage.action({'action':'cleanup','token':scan['token'],'paths':['cache/unused.bin']})['id']
 def test_cleanup_can_be_restored_without_overwriting_files(self):
  ident=self.trash();self.assertFalse((self.st.data/'cache/unused.bin').exists());self.st.storage.action({'action':'restore','id':ident});self.assertEqual((self.st.data/'cache/unused.bin').read_bytes(),b'cache')
 def test_cleanup_requires_an_unchanged_review(self):
  (self.st.data/'cache/a').write_bytes(b'a');scan=self.st.storage.scan();(self.st.data/'cache/a').write_bytes(b'changed')
  with self.assertRaisesRegex(ValueError,'changed'):self.st.storage.action({'action':'cleanup','token':scan['token'],'paths':['cache/a']})
 def test_new_reference_blocks_permanent_trash_deletion(self):
  ident=self.trash();review=self.st.storage.action({'action':'review_purge','id':ident});self.st.db.x("INSERT OR REPLACE INTO settings VALUES('test_reference','cache/unused.bin')")
  with self.assertRaisesRegex(ValueError,'referenced'):self.st.storage.action({'action':'purge','id':ident,'token':review['token'],'confirm':True})
 def test_permanent_deletion_requires_current_review_and_explicit_confirmation(self):
  ident=self.trash();review=self.st.storage.action({'action':'review_purge','id':ident})
  with self.assertRaises(ValueError):self.st.storage.action({'action':'purge','id':ident,'token':review['token']})
  result=self.st.storage.action({'action':'purge','id':ident,'token':review['token'],'confirm':True});self.assertEqual(result['freed_bytes'],5)
 def test_extra_unreviewed_trash_files_block_deletion(self):
  ident=self.trash();(self.st.data/'storage-trash'/ident/'new-file.txt').write_text('unreviewed')
  with self.assertRaisesRegex(ValueError,'changed'):self.st.storage.action({'action':'review_purge','id':ident})
 def test_cleanup_blocks_during_active_work(self):
  self.queue();scan=self.st.storage.scan()
  with self.assertRaisesRegex(ValueError,'renders'):self.st.storage.action({'action':'cleanup','token':scan['token'],'paths':['cache/a']})
 def test_waveform_is_cached_and_cut_preview_uses_trimmed_source(self):
  audio=self.st.data/'voices/line.wav'
  with wave.open(str(audio),'wb') as w:w.setparams((1,2,8000,0,'NONE',''));w.writeframes(b'\0\1'*8000)
  self.st.db.x("UPDATE shots SET audio='voices/line.wav' WHERE id=?",(self.sid,));a=self.st.workspace.action({'action':'waveform','shot_id':self.sid});self.assertTrue(a['peaks']);self.assertAlmostEqual(a['seconds'],1)
  with patch('workspace.subprocess.run',side_effect=AssertionError('Should use cached waveform')):self.assertEqual(self.st.workspace.action({'action':'waveform','shot_id':self.sid}),a)

class SpeechAndDiagnosticsTests(unittest.TestCase):
 def test_recognition_matches_script_punctuation_and_keeps_observed_timing(self):
  r=match_words('Hello, Aiko!',[{'text':'hello','start':.2,'end':.5,'probability':.99},{'text':'Aiko','start':.6,'end':1.1,'probability':.98}]);self.assertTrue(r['usable']);self.assertEqual(r['words'][0]['text'],'Hello,');self.assertEqual(r['words'][1]['start'],.6)
 def test_missing_or_low_confidence_words_are_not_presented_as_valid_alignment(self):
  self.assertFalse(match_words('Hello Aiko',[{'text':'Hello','start':0,'end':1,'probability':.99}])['usable']);self.assertFalse(match_words('Hello',[{'text':'Hello','start':0,'end':1,'probability':.2}])['usable'])
 def test_multilingual_tokenization_can_merge_recognized_subwords(self):
  r=match_words('こんにちは',[{'text':'こんに','start':0,'end':.5,'probability':.99},{'text':'ちは','start':.5,'end':1,'probability':.99}]);self.assertTrue(r['usable']);self.assertEqual(r['words'][0]['end'],1)
 def test_errors_provide_specific_repair_guidance_and_honest_retry_labels(self):
  for message,category in [('CUDA out of memory','memory'),('Missing node: SomeNode','nodes'),('model not found: file.safetensors','model'),('Connection refused','connection'),('Timed out after 3600s','timeout'),('Dialogue line changed','inputs')]:
   with self.subTest(category=category):
    r=diagnose({'error':message});self.assertEqual(r['category'],category);self.assertTrue(r['guidance']);self.assertEqual(r['retry_label'],'Retry same inputs')
  self.assertEqual(diagnose({'error':'Connection refused','prompt_id':'existing'})['retry_label'],'Reconnect render')

class SnapshotDispatchTests(unittest.TestCase):
 setUp=ImprovedWorkspaceTests.setUp
 tearDown=ImprovedWorkspaceTests.tearDown
 queue=ImprovedWorkspaceTests.queue
 def test_video_readiness_resolves_captured_image_slots_without_uploading(self):
  image=self.st.data/'shots/start.png';image.write_bytes(b'reference')
  tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','shots/start.png')",(self.sid,));self.api.approve({'take_id':tid})
  self.api.generate({'stage':'animate','shot_ids':[self.sid]});job=self.st.db.one("SELECT * FROM jobs WHERE stage='animate'");instance=self.st.db.one('SELECT * FROM instances')
  with patch.object(self.st.creative,'inspect_recipe',return_value={'problems':[]}) as inspect:
   self.assertTrue(self.st.snapshots.compatible(job,instance))
  graph=inspect.call_args.args[0]['workflow'];self.assertTrue(any(n['inputs'].get('image','').startswith('uploads/job-inputs/') for n in graph.values() if isinstance(n['inputs'].get('image'),str)))
 def test_captured_job_dispatches_even_if_global_workflow_is_no_longer_ready(self):
  import threading
  self.queue();job=self.st.db.one('SELECT * FROM jobs');instance=self.st.db.one('SELECT * FROM instances');self.st.health[instance['id']]={'ok':True}
  self.st.production.readiness={'checked':0,'servers':[{'id':instance['id'],'stages':{'keyframe':{'ready':False}}}]}
  self.st.production.inspect=lambda *a:self.st.production.readiness
  self.st.production.http=lambda *a,**kw:{n['class_type']:{'input':{}} for variants in app.STARTERS.values() for preset in variants.values() for n in preset['wf'].values()}
  worker=app.Worker(self.st);called=threading.Event();seen=[]
  def execute(ident,inst):seen.append(ident);called.set()
  worker.execute=execute;worker.dispatch();self.assertTrue(called.wait(1));self.assertEqual(seen,[job['id']])

class CutPreviewTests(unittest.TestCase):
 def test_cut_frames_follow_source_trim_and_selected_shots(self):
  with tempfile.TemporaryDirectory() as tmp:
   st=app.Studio(tmp);api=app.API(st);video=st.data/'shots/colors.mp4'
   subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=128x72:r=16:d=2','-f','lavfi','-i','color=blue:s=128x72:r=16:d=2','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0','-c:v','libx264',str(video)],check=True)
   ids=[]
   for i in range(2):
    sid=api.shot_save({'scene':str(i),'duration':1,'kind':'motion'})['id'];tid=st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/colors.mp4',16,64)",(sid,));api.approve({'take_id':tid});ids.append(sid)
   st.db.x('UPDATE shots SET trim_in=2 WHERE id=?',(ids[0],));result=st.workspace.action({'action':'cut','shot_id':ids[0]})
   def pixel(url):return subprocess.check_output(['ffmpeg','-v','error','-i',str(st.abs(url.removeprefix('/media/'))),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'])
   left,right=pixel(result['left']),pixel(result['right']);self.assertGreater(left[2],left[0]+100);self.assertGreater(right[0],right[2]+100);st.db.conn.close()
