"""Renders survive restarts, stop on the server when cancelled or late, and retry downloads."""
import io
import json
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image

from studio import Project

SCRIPT = "Panel 1\nKeyframe: Station.\nMika: Late again."


class Comfy(BaseHTTPRequestHandler):
    """A ComfyUI stand-in whose history, queue and downloads the test controls."""

    def reply(self, body, status=200, raw=False):
        data = body if raw else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        srv = self.server
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path == "/upload/image":
            return self.reply({"name": "ref.png"})
        if self.path == "/prompt":
            srv.prompts.append(json.loads(body))
            return self.reply({"prompt_id": f"p{len(srv.prompts)}"})
        if self.path == "/queue":
            srv.deleted += json.loads(body).get("delete", [])
            return self.reply({})
        if self.path == "/interrupt":
            srv.interrupts.append(json.loads(body or b"{}"))
            return self.reply({})
        self.reply({}, 404)

    def do_GET(self):
        srv = self.server
        if self.path == "/system_stats":
            return self.reply({"devices": [{"name": "Test GPU"}]})
        if self.path.startswith("/queue"):
            return self.reply({"queue_running": [[0, pid, {}, {"client_id": "x"}] for pid in srv.running], "queue_pending": []})
        if self.path.startswith("/history/"):
            pid = self.path.rsplit("/", 1)[1]
            if pid in srv.done:
                return self.reply({pid: {"outputs": {"60": {"images": [{"filename": "panel.png", "type": "output"}]}},
                                         "prompt": [0, pid, {"60": {"class_type": "SaveImage", "inputs": {}}}, {}]}})
            return self.reply({})
        if self.path.startswith("/history"):
            return self.reply({pid: {"prompt": [0, pid, {}, {"client_id": cid}]} for pid, cid in srv.known.items()})
        if self.path.startswith("/view?"):
            if srv.view_failures:
                srv.view_failures -= 1
                return self.reply({}, 500)
            return self.reply(srv.image, raw=True)
        self.reply({}, 404)

    def log_message(self, *args):
        pass


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Comfy)
        srv.prompts, srv.deleted, srv.interrupts, srv.running, srv.done, srv.known = [], [], [], [], set(), {}
        srv.view_failures = 0
        buffer = io.BytesIO()
        Image.new("RGB", (64, 64), "gray").save(buffer, "PNG")
        srv.image = buffer.getvalue()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        stack.callback(srv.server_close)
        stack.callback(srv.shutdown)
        self.comfy, self.url = srv, f"http://127.0.0.1:{srv.server_port}"
        self.p = self.project()
        s = self.p.action("plan", dict(title="T", script=SCRIPT, limit=4))
        c = s["chapters"][0]
        self.ctx = dict(chapter=c["id"], page=c["pages"][0]["id"])

    def project(self):
        p = Project(self.root)
        with p.edit() as s:
            s["servers"] = [dict(id="s1", name="GPU", url=self.url, roles=["panel", "sheet"], enabled=True)]
            s["timeout"] = 30
        return p

    def run_worker(self, p, until, seconds=8):
        stop = threading.Event()
        worker = threading.Thread(target=p.worker, args=(stop,), daemon=True)
        worker.start()
        try:
            deadline = time.monotonic() + seconds
            while not until(p.state()["jobs"]):
                self.assertLess(time.monotonic(), deadline, p.state()["jobs"])
                time.sleep(.05)
        finally:
            stop.set()
            worker.join(5)

    def test_restart_reconnects_to_the_running_render(self):
        self.p.action("render", self.ctx)
        self.comfy.done.add("p1")
        with self.p.edit() as s:     # as if the app closed after submitting
            s["jobs"][0].update(status="Rendering", server=self.url, server_name="GPU", prompt_id="p1", started=time.time(),
                                submitted=time.time(), client_id="manga-studio-" + s["jobs"][0]["id"])
        reopened = self.project()
        self.run_worker(reopened, lambda jobs: jobs[0]["status"] == "Ready")
        self.assertEqual(self.comfy.prompts, [])                     # collected, never submitted twice
        self.assertEqual(len(reopened.state()["chapters"][0]["pages"][0]["panels"][0]["takes"]), 1)

    def test_restart_finds_a_submission_by_client_id(self):
        self.p.action("render", self.ctx)
        with self.p.edit() as s:
            job = s["jobs"][0]
            job.update(status="Rendering", server=self.url, server_name="GPU", client_id="manga-studio-" + job["id"])
        self.comfy.known["p9"] = "manga-studio-" + self.p.state()["jobs"][0]["id"]
        self.comfy.done.add("p9")
        reopened = self.project()
        self.run_worker(reopened, lambda jobs: jobs[0]["status"] == "Ready")
        self.assertEqual(self.comfy.prompts, [])
        self.assertEqual(reopened.state()["jobs"][0]["prompt_id"], "p9")

    def test_stop_a_running_render_on_the_server(self):
        self.p.action("render", self.ctx)
        self.comfy.running.append("p1")
        stop = threading.Event()
        threading.Thread(target=self.p.worker, args=(stop,), daemon=True).start()
        try:
            deadline = time.monotonic() + 8
            while not self.p.state()["jobs"][0].get("prompt_id"):
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
            self.p.action("cancel", dict(id=self.p.state()["jobs"][0]["id"]))
            while self.p.state()["jobs"][0]["status"] != "Canceled":
                self.assertLess(time.monotonic(), deadline)
                time.sleep(.05)
        finally:
            stop.set()
        self.assertEqual(self.comfy.deleted, ["p1"])
        self.assertEqual(self.comfy.interrupts, [{"prompt_id": "p1"}])
        with self.assertRaisesRegex(ValueError, "already finished"):
            self.p.action("cancel", dict(id=self.p.state()["jobs"][0]["id"]))

    def test_timeout_stops_the_render_on_the_server(self):
        with self.p.edit() as s:
            s["timeout"] = 10
        self.p.action("render", self.ctx)
        with self.p.edit() as s:
            s["jobs"][0]["timeout"] = 1
        self.comfy.running.append("p1")
        self.run_worker(self.p, lambda jobs: jobs[0]["status"] == "Failed")
        self.assertIn("stopped on the server", self.p.state()["jobs"][0]["error"])
        self.assertEqual(self.comfy.deleted, ["p1"])

    def test_download_is_retried_then_offered_again(self):
        self.p.action("render", self.ctx)
        self.comfy.done.add("p1")
        self.comfy.view_failures = 2
        self.run_worker(self.p, lambda jobs: jobs[0]["status"] == "Ready", seconds=15)
        self.assertEqual(len(self.comfy.prompts), 1)

        self.p.action("render", dict(self.ctx, panel=self.p.state()["chapters"][0]["pages"][0]["panels"][0]["id"]))
        self.comfy.done.add("p2")
        self.comfy.view_failures = 10
        self.run_worker(self.p, lambda jobs: jobs[1]["status"] == "Failed", seconds=30)
        failed = self.p.state()["jobs"][1]
        self.assertIn("could not be downloaded", failed["error"])
        self.assertTrue(next(r for r in self.p.queue_view()["attention"] if r["job_id"] == failed["id"])["can_retry"])
        self.comfy.view_failures = 0
        self.p.action("retry", dict(id=failed["id"]))
        deadline = time.monotonic() + 8
        while self.p.state()["jobs"][1]["status"] != "Ready":
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.05)
        self.assertEqual(len(self.comfy.prompts), 2)                 # the retry downloaded, it did not re-render


if __name__ == "__main__":
    unittest.main()
