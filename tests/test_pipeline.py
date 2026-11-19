#!/usr/bin/env python3
"""
End-to-end test against two fake ComfyUI servers (tests/mock_comfy.py). Needs ffmpeg and Pillow.

    python3 tests/test_pipeline.py

Covers: script -> shots, cast + turnaround sheet + LoRA, keyframes on two servers, animate, draft clips,
full-quality re-render with the same seed, Continue chains (incl. swapping a part), first-last-frame
on the final part, upscale, uploaded dialogue audio, music, and assembly to exact lengths.
"""
import base64, io, json, os, shutil, subprocess, sys, time, urllib.request
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
    roles = ["sheet", "keyframe", "animate", "continue", "lipsync", "upscale"]
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
    next_id = post("/api/shot/save", {"scene": "Chase", "kind": "motion", "keyframe_prompt": "Street corner"})["id"]
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

    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=4", "-ar", "48000", str(RUN / "line.wav")])
    dia = state()["shots"][1]
    post("/api/shot/save", {"id": dia["id"], "audio_file": b64("line.wav", (RUN / "line.wav").read_bytes())})
    check(shot(dia["id"])["duration"] >= 4.4, "shot lengthens to fit its dialogue audio")
    post("/api/generate", {"stage": "upscale", "shot_ids": [long_id]})
    wait_jobs()

    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=220:d=3", str(RUN / "music.mp3")])
    post("/api/music", {"file": b64("music.mp3", (RUN / "music.mp3").read_bytes())})
    post("/api/assemble", {"use_music": True, "music_volume": 0.3})
    for _ in range(300):
        A = state()["assembly"]
        if not A["running"]:
            break
        time.sleep(1)
    check(not any("ERROR" in l for l in A["log"]), "assembly finished")
    out = RUN / "data" / A["folder"]
    total = sum(x["duration"] for x in state()["shots"])
    check(abs(duration(out / Path(A["output"]).name) - total) < 0.3, f"episode length matches the shot list ({total:.1f} s)")
    seq = shot(long_id)["seq"]
    check(abs(duration(out / "shots" / f"{int(seq):04d}_shot.mp4") - 10.0) < 0.05, "continued shot is exactly 10 s")
    check((out / "timeline.edl").exists(), "Resolve EDL written")


if __name__ == "__main__":
    try:
        main()
    finally:
        for p in procs:
            p.terminate()
    print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
    sys.exit(1 if failures else 0)
