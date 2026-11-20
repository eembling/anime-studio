import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app
from script_dialogue import dialogue_line,script_blocks,recovery_preview,recover_dialogue

class VoiceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=app.Studio(self.tmp.name);self.addCleanup(self.st.db.conn.close)
        self.api=app.API(self.st)

    def asset(self,name):
        aid=self.api.asset_save({'name':name,'kind':'character'})['id']
        file=f'voices/{aid}.wav';self.st.abs(file).write_bytes(b'reference')
        self.st.db.x("INSERT INTO voice_clips(asset_id,path,is_ref,transcript) VALUES(?,?,1,'reference words')",(aid,file))
        return aid

    def shot(self,text,**values):
        return self.api.shot_save({'scene':'Pier','dialogue':text,**values})['id']

    def test_markdown_labels_and_delivery_directions_are_not_spoken(self):
        for label in ['**TOBI:**','**TOBI**:','TOBI:','__TOBI:__']:
            with self.subTest(label=label):
                line=dialogue_line(label+' *(to the sheep)* Tell no one. *(beat)*')
                self.assertEqual(line['speaker'],'TOBI');self.assertEqual(line['text'],'Tell no one.')
                self.assertIn('to the sheep',line['direction']);self.assertIn('beat',line['direction'])
        self.assertIsNone(dialogue_line('**SEQUENCE: TOBI BUILDS A DEN** *(20:15 – 22:15)*'))
        self.assertIsNone(dialogue_line('*The hand belongs to TOBI: oversized pack.*'))

    def test_script_draft_and_revision_share_markdown_dialogue_parser(self):
        script='**Episode conflict:** Find a lantern\n\n**EXT. ROAD — DAWN**\n\n**TOBI:** *(quietly)* Hello.\n\n**PIPPA:** Good morning.'
        self.api.script_draft({'script':script})
        shots=self.api.state()['shots']
        self.assertEqual(len(shots),2)
        self.assertEqual(shots[0]['scene'],'EXT. ROAD — DAWN')
        self.assertEqual(shots[0]['lines'][0]['text'],'Hello.')
        self.assertEqual(shots[0]['lines'][0]['direction'],'quietly')
        revision=self.st.production.revision({'script':script})
        self.assertEqual([row['change'] for row in revision['rows']],['unchanged','unchanged'])
        self.assertEqual({a['name'] for a in self.api.state()['assets']},{'Tobi','Pippa'})

    def test_reviewed_recovery_preserves_generated_takes_and_existing_dialogue(self):
        sid=self.shot('',keyframe_prompt='**TOBI:** *(whispering)* Tell no one.',duration=7)
        other=self.shot('TOBI: Manually edited',keyframe_prompt='**TOBI:** Original text.')
        tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe','shots/original.png')",(sid,))
        self.st.db.x('UPDATE shots SET keyframe_take=? WHERE id=?',(tid,sid))
        preview=recover_dialogue(self.st,{})
        self.assertEqual([r['id'] for r in preview['rows']],[sid])
        result=recover_dialogue(self.st,{'apply':True,'token':preview['token'],'ids':[sid]})
        self.assertEqual(result['recovered'],1)
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(sid,))
        self.assertEqual(shot['duration'],7);self.assertEqual(shot['keyframe_take'],tid)
        self.assertTrue(shot['keyframe_prompt'].startswith('**TOBI:**'))
        self.assertEqual(shot['dialogue'],'TOBI: Tell no one.')
        self.assertEqual(self.st.shot_lines(shot)[0]['direction'],'whispering')
        self.assertEqual(self.st.db.one('SELECT dialogue FROM shots WHERE id=?',(other,))['dialogue'],'TOBI: Manually edited')
        checkpoint=self.st.editor.state()['history'][0]
        self.st.editor.history({'action':'restore','id':checkpoint['id']})
        self.assertEqual(self.st.db.one('SELECT dialogue FROM shots WHERE id=?',(sid,))['dialogue'],'')

    def test_recovery_rejects_stale_preview_and_empty_selection_is_noop(self):
        sid=self.shot('',keyframe_prompt='**TOBI:** Hi.')
        preview=recover_dialogue(self.st,{})
        self.assertEqual(recover_dialogue(self.st,{'apply':True,'token':preview['token'],'ids':[]})['recovered'],0)
        self.st.db.x("UPDATE shots SET dialogue='TOBI: New dialogue' WHERE id=?",(sid,))
        with self.assertRaisesRegex(ValueError,'changed'):
            recover_dialogue(self.st,{'apply':True,'token':preview['token'],'ids':[sid]})

    def test_speaker_change_does_not_inherit_other_speakers_audio(self):
        sid=self.shot('TOBI: Hello')
        self.st.abs('voices/hello.wav').write_bytes(b'clip')
        shot=self.st.db.one('SELECT * FROM shots WHERE id=?',(sid,));lines=self.st.shot_lines(shot)
        lines[0]['audio']='voices/hello.wav';self.st.save_lines(sid,lines)
        self.st.db.x("UPDATE shots SET dialogue='PIPPA: Hello' WHERE id=?",(sid,))
        self.assertIsNone(self.st.shot_lines(self.st.db.one('SELECT * FROM shots WHERE id=?',(sid,)))[0]['audio'])

    def test_queue_is_bound_to_speaker_and_occurrence_and_deduplicates_clicks(self):
        self.asset('Tobi');self.asset('Pippa')
        sid=self.shot('TOBI: No.\nPIPPA: No.\nTOBI: No.')
        with patch.object(self.st.production,'ready',return_value=True):
            first=self.api.generate({'stage':'voice','shot_ids':[sid],'lines':[{'shot_id':sid,'index':0}],'takes':2})
            again=self.api.generate({'stage':'voice','shot_ids':[sid],'takes':2})
        self.assertEqual(first['queued'],6);self.assertEqual(again['queued'],0)
        jobs=self.st.db.q('SELECT * FROM jobs ORDER BY id')
        self.assertEqual([j['speaker'] for j in jobs],['TOBI','TOBI','PIPPA','PIPPA','TOBI','TOBI'])
        worker=app.Worker(self.st)
        _,params=worker.compose(jobs[2]);self.assertNotEqual(params['files']['audio'],self.st.abs('voices/2.wav'));self.assertEqual(params['files']['audio'].read_bytes(),self.st.abs('voices/2.wav').read_bytes())
        self.assertEqual(self.st.job_line(self.st.db.one('SELECT * FROM shots WHERE id=?',(sid,)),jobs[4])['index'],2)

    def test_take_selection_and_ui_are_scoped_to_exact_line(self):
        sid=self.shot('TOBI: Yes.\nPIPPA: Yes.\nTOBI: Yes.')
        file='voices/take.wav';self.st.abs(file).write_bytes(b'take')
        tid=self.st.db.x("INSERT INTO voice_takes(shot_id,text,speaker,path,line_index) VALUES(?,'Yes.','TOBI',?,2)",(sid,file))
        lines=self.api.state()['shots'][0]['lines']
        self.assertEqual([len(l['takes']) for l in lines],[0,0,1])
        with self.assertRaisesRegex(ValueError,'another dialogue line'):
            self.api.line_set({'shot_id':sid,'index':1,'src':'take','take_id':tid})
        with patch.object(self.st,'rebuild_audio'):
            self.api.line_set({'shot_id':sid,'index':2,'src':'take','take_id':tid})
        updated=self.st.shot_lines(self.st.db.one('SELECT * FROM shots WHERE id=?',(sid,)))
        self.assertEqual([l['audio'] for l in updated],[None,None,file])

    def test_stage_directions_pass_to_tts_without_reading_them(self):
        self.asset('Tobi')
        sid=self.shot('**TOBI:** *(softly)* Hello there.')
        job={'id':1,'stage':'voice','shot_id':sid,'segment':0,'speaker':'TOBI','note':'Hello there.','seed':123}
        _,params=app.Worker(self.st).compose(job)
        self.assertEqual(params['prompt'],'Hello there.');self.assertEqual(params['direction'],'softly')
        wf=app.build_workflow(app.STARTERS['voice']['qwen3_tts_clone']['wf'],'voice',dict(params,images={'audio':'reference.wav'}))
        node=next(n for n in wf.values() if n['class_type']=='FB_Qwen3TTSVoiceClone')
        self.assertEqual(node['inputs']['target_text'],'Hello there.')
        self.assertEqual(node['inputs']['instruct'],'softly')

if __name__=='__main__':unittest.main()
