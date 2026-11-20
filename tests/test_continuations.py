import copy,json,shutil,subprocess,sys,tempfile,unittest,wave
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app

class ContinuationTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.media=tempfile.TemporaryDirectory();cls.source=Path(cls.media.name)/'source.mp4'
  subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=128x72:rate=16','-frames:v','81','-c:v','libx264','-pix_fmt','yuv420p',str(cls.source)],check=True)
 @classmethod
 def tearDownClass(cls):cls.media.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.st=app.Studio(self.tmp.name);self.api=app.API(self.st)
  self.st.worker=app.Worker(self.st);self.sid=self.api.shot_save({'scene':'Door','duration':12,'motion_prompt':'Walk to the door','camera':'Medium shot'})['id']
  self.path=self.st.data/'shots/base.mp4';shutil.copyfile(self.source,self.path)
  self.root=self.add_take('animate',None);self.api.approve({'take_id':self.root})
  self.st.production.http=lambda *a,**kw:{n['class_type']:{'input':{}} for variants in app.STARTERS.values() for preset in variants.values() for n in preset['wf'].values()}
 def tearDown(self):self.st.db.conn.close();self.tmp.cleanup()
 def shot(self):return self.st.db.one('SELECT * FROM shots WHERE id=?',(self.sid,))
 def add_take(self,stage,segment,signature=None):return self.st.db.x('INSERT INTO takes(shot_id,stage,path,fps,frames,segment,input_signature) VALUES(?,?,?,?,?,?,?)',(self.sid,stage,'shots/base.mp4',16,81,segment,signature))
 def save(self,segment=0,prompt='Open the door',camera='Close-up'):
  return self.st.sections.action({'action':'save','shot_id':self.sid,'segment':segment,'prompt':prompt,'camera':camera})
 def generate(self,**extra):return self.api.generate(dict(stage='continue',shot_ids=[self.sid],**extra))
 def test_missing_direction_requires_explicit_choice(self):
  r=self.generate();self.assertFalse(r['ok']);self.assertIn('Add direction',r['error']);self.assertEqual(self.st.db.q('SELECT * FROM jobs'),[])
 def test_shared_fallback_is_explicit_and_persisted(self):
  r=self.generate(use_shared_prompt=True);self.assertEqual(r['queued'],1);row=self.st.sections.row(self.sid,0);self.assertEqual(row['prompt'],'Walk to the door');self.assertEqual(row['fallback'],1)
 def test_independent_directions_preserve_shot_prompt(self):
  self.save();self.save(1,'Look through doorway','Pan');self.save(0,'Reach for handle','Low angle')
  self.assertEqual(self.st.sections.row(self.sid,1)['prompt'],'Look through doorway');self.assertEqual(self.shot()['motion_prompt'],'Walk to the door')
 def test_queued_prompt_source_seed_and_workflow_are_frozen(self):
  self.save();r=self.generate(seed=123);self.assertEqual(r['queued'],1);job=self.st.db.one('SELECT * FROM jobs');before=self.st.worker.compose(job);meta=self.st.creative.meta(job)
  self.assertIn('Open the door',before[1]['prompt']);self.assertIn('Close-up',before[1]['prompt']);self.assertEqual(meta['source_id'],self.root);self.assertEqual(job['seed'],123)
  self.save(prompt='Run away');self.st.db.x("UPDATE settings SET value='changed' WHERE key='motion_suffix'");self.st.db.x("UPDATE workflows SET fps=24 WHERE stage='animate'")
  self.assertEqual(self.st.worker.compose(job),before);self.assertEqual(meta['recipe']['fps'],16);self.assertTrue(meta['boundary_hash']);self.assertNotEqual(before[1]['files']['start'],self.st.last_frame(self.path))
  self.api.job_action({'action':'retry','ids':[job['id']]});self.assertEqual(self.st.worker.compose(self.st.db.one('SELECT * FROM jobs')),before)
 def test_rejects_stale_boundary_preview_and_duplicate_queue(self):
  self.save();token=self.st.sections.details(self.sid)['source_token'];other=self.add_take('animate',None);self.api.approve({'take_id':other})
  self.assertIn('source or timing changed',self.generate(source_token=token)['error']);self.assertEqual(self.generate()['queued'],1);self.assertEqual(self.generate()['queued'],0)
 def test_frame_capacity_comes_from_selected_workflow(self):
  self.st.db.x("UPDATE workflows SET fps=24,max_frames=49 WHERE stage='animate'");self.save();d=self.st.sections.details(self.sid);self.assertEqual(d['capacity'],2);self.generate();p=self.st.worker.compose(self.st.db.one('SELECT * FROM jobs'))[1];self.assertEqual(p['length'],49)
 def test_only_final_section_uses_next_endpoint(self):
  self.st.db.x('UPDATE shots SET end_next=1 WHERE id=?',(self.sid,));self.assertEqual(self.st.sections.details(self.sid)['key'],'animate')
  cont=self.add_take('continue',0);self.st.db.x('UPDATE shots SET cont_takes=? WHERE id=?',(json.dumps([cont]),self.sid));self.assertEqual(self.st.sections.details(self.sid)['key'],'animate_flf')
 def test_audio_extension_is_explicit_and_preserves_existing_content(self):
  audio=self.st.data/'voices/long.wav'
  with wave.open(str(audio),'wb') as w:w.setparams((1,2,8000,0,'NONE',''));w.writeframes(b'\0\0'*13*8000)
  self.st.db.x("UPDATE shots SET audio='voices/long.wav' WHERE id=?",(self.sid,));c=self.st.sections.coverage(self.shot());self.assertTrue(c['needs_extension']);self.assertEqual(self.shot()['duration'],12)
  self.st.sections.action({'action':'extend','shot_id':self.sid});self.assertEqual(self.shot()['duration'],13);self.assertEqual(self.shot()['clip_take'],self.root);self.assertTrue(self.path.exists())
 def test_changed_upstream_take_retains_but_invalidates_later_sections(self):
  self.save();sig=self.st.production.signature(self.shot(),'continue',0);cont=self.add_take('continue',0,sig);self.api.approve({'take_id':cont})
  self.assertEqual(self.st.production.provenance(self.st.take(cont),self.shot()),'current');other=self.add_take('animate',None);self.api.approve({'take_id':other})
  self.assertEqual(json.loads(self.shot()['cont_takes']),[cont]);self.assertEqual(self.st.production.provenance(self.st.take(cont),self.shot()),'outdated')
 def test_generation_waits_for_selected_predecessor(self):
  self.save(1);r=self.generate(segment=1);self.assertFalse(r['ok']);self.assertIn('previous section',r['error'])
 def test_new_sections_survive_history_and_legacy_history_clears_them(self):
  self.save();checkpoint=self.st.editor.checkpoint('Section direction');self.save(prompt='Other');self.st.editor.history({'action':'restore','id':checkpoint});self.assertEqual(self.st.sections.row(self.sid,0)['prompt'],'Open the door')
  snapshot=self.st.editor.snapshot();snapshot.pop('shot_sections');snapshot.pop('section_takes');self.st.editor.apply(snapshot);self.assertIsNone(self.st.sections.row(self.sid,0))
 def test_existing_chain_has_no_fabricated_historical_direction(self):
  cont=self.add_take('continue',0);self.api.approve({'take_id':cont});state=self.st.sections.state(self.shot());self.assertEqual(state['sections'],[]);self.assertEqual(state['takes'],{})
 def test_section_camera_and_prompt_changes_invalidate_only_that_section(self):
  self.save();sig=self.st.production.signature(self.shot(),'continue',0);self.save(1,'Turn around');self.assertEqual(sig,self.st.production.signature(self.shot(),'continue',0));self.save(prompt='Different action');self.assertNotEqual(sig,self.st.production.signature(self.shot(),'continue',0))

 def test_twelve_second_episode_trims_padding_and_keeps_one_continuous_audio_track(self):
  import math,struct
  self.st.db.x("UPDATE settings SET value='128' WHERE key='out_width'");self.st.db.x("UPDATE settings SET value='72' WHERE key='out_height'")
  audio=self.st.data/'voices/pulses.wav'
  with wave.open(str(audio),'wb') as w:
   w.setparams((1,2,8000,0,'NONE',''))
   w.writeframes(b''.join(struct.pack('<h',int(14000*math.sin(i*2*math.pi*440/8000)) if any(t<=i/8000<t+.2 for t in (1,6,11)) else 0) for i in range(12*8000)))
  self.st.db.x("UPDATE shots SET audio='voices/pulses.wav',audio_mode='file' WHERE id=?",(self.sid,))
  first=self.add_take('continue',0);second=self.add_take('continue',1);self.st.db.x('UPDATE shots SET cont_takes=? WHERE id=?',(json.dumps([first,second]),self.sid))
  app.assemble(self.st,False,0)
  self.assertFalse(any('ERROR' in line for line in self.st.assembly['log']),self.st.assembly['log'])
  output=self.st.abs(self.st.assembly['output']);self.assertAlmostEqual(app.media_seconds(output),12,delta=.05)
  pcm=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-ac','1','-ar','8000','-f','s16le','-'])
  import array
  values=array.array('h');values.frombytes(pcm)
  active=[i/8000 for i in range(0,min(len(values),96000),800) if max(abs(v) for v in values[i:i+800])>4000]
  for marker in (1,6,11):self.assertTrue(any(abs(t-marker)<.15 for t in active),active)
  self.assertFalse(any(4.8<t<5.2 or 9.8<t<10.2 for t in active),active)
 def test_scene_preparation_blocks_for_missing_direction_then_uses_explicit_fallback(self):
  # Start directly at animation to isolate continuation planning from keyframe/voice setup.
  pid=self.st.production.plan({'scene':'Door'})['id'];self.st.db.x("UPDATE production_plans SET phase='animation' WHERE id=?",(pid,))
  self.st.production.approve(self.sid)
  with __import__('unittest.mock',fromlist=['patch']).patch.object(self.st.production,'snapshot',wraps=self.st.production.snapshot):
   # Existing fixture root has unknown provenance; a valid keyframe approval is enough for planning.
   key=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','shots/base.mp4')",(self.sid,))
   self.st.db.x('UPDATE shots SET keyframe_take=? WHERE id=?',(key,self.sid));self.st.production.approve(self.sid)
   self.st.production.tick()
  plan=self.st.db.one('SELECT * FROM production_plans WHERE id=?',(pid,))
  self.assertEqual(plan['status'],'blocked');self.assertIn('Add direction',plan['detail'])
  options=json.loads(plan['options']);options['continue_shared']=True;self.st.db.x("UPDATE production_plans SET status='active',options=? WHERE id=?",(json.dumps(options),pid));self.st.production.tick()
  self.assertEqual(self.st.db.one("SELECT count(*) n FROM jobs WHERE stage='continue'")['n'],1)

 def test_last_frame_cache_distinguishes_same_filename_in_different_folders(self):
  other=self.st.data/'uploads/base.mp4';shutil.copyfile(self.source,other)
  a=self.st.last_frame(self.path);b=self.st.last_frame(other);self.assertNotEqual(a,b);self.assertTrue(a.exists());self.assertTrue(b.exists())
 def test_regenerate_affected_retries_the_stale_section_not_the_base_clip(self):
  self.save();sig=self.st.production.signature(self.shot(),'continue',0);cont=self.add_take('continue',0,sig);self.api.approve({'take_id':cont});self.save(prompt='New section direction')
  r=self.api.regenerate({'shot_ids':[self.sid]});self.assertEqual(r['results'][0]['stage'],'continue');self.assertEqual(r['results'][0]['queued'],1)
  self.assertEqual(self.st.db.one('SELECT stage,segment FROM jobs'),{'stage':'continue','segment':0})

 def test_continuation_coverage_includes_source_trim(self):
  self.st.db.x('UPDATE shots SET duration=5,trim_in=2 WHERE id=?',(self.sid,));self.save();r=self.generate();self.assertEqual(r['queued'],1);self.assertAlmostEqual(self.st.sections.details(self.sid)['remaining'],1.9375)

 def test_legacy_chain_requires_review_after_upstream_change_without_inventing_direction(self):
  cont=self.add_take('continue',0);self.api.approve({'take_id':cont});other=self.add_take('animate',None);self.api.approve({'take_id':other})
  self.assertEqual(self.st.production.provenance(self.st.take(cont),self.shot()),'outdated');self.assertIsNone(self.st.sections.row(self.sid,0));self.assertIsNone(self.st.take(cont)['input_signature'])

 def test_final_section_of_twelve_second_shot_requests_only_remaining_window(self):
  cont=self.add_take('continue',0);self.api.approve({'take_id':cont});self.save(1,'Turn toward companion')
  details=self.st.sections.details(self.sid);self.assertAlmostEqual(details['requested_seconds'],1.9375)
  self.assertEqual(self.generate(segment=1)['queued'],1)
  job=self.st.db.one('SELECT * FROM jobs');self.assertEqual(self.st.worker.compose(job)[1]['length'],33)
  self.assertEqual(self.st.creative.meta(job)['previous_parts'],[self.root,cont])
