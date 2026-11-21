from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import collect_outputs


class RenderOutputTests(unittest.TestCase):
    def test_temp_and_wrong_media_are_not_final(self):
        entries = {'outputs': {'1': {'images': [{'filename': 'preview.png', 'type': 'temp'}]},
                               '2': {'audio': [{'filename': 'sample.wav'}]}}}
        with self.assertRaisesRegex(ValueError, 'no final output'):
            collect_outputs(entries, 'animate')

    def test_only_explicit_saver_is_imported(self):
        outputs = {'2': {'videos': [{'filename': 'diagnostic.mp4'}]},
                   '9': {'images': [{'filename': 'frame-5.png'}, {'filename': 'frame-2.png'}]}}
        with self.assertRaisesRegex(ValueError, 'Several nodes'):
            collect_outputs({'outputs': outputs}, 'animate')
        graph = {'2': {'_meta': {'title': 'SAVE'}},
                 '9': {'_meta': {'title': 'FINAL_OUTPUT'}}}
        for order in [outputs, dict(reversed(list(outputs.items())))]:
            self.assertEqual([row['filename'] for row in collect_outputs({'outputs': order}, 'animate', graph)],
                             ['frame-5.png', 'frame-2.png'])
