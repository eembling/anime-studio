from io import BytesIO
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from anime_studio import comfy_download, comfy_upload


class TransferTests(unittest.TestCase):
    def test_multipart_content_length_and_chunk_sizes(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'voice.wav'
            with path.open('wb') as stream:
                stream.truncate(3 * (1 << 20) + 15)

            def send(method, url, chunks, headers, timeout):
                count = 0
                for chunk in chunks:
                    self.assertLessEqual(len(chunk), 1 << 20)
                    count += len(chunk)
                self.assertEqual(count, int(headers['Content-Length']))
                return b'{"name":"voice.wav"}'

            with patch('anime_studio.http', side_effect=send):
                self.assertEqual(comfy_upload('http://mock', path, 'voice.wav'), 'voice.wav')

    def test_short_download_is_removed_and_retry_is_atomic(self):
        with TemporaryDirectory() as folder:
            destination = Path(folder) / 'take.mp4'
            for data in [b'short', b'fully here']:
                response = BytesIO(data)
                response.headers = {'Content-Length': '10'}
                with patch('anime_studio._opener.open', return_value=response):
                    if len(data) != 10:
                        with self.assertRaisesRegex(RuntimeError, 'ended after'):
                            comfy_download('http://mock/view', destination)
                        self.assertFalse(destination.exists())
                    else:
                        comfy_download('http://mock/view', destination)
                        self.assertEqual(destination.read_bytes(), data)
                self.assertFalse(destination.with_suffix('.mp4.part').exists())
