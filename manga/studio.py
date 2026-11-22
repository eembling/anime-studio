"""Manga Studio local application and image render queue."""
import hashlib
import argparse
import base64
import io
import json
import random
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
import zipfile
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "app"))
from anime_studio import build_workflow, comfy_upload, find_remote_submission, http, http_json
from cast_exchange import MAX_ARCHIVE, read_manifest
from comfy_events import ProgressEvents
from render_queue import queue_snapshot, workflow_models
from compose import compose, chapter_pdf, geometry, row_count, DPIS, LAYOUTS, PRESETS, TONES
from generation import DEFAULTS, LIMITS, anime_servers, server as make_server, panel_inputs, prompt_inputs, validate_graph, make_reference_sheet
from planner import asset, paginate, uid
from lettering import from_lettering, plain, validate as validate_balloons, speakers_needing_cast
from PIL import Image, ImageOps

WORKFLOW = HERE.parent / "workflows" / "keyframe__qwen_edit_2509_api.json"


def find(items, key):
    for item in items:
        if item["id"] == key:
            return item
    raise ValueError("This item is no longer available. Refresh the workspace.")


def initial():
    return dict(script="", title="Untitled chapter", chapters=[], active="", assets=[], jobs=[],
                server="http://127.0.0.1:8188", direction="rtl", limit=4,
                style="Black-and-white manga, expressive ink lines, spot blacks and screentone.")


KEEP_FINISHED = 300


def prune(jobs):
    """Keep waiting and running jobs and the most recent finished ones; their artwork stays with the panels."""
    finished = [j for j in jobs if j["status"] not in ("Queued", "Rendering")]
    drop = {j["id"] for j in finished[:-KEEP_FINISHED]} if len(finished) > KEEP_FINISHED else set()
    return [j for j in jobs if j["id"] not in drop]


def panels(chapter):
    return [panel for page in chapter["pages"] for panel in page["panels"]]


