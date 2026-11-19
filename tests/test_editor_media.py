"""FFmpeg checks for editorial trims and independent delivery audio stems."""
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
class EditorialMediaTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.st=app.Studio(self.tmp.name);self.addCleanup(self.st.db.conn.close)
        self.api=app.API(self.st)
        for k,v in [('out_width','64'),('out_height','64'),('out_fps','10')]:
            self.st.db.x('INSERT OR REPLACE INTO settings VALUES(?,?)',(k,v))

    def test_source_trim_uses_later_picture_in_final_assembly(self):
        source=self.st.data/'shots/source.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=red:s=64x64:r=10:d=0.6','-f','lavfi','-i','color=blue:s=64x64:r=10:d=1.4','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]','-map','[v]','-c:v','libx264','-pix_fmt','yuv420p',str(source)])
        sid=self.api.shot_save({'scene':'Trim','duration':.5,'kind':'motion'})['id']
        tid=self.st.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/source.mp4',10,20)",(sid,))
        self.st.db.x('UPDATE shots SET clip_take=?,trim_in=.7 WHERE id=?',(tid,sid))
        app.assemble(self.st,False,.25)
        self.assertFalse(any('ERROR' in x for x in self.st.assembly['log']),self.st.assembly['log'])
        out=self.st.abs(self.st.assembly['output'])
        rgb=subprocess.check_output(['ffmpeg','-v','error','-i',str(out),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'])
        self.assertGreater(rgb[2],rgb[0]+100)
        self.assertAlmostEqual(app.media_seconds(out),.5,delta=.15)

    def test_delivery_stems_do_not_contain_dialogue_in_effects(self):
        master=self.st.data/'exports/master.mp4'
        app.ffmpeg(['-f','lavfi','-i','color=black:s=64x64:r=10:d=1','-f','lavfi','-i','sine=frequency=440:duration=1','-shortest','-c:v','libx264','-c:a','aac',str(master)])
        self.st.soundtrack.register(master,master,1,True)
        record=self.st.soundtrack.exports()[0]
        result=self.st.editor.delivery({'id':record['id']})
        self.assertEqual(set(result['files']),{'dialogue.wav','music.wav','sfx.wav','loudness.txt'})
        import array
        def peak(name):
            path=self.st.abs(result['files'][name].removeprefix('/media/'))
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-f','f32le','-ac','1','-'])
            samples=array.array('f');samples.frombytes(raw)
            return max(abs(v) for v in samples)
        self.assertGreater(peak('dialogue.wav'),.01)
        self.assertLess(peak('sfx.wav'),.001)
        self.assertLess(peak('music.wav'),.001)
        report=self.st.abs(result['files']['loudness.txt'].removeprefix('/media/')).read_text()
        self.assertIn('input_i',report)

if __name__=='__main__':unittest.main()
