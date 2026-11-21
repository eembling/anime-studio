from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker, build_workflow, collect_outputs, ffmpeg


class GeneratedSoundTests(unittest.TestCase):
    def test_effect_candidate_binds_duration_and_follows_anchor_shot(self):
        with tempfile.TemporaryDirectory() as root:
            studio = Studio(root)
            self.addCleanup(studio.db.conn.close)
            self.addCleanup(studio.shot_previews.shutdown)
            studio.production.ready = lambda *args: True
            api = API(studio)
            api.workflow({'stage':'sound_effect','starter':'stable_audio_open'})
            first = api.shot_save({'kind':'motion','duration':3})['id']
            anchor = api.shot_save({'kind':'motion','duration':3})['id']
            result = api.generate({'stage':'sound_effect','shot_ids':[anchor],'prompt':'A short bell impact',
                                   'duration':1.25,'kind':'sfx','anchor_offset':0.5,'seed':12})
            self.assertEqual(result['queued'],1)
            job = studio.db.one('SELECT * FROM jobs WHERE id=?',(result['job_ids'][0],))
            meta = studio.snapshots.meta(job)
            graph = build_workflow(meta['workflow'], meta['key'], meta['params'])
            self.assertEqual(graph['3']['inputs']['text'],'A short bell impact')
            self.assertEqual(graph['5']['inputs']['seconds'],1.25)
            self.assertEqual(graph['6']['inputs']['seed'],12)
            saved = {'filename':'candidate.wav','type':'output'}
            self.assertEqual(collect_outputs({'outputs':{'8':{'audio':[saved]}}}, 'sound_effect', graph), [saved])
            output = studio.data/'mock.wav'
            ffmpeg(['-f','lavfi','-i','sine=duration=1.25',str(output)])
            Worker(studio).finish(job,'sound_effect',[output])
            sound = studio.finishing.state()['library'][0]
            generation = sound['generation']
            api.audio_cue_save({'library_id':sound['id'],'anchor_shot':generation['shot_id'],
                                'anchor_offset':generation['anchor_offset'],'kind':generation['kind']})
            self.assertEqual(studio.soundtrack.cues()[0]['start'],3.5)
            studio.editor.timeline({'action':'reorder','ids':[anchor,first]})
            self.assertEqual(studio.soundtrack.cues()[0]['start'],0.5)

    def test_music_candidate_revision_keeps_cue_placement_and_reaches_stems(self):
        import array
        import subprocess
        with tempfile.TemporaryDirectory() as root:
            studio = Studio(root)
            self.addCleanup(studio.db.conn.close)
            self.addCleanup(studio.shot_previews.shutdown)
            studio.production.ready = lambda *args: True
            api = API(studio)
            api.workflow({'stage':'music_cue','starter':'ace_step_1_5_turbo'})
            shot = api.shot_save({'kind':'motion','duration':3})['id']
            ids = []
            for seed, frequency in [(35, 440), (36, 880)]:
                result = api.generate({'stage':'music_cue','shot_ids':[shot],'prompt':'Muted bells and strings',
                                       'duration':2,'tempo':96,'anchor_offset':0.5,'seed':seed})
                job = studio.db.one('SELECT * FROM jobs WHERE id=?',(result['job_ids'][0],))
                meta = studio.snapshots.meta(job)
                graph = build_workflow(meta['workflow'], meta['key'], meta['params'])
                self.assertEqual(graph['2']['inputs']['tags'],'Muted bells and strings')
                self.assertEqual(graph['2']['inputs']['bpm'],96)
                self.assertEqual(graph['2']['inputs']['duration'],2)
                self.assertEqual(graph['4']['inputs']['seconds'],2)
                saved = {'filename':'score.wav','type':'output'}
                self.assertEqual(collect_outputs({'outputs':{'8':{'audio':[saved]}}}, 'music_cue', graph), [saved])
                output = studio.data/f'{seed}.wav'
                ffmpeg(['-f','lavfi','-i',f'sine=frequency={frequency}:duration=2',str(output)])
                Worker(studio).finish(job,'music_cue',[output])
                ids.append(next(sound['id'] for sound in studio.finishing.state()['library'] if sound['generation']['seed']==seed))
            cue = api.audio_cue_save({'library_id':ids[0],'anchor_shot':shot,'anchor_offset':0.5,'kind':'music'})
            current = studio.soundtrack.cues()[0]
            api.audio_cue_save({**current,'id':cue['id'],'library_id':ids[1]})
            chosen = studio.soundtrack.cues()[0]
            self.assertEqual(chosen['path'],f'sound-library/{ids[1]}.wav')
            self.assertEqual(chosen['start'],0.5)
            self.assertEqual(len(studio.finishing.state()['library']),2)
            master = studio.data/'master.mp4'
            ffmpeg(['-f','lavfi','-i','color=s=64x64:r=16:d=3','-c:v','libx264',str(master)])
            mixed = studio.data/'mix.mp4'
            stems = studio.data/'stems'
            stems.mkdir()
            studio.finishing.mix_audio(master,mixed,studio.soundtrack.cues(),None,0,3,stem_dir=stems)
            for audio in (mixed, stems/'music.wav'):
                data = subprocess.check_output(['ffmpeg','-v','error','-ss','1','-i',str(audio),'-t','0.1',
                                                '-f','s16le','-ac','1','-ar','48000','-'])
                samples = array.array('h',data)
                crossings = sum(a <= 0 < b for a,b in zip(samples,samples[1:]))
                self.assertAlmostEqual(crossings,88,delta=3)
