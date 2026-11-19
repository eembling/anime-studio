import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app

class ShotVoiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=app.Studio(self.tmp.name);self.addCleanup(self.st.db.conn.close);self.api=app.API(self.st)
        self.tobi=self.character('Tobi');self.pippa=self.character('Pippa')
        self.location=self.api.asset_save({'name':'Tavern','kind':'location'})['id']
        self.sid=self.api.shot_save({'dialogue':'Hello there.','asset_ids':[self.tobi,self.pippa,self.location]})['id']

    def character(self,name):
        aid=self.api.asset_save({'name':name,'kind':'character'})['id']
        file=f'voices/ref-{aid}.wav';self.st.abs(file).write_bytes(name.encode())
        self.st.db.x("INSERT INTO voice_clips(asset_id,path,is_ref,transcript) VALUES(?,?,1,?)",(aid,file,name+' reference words'))
        return aid

    def shot(self):return next(s for s in self.api.state()['shots'] if s['id']==self.sid)
    def save(self,rows,generate=None,**values):
        body={'shot_id':self.sid,'token':self.shot()['voice_token'],'lines':rows,'generate':generate or [],**values}
        with patch.object(self.st.production,'ready',return_value=True):return self.api.shot_voices(body)
    def row(self,char=None,text='Hello there.',**values):
        line=self.shot()['lines'][0]
        return {'line_id':line['line_id'],'character_id':char,'text':text,'direction':'',**values}

    def test_multiple_cast_and_location_do_not_guess_a_speaker(self):
        line=self.shot()['lines'][0]
        self.assertIsNone(line['character_id']);self.assertEqual(line['speaker'],'')
        self.assertIsNone(self.st.speaker_asset('Tavern'))

    def test_dialogue_in_visual_prompt_is_suggested_without_changing_it(self):
        self.api.shot_save({'id':self.sid,'dialogue':'','keyframe_prompt':'**TOBI:** *(quietly)* Tell no one.'})
        shot=self.shot()
        self.assertEqual(shot['lines'],[])
        self.assertEqual(shot['voice_suggestion'][0]['text'],'Tell no one.')
        self.assertEqual(shot['voice_suggestion'][0]['direction'],'quietly')
        self.assertEqual(shot['dialogue'],'')
        self.assertEqual(shot['keyframe_prompt'],'**TOBI:** *(quietly)* Tell no one.')

    def test_generates_exact_saved_words_from_selected_character(self):
        result=self.save([self.row(self.pippa,'These are the words.',direction='softly')],[0],takes=2)
        self.assertEqual(result['queued'],2)
        line=self.shot()['lines'][0];self.assertEqual(line['character_id'],self.pippa)
        job=self.st.db.one('SELECT * FROM jobs ORDER BY id')
        self.assertEqual(job['voice_character_id'],self.pippa);self.assertEqual(job['voice_line_id'],line['line_id'])
        _,params=app.Worker(self.st).compose(job)
        self.assertEqual(params['prompt'],'These are the words.')
        self.assertEqual(params['files']['audio'],self.st.abs(f'voices/ref-{self.pippa}.wav'))
        self.assertEqual(params['ref_text'],'Pippa reference words');self.assertEqual(params['direction'],'softly')
        self.assertEqual(self.shot()['lines'][0]['pending'],['pending','pending'])

    def test_bound_character_survives_rename_and_queued_reference_is_frozen(self):
        self.save([self.row(self.pippa)],[0])
        job=self.st.db.one('SELECT * FROM jobs')
        self.st.db.x("UPDATE assets SET name='Pippa Renamed' WHERE id=?",(self.pippa,))
        self.st.db.x("UPDATE voice_clips SET transcript='Changed reference transcript' WHERE asset_id=?",(self.pippa,))
        line=self.shot()['lines'][0]
        self.assertEqual(line['character_name'],'Pippa Renamed')
        _,params=app.Worker(self.st).compose(job)
        self.assertEqual(params['ref_text'],'Pippa reference words')
        self.assertEqual(self.st.line_character(line)['id'],self.pippa)

    def test_character_change_clears_selected_audio_without_deleting_takes(self):
        self.save([self.row(self.tobi)])
        line=self.shot()['lines'][0];file='voices/generated.wav';self.st.abs(file).write_bytes(b'audio')
        tid=self.st.db.x("INSERT INTO voice_takes(shot_id,text,speaker,path,line_index,character_id,line_id) VALUES(?,?,?,?,?,?,?)",(self.sid,line['text'],line['speaker'],file,0,self.tobi,line['line_id']))
        with patch.object(self.st,'rebuild_audio'):
            self.api.line_set({'shot_id':self.sid,'index':0,'src':'take','take_id':tid,'line_id':line['line_id']})
        self.save([self.row(self.pippa)])
        self.assertIsNone(self.shot()['lines'][0]['audio']);self.assertEqual(self.shot()['lines'][0]['takes'],[])
        self.assertIsNotNone(self.st.db.one('SELECT id FROM voice_takes WHERE id=?',(tid,)))
        self.assertTrue(self.st.abs(file).is_file())
        with self.assertRaisesRegex(ValueError,'another dialogue line'):
            self.api.line_set({'shot_id':self.sid,'index':0,'src':'take','take_id':tid})

    def test_repeated_words_keep_distinct_ids_across_reordering(self):
        self.save([self.row(self.tobi,'Yes.'),{'text':'Yes.','character_id':self.tobi}],[0,1])
        first,second=self.shot()['lines'];self.assertNotEqual(first['line_id'],second['line_id'])
        jobs=self.st.db.q('SELECT * FROM jobs ORDER BY id')
        self.save([{'line_id':l['line_id'],'character_id':l['character_id'],'text':l['text']} for l in [second,first]])
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(self.sid,))
        self.assertEqual(self.st.job_line(shot,jobs[0])['index'],1)
        self.assertEqual(self.st.job_line(shot,jobs[1])['index'],0)

    def test_changed_delivery_cannot_attach_old_job_to_current_line(self):
        self.save([self.row(self.tobi,direction='angry')],[0]);job=self.st.db.one('SELECT * FROM jobs')
        self.save([self.row(self.tobi,direction='calm')],[0])
        self.assertEqual(self.st.db.one('SELECT COUNT(*) n FROM jobs')['n'],2)
        self.assertEqual(self.shot()['lines'][0]['pending'],['pending'])
        self.assertIsNone(self.st.job_line(self.st.db.one('SELECT * FROM shots WHERE id=?',(self.sid,)),job))

    def test_locations_and_stale_drafts_are_rejected_before_mutation(self):
        before=self.shot()['dialogue']
        with self.assertRaisesRegex(ValueError,'not a location'):self.save([self.row(self.location)])
        self.assertEqual(self.shot()['dialogue'],before)
        token=self.shot()['voice_token'];self.api.shot_save({'id':self.sid,'dialogue':'Changed words'})
        with self.assertRaisesRegex(ValueError,'changed'):
            self.api.shot_voices({'shot_id':self.sid,'token':token,'lines':[]})

    def test_no_cross_character_fuzzy_recording_match(self):
        self.st.db.x("UPDATE voice_clips SET transcript='Hello there.'")
        self.assertEqual(self.api.match({'shot_ids':[self.sid]})['matched'],0)
        self.save([self.row(self.pippa)])
        self.st.db.x('DELETE FROM voice_clips WHERE asset_id=?',(self.pippa,))
        self.assertEqual(self.api.match({'shot_ids':[self.sid]})['matched'],0)

    def test_whole_shot_override_requires_explicit_switch(self):
        self.st.db.x("UPDATE shots SET audio_mode='file',audio='voices/whole.wav' WHERE id=?",(self.sid,))
        with self.assertRaisesRegex(ValueError,'whole-shot'):self.save([self.row(self.tobi)])
        self.save([self.row(self.tobi)],replace_file=True)
        self.assertEqual(self.shot()['audio_mode'],'lines');self.assertIsNone(self.shot()['audio'])

    def test_punctuation_edit_invalidates_the_old_selected_audio(self):
        self.save([self.row(self.tobi,'Really?')]);line=self.shot()['lines'][0]
        self.st.abs('voices/old.wav').write_bytes(b'old')
        line.update(audio='voices/old.wav',src='tts');self.st.save_lines(self.sid,[line])
        self.api.shot_save({'id':self.sid,'dialogue':'Tobi: Really!'})
        self.assertIsNone(self.shot()['lines'][0]['audio'])

    def test_first_new_take_replaces_temporary_audio_and_manual_selection_is_protected(self):
        self.save([self.row(self.tobi)])
        self.st.abs('voices/temp.wav').write_bytes(b'temp')
        line=self.shot()['lines'][0];line.update(audio='voices/temp.wav',src='temp');self.st.save_lines(self.sid,[line])
        with patch.object(self.st,'rebuild_audio'):
            self.save([self.row(self.tobi)],[0],takes=2)
        jobs=self.st.db.q('SELECT * FROM jobs ORDER BY id')
        self.assertEqual([j['voice_autoselect'] for j in jobs],[1,0])
        self.assertEqual(jobs[0]['voice_previous_audio'],'voices/temp.wav')
        def convert(args):Path(args[-1]).write_bytes(b'generated voice')
        self.st.abs('jobs/result.wav').write_bytes(b'render')
        with patch.object(app,'ffmpeg',side_effect=convert),patch.object(app,'media_seconds',return_value=1),patch.object(self.st,'rebuild_audio'):
            app.Worker(self.st).finish(jobs[0],'voice',[self.st.abs('jobs/result.wav')])
        chosen=self.shot()['lines'][0]
        self.assertEqual(chosen['src'],'tts');self.assertNotEqual(chosen['audio'],'voices/temp.wav')
        self.assertEqual(chosen['takes'][0]['character_id'],self.tobi)
        self.assertEqual(chosen['takes'][0]['line_id'],chosen['line_id'])
        self.st.db.x("UPDATE jobs SET status='done'")
        with patch.object(self.st,'rebuild_audio'):
            self.save([self.row(self.tobi)],[0])
        latest=self.st.db.q('SELECT * FROM jobs ORDER BY id DESC')[0]
        self.assertEqual(latest['voice_autoselect'],1)
        self.st.abs('voices/manual.wav').write_bytes(b'manual selection')
        current=self.shot()['lines'][0];current.update(audio='voices/manual.wav',src='upload');self.st.save_lines(self.sid,[current])
        self.st.abs('jobs/result.wav').write_bytes(b'another render')
        with patch.object(app,'ffmpeg',side_effect=convert),patch.object(app,'media_seconds',return_value=1),patch.object(self.st,'rebuild_audio'):
            app.Worker(self.st).finish(latest,'voice',[self.st.abs('jobs/result.wav')])
        self.assertEqual(self.shot()['lines'][0]['audio'],'voices/manual.wav')

    def test_readiness_failure_reports_that_dialogue_was_saved(self):
        body={'shot_id':self.sid,'token':self.shot()['voice_token'],'lines':[self.row(self.pippa)],'generate':[0]}
        with patch.object(self.st.production,'ready',return_value=False):result=self.api.shot_voices(body)
        self.assertFalse(result['ok']);self.assertTrue(result['dialogue_saved'])
        self.assertEqual(self.shot()['lines'][0]['character_id'],self.pippa)

if __name__=='__main__':unittest.main()
