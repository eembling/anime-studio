#!/usr/bin/env python3
"""Real FFmpeg checks: cue timing, looping, dialogue preservation and video stream copy."""
import array
import base64
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import anime_studio as app
from soundtrack import seconds, probe


class SoundtrackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.st = app.Studio(self.root)
        self.st.worker = app.Worker(self.st)
        self.audio = self.st.soundtrack
        self.master = self.root/'master.mp4'
        self.tone = self.root/'tone.wav'
        app.ffmpeg(['-f','lavfi','-i','color=c=blue:s=160x90:r=24:d=3','-f','lavfi','-i','sine=frequency=220:sample_rate=48000:duration=3',
                    '-filter:a','volume=0.2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-t','3',str(self.master)])
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=0.4',str(self.tone)])

    def tearDown(self):
        self.st.db.conn.close()
        self.temp.cleanup()

    def cue(self, **extra):
        body=dict(name='Bell',kind='sfx',file={'name':'bell.wav','data':base64.b64encode(self.tone.read_bytes()).decode()},
                  start='00:01',offset=0,duration=.4,volume=.8,fade_in=0,fade_out=0,loop=False,enabled=True)
        body.update(extra)
        return self.audio.save(body)['id']

    def samples(self, path):
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a','-ac','1','-ar','48000','-f','f32le','-'])
        data=array.array('f');data.frombytes(raw);return data

    def amplitude(self, data, start, frequency):
        chunk=data[int(start*48000):int((start+.1)*48000)]
        n=len(chunk)
        return 2*abs(sum(x*complex(math.cos(2*math.pi*frequency*i/48000),math.sin(2*math.pi*frequency*i/48000)) for i,x in enumerate(chunk)))/n

    def video_hashes(self,path):
        return subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_packets','-show_entries','packet=data_hash','-show_data_hash','sha256','-of','csv=p=0',str(path)])

    def test_sound_is_only_at_requested_time_and_picture_is_unchanged(self):
        self.cue()
        original=hashlib.sha256(self.master.read_bytes()).hexdigest()
        output=self.root/'mixed.mp4'
        self.audio.mix(self.master,output,self.audio.cues())
        data=self.samples(output)
        self.assertLess(self.amplitude(data,.5,880),.002)
        self.assertGreater(self.amplitude(data,1.1,880),.03)
        self.assertLess(self.amplitude(data,1.8,880),.002)
        self.assertGreater(self.amplitude(data,.5,220),.01)
        self.assertEqual(self.video_hashes(self.master),self.video_hashes(output))
        self.assertEqual(original,hashlib.sha256(self.master.read_bytes()).hexdigest())
        self.assertAlmostEqual(probe(output)[0],3,delta=.05)
        self.assertTrue(output.with_suffix('.audio.json').exists())

    def test_loop_fills_duration_and_disabled_cues_do_not_accumulate(self):
        self.cue(start=.5,duration=1.2,loop=True,fade_in=.1,fade_out=.1)
        output=self.root/'loop.mp4';self.audio.mix(self.master,output,self.audio.cues())
        data=self.samples(output)
        self.assertGreater(self.amplitude(data,1.3,880),.02)
        self.assertLess(self.amplitude(data,2,880),.002)
        cues=self.audio.cues();cues[0]['enabled']=0
        clean=self.root/'second_mix.mp4';self.audio.mix(self.master,clean,cues)
        self.assertEqual(self.master.read_bytes(),clean.read_bytes())

    def test_loop_repeats_trimmed_tail_without_reintroducing_skipped_intro(self):
        app.ffmpeg(['-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=0.2',
                    '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=0.2',
                    '-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]',str(self.tone)])
        self.cue(start=.5,offset=.2,duration=1.2,loop=True)
        output=self.root/'offset_loop.mp4';self.audio.mix(self.master,output,self.audio.cues())
        data=self.samples(output)
        self.assertGreater(self.amplitude(data,1.1,880),.03)
        self.assertLess(self.amplitude(data,1.1,440),.002)

    def test_edit_validation_and_timecodes(self):
        self.assertEqual(seconds('01:02.5'),62.5)
        self.assertEqual(seconds('1:02:03'),3723)
        for value in ('NaN','inf','-1','1:60','nope'):
            with self.assertRaises(ValueError): seconds(value)
        for field,value in [('volume',float('nan')),('offset',10),('duration',-1),('fade_in',2)]:
            with self.assertRaises(ValueError): self.cue(**{field:value})
        self.assertFalse(self.audio.cues())
        self.assertEqual(list((self.root/'soundtrack').glob('*')),[])

    def test_cues_persist_and_old_exports_are_discovered(self):
        aid=self.cue(start=4)
        older=self.root/'exports'/'Old_episode';older.mkdir(parents=True)
        shutil=__import__('shutil');shutil.copyfile(self.master,older/'episode.mp4')
        self.assertEqual(self.audio.discover()['found'],1)
        self.assertEqual(self.audio.exports()[0]['clean'],0)
        self.assertEqual(self.audio.discover()['found'],0)
        # An out-of-range cue is skipped, and the master is never modified.
        out=self.root/'skip.mp4';log=[];self.audio.mix(self.master,out,self.audio.cues(),log=log)
        self.assertIn('starts after',log[0]);self.assertEqual(out.read_bytes(),self.master.read_bytes())
        self.audio.delete({'id':aid});self.assertFalse(self.audio.cues())
        self.assertTrue(list((self.root/'soundtrack').glob('*')))

    def test_full_build_includes_bed_and_timed_cues_and_keeps_clean_master(self):
        api=app.API(self.st)
        api.settings({'settings':{'out_width':'160','out_height':'90','out_fps':'24','music':'tone.wav'}})
        api.shot_save({'scene':'Test','kind':'still','duration':2})
        self.cue()
        app.assemble(self.st,True,.2)
        self.assertFalse(any('ERROR' in line for line in self.st.assembly['log']))
        output=self.root/self.st.assembly['output']
        manifest=json.loads(output.with_suffix('.audio.json').read_text())
        self.assertEqual(len(manifest['cues']),2)
        self.assertEqual(self.audio.exports()[0]['clean'],1)
        master=self.root/self.audio.exports()[0]['master']
        self.assertTrue(master.exists())
        self.assertEqual(self.video_hashes(master),self.video_hashes(output))
        self.assertAlmostEqual(probe(output)[0],2,delta=.05)

    def test_export_status_follows_episode_edits_and_remixes_keep_history(self):
        api=app.API(self.st)
        sid=api.shot_save({'scene':'Timing','kind':'still','duration':2})['id']
        app.assemble(self.st,False,.25)
        original=api.state()['audio_exports'][0]
        self.assertEqual(original['edit_status'],'current')
        self.assertEqual(original['build']['fps'],24)
        self.cue()
        self.assertEqual(api.state()['audio_exports'][0]['edit_status'],'changed')
        source=self.audio.exports()[0]
        self.audio._export(source,self.root/source['master'],self.audio.cues(),'',.25)
        latest, earlier=api.state()['audio_exports']
        self.assertEqual(latest['edit_status'],'current')
        self.assertEqual(earlier['id'],original['id'])
        self.assertEqual(earlier['edit_status'],'changed')
        self.st.db.x('UPDATE shots SET duration=3 WHERE id=?',(sid,))
        self.assertEqual([row['edit_status'] for row in api.state()['audio_exports']],['changed','changed'])

    def test_remix_uses_clean_master_and_never_uses_previous_mix(self):
        self.audio.register(self.master,self.master,3)
        self.cue()
        source=self.audio.exports()[0]
        self.st.assembly['running']=True
        with self.assertRaisesRegex(ValueError,'current export'):
            self.audio.remix({'export_id':source['id']})
        self.st.assembly['running']=False
        self.audio._export(source,self.master,self.audio.cues(),'',.25)
        first=self.root/self.st.assembly['output']
        self.assertTrue(first.exists())
        self.audio._export(self.audio.exports()[0],self.master,[],'',.25)
        second=self.root/self.st.assembly['output']
        self.assertNotEqual(first,second)
        self.assertEqual(second.read_bytes(),self.master.read_bytes())
        self.assertEqual(self.audio.exports()[0]['master'],'master.mp4')

if __name__=='__main__':
    unittest.main()
