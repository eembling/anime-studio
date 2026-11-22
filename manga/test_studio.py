"""Exercise the production path using a local ComfyUI stand-in."""
import base64
import io
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from studio import Project, handler, Image
from compose import frames


class Comfy(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/upload/image":
            result = {"name":"reference.png"}
        else:
            self.server.graphs.append(json.loads(body)["prompt"])
            result = {"prompt_id":"illustration"}
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        if self.path.startswith("/view?"):
            self.wfile.write(self.server.image)
        else:
            self.wfile.write(b'{"illustration":{"outputs":{"60":{"images":[{"filename":"panel.png","type":"output"}]}}}}')

    def log_message(self, *args):
        pass


class StudioFlow(unittest.TestCase):
    def test_chapter(self):
        with ExitStack() as clean:
            root = Path(clean.enter_context(tempfile.TemporaryDirectory()))
            p = Project(root)
            def serve(cls):
                srv = ThreadingHTTPServer(("127.0.0.1",0),cls)
                srv.graphs = []
                threading.Thread(target=srv.serve_forever,daemon=True).start()
                clean.callback(srv.server_close)
                clean.callback(srv.shutdown)
                return srv, f"http://127.0.0.1:{srv.server_port}"
            comfy, remote = serve(Comfy)
            studio, base = serve(handler(p))
            image = io.BytesIO()
            Image.new("RGB",(100,120),"gray").save(image,"PNG")
            comfy.image = image.getvalue()
            def post(op, **body):
                req = urllib.request.Request(base+"/api/"+op,data=json.dumps(body).encode(),headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(req) as resp:
                    return json.load(resp)
            def get(path):
                with urllib.request.urlopen(base+path) as resp:
                    return resp.read()
            draft = dict(title="Platform",limit=2,script="EXT. STATION\nMika waits.\n**Mika:** Last train?\nCAPTION: Midnight.\nPAGE\nA headlight arrives.")
            s = post("plan", **draft)
            c = s["chapters"][0]
            self.assertEqual([len(page["panels"]) for page in c["pages"]],[2,1])
            self.assertEqual(frames(2,"grid","rtl")[0][0], frames(2,"grid","ltr")[1][0])
            a = s["assets"][0]
            post("settings", server=remote, direction="rtl",style="Jet black ink.")
            s = post("reference",id=a["id"],label="Front",collection="Uniform",image=base64.b64encode(comfy.image).decode())
            self.assertTrue(s["assets"][0]["selected"])
            ctx = dict(chapter=c["id"],page=c["pages"][0]["id"])
            post("sheet",id=a["id"])
            post("render",**ctx)
            post("render",chapter=c["id"],page=c["pages"][1]["id"])
            self.assertEqual(Image.open(io.BytesIO(get("/proof/"+ctx["chapter"]+"/"+ctx["page"]))).size,(1200,1800))
            with self.assertRaises(urllib.error.HTTPError):
                get("/cbz/"+c["id"])
            stop = threading.Event()
            worker = threading.Thread(target=p.worker,args=(stop,),daemon=True)
            worker.start()
            try:
                deadline = time.monotonic()+12
                while any(j["status"] in ("Queued","Rendering") for j in p.state()["jobs"]):
                    self.assertLess(time.monotonic(),deadline)
                    time.sleep(.05)
            finally:
                stop.set()
                worker.join(3)
            s = p.state()
            self.assertEqual([j["status"] for j in s["jobs"]],["Ready"]*4)
            self.assertEqual(len(s["assets"][0]["refs"]),2)
            self.assertEqual(comfy.graphs[1]["10"]["inputs"]["image"],"reference.png")
            self.assertNotIn("11", comfy.graphs[1])
            self.assertIn("Last train?",comfy.graphs[1]["20"]["inputs"]["prompt"])
            for page in s["chapters"][0]["pages"]:
                for panel in page["panels"]:
                    post("take",chapter=c["id"],page=page["id"],panel=panel["id"],take=panel["takes"][0])
            with zipfile.ZipFile(io.BytesIO(get("/cbz/"+c["id"]))) as book:
                self.assertEqual(book.namelist(),["0001.png","0002.png"])
                self.assertEqual(book.read("0001.png"),get("/png/"+ctx["chapter"]+"/"+ctx["page"]))
            post("split",**ctx,panel=c["pages"][0]["panels"][1]["id"])
            post("plan",**draft)
            self.assertEqual(len(Project(root).state()["chapters"][0]["pages"]),3)


if __name__ == "__main__":
    unittest.main()
