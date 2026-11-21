import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import STARTERS, check_workflow
from clip_audio import audio_inputs


class AudioConditioningTests(unittest.TestCase):
    def test_native_and_custom_conditioning_paths(self):
        graph = copy.deepcopy(STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        self.assertEqual(audio_inputs(graph), ['11'])
        self.assertEqual(check_workflow(graph, 'lipsync'), [])
        graph['22']['class_type'] = 'CustomLipSync'
        self.assertEqual(audio_inputs(graph), [])
        graph['22']['_meta']['title'] = 'AUDIO_CONDITIONING'
        self.assertEqual(audio_inputs(graph), ['11'])

    def test_final_mux_does_not_count_as_conditioning(self):
        graph = copy.deepcopy(STARTERS['lipsync']['wan22_s2v_14b_fp8']['wf'])
        del graph['22']['inputs']['audio_encoder_output']
        graph['50']['class_type'] = 'VHS_VideoCombine'
        graph['50']['inputs']['audio'] = ['11', 0]
        self.assertEqual(audio_inputs(graph), [])
        self.assertTrue(any('visual conditioning' in problem for problem in check_workflow(graph, 'lipsync')))
