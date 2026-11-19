#!/usr/bin/env python3
"""
End-to-end test against two fake ComfyUI servers (tests/mock_comfy.py). Needs ffmpeg and Pillow.

    python3 tests/test_pipeline.py

Covers: script -> shots, cast + turnaround sheet + LoRA, keyframes on two servers, animate, draft clips,
full-quality re-render with the same seed, Continue chains (incl. swapping a part), first-last-frame
on the final part, upscale, uploaded dialogue audio, music, and assembly to exact lengths.
"""
import base64, io, json, shutil, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN = HERE / "_run"
APP_PORT, M1, M2 = 8790, 9288, 9289
B = f"http://127.0.0.1:{APP_PORT}"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
procs, failures = [], []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        failures.append(msg)


def post(u, b):
    return json.loads(op.open(urllib.request.Request(B + u, data=json.dumps(b).encode(),
                                                     headers={"Content-Type": "application/json"})).read())


def state():
    return json.loads(op.open(B + "/api/state").read())


def shot(sid):
    return {x["id"]: x for x in state()["shots"]}[sid]


def wait_jobs():
    for _ in range(240):
        c = state()["job_counts"]
        if not c.get("pending") and not c.get("running"):
            break
        time.sleep(1)
    errs = [(j["id"], j["stage"], j["error"][:200]) for j in state()["jobs"] if j["status"] == "error"]
    check(not errs, f"no job errors {errs if errs else ''}")


def b64(name, data):
    return {"name": name, "data": "data:x;base64," + base64.b64encode(data).decode()}


def png(color):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (512, 512), color).save(buf, "PNG")
    return buf.getvalue()


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out)


