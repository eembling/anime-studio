from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import video_gpu_wait


class GPUHeadroomTests(unittest.TestCase):
    def test_competing_allocation_and_stale_telemetry(self):
        job = {'stage': 'lipsync'}
        shared = {'t': 100, 'hardware': [{'vram_free': 2 * 2**30}]}
        available = {'t': 100, 'hardware': [{'vram_free': 10 * 2**30}]}
        self.assertIn('2.0 GiB free', video_gpu_wait(job, shared, 8, 105))
        self.assertEqual(video_gpu_wait(job, available, 8, 105), '')
        self.assertIn('telemetry', video_gpu_wait(job, available, 8, 140))
        self.assertEqual(video_gpu_wait({**job, 'prompt_id': 'accepted'}, shared, 8, 105), '')
        self.assertEqual(video_gpu_wait(job, {}, 0, 105), '')
