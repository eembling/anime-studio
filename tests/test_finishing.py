"""Real media regressions for finishing, mixer routing and reusable sounds."""
import array
import base64
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


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class FinishingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.st=app.Studio(self.temp.name)
        self.addCleanup(self.st.db.conn.close)
        self.api=app.API(self.st)
        self.st.db.x('UPDATE instances SET enabled=0')
        for key,value in [('out_width','128'),('out_height','72'),('out_fps','24')]:
            self.st.db.x('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))
        self.shots=[]
        for index,color in enumerate(('red','blue')):
            sid=self.api.shot_save({'scene':'Scene','kind':'still','duration':1})['id']
            file=self.st.data/'shots'/f'{color}.png'
            app.ffmpeg(['-f','lavfi','-i',f'color={color}:s=128x72','-frames:v','1',str(file)])
            tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path) VALUES(?,'keyframe',?)",(sid,self.st.rel(file)))
            self.st.db.x('UPDATE shots SET keyframe_take=? WHERE id=?',(tid,sid))
            self.shots.append(sid)
        self.tone=self.st.data/'voices/tone.wav'
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=440:duration=2','-af','volume=3',str(self.tone)])

    def pixels(self,path,time):
        return subprocess.check_output(['ffmpeg','-v','error','-ss',str(time),'-i',str(path),'-frames:v','1','-vf','scale=1:1',
                                        '-f','rawvideo','-pix_fmt','rgb24','-'])

    def samples(self,path,start=0,length=2):
        raw=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-t',str(length),'-i',str(path),
                                     '-vn','-f','f32le','-ac','2','-ar','48000','-'])
        values=array.array('f');values.frombytes(raw)
        return list(values[::2]),list(values[1::2])

    def rms(self,values):
        return (sum(v*v for v in values)/max(1,len(values)))**.5

    def build(self):
        app.assemble(self.st,False,.25)
        self.assertIsNotNone(self.st.assembly['output'],self.st.assembly['log'])
        self.assertFalse(any('ERROR:' in line for line in self.st.assembly['log']))
        return self.st.abs(self.st.assembly['output'])

    def test_dissolve_and_black_preserve_runtime_and_blend_boundary(self):
        for transition in ('dissolve','black'):
            with self.subTest(transition=transition):
                self.st.db.x('UPDATE shots SET transition=?,transition_seconds=.5 WHERE id=?',(transition,self.shots[0]))
                output=self.build()
                self.assertAlmostEqual(app.media_seconds(output),2,delta=.06)
                middle=self.pixels(output,1.2 if transition=='dissolve' else 1)
                if transition=='dissolve':
                    self.assertGreater(middle[0],25)
                    self.assertGreater(middle[2],25)
                else:
                    self.assertLess(max(middle),30)

    def test_dialogue_can_cross_picture_cut_and_pan_to_one_side(self):
        sid=self.shots[1]
        self.st.db.x("UPDATE shots SET audio='voices/tone.wav',audio_mode='file',dialogue_offset=-.4,dialogue_tail=.5 WHERE id=?",(sid,))
        mixer=self.st.finishing.mixer()
        mixer['dialogue']['pan']=-1
        self.st.finishing.action({'action':'mixer','mixer':mixer})
        output=self.build()
        left,right=self.samples(output,.7,.15)
        self.assertGreater(self.rms(left),.01)
        self.assertLess(self.rms(right),.001)
        before,_=self.samples(output,.1,.15)
        self.assertLess(self.rms(before),.001)

    def test_library_reuse_mute_and_saved_episode_sharing(self):
        file={'name':'bell.wav','data':'data:audio/wav;base64,'+base64.b64encode(self.tone.read_bytes()).decode()}
        ident=self.st.finishing.action({'action':'library_save','name':'Bell','category':'Foley','tags':'metal, door','file':file})['id']
        result=self.st.soundtrack.save({'library_id':ident,'name':'Bell cue','kind':'ambience','start':0,'duration':1})
        cue=self.st.db.one('SELECT * FROM audio_cues WHERE id=?',(result['id'],))
        sound=self.st.db.one('SELECT * FROM sound_library WHERE id=?',(ident,))
        self.assertEqual(cue['path'],sound['path'])
        mixer=self.st.finishing.mixer();mixer['ambience']['mute']=True
        self.st.finishing.action({'action':'mixer','mixer':mixer})
        app.assemble(self.st,False,.25)
        left,_=self.samples(self.st.abs(self.st.assembly['output']))
        self.assertLess(self.rms(left),.001)
        self.assertNotIn('sound_library',self.st.editor.snapshot(False))
        self.st.finishing.action({'action':'library_delete','id':ident})
        self.assertTrue(self.st.abs(cue['path']).exists())

    def test_cached_shot_render_contains_dialogue_and_absolute_effect(self):
        self.st.db.x("UPDATE shots SET audio='voices/tone.wav',audio_mode='file' WHERE id=?",(self.shots[1],))
        self.st.soundtrack.save({'name':'effect','kind':'sfx','start':1.1,'duration':.4,'file':{
            'name':'effect.wav','data':'data:audio/wav;base64,'+base64.b64encode(self.tone.read_bytes()).decode()}})
        response=self.st.finishing.action({'action':'render','shot_id':self.shots[1]})
        self.assertFalse(response['cached'])
        for _ in range(200):
            if not self.st.assembly['running']:break
            time.sleep(.05)
        self.assertFalse(self.st.assembly['running'])
        output=self.st.assembly['output']
        self.assertIsNotNone(output,self.st.assembly['log'])
        self.assertAlmostEqual(app.media_seconds(self.st.abs(output)),1,delta=.05)
        self.assertGreater(self.rms(self.samples(self.st.abs(output))[0]),.01)
        response=self.st.finishing.action({'action':'render','shot_id':self.shots[1]})
        self.assertTrue(response['cached'])

    def test_window_keeps_loop_phase_and_fade_envelope(self):
        cue=dict(name='bell',kind='sfx',path='voices/tone.wav',start=1,duration=5,
                 offset=.2,volume=1,loop=1,enabled=1,fade_in=2,fade_out=1)
        rows=self.st.finishing.window_tracks([cue],2.5,1)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['window_skip'],1.5)
        self.assertEqual(rows[0]['original_duration'],5)
        self.assertEqual(rows[0]['start'],0)

    def test_ducking_reduces_music_during_speech(self):
        self.st.db.x("UPDATE shots SET audio='voices/tone.wav',audio_mode='file' WHERE id=?",(self.shots[0],))
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=2000:duration=2','-af','volume=3',str(self.st.data/'uploads/music.wav')])
        self.st.soundtrack.save({'name':'Music','kind':'music','start':0,'duration':2,'file':{
            'name':'music.wav','data':'data:audio/wav;base64,'+base64.b64encode((self.st.data/'uploads/music.wav').read_bytes()).decode()}})
        plain=self.build()
        mixer=self.st.finishing.mixer();mixer['duck']=True
        self.st.finishing.action({'action':'mixer','mixer':mixer})
        ducked=self.build()
        def music_level(file):
            raw=subprocess.check_output(['ffmpeg','-v','error','-ss','0.4','-t','0.4','-i',str(file),'-vn',
                                         '-af','highpass=f=1500','-f','f32le','-ac','1','-'])
            values=array.array('f');values.frombytes(raw)
            return self.rms(values)
        self.assertLess(music_level(ducked),music_level(plain)*.7)
