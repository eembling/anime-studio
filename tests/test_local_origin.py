"""The local backend only answers the Anime Studio app on this computer."""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from anime_studio import API, Studio, make_handler


class LocalOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.studio = Studio(self.tmp.name)
        self.addCleanup(self.studio.db.conn.close)
        self.addCleanup(self.studio.shot_previews.shutdown)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.studio, API(self.studio)))
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def shots(self):
        return self.studio.db.one("SELECT COUNT(*) n FROM shots")["n"]

    def test_app_pages_still_work(self):
        own = f"http://127.0.0.1:{self.port}"
        status, _ = self.request("GET", "/api/ping")
        self.assertEqual(status, 200)
        status, body = self.request("POST", "/api/shot/save", json.dumps({"kind": "motion"}),
                                    {"Content-Type": "application/json", "Origin": own})
        self.assertEqual(status, 200, body)
        status, _ = self.request("POST", "/api/shot/save", json.dumps({"kind": "motion"}),
                                 {"Content-Type": "application/json", "Host": f"localhost:{self.port}"})
        self.assertEqual(status, 200)
        self.assertEqual(self.shots(), 2)

    def test_cross_site_post_is_refused(self):
        body = json.dumps({"kind": "motion", "keyframe_prompt": "from another website"})
        for headers in ({"Content-Type": "text/plain", "Origin": "https://evil.example"},
                        {"Content-Type": "application/json", "Origin": "https://evil.example"},
                        {"Content-Type": "text/plain"},
                        {"Content-Type": "application/x-www-form-urlencoded"},
                        {"Content-Type": "application/json", "Origin": "null"}):
            with self.subTest(headers=headers):
                status, _ = self.request("POST", "/api/shot/save", body, headers)
                self.assertEqual(status, 403)
        self.assertEqual(self.shots(), 0)

    def test_dns_rebinding_host_is_refused(self):
        for path in ("/api/state", "/", "/api/ping"):
            with self.subTest(path=path):
                status, body = self.request("GET", path, headers={"Host": f"evil.example:{self.port}"})
                self.assertEqual(status, 403)
                self.assertNotIn(b"project_id", body)
        status, _ = self.request("POST", "/api/shot/save", "{}",
                                 {"Content-Type": "application/json", "Host": f"evil.example:{self.port}",
                                  "Origin": f"http://evil.example:{self.port}"})
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