def main():
    shutil.rmtree(RUN, ignore_errors=True)
    RUN.mkdir()
    for port, name in ((M1, "m1"), (M2, "m2")):
        procs.append(subprocess.Popen([sys.executable, str(HERE / "mock_comfy.py"), str(port), str(RUN / name), "RTX 3060"],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    procs.append(subprocess.Popen([sys.executable, str(HERE.parent / "app" / "anime_studio.py"), "--port", str(APP_PORT),
                                   "--data", str(RUN / "data")], stdout=open(RUN / "app.log", "w"), stderr=subprocess.STDOUT))
    time.sleep(3)

    s = state()
    iid = s["instances"][0]["id"]
    roles = ["sheet", "keyframe", "animate", "continue", "lipsync", "upscale", "voice", "voice_design"]
    post("/api/instance/save", {"id": iid, "name": "GPU0", "url": f"127.0.0.1:{M1}", "roles": roles, "enabled": True})
    post("/api/instance/save", {"name": "GPU1", "url": f"http://127.0.0.1:{M2}", "roles": ["keyframe", "animate", "continue"]})
    post("/api/instance/check", {})
    check(s["settings"]["default_duration"] == "4", "default shot length is 4 s")

    script = "# Rooftop\n\nAiko leans on the fence.\n\nAIKO: You came after all.\n\nKENJI (quietly): I always do."
    r = post("/api/script/draft", {"script": script})
    check(r["made"] == 3, "script makes 3 shots")
    s = state()
    aiko = [a for a in s["assets"] if a["name"] == "Aiko"][0]
    check(aiko["id"] in s["shots"][0]["asset_ids"], "action shot matched to a speaker named later in the script")
    check(s["shots"][0]["duration"] == 4.0, "motion shot from script gets the 4 s default")

    post("/api/asset/save", {"id": aiko["id"], "name": "Aiko", "kind": "character", "description": "teal bob",
                             "lora": "aiko.safetensors", "lora_strength": 0.8, "file": b64("a.png", png((0, 128, 128)))})
    post("/api/generate", {"stage": "sheet", "asset_ids": [aiko["id"]], "takes": 1})
    wait_jobs()
    long_id = post("/api/shot/save", {"scene": "Chase", "kind": "motion", "keyframe_prompt": "Aiko runs",
                                      "duration": 10, "end_next": True, "asset_ids": [aiko["id"]]})["id"]
    post("/api/shot/save", {"scene": "Chase", "kind": "motion", "keyframe_prompt": "Street corner"})
    ids = [x["id"] for x in state()["shots"]]
    post("/api/generate", {"stage": "keyframe", "shot_ids": ids, "takes": 2})
    wait_jobs()
    used = {j["instance_id"] for j in state()["jobs"] if j["stage"] == "keyframe"}
    check(len(used) == 2, "keyframes spread over both servers")

    post("/api/generate", {"stage": "animate", "shot_ids": [long_id], "draft": True})
    wait_jobs()
    sh = shot(long_id)
    d = [t for t in sh["takes"] if t["stage"] == "animate"][-1]
    check(d["draft"] == 1 and d["frames"] == 49, "draft clip has 49 frames")
    post("/api/generate", {"stage": "animate", "shot_ids": [long_id], "draft": False, "seed": d["seed"]})
    wait_jobs()
    full = [t for t in shot(long_id)["takes"] if t["stage"] == "animate" and not t["draft"]][-1]
    check(full["seed"] == d["seed"] and full["frames"] == 81, "full-quality re-render keeps the seed, 81 frames")
    post("/api/approve", {"take_id": full["id"]})
    sh = shot(long_id)
    check(abs(sh["remaining"] - 4.94) < 0.05, "10 s shot: 4.9 s left after one clip")

    post("/api/generate", {"stage": "continue", "shot_ids": [long_id], "takes": 2})
    wait_jobs()
    sh = shot(long_id)
    check(len(sh["cont"]) == 1 and sh["remaining"] == 0, "Continue fills the rest of the shot")
    r = post("/api/generate", {"stage": "continue", "shot_ids": [long_id]})
    check(r["queued"] == 0, "Continue refuses when the shot is fully animated")
    other = [t for t in sh["takes"] if t["stage"] == "continue" and t["id"] not in sh["cont"]][0]
    post("/api/approve", {"take_id": other["id"]})
    check(shot(long_id)["cont"] == [other["id"]], "swapping a part keeps the chain consistent")
    flf = [p for p in (RUN / "m1").glob("last_*.json")] + [p for p in (RUN / "m2").glob("last_*.json")]
    conts = [json.loads(p.read_text()) for p in flf if "continues seamlessly" in p.read_text()]
    check(conts and all(any(n["class_type"] == "WanFirstLastFrameToVideo" for n in wf.values()) for wf in conts),
          "final part lands on the next shot's keyframe (first-last frame)")
    loras = [json.loads(p.read_text()) for p in flf if "Picture 1 is Aiko" in p.read_text()]
    check(loras and all(any(n["inputs"].get("lora_name") == "aiko.safetensors" for n in wf.values()) for wf in loras),
          "character LoRA injected into Aiko's keyframes")

    # ---------- dialogue audio
    def tone(name, secs, hz=440):
        path = RUN / name
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f={hz}:d={secs}", "-ar", "44100", str(path)])
        return b64(name, path.read_bytes())

    dia = state()["shots"][1]                      # "AIKO: You came after all."
    r = post("/api/clips/upload", {"asset_id": aiko["id"], "files": [tone("aiko_01_you_came_after_all.wav", 2.0),
                                                                      tone("aiko_see_you_tomorrow.mp3", 1.5)]})
    clips = [a for a in state()["assets"] if a["id"] == aiko["id"]][0]["clips"]
    check(r["added"] == 2 and [c["transcript"] for c in clips] == ["you came after all", "see you tomorrow"],
          "clip transcripts come from file names")
    check(sum(c["is_ref"] for c in clips) == 1, "first clip becomes the reference voice")
    check(r["matched"] == 1, "uploading clips matches them to dialogue lines")
    l0 = shot(dia["id"])["lines"][0]
    check(l0["src"] == "clip" and shot(dia["id"])["audio"], "matched line has audio and the shot gets a dialogue track")

    two = post("/api/shot/save", {"scene": "Rooftop", "kind": "still", "keyframe_prompt": "both",
                                  "dialogue": "Aiko: You came after all!\nKenji: I always do."})["id"]
    post("/api/lines/match", {"shot_ids": [two]})
    sh = shot(two)
    check(sh["lines"][0]["src"] == "clip" and not sh["lines"][1]["audio"], "match tolerates punctuation; Kenji has no clips")
    post("/api/line/set", {"shot_id": two, "index": 1, "src": "upload", "file": tone("kenji.wav", 1.2, 600)})
    sh = shot(two)
    track = duration(RUN / "data" / sh["audio"])
    check(abs(track - (2.0 + 0.3 + 1.2)) < 0.15, f"shot track = line 1 + pause + line 2 ({track:.2f} s)")
    check(sh["duration"] >= round(track + 0.4, 1) - 0.01, "shot lengthens to fit its dialogue")
    post("/api/line/set", {"shot_id": two, "index": 1, "src": "none"})
    check(abs(duration(RUN / "data" / shot(two)["audio"]) - (2.0 + 0.3 + 1.0)) < 0.2, "a line without audio keeps its place as silence")

    tts = {"1": {"class_type": "LoadAudio", "inputs": {"audio": "x.wav"}, "_meta": {"title": "AUDIO_IN"}},
           "2": {"class_type": "FakeTTS", "inputs": {"text": "", "voice": ["1", 0], "ref": ["4", 0], "seed": 1}, "_meta": {"title": "PROMPT"}},
           "3": {"class_type": "SaveAudio", "inputs": {"audio": ["2", 0], "filename_prefix": "x"}},
           "4": {"class_type": "FakeText", "inputs": {"text": ""}, "_meta": {"title": "REF_TEXT"}}}
    r = post("/api/workflow", {"stage": "voice", "file": b64("tts.json", json.dumps(tts).encode())})
    check(r.get("ok"), "voice workflow accepted")
    r = post("/api/generate", {"stage": "voice", "shot_ids": [two]})
    check(r["queued"] == 0 and r["skipped"], "TTS skips speakers without a voice clip")
    post("/api/shot/save", {"id": two, "dialogue": "Aiko: You came after all!\nAiko: Let's go home."})
    r = post("/api/generate", {"stage": "voice", "shot_ids": [two]})
    check(r["queued"] == 1, "TTS queues only the line without audio")
    wait_jobs()
    sh = shot(two)
    check(sh["lines"][1]["src"] == "tts" and sh["lines"][0]["src"] == "clip", "TTS line lands on the right line; edited dialogue keeps matched audio")

    sent = [json.loads(p.read_text()) for p in list((RUN / "m1").glob("last_*.json")) + list((RUN / "m2").glob("last_*.json"))
            if '"FakeTTS"' in p.read_text()]
    check(sent and sent[-1]["4"]["inputs"]["text"] == "you came after all", "TTS gets the reference clip's transcript (REF_TEXT)")

    # reference voice from the cast dialog: new character + recording + what is said
    mina = post("/api/asset/save", {"name": "Mina", "kind": "character", "description": "calm"})
    check(isinstance(mina.get("id"), int), "creating a cast member returns its id")
    post("/api/clips/upload", {"asset_id": mina["id"], "files": [tone("random_name.wav", 6, 520)], "as_ref": True,
                               "transcript": "The sea is quiet tonight."})
    m = [a for a in state()["assets"] if a["id"] == mina["id"]][0]
    check(len(m["clips"]) == 1 and m["clips"][0]["is_ref"] and m["clips"][0]["transcript"] == "The sea is quiet tonight.",
          "reference voice saved with the typed transcript")
    post("/api/clips/upload", {"asset_id": mina["id"], "files": [tone("better.wav", 8, 520)], "as_ref": True, "transcript": "New words."})
    m = [a for a in state()["assets"] if a["id"] == mina["id"]][0]
    refs = [c for c in m["clips"] if c["is_ref"]]
    check(len(refs) == 1 and refs[0]["transcript"] == "New words." and len(m["clips"]) == 2, "replacing the reference keeps one reference")

    # Voice design: keep multiple takes and the sample transcript, without replacing an existing reference.
    designed = post("/api/asset/save", {"name": "Designed", "kind": "character",
                                      "voice_desc": "Calm low voice", "voice_sample": "Welcome home."})["id"]
    r = post("/api/generate", {"stage": "voice_design", "asset_ids": [designed], "takes": 2})
    check(r["queued"] == 2, "voice design queues two takes")
    wait_jobs()
    a = next(a for a in state()["assets"] if a["id"] == designed)
    check(len(a["clips"]) == 2 and sum(c["is_ref"] for c in a["clips"]) == 1,
          "designed takes saved with exactly one automatic reference")
    check(all(c["transcript"] == "Welcome home." and c["path"].endswith(".wav") for c in a["clips"]),
          "designed references keep the spoken transcript as WAV files")
    sent_design = [json.loads(p.read_text()) for p in (RUN / "m1").glob("last_*.json")
                   if "FB_Qwen3TTSVoiceDesign" in p.read_text()]
    node = sent_design[-1]["1"]["inputs"] if sent_design else {}
    check(node.get("text") == "Welcome home." and node.get("instruct") == "Calm low voice"
          and node.get("model_choice") == "1.7B", "voice design sends text, description and supported model")
    post("/api/clip/update", {"id": a["clips"][1]["id"], "is_ref": True})
    a = next(a for a in state()["assets"] if a["id"] == designed)
    check(a["clips"][1]["is_ref"] == 1, "another designed take can become the reference")
    post("/api/asset/delete", {"id": designed})
    check(not any(a["id"] == designed for a in state()["assets"]), "designed cast entry can be deleted")

    # ---------- Voices tab: the built-in Qwen3-TTS workflow, takes per line
    post("/api/workflow", {"stage": "voice", "starter": "qwen3_tts_clone"})
    post("/api/settings", {"settings": {"tts_model": "0.6B", "tts_language": "Japanese"}})
    three = post("/api/shot/save", {"scene": "Pier", "kind": "still", "keyframe_prompt": "Mina at the pier",
                                    "dialogue": "Mina: The tide is turning.\nMina: We should go."})["id"]
    post("/api/line/direction", {"shot_id": three, "index": 0, "direction": "softly"})
    r = post("/api/generate", {"stage": "voice", "lines": [{"shot_id": three, "index": 0}], "takes": 2})
    check(r["queued"] == 2, "two takes queued for one line")
    wait_jobs()
    l0 = shot(three)["lines"][0]
    check(len(l0["takes"]) == 2 and l0["src"] == "tts" and l0["audio"] == l0["takes"][0]["path"],
          "both takes kept, the first is chosen automatically")
    sent = [json.loads(p.read_text()) for p in list((RUN / "m1").glob("last_*.json")) + list((RUN / "m2").glob("last_*.json"))
            if "FB_Qwen3TTSVoiceClone" in p.read_text() and "The tide is turning." in p.read_text()]
    node = sent[-1]["2"]["inputs"] if sent else {}
    check(node.get("target_text") == "The tide is turning." and node.get("ref_text") == "New words."
          and node.get("language") == "Japanese" and node.get("model_choice") == "0.6B" and node.get("instruct") == "softly",
          "Qwen3-TTS node gets the line, reference transcript, language, model size and delivery note")
    post("/api/line/set", {"shot_id": three, "index": 0, "src": "take", "take_id": l0["takes"][1]["id"]})
    check(shot(three)["lines"][0]["audio"] == l0["takes"][1]["path"], "choosing another take uses it")
    post("/api/voice_take/delete", {"id": l0["takes"][1]["id"]})
    l0 = shot(three)["lines"][0]
    check(len(l0["takes"]) == 1 and not l0["audio"], "deleting the chosen take removes it from the line")
    r = post("/api/generate", {"stage": "voice", "shot_ids": [three], "takes": 1})
    check(r["queued"] == 2, "Generate all missing covers every line without audio")
    wait_jobs()
    sh = shot(three)
    check(all(l["audio"] for l in sh["lines"]) and sh["lines"][0]["direction"] == "softly", "all lines voiced; delivery note kept")

    post("/api/shot/save", {"id": dia["id"], "audio_file": tone("whole.wav", 4)})
    check(shot(dia["id"])["audio_mode"] == "file" and shot(dia["id"])["duration"] >= 4.4, "one file for the whole shot still works")
    post("/api/shot/save", {"id": dia["id"], "clear_audio": True})
    check(shot(dia["id"])["audio_mode"] == "lines" and shot(dia["id"])["lines"][0]["src"] == "clip", "removing it goes back to line audio")
    post("/api/generate", {"stage": "upscale", "shot_ids": [long_id]})
    wait_jobs()

    # Native S2V preset: both files must be uploaded and the short shot padded for the model.
    lip = post("/api/shot/save", {"scene": "Lip test", "kind": "still", "keyframe_prompt": "Aiko talks",
                                  "duration": 2, "audio_file": tone("speech.wav", 1)})["id"]
    post("/api/generate", {"stage": "keyframe", "shot_ids": [lip], "takes": 1})
    wait_jobs()
    r = post("/api/generate", {"stage": "lipsync", "shot_ids": [lip], "takes": 1})
    check(r["queued"] == 1, "native S2V preset queues without importing a workflow")
    wait_jobs()
    sh = shot(lip)
    check(sh["clip_take"] and not sh["outdated"], "S2V result becomes the current talking clip")
    sent_lip = [json.loads(p.read_text()) for p in (RUN / "m1").glob("last_*.json")
                if '"WanSoundImageToVideo"' in p.read_text()]
    wf = sent_lip[-1] if sent_lip else {}
    check(wf.get("22", {}).get("inputs", {}).get("length") == 73 and
          wf.get("11", {}).get("inputs", {}).get("audio", "").startswith("anime_j"),
          "S2V gets uploaded dialogue and at least 73 frames")
    post("/api/shot/delete", {"ids": [lip]})

    # Guided scene preparation uses the real dispatcher and stops before expensive animation.
    guided = post("/api/shot/save", {"scene": "Guided test", "kind": "motion", "keyframe_prompt": "Empty pier",
                                      "motion_prompt": "gentle waves", "duration": 6})["id"]
    pid = post("/api/production/plan", {"scene": "Guided test", "priority": 4})["id"]
    for _ in range(60):
        p = next(p for p in state()["production"]["plans"] if p["id"] == pid)
        if p["status"] in ("approval", "blocked"):
            break
        time.sleep(1)
    check(p["status"] == "approval", "scene plan pauses for keyframe approval")
    sh = shot(guided)
    check(sh["keyframe_take"] and not sh["clip_take"], "scene plan does not animate before approval")
    post("/api/approve", {"take_id": sh["keyframe_take"]})
    for _ in range(100):
        p = next(p for p in state()["production"]["plans"] if p["id"] == pid)
        if p["status"] in ("done", "blocked"):
            break
        time.sleep(1)
    check(p["status"] == "done", "scene plan completes animation and continuation: " + p["detail"])
    sh = shot(guided)
    check(sh["clip_take"] and sh["remaining"] < 0.25 and not sh["outdated"], "guided scene has a complete current animation chain")
    post("/api/shot/save", {"id": guided, "motion_prompt": "stormy waves"})
    check("animate" in shot(guided)["outdated"], "motion edits flag selected animation as outdated")
    post("/api/shot/save", {"id": guided, "motion_prompt": "gentle waves"})
    check(not shot(guided)["outdated"], "restoring original inputs restores render validity")
    before_jobs = state()["job_counts"]
    post("/api/assemble", {"preview": True, "scene": "Guided test", "use_music": False})
    for _ in range(60):
        A = state()["assembly"]
        if not A["running"]:
            break
        time.sleep(1)
    check(A.get("kind") == "animatic" and A["output"] and not any("ERROR" in x for x in A["log"]), "scene animatic renders successfully")
    check(state()["job_counts"] == before_jobs, "animatic creates no GPU jobs")
    check(abs(duration(RUN / "data" / A["output"]) - 6) < 0.15, "animatic respects shot timing")
    post("/api/shot/delete", {"ids": [guided]})

    # ---------- review fixes (v2.5)
    r = post("/api/settings", {"settings": {"tts_language": "english", "tts_model": "1.7b"}})
    st_ = state()["settings"]
    check(st_["tts_language"] == "English" and st_["tts_model"] == "1.7B", "TTS settings normalized to the node's options")
    post("/api/settings", {"settings": {"tts_language": "Klingon"}})
    check(state()["settings"]["tts_language"] == "Auto", "unknown TTS language falls back to Auto")
    r = post("/api/reveal", {"path": "../../etc"})
    check(r.get("ok") is False, "Show in Finder refuses paths outside the project")

    # a deleted shot's takes must not reappear on a new shot that reuses its id
    gone = post("/api/shot/save", {"scene": "Temp", "kind": "motion", "keyframe_prompt": "x", "dialogue": "Aiko: You came after all."})["id"]
    post("/api/generate", {"stage": "keyframe", "shot_ids": [gone], "takes": 1})
    wait_jobs()
    post("/api/generate", {"stage": "voice", "shot_ids": [gone], "takes": 1, "replace": True})
    wait_jobs()
    check(shot(gone)["takes"] and shot(gone)["lines"][0]["takes"], "throwaway shot has a keyframe and a voice take")
    post("/api/shot/delete", {"ids": [gone]})
    fresh = post("/api/shot/save", {"scene": "Fresh", "kind": "motion", "keyframe_prompt": "y", "dialogue": "Aiko: You came after all."})["id"]
    fs = shot(fresh)
    check(fresh == gone and not fs["takes"] and not fs["lines"][0]["takes"], f"new shot reusing id {fresh} starts clean")

    # warning when a shot is shorter than its dialogue
    short = post("/api/shot/save", {"scene": "Short", "kind": "still", "keyframe_prompt": "z", "dialogue": "Kenji: I always do."})["id"]
    post("/api/line/set", {"shot_id": short, "index": 0, "src": "upload", "file": tone("long.wav", 3.0, 500)})
    post("/api/shot/save", {"id": short, "duration": 1.5})

    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=3", str(RUN / "music.mp3")])
    post("/api/music", {"file": b64("music.mp3", (RUN / "music.mp3").read_bytes())})
    post("/api/assemble", {"use_music": True, "music_volume": 0.3})
    for _ in range(300):
        A = state()["assembly"]
        if not A["running"]:
            break
        time.sleep(1)
    check(not any("ERROR" in l for l in A["log"]), "assembly finished")
    check(any("WARNING" in l and "cut off" in l for l in A["log"]), "assembly warns when dialogue is longer than its shot")
    out = RUN / "data" / A["folder"]
    total = sum(x["duration"] for x in state()["shots"])
    check(abs(duration(out / Path(A["output"]).name) - total) < 0.3, f"episode length matches the shot list ({total:.1f} s)")
    seq = shot(long_id)["seq"]
    check(abs(duration(out / "shots" / f"{int(seq):04d}_shot.mp4") - 10.0) < 0.05, "continued shot is exactly 10 s")
    check((out / "timeline.edl").exists(), "Resolve EDL written")
    # Post-render soundtrack export must not render shots or stack music on the prior mix.
    original_output = A["output"]
    exports = state()["audio_exports"]
    check(bool(exports) and exports[0]["clean"], "episode retains a clean dialogue-only master")
    cue = post("/api/audio/cue/save", {"name": "Bell", "kind": "sfx", "start": "00:01", "duration": .5,
                    "volume": .5, "fade_in": .05, "fade_out": .05, "file": tone("bell.wav", .6, 880)})
    check(cue.get("ok"), "timed sound effect is saved")
    before_jobs = state()["job_counts"]
    post("/api/audio/remix", {"export_id": exports[0]["id"], "use_music": False})
    for _ in range(60):
        A = state()["assembly"]
        if not A["running"]:
            break
        time.sleep(1)
    check(A["output"] and A["output"] != original_output and not any("ERROR" in x for x in A["log"]), "new soundtrack exports after picture render")
    check(state()["job_counts"] == before_jobs, "soundtrack export submits no GPU jobs")
    check(abs(duration(RUN / "data" / A["output"]) - total) < .3, "soundtrack export preserves episode duration")



    # Character-driven voice generation is available directly on a saved shot.
    bound = post('/api/shot/save', {'scene':'Shot voice test','kind':'still','dialogue':'Words for this shot.','asset_ids':[aiko['id'],mina['id']]})['id']
    sh = shot(bound)
    check(sh['lines'][0]['character_id'] is None, 'multi-character shots require an explicit speaking character')
    response = post('/api/shot/voices', {'shot_id':bound,'token':sh['voice_token'],
        'lines':[{'line_id':sh['lines'][0]['line_id'],'character_id':mina['id'],'text':'This voice belongs to this shot.','direction':'warmly'}],
        'generate':[0],'takes':2})
    check(response.get('queued') == 2, 'shot dialogue saves and queues character voices in one action')
    wait_jobs()
    sh = shot(bound); line = sh['lines'][0]
    check(line['character_id']==mina['id'] and len(line['takes'])==2 and line['audio'], 'generated character takes are attached to their exact shot line')
    old_take=line['takes'][0]['id']
    response=post('/api/shot/voices', {'shot_id':bound,'token':sh['voice_token'],
        'lines':[{'line_id':line['line_id'],'character_id':aiko['id'],'text':line['text'],'direction':''}], 'generate':[]})
    check(response.get('ok') and not shot(bound)['lines'][0]['audio'], 'changing the speaking character clears the previous selected audio')
    response=post('/api/line/set', {'shot_id':bound,'index':0,'src':'take','take_id':old_take})
    check(response.get('ok') is False, 'a take from the previous character cannot be selected on the changed line')
    post('/api/shot/delete', {'ids':[bound]})


if __name__ == "__main__":
    try:
        main()
    finally:
        for p in procs:
            p.terminate()
    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
    sys.exit(1 if failures else 0)
