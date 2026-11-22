"""Speech balloons: planning, tagging, migration, placement, prompts and rendering."""
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from PIL import Image

from studio import Project, comic_info
from compose import compose, chapter_pdf, frames, geometry, page_size
from generation import panel_inputs
from lettering import auto_positions, from_lettering, validate

SCRIPT = """EXT. LAST PLATFORM — NIGHT
Lighting: Cold moonlight.
Mika stands beneath the broken clock.
Mika: The last train is late.
Kenji (shouting): Get back from the edge!
Mika (whispering): Don't tell anyone.
CAPTION: Three years later.
SFX: RUMBLE
Kenji (thinking): She knew all along.
"""


class LetteringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.p = Project(Path(self.tmp.name))
        s = self.p.action("plan", dict(title="Platform", script=SCRIPT, limit=6))
        self.chapter = s["chapters"][0]
        self.page = self.chapter["pages"][0]
        self.ids = {a["name"]: a["id"] for a in s["assets"]}
        self.ctx = dict(chapter=self.chapter["id"], page=self.page["id"])

    def panels(self):
        return self.p.state()["chapters"][0]["pages"][0]["panels"]

    def art(self):
        buffer = io.BytesIO()
        Image.new("RGB", (640, 640), "#8899aa").save(buffer, "PNG")
        name = self.p.image(buffer.getvalue())
        with self.p.edit() as s:
            for panel in s["chapters"][0]["pages"][0]["panels"]:
                panel.update(takes=[name], selected=name)
        return self.p.state()

    def test_planner_tags_each_line_with_its_speaker_and_style(self):
        balloons = [b for panel in self.panels() for b in panel["balloons"]]
        self.assertEqual([(b["kind"], b["speaker"]) for b in balloons], [
            ("speech", self.ids["Mika"]), ("shout", self.ids["Kenji"]), ("whisper", self.ids["Mika"]),
            ("caption", ""), ("sfx", ""), ("thought", self.ids["Kenji"])])
        self.assertEqual(balloons[0]["text"], "The last train is late.")     # no "MIKA:" in the balloon
        panels = self.panels()
        self.assertEqual(panels[0]["camera"], "Wide establishing shot")
        self.assertTrue(panels[0]["camera_suggested"])
        self.assertIn("Kenji, reacting in the moment", panels[1]["prompt"])
        self.assertNotIn("A visual beat", " ".join(p["prompt"] for p in panels))
        cameras = [p["camera"] for p in panels]
        self.assertFalse(any(cameras[i] == cameras[i + 1] == cameras[i + 2] for i in range(len(cameras) - 2)))

    def test_editing_balloons_validates_tags_and_keeps_positions(self):
        panel = self.panels()[0]
        base = dict(self.ctx, panel=panel["id"], prompt=panel["prompt"], cast=panel["cast"])
        with self.assertRaisesRegex(ValueError, "choose who says it"):
            self.p.action("panel", dict(base, balloons=[{"kind": "speech", "text": "Hi"}]))
        location = self.p.action("asset", dict(id="", name="Platform", kind="location", description="", lora="", strength=1))["assets"][-1]
        with self.assertRaisesRegex(ValueError, "not a location"):
            self.p.action("panel", dict(base, balloons=[{"kind": "speech", "speaker": location["id"], "text": "Hi"}]))
        first = panel["balloons"][0]
        self.p.action("balloon-move", dict(self.ctx, panel=panel["id"], balloon=first["id"], x=.2, y=.3, tail_x=.4, tail_y=.9))
        self.p.action("panel", dict(base, balloons=[
            {"id": first["id"], "kind": "speech", "speaker": self.ids["Mika"], "text": "The last train is late again."},
            {"kind": "shout", "speaker": self.ids["Kenji"], "text": "Mika!"},
            {"kind": "speech", "speaker": self.ids["Kenji"], "text": "From the stairs", "off_panel": True}]))
        saved = self.panels()[0]
        self.assertEqual((saved["balloons"][0]["x"], saved["balloons"][0]["tail_y"]), (.2, .9))
        self.assertIn(self.ids["Kenji"], saved["cast"])                     # a drawn speaker joins the panel cast
        self.assertEqual(saved["lettering"].splitlines()[1], "Kenji: Mika!")
        with self.assertRaisesRegex(ValueError, "Keep balloons on the panel"):
            self.p.action("balloon-move", dict(self.ctx, panel=panel["id"], balloon=first["id"], x=4))

    def test_legacy_lettering_is_converted_once(self):
        with self.p.edit() as s:
            panel = s["chapters"][0]["pages"][0]["panels"][0]
            panel.pop("balloons")
            panel.update(lettering="Mika: First line.\nCAPTION: Later.\nStranger: Who?", treatment="caption")
        reopened = Project(Path(self.tmp.name)).state()["chapters"][0]["pages"][0]["panels"][0]
        self.assertEqual([(b["kind"], b["text"]) for b in reopened["balloons"]],
                         [("speech", "First line."), ("caption", "Later."), ("speech", "Who?")])
        self.assertEqual(reopened["balloons"][2]["speaker"], "")             # unknown name: left for the user to tag
        self.assertEqual(reopened["treatment"], "auto")
        self.assertEqual(from_lettering("", []), [])

    def test_prompt_names_the_speaker_and_keeps_balloon_areas_clear(self):
        text = panel_inputs(self.p.state(), self.chapter, self.page, 1)["prompt"]
        self.assertIn("Acting: Kenji is shouting", text)
        self.assertIn("Leave the upper right of the panel as calm", text)
        self.assertIn("without drawing any text", text)
        self.assertIn("Avoid: Blurred linework", text)       # starter graph samples at CFG 1

    def test_auto_positions_follow_reading_order(self):
        balloons = [dict(kind="speech", x=None, y=None) for _ in range(2)]
        self.assertEqual([b["x"] > .5 for b in auto_positions(balloons, "rtl")], [True, False])
        self.assertEqual([b["x"] > .5 for b in auto_positions(balloons, "ltr")], [False, True])

    def test_render_every_balloon_kind_and_export(self):
        state = self.art()
        page = state["chapters"][0]["pages"][0]
        for settings in (state, dict(state, page_preset="b5", tone="screentone", lettering_caps=True),
                         dict(state, page_preset="a5", tone="gray", bleed=True)):
            image = Image.open(io.BytesIO(compose(page, "rtl", self.p.media, settings=settings)))
            w, h, bleed = page_size(settings.get("page_preset", "web"), 300)
            self.assertEqual(image.size, (w + 2 * bleed * bool(settings.get("bleed")), h + 2 * bleed * bool(settings.get("bleed"))))
        # Art fills the whole panel: nothing is cropped to make room for a caption strip.
        image = Image.open(io.BytesIO(compose(page, "rtl", self.p.media, settings=state))).convert("RGB")
        x, y, w, h = frames(len(page["panels"]), page["layout"], "rtl")[4]
        self.assertEqual(image.getpixel((x + w // 2, y + h - 12)), (0x88, 0x99, 0xaa))
        pdf = chapter_pdf(state["chapters"][0]["pages"], "rtl", self.p.media, state)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertIn("<Manga>YesAndRightToLeft</Manga>", comic_info(state["chapters"][0], state))

    def test_japanese_lettering_runs_vertically(self):
        with self.p.edit() as s:
            s["chapters"][0]["pages"][0]["panels"][0]["balloons"][0]["text"] = "最終電車は遅れている"
        state = self.art()
        compose(state["chapters"][0]["pages"][0], "rtl", self.p.media, settings=state)

    def test_layouts_and_row_heights(self):
        for layout in ("feature", "grid", "strips", "three", "tall", "splash"):
            for count in range(1, 10):
                boxes = frames(count, layout, "rtl")
                self.assertEqual(len(boxes), count, (layout, count))
                self.assertTrue(all(w > 0 and h > 0 and x >= 0 and y >= 0 for x, y, w, h in boxes))
        self.p.action("layout", dict(self.ctx, layout="three", direction="rtl"))
        with self.assertRaisesRegex(ValueError, "Row heights"):
            self.p.action("row-weights", dict(self.ctx, weights=[1]))
        self.p.action("row-weights", dict(self.ctx, weights=[2, 1]))
        page = self.p.state()["chapters"][0]["pages"][0]
        boxes = frames(6, "three", "rtl", weights=page["weights"])
        self.assertAlmostEqual(boxes[0][3] / boxes[3][3], 2, places=1)
        geo = self.p.action("geometry", self.ctx)
        self.assertEqual(len(geo["gutters"]), 1)
        self.assertEqual(len(geo["panels"]), 6)

    def test_side_switch_warning(self):
        panels = self.panels()
        with self.p.edit() as s:
            ps = s["chapters"][0]["pages"][0]["panels"]
            ps[0]["balloons"][0].update(x=.8, y=.2)
            ps[1]["balloons"] = [dict(ps[0]["balloons"][0], id="b" * 32, x=.2, y=.2)]
        geo = self.p.action("geometry", self.ctx)
        self.assertEqual(geo["warnings"][0]["panels"], [1, 2])
        self.assertEqual(geo["warnings"][0]["speaker"], panels[0]["balloons"][0]["speaker"])


class StorageTests(unittest.TestCase):
    def test_jobs_live_in_their_own_table_and_are_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Project(Path(tmp))
            with p.edit() as s:
                s["jobs"] = [dict(id=f"{n:032x}", status="Ready", kind="panel", workflow={"graph": {"1": {}}}) for n in range(320)]
                s["jobs"].append(dict(id="f" * 32, status="Queued", kind="panel", workflow={"graph": {}}))
            state = p.state(light=True)
            self.assertEqual(len(state["jobs"]), 301)
            self.assertNotIn("workflow", state["jobs"][0])
            self.assertIn("workflow", p.state()["jobs"][0])
            self.assertEqual([j["id"] for j in p.active_jobs()], ["f" * 32])
            with sqlite3.connect(Path(tmp) / "manga.sqlite3") as db:
                blob = json.loads(db.execute("SELECT data FROM workspace").fetchone()[0])
            self.assertNotIn("jobs", blob)
            before = p.version()
            p.action("draft", dict(script="x", title="t", limit=4))
            self.assertEqual(p.version(), before + 1)

    def test_old_projects_move_jobs_out_of_the_workspace(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp) / "manga.sqlite3")
            db.execute("CREATE TABLE workspace (id INTEGER PRIMARY KEY, data TEXT NOT NULL)")
            old = dict(script="", title="Old", chapters=[], active="", assets=[], server="http://127.0.0.1:8188",
                       direction="rtl", limit=4, style="", jobs=[dict(id="a" * 32, status="Rendering", kind="panel")])
            db.execute("INSERT INTO workspace VALUES (1,?)", (json.dumps(old),))
            db.commit()
            db.close()
            p = Project(Path(tmp))
            job, = p.state()["jobs"]
            self.assertEqual(job["status"], "Rendering")
            self.assertTrue(job["resume"])                   # reconnects instead of being thrown away


if __name__ == "__main__":
    unittest.main()
