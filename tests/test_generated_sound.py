from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import API, Studio, Worker, build_workflow, ffmpeg


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
