import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import STARTERS, build_workflow, check_workflow


class VisualPromptBindingTests(unittest.TestCase):
    def setUp(self):
        self.graph = copy.deepcopy(STARTERS['animate']['wan22_14b_fp8']['wf'])

    def test_disconnected_and_swapped_explicit_bindings_are_rejected(self):
        unused = copy.deepcopy(self.graph['11'])
        self.graph['100'] = unused
        self.assertTrue(any('Tagged PROMPT' in p for p in check_workflow(self.graph, 'animate')))
        del self.graph['100']
        inputs = self.graph['13']['inputs']
        inputs['positive'], inputs['negative'] = inputs['negative'], inputs['positive']
        self.assertTrue(any('Tagged NEGATIVE' in p for p in check_workflow(self.graph, 'animate')))

    def test_both_explicit_positive_encoders_receive_direction(self):
        self.graph['100'] = copy.deepcopy(self.graph['11'])
        self.graph['21']['inputs']['positive'] = ['100', 0]
        self.assertEqual(check_workflow(self.graph, 'animate'), [])
        result = build_workflow(self.graph, 'animate', {'prompt': 'A slow dolly in',
                                'negative': 'Flicker', 'images': {'start': 'shot.png'}, 'seed': 1, 'prefix': 'test'})
        self.assertEqual(result['11']['inputs']['text'], 'A slow dolly in')
        self.assertEqual(result['100']['inputs']['text'], 'A slow dolly in')
        self.assertEqual(result['12']['inputs']['text'], 'Flicker')
