"""Cross-path regressions for frame timing, saved mixes and export snapshots."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import anime_studio as app
from timeline import layout
from export_runtime import run
from audio_processing import envelope


class ExportReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.st=app.Studio(self.tmp.name);self.api=app.API(self.st)
        self.sid=self.api.shot_save({'scene':'A','kind':'still','duration':.1})['id']

    def tearDown(self):
        self.st.exports.shutdown()
        deadline=time.time()+5
        while self.st.exports.events and time.time()<deadline:time.sleep(.01)
        self.st.db.conn.close();self.tmp.cleanup()

    def wait(self,ident):
        for _ in range(300):
            row=self.st.db.one('SELECT * FROM export_jobs WHERE id=?',(ident,))
            if row['status'] in ('done','error','cancelled'):return row
            time.sleep(.01)
        self.fail('Export did not finish')

    def test_fractional_timing_is_shared_by_cues_and_preview_state(self):
        self.st.db.x('UPDATE shots SET duration=.1 WHERE id=?',(self.sid,))
        second=self.api.shot_save({'scene':'A','duration':.5})['id']
        self.st.db.x("INSERT INTO audio_cues(name,kind,path,start,offset,duration,volume,fade_in,fade_out,loop,enabled,source_seconds,anchor_shot,anchor_offset) VALUES('cue','sfx','uploads/test.wav',0,0,1,1,0,0,0,1,1,?,0)",(second,))
        state=self.api.state()
        self.assertAlmostEqual(state['shots'][1]['timeline_start'],2/24)
        self.assertAlmostEqual(state['audio_cues'][0]['start'],2/24)
        rows=layout([{'id':i,'duration':.1} for i in range(100)],24)
        self.assertEqual(rows[-1]['start_frame'],198)

    def test_route_and_source_changes_invalidate_export_signatures(self):
        path=self.st.data/'uploads/test.wav';path.write_bytes(b'first')
        cue=dict(kind='music',path='uploads/test.wav',start=0,offset=0,duration=1,volume=1,fade_in=0,fade_out=0,enabled=1,loop=0)
        before=self.st.soundtrack.mix_signature([cue],'')
        self.assertNotEqual(before,self.st.soundtrack.mix_signature([dict(cue,kind='ambience')],''))
        path.write_bytes(b'changed bytes')
        self.assertNotEqual(before,self.st.soundtrack.mix_signature([cue],''))

    def test_exports_do_not_block_edit_lock_and_can_resume_captured_state(self):
        started=threading.Event()
        def slow(studio,*args,**kwargs):
            started.set()
            run([sys.executable,'-c','import time;time.sleep(30)'])
        with patch.object(app,'assemble',side_effect=slow):
            ident=self.api.assemble({'use_music':False})['job_id']
            self.assertTrue(started.wait(3))
            acquired=self.st.production.lock.acquire(timeout=.2)
            self.assertTrue(acquired)
            if acquired:self.st.production.lock.release()
            self.st.exports.action({'action':'cancel','id':ident})
            self.assertEqual(self.wait(ident)['status'],'cancelled')
        self.st.db.x("UPDATE shots SET scene='Edited live project'")
        captured=[]
        def resumed(studio,*args,**kwargs):
            captured.append(studio.ordered_shots()[0]['scene'])
            raise ValueError('Intentional fixture end')
        with patch.object(app,'assemble',side_effect=resumed):
            self.st.exports.action({'action':'resume','id':ident})
            self.assertEqual(self.wait(ident)['status'],'error')
        self.assertEqual(captured,['A'])

    def test_duck_envelope_attacks_and_releases_without_fixed_gain_switch(self):
        gain=envelope([.2]*48000+[0]*48000)
        self.assertEqual(gain[0],1)
        self.assertLess(gain[30],.3)
        self.assertGreater(gain[-1],gain[51])
        self.assertLess(gain[-1],1)

    def test_unrelated_script_change_keeps_scoped_preview_key(self):
        calls=[]
        with patch.object(self.st.exports,'submit',side_effect=lambda spec:calls.append(spec) or {'ok':True}):
            self.st.finishing.action({'action':'render','shot_id':self.sid})
            self.api.settings({'settings':{'script':'Unrelated metadata edit'}})
            self.st.finishing.action({'action':'render','shot_id':self.sid})
        self.assertEqual(calls[0]['cache'],calls[1]['cache'])
