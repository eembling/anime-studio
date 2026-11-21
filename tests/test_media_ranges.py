from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from anime_studio import make_handler


class MediaRangeTests(unittest.TestCase):
    def test_ranges_against_http_handler(self):
        with TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "clip").write_bytes(b"abcde")
            (root / "empty").touch()
            with ThreadingHTTPServer(("127.0.0.1", 0), make_handler(SimpleNamespace(data=root), None)) as server:
                thread = Thread(target=server.serve_forever)
                thread.start()
                try:
                    for path, value, status, data in [
                        ("clip", "0-1", 206, b"ab"),
                        ("clip", "-2", 206, b"de"),
                        ("clip", "4-", 206, b"e"),
                        ("clip", "5-", 416, b""),
                        ("clip", "2-1", 416, b""),
                        ("clip", "-0", 416, b""),
                        ("empty", "0-", 416, b""),
                        ("clip", "0-1,3-4", 200, b"abcde"),
                        ("clip", "0-" + "9" * 5000, 206, b"abcde"),
                    ]:
                        with self.subTest(path=path, value=value[:20]):
                            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                            try:
                                connection.request("GET", "/media/" + path, headers={"Range": "bytes=" + value})
                                response = connection.getresponse()
                                self.assertEqual((response.status, response.read()), (status, data))
                                self.assertEqual(response.getheader("Content-Length"), str(len(data)))
                                if status == 416:
                                    self.assertEqual(response.getheader("Content-Range"), f"bytes */{(root / path).stat().st_size}")
                            finally:
                                connection.close()
                finally:
                    server.shutdown()
                    thread.join()
