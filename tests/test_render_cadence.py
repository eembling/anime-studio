from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import ffmpeg, validate_render_cadence


class RenderCadenceTests(unittest.TestCase):
    def test_match_mismatch_and_variable_timestamps(self):
        with TemporaryDirectory() as root:
            for index, (rate, filter_graph, expected) in enumerate([
                (16, 'null', 16),
                (24, 'null', None),
                (16, "setpts=PTS+if(gte(N\\,8)\\,1\\,0)", None),
            ]):
                with self.subTest(rate=rate, filter_graph=filter_graph):
                    path = Path(root) / f'{index}.mp4'
                    ffmpeg(['-f', 'lavfi', '-i', f'color=s=64x64:r={rate}:d=1',
                            '-vf', filter_graph, '-fps_mode', 'vfr', '-c:v', 'libx264', str(path)])
                    if expected is None:
                        with self.assertRaisesRegex(ValueError, '16 fps cadence'):
                            validate_render_cadence(path, 16)
                    else:
                        self.assertEqual(validate_render_cadence(path, 16), expected)
