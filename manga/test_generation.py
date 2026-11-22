import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from studio import Project, WORKFLOW
from PIL import Image
from generation import group_references
from anime_studio import build_workflow

SCRIPT = """EXT. PLATFORM — NIGHT
Lighting: Amber lanterns.
Panel 1
Keyframe: Mei leans on the railing.
Camera: Low angle
Cast: Mei
Direction: Leave room for lettering.
Mei: Nearly there.
PAGE
Panel 2
Keyframe: Oda arrives.
Cast: Oda"""


class GenerationTests(unittest.TestCase):
    def test_review_and_render_share_direction(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Project(Path(tmp))
            body = dict(title="Crossing",script=SCRIPT,limit=4)
            review = p.action("plan-preview",body)
            self.assertEqual(len(review),2)
            self.assertEqual(p.state()["assets"],[])
            s = p.action("plan",dict(body,indices=[0]))
            self.assertEqual([a["name"] for a in s["assets"]],["Mei"])
            with p.edit() as state:
                state["assets"][0].update(selected="mei.png",lora="mei.safetensors",strength=.8)
            c = s["chapters"][0]
            ctx = dict(chapter=c["id"],page=c["pages"][0]["id"])
            item, = p.action("preview-prompts",ctx)
            job, = p.action("render",ctx)["jobs"]
            self.assertEqual(job["prompt"],item["prompt"])
            self.assertEqual(job["negative"],item["negative"])
            for part in ("Amber lanterns","Picture 1 is Mei","Low angle","Leave room","leans on the railing"):
                self.assertIn(part,job["prompt"])
            self.assertEqual(job["refs"],["mei.png"])
            self.assertEqual(job["loras"],[["mei.safetensors",.8]])
            self.assertIn("graph", job["workflow"])

    def test_overflow_reaches_render_graph_and_matches_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Project(Path(tmp))
            script = "Panel 1\nKeyframe: Station.\nPAGE\nPanel 2\nCast: A, B, C, D\nKeyframe: Everyone arrives."
            s = p.action("plan",dict(title="Cast",script=script,limit=4))
            c = s["chapters"][0]
            colors = ["red","green","blue","yellow","purple","orange"]
            files = []
            for i,color in enumerate(colors):
                path = p.media / f"ref{i}.png"
                Image.new("RGB",(40,40),color).save(path)
                files.append(path.name)
            with p.edit() as state:
                for a,image in zip(state["assets"],files):
                    a["selected"] = image
                a = state["assets"][0]
                a["refs"].append(dict(id=files[4],label="Costume detail"))
                pages = state["chapters"][0]["pages"]
                pages[0]["panels"][0]["selected"] = files[5]
                pages[1]["panels"][0].update(previous=True,extra_refs=[[a["id"],files[4]]])
            ctx = dict(chapter=c["id"],page=c["pages"][1]["id"])
            preview, = p.action("preview-prompts",ctx)
            job, = p.action("render",ctx)["jobs"]
            groups = [files[:3],files[3:5],files[5:]]
            self.assertEqual(preview["ref_groups"],groups)
            self.assertEqual(job["prompt"],preview["prompt"])
            self.assertIn("Picture 2, row 1, column 2: A — Costume detail",job["prompt"])
            self.assertIn("Picture 3 is the previous panel",job["prompt"])
            uploads = []
            def upload(base,path,name):
                with Image.open(path) as im:
                    uploads.append(im.copy())
                return name
            class Submitted(Exception): pass
            def submit(method,url,body):
                graph=body["prompt"]
                self.assertEqual(graph["20"]["inputs"]["prompt"],preview["prompt"])
                for i,node in enumerate(["10","11","12"],1):
                    self.assertEqual(graph["20"]["inputs"][f"image{i}"],[node,0])
                self.assertEqual(graph["12"]["inputs"]["image"],files[5])
                raise Submitted()
            with patch("studio.comfy_upload",upload),patch("studio.http_json",submit),self.assertRaises(Submitted):
                p.render(dict(job,server="http://mock"))
            for sheet,group in zip(uploads,groups):
                cols=2 if len(group)>1 else 1
                cell=sheet.width//cols
                for i,name in enumerate(group):
                    with Image.open(p.media/name) as ref:
                        self.assertEqual(sheet.getpixel(((i%cols)*cell+cell//2,(i//cols)*cell+cell//2)),ref.getpixel((0,0)))

    def test_imported_four_input_graph(self):
        graph=json.loads(WORKFLOW.read_text())
        graph["13"]=dict(class_type="LoadImage",inputs={"image":"placeholder.png"},_meta={"title":"REF4"})
        graph["20"]["inputs"]["image4"]=["13",0]
        names=["a","b","c","d"]
        self.assertEqual(group_references(names,4),[[n] for n in names])
        inputs=dict(seed=11,prefix="test",images={f"ref{i}":n for i,n in enumerate(names,1)})
        result=build_workflow(graph,"keyframe",inputs,all_references=True)
        self.assertEqual(result["13"]["inputs"]["image"],"d")

    def test_queue_reports_sampler_steps_until_art_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Project(Path(tmp))
            chapter = p.action("plan",dict(title="Queue",script=SCRIPT,limit=4))["chapters"][0]
            for page in chapter["pages"]:
                p.action("render",dict(chapter=chapter["id"],page=page["id"]))
            with p.edit() as state:
                job = state["jobs"][0]
                job.update(status="Rendering",started=time.time()-10,server="http://renderer")
            callback = []
            def listen(base,client_id,event):
                callback.append(event)
                return listener
            def comfy(method,url,obj=None):
                if method=="POST":
                    self.assertEqual(obj["client_id"],"manga-studio-"+job["id"])
                    return {"prompt_id":"sample"}
                callback[0]({"type":"progress","data":{"prompt_id":"sample","value":2,"max":4}})
                callback[0]({"type":"progress","data":{"prompt_id":"other","value":3,"max":4}})
                q = p.queue_view()
                self.assertEqual(q["active"][0]["progress"],"Sampler 2/4")
                self.assertEqual(q["queued"][0]["queue_position"],1)
                self.assertFalse(q["completed"])
                return {"sample":{"outputs":{"60":{"images":[{"filename":"take.png"}]}}}}
            from unittest.mock import Mock
            listener=Mock()
            listener.start.return_value=listener
            with patch("studio.ProgressEvents",listen),patch("studio.http_json",comfy),patch("studio.http",return_value=b"art"),patch.object(p,"image",return_value="take.png"):
                p.execute(job)
            listener.close.assert_called_once()
            q=p.queue_view()
            self.assertFalse(q["active"])
            self.assertEqual(q["completed"][0]["estimated_progress"],100)
            self.assertGreaterEqual(q["completed"][0]["elapsed_seconds"],10)

    def test_delete_old_takes_preserves_shared_media_and_checks_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Project(Path(tmp))
            chapter = project.action("plan",dict(title="Takes",script=SCRIPT,limit=4))["chapters"][0]
            page = chapter["pages"][0]
            ctx = dict(chapter=chapter["id"],page=page["id"],panel=page["panels"][0]["id"])
            old,current,shared = [f"{i:032x}.png" for i in (1,2,3)]
            for name in (old,current,shared):
                (project.media/name).write_bytes(b"fixture")
            with project.edit() as state:
                pages = state["chapters"][0]["pages"]
                pages[0]["panels"][0].update(takes=[old,current,shared],selected=current)
                pages[1]["panels"][0].update(takes=[shared],selected=shared)
                state["jobs"].append(dict(status="Rendering",refs=[old]))
            with self.assertRaisesRegex(ValueError,"active render"):
                project.action("delete-takes",dict(ctx,takes=[old,shared],selected=current))
            with project.edit() as state:
                state["jobs"][0]["status"]="Ready"
            with self.assertRaisesRegex(ValueError,"selected artwork changed"):
                project.action("delete-takes",dict(ctx,takes=[current],selected=""))
            state=project.action("delete-takes",dict(ctx,takes=[old,shared],selected=current))
            self.assertEqual(state["chapters"][0]["pages"][0]["panels"][0]["takes"],[current])
            self.assertFalse((project.media/old).exists())
            self.assertTrue((project.media/shared).is_file())
            project.action("delete-takes",dict(ctx,takes=[current],selected=current))
            saved=project.state()["chapters"][0]["pages"][0]["panels"][0]
            self.assertEqual((saved["takes"],saved["selected"]),([],""))
            self.assertFalse((project.media/current).exists())

    def test_three_servers_overlap_and_respect_roles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "generation.json"
            config.write_text(json.dumps(dict(servers=[
                dict(name=str(i),url=f"http://gpu:{8188+i}",roles=roles,enabled=True)
                for i,roles in enumerate(["keyframe","sheet","keyframe"])])))
            p = Project(root,config)
            s = p.action("plan",dict(title="Fleet",script=SCRIPT,limit=4))
            c = s["chapters"][0]
            with p.edit() as state:
                state["assets"][0]["selected"]="mei.png"
            for page in c["pages"]:
                p.action("render",dict(chapter=c["id"],page=page["id"]))
            p.action("sheet",dict(id=s["assets"][0]["id"]))
            barrier = threading.Barrier(3)
            observed = []
            def render(job):
                observed.append((job["server_name"],job["kind"]))
                barrier.wait(timeout=4)
                return "take.png"
            stop = threading.Event()
            with patch.object(p,"render",render),patch.object(p,"check_server",return_value={"ok":True}):
                worker=threading.Thread(target=p.worker,args=(stop,))
                worker.start()
                try:
                    deadline=time.monotonic()+6
                    while not all(j["status"]=="Ready" for j in p.state()["jobs"]):
                        self.assertLess(time.monotonic(),deadline)
                        time.sleep(.05)
                finally:
                    stop.set()
                    worker.join()
            self.assertEqual(set(observed),{("0","panel"),("1","sheet"),("2","panel")})


if __name__=="__main__":
    unittest.main()