class Project:
    def __init__(self, root, anime_config=None):
        self.anime_config = anime_config
        self.health = {}
        self.live_progress = {}
        self.root = root
        self.media = root / "art"
        self.media.mkdir(parents=True, exist_ok=True)
        self.database = root / "manga.sqlite3"
        self.resumed = False
        with closing(sqlite3.connect(self.database)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS workspace (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO workspace (id, data) VALUES (1,?)", (json.dumps(initial()),))
            if "version" not in {r[1] for r in db.execute("PRAGMA table_info(workspace)")}:
                db.execute("ALTER TABLE workspace ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, seq INTEGER NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL)")
            db.execute("CREATE INDEX IF NOT EXISTS jobs_status ON jobs(status)")
            db.commit()
        with self.edit() as state:
            for k,v in DEFAULTS.items():
                state.setdefault(k,v)
            if "servers" not in state:
                url = state["server"]
                try:
                    imported = anime_servers(anime_config) if url == initial()["server"] else []
                except (ValueError, OSError, KeyError, TypeError):
                    imported = []
                state["servers"] = imported or [make_server("ComfyUI",url,["panel","sheet"])]
            state.setdefault("workflow",None)
            # Panels planned before balloons existed keep their words: convert their lettering once.
            for chapter in state["chapters"]:
                for item in panels(chapter):
                    if "balloons" not in item:
                        item["balloons"] = from_lettering(item.get("lettering",""), state["assets"])
                        if item.get("treatment") == "caption":
                            item["treatment"] = "auto"
                    for b in item["balloons"]:
                        if b["speaker"] and not b["off_panel"] and b["speaker"] not in item["cast"]:
                            item["cast"].append(b["speaker"])
            for job in state["jobs"]:
                if job["status"] == "Rendering":
                    # Keep it running: the worker reconnects to ComfyUI and collects the finished artwork.
                    job.update(resume=True, progress="Reconnecting after restart")

    @contextmanager
    def edit(self):
        """One transaction over the workspace and its jobs. Jobs live in their own table so polling stays cheap."""
        db = sqlite3.connect(self.database, timeout=20)
        try:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT data FROM workspace WHERE id=1").fetchone()[0])
            if state.get("jobs"):      # projects saved before jobs had their own table
                for n, job in enumerate(state["jobs"]):
                    db.execute("INSERT OR REPLACE INTO jobs VALUES (?,?,?,?)", (job["id"], n, job["status"], json.dumps(job)))
            rows = db.execute("SELECT id, data FROM jobs ORDER BY seq").fetchall()
            before = dict(rows)
            state["jobs"] = [json.loads(data) for _, data in rows]
            yield state
            jobs = state.pop("jobs")
            for job in jobs:
                job.setdefault("id", uid())
            jobs = prune(jobs)
            db.execute("UPDATE workspace SET data=?, version=version+1 WHERE id=1", (json.dumps(state),))
            keep = set()
            next_seq = (db.execute("SELECT MAX(seq) FROM jobs").fetchone()[0] or 0) + 1
            for job in jobs:
                keep.add(job["id"])
                data = json.dumps(job)
                if before.get(job["id"]) == data:
                    continue
                if job["id"] in before:
                    db.execute("UPDATE jobs SET status=?, data=? WHERE id=?", (job["status"], data, job["id"]))
                else:
                    db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job["id"], next_seq, job["status"], data))
                    next_seq += 1
            for gone in set(before) - keep:
                db.execute("DELETE FROM jobs WHERE id=?", (gone,))
            db.commit()
            state["jobs"] = jobs
        finally:
            db.close()

    def version(self):
        with closing(sqlite3.connect(self.database)) as db:
            return db.execute("SELECT version FROM workspace WHERE id=1").fetchone()[0]

    def state(self, light=False):
        """The workspace. light=True drops each job's captured workflow graph (the UI never needs it)."""
        with closing(sqlite3.connect(self.database)) as db:
            data, version = db.execute("SELECT data, version FROM workspace WHERE id=1").fetchone()
            jobs = [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs ORDER BY seq")]
        state = json.loads(data)
        if light:
            for job in jobs:
                job.pop("workflow", None)
        state.update(jobs=jobs, version=version)
        return state

    def active_jobs(self):
        with closing(sqlite3.connect(self.database)) as db:
            return [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs WHERE status IN ('Queued','Rendering') ORDER BY seq")]

    def queue_view(self):
        state = self.state()
        statuses = {"Rendering":"running","Queued":"pending","Ready":"done","Failed":"error","Canceled":"cancelled"}
        rows = []
        for position,job in enumerate(state["jobs"],1):
            workflow = job.get("workflow") or {}
            rows.append(dict(id=position, job_id=job["id"], stage=job["kind"], label=job["label"],
                             chapter=job.get("chapter"),panel=job.get("target"),
                             status=statuses[job["status"]], started=job.get("started") if job["status"]=="Rendering" or job.get("finished") else None,
                             finished=job.get("finished"),
                             instance_id=job.get("server"), server_name=job.get("server_name"),
                             error=job.get("error"), prompt=job["prompt"], negative=job.get("negative",""),
                             width=job["width"], height=job["height"], workflow_name=workflow.get("name","Qwen Image Edit 2509"),
                             models_json=json.dumps(workflow_models(workflow.get("graph"))) if job["status"]=="Rendering" else None,
                             progress=("Stopping on the server" if job.get("cancel_requested") else self.live_progress.get(job["id"],job.get("progress",""))) if job["status"]=="Rendering" else "",
                             can_retry=job["status"]=="Failed" and bool(job.get("prompt_id")) and "could not be downloaded" in (job.get("error") or ""),
                             stopping=bool(job.get("cancel_requested"))))
        servers = [dict(id=v["url"],enabled=v["enabled"],roles=",".join(v["roles"])) for v in state["servers"]]
        health = {v["url"]:self.health.get(v["url"],(0,{}))[1] for v in state["servers"]}
        return queue_snapshot(rows,rows,servers,health)

    def image(self, data):
        with Image.open(io.BytesIO(data)) as im:
            if im.width * im.height > 36000000:
                raise ValueError("Choose an image under 36 megapixels.")
            name = uid() + ".png"
            ImageOps.exif_transpose(im).convert("RGB").save(self.media / name)
            return name

    def import_library(self, stream):
        created = []
        counts = dict(added=0, updated=0, unchanged=0)
        try:
            with zipfile.ZipFile(stream) as archive, self.edit() as state:
                manifest = read_manifest(archive)
                warnings = manifest.get("warnings", []).copy()
                assets = {a["name"].strip().casefold(): a for a in state["assets"]}
                media = {r["source_hash"]: r["id"] for a in state["assets"] for r in a["refs"]
                         if r.get("source_hash") and (self.media / r["id"]).is_file()}
                for entry in manifest["entries"]:
                    key = entry["name"].strip().casefold()
                    existing = assets.get(key)
                    if existing and existing["kind"] != entry["kind"]:
                        warnings.append(f"{entry['name']} was skipped. That name belongs to a {existing['kind']}.")
                        continue
                    target = existing or asset(entry["name"].strip(), entry["kind"])
                    changed = False
                    if not target["description"] and entry["description"]:
                        target["description"] = entry["description"]
                        changed = True
                    if not target["lora"] and entry["lora"]:
                        target.update(lora=entry["lora"], strength=entry["strength"])
                        changed = True
                    known = {(r.get("source_hash"),r["collection"],r["label"]) for r in target["refs"] if r.get("source_hash")}
                    for ref in entry["refs"]:
                        digest = ref["image"]
                        if digest not in media:
                            raw = archive.read("images/"+digest)
                            if hashlib.sha256(raw).hexdigest() != digest:
                                raise ValueError("Reference image content does not match the library manifest.")
                            media[digest] = self.image(raw)
                            created.append(self.media / media[digest])
                        membership = (digest,ref["collection"],ref["label"])
                        if membership not in known:
                            target["refs"].append(dict(id=media[digest], collection=ref["collection"], label=ref["label"], source_hash=digest))
                            known.add(membership)
                            changed = True
                    if not target["selected"] and entry["selected"]:
                        target["selected"] = media[entry["selected"]]
                        changed = True
                    if existing is None:
                        state["assets"].append(target)
                        assets[key] = target
                        counts["added"] += 1
                    else:
                        counts["updated" if changed else "unchanged"] += 1
            return dict(state=state, counts=counts, warnings=warnings)
        except Exception:
            for file in created:
                file.unlink(missing_ok=True)
            raise

    def delete_takes(self, body):
        names = body["takes"]
        if not isinstance(names,list) or not names or not all(isinstance(n,str) and re.fullmatch(r"[a-f0-9]{32}\.png",n) for n in names):
            raise ValueError("Choose one or more panel takes to delete.")
        names = set(names)
        with self.edit() as state:
            chapter = find(state["chapters"],body["chapter"])
            page = find(chapter["pages"],body["page"])
            panel = find(page["panels"],body["panel"])
            if not names.issubset(panel["takes"]):
                raise ValueError("The available takes changed. Close this dialog and review the panel again.")
            if panel["selected"] in names:
                if body.get("selected") != panel["selected"]:
                    raise ValueError("The selected artwork changed. Review the panel before deleting.")
                panel["selected"] = ""
            busy = {ref for j in state["jobs"] if j["status"] in ("Queued","Rendering") for ref in j.get("refs",[])}
            if names & busy:
                raise ValueError("A waiting or active render uses one of these takes. Wait for it to finish or remove that take from your selection.")
            panel["takes"] = [n for n in panel["takes"] if n not in names]

        # Commit the edit first so a failed database write cannot destroy linked media.
        with self.edit() as state:
            used = {n for c in state["chapters"] for p in panels(c) for n in [*p["takes"],p["selected"]]}
            used.update(r["id"] for a in state["assets"] for r in a["refs"])
            used.update(a["selected"] for a in state["assets"])
            used.update(ref for j in state["jobs"] if j["status"] in ("Queued","Rendering") for ref in j.get("refs",[]))
            for name in names-used:
                (self.media/name).unlink(missing_ok=True)
        return state

    def action(self, op, b):
        if op == "delete-takes":
            return self.delete_takes(b)
        if op == "plan-preview":
            state = self.state()
            pages = paginate(b["script"],int(b["limit"]),state["assets"])
            for page in pages:
                for panel in page["panels"]:
                    panel["names"] = [find(state["assets"],k)["name"] for k in panel["cast"]]
            return pages
        with self.edit() as s:
            if op in ("draft", "plan"):
                s.update(script=b["script"], title=b["title"].strip() or "Untitled chapter", limit=int(b["limit"]))
                if op == "plan":
                    original = {a["id"] for a in s["assets"]}
                    pages = paginate(s["script"],s["limit"],s["assets"])
                    if "indices" in b:
                        chosen = set(b["indices"])
                        if not chosen or not chosen.issubset(range(len(pages))):
                            raise ValueError("Select at least one reviewed page.")
                        pages = [p for i,p in enumerate(pages) if i in chosen]
                        used = {k for page in pages for panel in page["panels"] for k in panel["cast"]}
                        s["assets"] = [a for a in s["assets"] if a["id"] in original|used]
                    chapter = dict(id=uid(), title=s["title"], script=s["script"], limit=s["limit"], direction=s["direction"],
                                   pages=pages)
                    s["chapters"].append(chapter)
                    s["active"] = chapter["id"]
            elif op == "settings":
                if "server" in b:  # Legacy single-server settings client
                    entry = make_server("ComfyUI",b["server"],["sheet","panel"])
                    s.update(server=entry["url"], servers=[entry])
                if b.get("direction",s["direction"]) not in ("rtl","ltr"):
                    raise ValueError("Choose a reading direction.")
                for key in ("negative","style","direction"):
                    if key in b:
                        s[key] = b[key]
                if "page_preset" in b:
                    if b["page_preset"] not in PRESETS:
                        raise ValueError("Choose a page size.")
                    s["page_preset"] = b["page_preset"]
                if "dpi" in b:
                    if int(b["dpi"]) not in DPIS:
                        raise ValueError("Choose 300 or 600 dpi.")
                    s["dpi"] = int(b["dpi"])
                if "tone" in b:
                    if b["tone"] not in TONES:
                        raise ValueError("Choose a tone treatment.")
                    s["tone"] = b["tone"]
                for key in ("bleed","lettering_caps","vertical_cjk"):
                    if key in b:
                        s[key] = b[key] in (True,"on","true","1",1)
                for key,(lo,hi) in LIMITS.items():
                    if key in b:
                        value = int(b[key])
                        if not lo <= value <= hi:
                            raise ValueError(f"{key} must be between {lo} and {hi}.")
                        s[key] = value
            elif op == "server-save":
                entry = make_server(b["name"],b["url"],b["roles"],b["enabled"])
                if any(v["url"]==entry["url"] and v["id"]!=b.get("id") for v in s["servers"]):
                    raise ValueError("That server address is already configured.")
                if b.get("id"):
                    find(s["servers"],b["id"]).update(entry,id=b["id"])
                else:
                    s["servers"].append(entry)
            elif op == "server-remove":
                s["servers"].remove(find(s["servers"],b["id"]))
            elif op == "anime-servers":
                entries = anime_servers(self.anime_config)
                if not entries:
                    raise ValueError("No saved Anime Studio image servers were found on this Mac.")
                urls = {v["url"] for v in s["servers"]}
                s["servers"].extend(v for v in entries if v["url"] not in urls)
            elif op in ("anime-workflow","anime-generation"):
                if not self.anime_config or not self.anime_config.is_file():
                    raise ValueError("Saved Anime Studio generation settings were not found.")
                config = json.loads(self.anime_config.read_text())
                if op == "anime-workflow":
                    item = config["workflows"]["keyframe"]
                    graph = json.loads(item["json"])
                    validate_graph(graph)
                    s["workflow"] = dict(name=item["name"],graph=graph)
                else:
                    settings = config["settings"]
                    values = dict(panel_size=max(int(settings["kf_width"]),int(settings["kf_height"])),
                                  sheet_width=int(settings["sheet_width"]),sheet_height=int(settings["sheet_height"]),
                                  timeout=int(settings["job_timeout"]))
                    for k,value in values.items():
                        low,high = LIMITS[k]
                        if not low<=value<=high:
                            raise ValueError(f"The saved Anime Studio {k} is outside {low}–{high}.")
                    s.update(values)
            elif op == "workflow":
                if b.get("graph") is not None:
                    validate_graph(b["graph"])
                    s["workflow"] = dict(name=b["name"],graph=b["graph"])
                else:
                    s["workflow"] = None
            elif op == "asset":
                a = find(s["assets"], b["id"]) if b["id"] else asset("")
                name = b["name"].strip()
                if not name or any(x["id"] != a["id"] and x["name"].casefold() == name.casefold() for x in s["assets"]):
                    raise ValueError("Enter a unique cast name.")
                if b["kind"] not in ("character", "location", "prop"):
                    raise ValueError("Unknown cast type.")
                a.update(name=name, kind=b["kind"], description=b["description"], lora=b["lora"], strength=float(b["strength"]))
                if not b["id"]:
                    s["assets"].append(a)
                if b.get("image"):
                    self.add_reference(a, b)
            elif op in ("reference", "use-reference", "sheet"):
                a = find(s["assets"], b["id"])
                if op == "reference":
                    self.add_reference(a, b)
                elif op == "use-reference":
                    a["selected"] = find(a["refs"], b["ref"])["id"]
                else:
                    if not a["selected"]:
                        raise ValueError("Add a reference image first.")
                    inputs = prompt_inputs(s,[a],["Turnaround sheet, front, side and back views of the same character."])
                    inputs.update(width=s["sheet_width"],height=s["sheet_height"])
                    self.enqueue(s,a,inputs,"sheet","",a["name"])
            elif op == "restore-script":
                chapter = find(s["chapters"], b["id"])
                s.update(script=chapter["script"], title=chapter["title"], limit=chapter.get("limit", 4))
            elif op == "chapter":
                s["active"] = find(s["chapters"], b["id"])["id"]
            elif op == "cancel":
                j = find(s["jobs"], b["id"])
                if j["status"] == "Queued":
                    j.update(status="Canceled", finished=time.time())
                elif j["status"] == "Rendering":
                    j["cancel_requested"] = True       # the render thread stops it on the server
                else:
                    raise ValueError("This render has already finished.")
            elif op == "retry":
                j = find(s["jobs"], b["id"])
                if j["status"] != "Failed" or not j.get("prompt_id") or not j.get("server"):
                    raise ValueError("Only a finished render that failed to download can be retried. Queue a new take instead.")
                j.update(status="Rendering", resume=True, error="", cancel_requested=False, started=time.time())
                retry = dict(j)
                threading.Thread(target=self.execute, args=(retry,), daemon=True).start()
            else:
                chapter = find(s["chapters"], b["chapter"])
                page = find(chapter["pages"], b["page"])
                if op == "page-direction":
                    for key in ("direction_notes","lighting","continuity"):
                        page[key] = b[key]
                elif op == "preview-prompts":
                    return [dict(panel=i+1,**panel_inputs(s,chapter,page,i)) for i in range(len(page["panels"]))]
                elif op == "layout":
                    if b["layout"] not in LAYOUTS or b["direction"] not in ("rtl","ltr"):
                        raise ValueError("Choose a valid layout and reading direction.")
                    if page["layout"] != b["layout"]:
                        page.pop("weights", None)
                    page["layout"] = b["layout"]
                    chapter["direction"] = b["direction"]
                elif op == "row-weights":
                    weights = b["weights"]
                    rows = row_count(len(page["panels"]), page["layout"])
                    if weights is not None and (not isinstance(weights,list) or len(weights)!=rows or
                                                not all(isinstance(v,(int,float)) and .2<=v<=5 for v in weights)):
                        raise ValueError("Row heights must be between 0.2 and 5 for every row.")
                    if weights is None:
                        page.pop("weights", None)
                    else:
                        page["weights"] = [round(float(v),3) for v in weights]
                elif op == "geometry":
                    return geometry(page, chapter["direction"], s)
                elif op == "balloon-move":
                    p = find(page["panels"], b["panel"])
                    item = find(p["balloons"], b["balloon"])
                    for key in ("x","y","tail_x","tail_y"):
                        if key in b:
                            value = b[key]
                            if value is not None and not -.5 <= float(value) <= 1.5:
                                raise ValueError("Keep balloons on the panel.")
                            item[key] = None if value is None else round(float(value),4)
                elif op == "split":
                    index = page["panels"].index(find(page["panels"], b["panel"]))
                    if not index:
                        raise ValueError("Select the second panel or later to split this page.")
                    new_page = dict(page, id=uid(), panels=page["panels"][index:])
                    del page["panels"][index:]
                    chapter["pages"].insert(chapter["pages"].index(page)+1, new_page)
                elif op in ("panel", "take"):
                    p = find(page["panels"], b["panel"])
                    if op == "take":
                        if b["take"] not in p["takes"]:
                            raise ValueError("Unknown art take.")
                        p["selected"] = b["take"]
                    else:
                        if "extra_refs" in b:
                            extra = []
                            for aid,image in b["extra_refs"]:
                                find(find(s["assets"],aid)["refs"],image)
                                if [aid,image] not in extra:
                                    extra.append([aid,image])
                            p["extra_refs"] = extra
                        for key in b["cast"]:
                            find(s["assets"], key)
                        treatment = b.get("treatment", "auto")
                        if treatment == "caption":
                            treatment = "auto"
                        if treatment not in ("auto", "upper-left", "upper-right"):
                            raise ValueError("Choose where the first balloon goes.")
                        if "camera" in b and b["camera"] != p.get("camera"):
                            p["camera_suggested"] = False
                        for key in ("camera","director","previous"):
                            if key in b:
                                p[key] = b[key]
                        cast = list(dict.fromkeys(b["cast"]))
                        if "balloons" in b:
                            balloons = validate_balloons(b["balloons"], s["assets"], p.get("balloons", []))
                        else:   # older clients send plain lettering text
                            balloons = from_lettering(b.get("lettering",""), s["assets"])
                        cast += [k for k in speakers_needing_cast(balloons, cast) if k not in cast]
                        p.update(treatment=treatment, prompt=b["prompt"], balloons=balloons,
                                 lettering=plain(balloons, s["assets"]), cast=cast)
                elif op == "render":
                    targets = [find(page["panels"], b["panel"])] if b.get("panel") else [p for p in page["panels"] if not p["takes"]]
                    count = 0
                    for i,p in enumerate(page["panels"]):
                        if p not in targets or any(j["target"] == p["id"] and j["status"] in ("Queued","Rendering") for j in s["jobs"]):
                            continue
                        self.enqueue(s,p,panel_inputs(s,chapter,page,i),"panel",chapter["id"],
                                     f'{chapter["title"]} · Page {chapter["pages"].index(page)+1}, panel {i+1}')
                        count += 1
                    if not count:
                        raise ValueError("No panels available to queue. Use New take for another version.")
                else:
                    raise ValueError("Unknown action.")
            return s

    def add_reference(self, a, b):
        name = self.image(base64.b64decode(b["image"], validate=True))
        a["refs"].append(dict(id=name, label=b.get("label") or "Reference", collection=b.get("collection") or "Model references"))
        if not a["selected"]:
            a["selected"] = name

    def enqueue(self, s, target, inputs, kind, chapter, label):
        if not any(v["enabled"] and kind in v["roles"] for v in s["servers"]):
            raise ValueError(f"Enable a ComfyUI server for {kind} artwork in Settings.")
        selected = s["workflow"]
        graph = selected["graph"] if selected else json.loads(WORKFLOW.read_text())
        s["jobs"].append(dict(id=uid(),status="Queued",target=target["id"],chapter=chapter,label=label,
                              kind=kind,workflow=selected or dict(name="Qwen Image Edit 2509",graph=graph),timeout=s["timeout"],seed=random.randrange(2**52),error="",**inputs))

    def check_server(self, entry):
        try:
            stats = http_json("GET",entry["url"]+"/system_stats",timeout=3)
            devices = ", ".join(d.get("name","GPU") for d in stats.get("devices",[]))
            status = dict(ok=True,info=devices or "Connected")
        except Exception as exc:
            status = dict(ok=False,info=str(exc)[:180])
        self.health[entry["url"]] = (time.monotonic(),status)
        return status

    def worker(self, stop=None):
        stop = stop or threading.Event()
        last_health = 0
        while not stop.is_set():
            if not self.resumed:
                self.resumed = True
                for job in self.active_jobs():
                    if job["status"] == "Rendering":
                        threading.Thread(target=self.execute, args=(job,), daemon=True).start()
            active = self.active_jobs()
            waiting = [j for j in active if j["status"]=="Queued"]
            if not waiting:
                if time.monotonic()-last_health > 60:   # keep Settings' connection status fresh while idle
                    last_health = time.monotonic()
                    for entry in self.state()["servers"]:
                        if entry["enabled"]:
                            self.check_server(entry)
                stop.wait(.5)
                continue
            busy = {j.get("server") for j in active if j["status"]=="Rendering"}
            for entry in self.state()["servers"]:
                url = entry["url"]
                if not entry["enabled"] or url in busy or not any(j["kind"] in entry["roles"] for j in waiting):
                    continue
                last,status = self.health.get(url,(-100,{}))
                if time.monotonic()-last>15:
                    status = self.check_server(entry)
                if not status.get("ok"):
                    continue
                with self.edit() as s:
                    current = next((v for v in s["servers"] if v==entry),None)
                    job = next((j for j in s["jobs"] if j["status"]=="Queued" and j["kind"] in entry["roles"]),None)
                    if not current or not job or any(j["status"]=="Rendering" and j.get("server")==url for j in s["jobs"]):
                        continue
                    job.update(status="Rendering", started=time.time(), server=url, server_name=entry["name"],
                               client_id="manga-studio-"+job["id"])
                waiting = [j for j in waiting if j["id"]!=job["id"]]
                busy.add(url)
                threading.Thread(target=self.execute,args=(job,),daemon=True).start()
            stop.wait(.35)

    def update_job(self, job_id, **fields):
        with self.edit() as s:
            job = find(s["jobs"], job_id)
            job.update(fields)
            return dict(job)

    def execute(self,job):
        try:
            name = self.render(job)
            with self.edit() as s:
                current = find(s["jobs"], job["id"])
                if current.get("cancel_requested"):
                    current.update(status="Canceled", finished=time.time(), error="", resume=False)
                    return
                if job["kind"] == "sheet":
                    find(s["assets"], job["target"])["refs"].append(dict(id=name, label="Turnaround sheet", collection="Turnarounds"))
                else:
                    p = find(panels(find(s["chapters"], job["chapter"])), job["target"])
                    p["takes"].append(name)
                current.update(status="Ready",finished=time.time(),resume=False,error="")
        except Canceled:
            self.update_job(job["id"], status="Canceled", finished=time.time(), error="", resume=False)
        except Exception as exc:
            with self.edit() as s:
                current = next((j for j in s["jobs"] if j["id"]==job["id"]), None)
                if current:
                    current.update(status="Failed", finished=time.time(), error=str(exc), resume=False)
        finally:
            self.live_progress.pop(job["id"],None)

    def cancel_requested(self, job_id):
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return not row or bool(json.loads(row[0]).get("cancel_requested"))

    def submit(self, job, base, client_id):
        """Upload references and queue the graph; returns ComfyUI's prompt id."""
        groups = job.get("ref_groups", [[name] for name in job["refs"]])
        self.live_progress[job["id"]] = "Preparing reference images"
        images = {}
        with TemporaryDirectory() as temp:
            for i,group in enumerate(groups,1):
                if len(group)==1:
                    path = self.media / group[0]
                else:
                    path = Path(temp) / f"manga-{job['id']}-ref{i}.png"
                    make_reference_sheet(self.media,group,path)
                images[f"ref{i}"] = comfy_upload(base,path,path.name)
        graph = (job.get("workflow") or {}).get("graph") or json.loads(WORKFLOW.read_text())
        wf = build_workflow(graph, "keyframe", dict(job, images=images,
                            seed=job.get("seed",random.randrange(2**52)), prefix="manga/" + job["id"]), all_references=True)
        prompt_id = http_json("POST", base+"/prompt", {"prompt":wf,"client_id":client_id})["prompt_id"]
        self.update_job(job["id"], prompt_id=prompt_id, submitted=time.time())
        return prompt_id, wf

    def render(self, job):
        base = job["server"]
        client_id = job.get("client_id") or "manga-studio-"+job["id"]
        prompt_id, wf = job.get("prompt_id"), None
        if job.get("resume") and not prompt_id:
            self.live_progress[job["id"]] = "Reconnecting to ComfyUI"
            prompt_id = find_remote_submission(base, client_id)
            if prompt_id:
                self.update_job(job["id"], prompt_id=prompt_id)
        graph = (job.get("workflow") or {}).get("graph") or json.loads(WORKFLOW.read_text())
        def on_event(event):
            data = event.get("data",{})
            if data.get("prompt_id") not in (None,prompt_id):
                return
            if event["type"]=="progress" and data.get("max"):
                message = f"Sampler {data.get('value',0)}/{data['max']}"
            elif event["type"]=="executing":
                node = (wf or graph).get(str(data.get("node")),{})
                message = node.get("_meta",{}).get("title") or node.get("class_type") or "Collecting artwork"
            else:
                return
            self.live_progress[job["id"]] = message
        self.live_progress[job["id"]] = "Waiting for ComfyUI"
        events = ProgressEvents(base,client_id,on_event).start()
        try:
            if not prompt_id:
                if self.cancel_requested(job["id"]):
                    raise Canceled()
                prompt_id, wf = self.submit(job, base, client_id)
            started = job.get("submitted") or time.time()
            deadline = started + job.get("timeout",3600)
            save_nodes = None
            while time.time() < deadline:
                if self.cancel_requested(job["id"]):
                    interrupt(base, prompt_id)
                    raise Canceled()
                entry = http_json("GET", base+"/history/"+prompt_id).get(prompt_id, {})
                if entry.get("status", {}).get("status_str") == "error":
                    raise ValueError("ComfyUI render failed: " + str(entry["status"].get("messages", []))[-1000:])
                if save_nodes is None:
                    source = wf or (entry.get("prompt") or [None, None, None])[2] or graph
                    save_nodes = [k for k,n in source.items() if n.get("class_type")=="SaveImage"]
                images = [im for k in save_nodes for im in entry.get("outputs",{}).get(k,{}).get("images",[])]
                if images:
                    self.live_progress[job["id"]] = "Saving artwork"
                    return self.download(base, images[0])
                time.sleep(1)
            interrupt(base, prompt_id)
            raise ValueError("Timed out waiting for ComfyUI. The render was stopped on the server.")
        finally:
            events.close()

    def download(self, base, image, attempts=4):
        """A finished render is worth several tries: a dropped connection should not cost a new take."""
        query = urllib.parse.urlencode(image)
        for attempt in range(attempts):
            try:
                return self.image(http("GET", base+"/view?"+query, timeout=120))
            except Exception as exc:
                if attempt == attempts-1:
                    raise ValueError("The artwork finished but could not be downloaded: "+str(exc)[:200]+". Use Retry download.") from exc
                time.sleep(2*(attempt+1))


class Canceled(Exception):
    pass


def interrupt(base, prompt_id):
    """Remove a waiting prompt from ComfyUI's queue, or stop it if it is the one running."""
    if not prompt_id:
        return
    try:
        http_json("POST", base + "/queue", {"delete": [prompt_id]}, 10)
        queue = http_json("GET", base + "/queue", timeout=10)
        if any(item[1] == prompt_id for item in queue.get("queue_running", [])):
            http_json("POST", base + "/interrupt", {"prompt_id": prompt_id}, 10)
    except Exception:
        pass

def comic_info(chapter, state):
    """ComicInfo.xml so comic readers know the title, page count and reading direction."""
    from xml.sax.saxutils import escape
    manga = "YesAndRightToLeft" if chapter["direction"] == "rtl" else "No"
    pages = "".join(f'<Page Image="{i}"/>' for i in range(len(chapter["pages"])))
    return ('<?xml version="1.0" encoding="utf-8"?>\n<ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<Title>{escape(chapter["title"])}</Title><PageCount>{len(chapter["pages"])}</PageCount>'
            f'<Manga>{manga}</Manga><BlackAndWhite>{"Yes" if state.get("tone") in ("gray","screentone") else "Unknown"}</BlackAndWhite>'
            f'<Pages>{pages}</Pages></ComicInfo>')


def handler(project):
    class Handler(BaseHTTPRequestHandler):
        def send(self, body, mime, filename=None, etag=None):
            self.send_response(200)
            self.send_header("Content-Type", mime)
            if etag:
                self.send_header("ETag", '"'+etag+'"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", 'attachment; filename="'+filename+'"')
            self.end_headers()
            self.wfile.write(body)

        def local(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}")

        def do_GET(self):
            if not self.local():
                return self.send_error(403)
            path = urllib.parse.urlsplit(self.path).path
            try:
                if path in ("/", "/studio.css", "/studio.js"):
                    name = "index.html" if path == "/" else path[1:]
                    self.send((HERE/name).read_bytes(), {"/":"text/html; charset=utf-8","/studio.js":"text/javascript","/studio.css":"text/css"}[path])
                elif path == "/settings.css":
                    self.send((HERE.parent/"app"/"settings.css").read_bytes(),"text/css")
                elif path == "/workflow.json":
                    custom = project.state()["workflow"]
                    self.send(json.dumps(custom["graph"]).encode() if custom else WORKFLOW.read_bytes(),"application/json","manga-image-workflow.json")
                elif path == "/queue":
                    self.send(json.dumps(project.queue_view()).encode(),"application/json")
                elif path == "/state":
                    version = str(project.version())
                    if self.headers.get("If-None-Match") == '"'+version+'"':
                        self.send_response(304)
                        self.send_header("ETag", '"'+version+'"')
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    self.send(json.dumps(project.state(light=True)).encode(), "application/json", etag=version)
                elif re.fullmatch(r"/art/[a-f0-9]{32}\.png", path):
                    self.send((project.media / path.split("/")[-1]).read_bytes(), "image/png")
                elif re.fullmatch(r"/(proof|png|cbz|pdf)/[a-f0-9]{32}(/[a-f0-9]{32})?",path):
                    _, mode, chapter_id, *page_id = path.split("/")
                    state = project.state()
                    chapter = find(state["chapters"], chapter_id)
                    title = re.sub(r"[^\w .-]", "", chapter["title"]).strip()[:70]
                    filename = title if title.isascii() and title else "chapter"
                    if mode == "cbz":
                        book = io.BytesIO()
                        with zipfile.ZipFile(book,"w") as archive:
                            archive.writestr("ComicInfo.xml", comic_info(chapter, state))
                            for i,page in enumerate(chapter["pages"], 1):
                                archive.writestr(f"{i:04}.png", compose(page,chapter["direction"],project.media,settings=state))
                        self.send(book.getvalue(),"application/vnd.comicbook+zip", filename+".cbz")
                    elif mode == "pdf":
                        self.send(chapter_pdf(chapter["pages"],chapter["direction"],project.media,state),"application/pdf",filename+".pdf")
                    else:
                        page = find(chapter["pages"], page_id[0])
                        self.send(compose(page, chapter["direction"], project.media, mode=="proof", state), "image/png",
                                  f'page-{chapter["pages"].index(page)+1:04}.png' if mode=="png" else None)
                else:
                    self.send_error(404)
            except (ValueError, OSError, IndexError) as exc:
                self.send_error(400, "Invalid request", str(exc))

        def do_POST(self):
            if not self.local() or self.headers.get("Origin") not in (None, "http://"+self.headers["Host"]):
                return self.send_error(403)
            try:
                size = int(self.headers.get("Content-Length", 0))
                if self.path == "/api/cast/import":
                    if self.headers.get_content_type() != "application/zip":
                        return self.send_error(415)
                    if not 0 < size <= MAX_ARCHIVE:
                        raise ValueError("Choose a cast library up to 256 MB.")
                    result = project.import_library(io.BytesIO(self.rfile.read(size)))
                    return self.send(json.dumps(result).encode(), "application/json")
                if self.headers.get_content_type() != "application/json":
                    return self.send_error(415)
                if not 0 < size <= 32*1024*1024:
                    raise ValueError("Choose a smaller image or script.")
                data = json.loads(self.rfile.read(size))
                if self.path == "/api/check-servers":
                    result = {v["id"]:project.check_server(v) for v in project.state()["servers"] if v["enabled"]}
                    return self.send(json.dumps(result).encode(),"application/json")
                state = project.action(self.path.removeprefix("/api/"), data)
                if isinstance(state, dict) and "jobs" in state and "chapters" in state:
                    state = dict(state, jobs=[{k: v for k, v in j.items() if k != "workflow"} for j in state["jobs"]],
                                 version=project.version())
                self.send(json.dumps(state).encode(), "application/json")
            except (ValueError, KeyError, TypeError, OSError, zipfile.BadZipFile, Image.DecompressionBombError) as exc:
                body = json.dumps({"error": str(exc)}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    return Handler


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8711)
    parser.add_argument("--data", type=Path, default=Path.home() / "Library/Application Support/MangaStudio")
    args = parser.parse_args()
    args.data.mkdir(parents=True, exist_ok=True)
    lock = (args.data / "studio.lock").open("a+b")
    try:
        if sys.platform == "win32":
            import msvcrt
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        parser.exit(1, "Another Manga Studio is using this project.\n")
    project = Project(args.data, Path.home()/"Library/Application Support/AnimeStudio/generation-settings.json")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(project))
    print(f"http://127.0.0.1:{server.server_port}/", flush=True)
    threading.Thread(target=project.worker, daemon=True).start()
    server.serve_forever()
