"""Minimal fake ComfyUI API for testing Anime Studio. Validates links/files and renders placeholder frames."""
import json, sys, threading, time, uuid, re, subprocess
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from urllib.parse import urlparse, parse_qs
from PIL import Image, ImageDraw, ImageOps

port = int(sys.argv[1]); root = Path(sys.argv[2]); gpu = sys.argv[3]
inp, out = root / "input", root / "output"
inp.mkdir(parents=True, exist_ok=True); out.mkdir(parents=True, exist_ok=True)
history, prompts_seen = {}, []
REQUIRED = {
    "ImageCompositeMasked": ["destination", "source", "mask", "x", "y", "resize_source"],
    "TextEncodeQwenImageEditPlus": ["clip", "prompt"], "WanImageToVideo": ["positive", "negative", "vae", "width", "height", "length", "batch_size"],
    "KSamplerAdvanced": ["model", "positive", "negative", "latent_image", "add_noise", "noise_seed", "steps", "cfg", "sampler_name", "scheduler", "start_at_step", "end_at_step", "return_with_leftover_noise"],
    "KSampler": ["model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"],
    "RIFE VFI": ["ckpt_name", "frames", "clear_cache_after_n_frames", "multiplier", "fast_mode", "ensemble", "scale_factor", "dtype", "torch_compile", "batch_size"],
    "LoadImage": ["image"], "LoadVideo": ["file"], "LoadAudio": ["audio"], "WanFirstLastFrameToVideo": ["positive", "negative", "vae", "width", "height", "length", "batch_size"],
    "ImageScale": ["image", "upscale_method", "width", "height", "crop"],
    "AudioEncoderLoader": ["audio_encoder_name"], "AudioEncoderEncode": ["audio_encoder", "audio"],
    "WanSoundImageToVideo": ["positive", "negative", "vae", "audio_encoder_output", "ref_image", "width", "height", "length", "batch_size"],
    "FB_Qwen3TTSVoiceDesign": ["text", "instruct", "model_choice", "language"],
    "FB_Qwen3TTSVoiceClone": ["target_text", "model_choice", "device", "precision", "language"], "SaveAudio": ["audio", "filename_prefix"], "UpscaleModelLoader": ["model_name"], "LoraLoaderModelOnly": ["model", "lora_name", "strength_model"], "SaveImage": ["images", "filename_prefix"],
}

def validate(wf):
    errs = {}
    for k, n in wf.items():
        for name, v in n["inputs"].items():
            if isinstance(v, list) and str(v[0]) not in wf:
                errs[k] = f"link {name} -> missing node {v[0]}"
        for r in REQUIRED.get(n["class_type"], []):
            if r not in n["inputs"]:
                errs[k] = f"required input missing: {r}"
        if n["class_type"] == "LoadImage" and not (inp / n["inputs"]["image"]).exists():
            errs[k] = f"image not found {n['inputs']['image']}"
        if n["class_type"] == "LoadAudio" and not (inp / n["inputs"]["audio"]).exists():
            errs[k] = "audio not found"
        if n["class_type"] == "LoadVideo" and (not (inp / n["inputs"]["file"]).exists() or "/" in n["inputs"]["file"]):
            errs[k] = f"video not in input root {n['inputs']['file']}"
    return errs

def render(pid, wf):
    time.sleep(1.0)
    cls = {n["class_type"]: (k, n) for k, n in wf.items()}
    if "SaveAudio" in cls:   # fake text-to-speech: a tone whose length follows the text
        save_k, save = cls["SaveAudio"]
        text = next((n["inputs"].get("target_text") or n["inputs"].get("text") for n in wf.values()
                     if (n.get("_meta") or {}).get("title") == "PROMPT"), "x")
        sub = "audio"; (out / sub).mkdir(parents=True, exist_ok=True)
        fn = f"tts_{pid[:8]}.flac"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=f=330:d={max(0.5, len(text) / 15):.2f}", str(out / sub / fn)])
        history[pid] = {"outputs": {save_k: {"audio": [{"filename": fn, "subfolder": sub, "type": "output"}]}},
                        "status": {"status_str": "success", "completed": True, "messages": []}}
        return
    save_k, save = cls["SaveImage"]
    prefix = save["inputs"]["filename_prefix"]
    sub, base = (prefix.rsplit("/", 1) + [""])[:2] if "/" in prefix else ("", prefix)
    (out / sub).mkdir(parents=True, exist_ok=True)
    vk = next((c for c in ("WanImageToVideo", "WanFirstLastFrameToVideo", "WanSoundImageToVideo", "Wan22ImageToVideoLatent") if c in cls), None)
    if vk:
        n = cls[vk][1]["inputs"]; frames, w, h = n["length"], n["width"], n["height"]
        start = [nn for nn in wf.values() if nn["class_type"] == "LoadImage" and (nn.get("_meta") or {}).get("title") == "START_IMAGE"][0]
        src = Image.open(inp / start["inputs"]["image"]).convert("RGB").resize((w, h))
    elif "LoadVideo" in cls:
        vid = inp / cls["LoadVideo"][1]["inputs"]["file"]
        frames = int(subprocess.check_output(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(vid)]).strip())
        sc = cls["ImageScale"][1]["inputs"]; w, h = sc["width"], sc["height"]; src = Image.new("RGB", (w, h), (40, 40, 90))
    else:
        lat = [n for n in wf.values() if n["class_type"] == "EmptySD3LatentImage"][0]["inputs"]
        frames, w, h = 1, lat["width"], lat["height"]; src = Image.new("RGB", (w, h), (hash(pid) % 255, 120, 160))
    imgs = []
    for i in range(frames):
        im = src.copy(); d = ImageDraw.Draw(im)
        d.rectangle([i * 6 % w, 20, i * 6 % w + 40, 60], fill=(255, 200, 0)); d.text((10, h - 20), f"{gpu} {pid[:6]} f{i}", fill=(255, 255, 255))
        if 'ImageCompositeMasked' in cls:
            composite = cls['ImageCompositeMasked'][1]['inputs']
            destination = wf[str(composite['destination'][0])]['inputs']['image']
            mask_node = wf[str(composite['mask'][0])]['inputs']['image']
            original = Image.open(inp/destination).convert('RGB')
            mask = ImageOps.invert(Image.open(inp/mask_node).convert('RGBA').getchannel('A')).resize(original.size)
            im = Image.composite(im.resize(original.size), original, mask)
        fn = f"{base}_{i + 1:05d}_.png"; im.save(out / sub / fn); imgs.append({"filename": fn, "subfolder": sub, "type": "output"})
    history[pid] = {"prompt": [0, pid, wf, {}, []], "outputs": {save_k: {"images": imgs}},
                    "status": {"status_str": "success", "completed": True, "messages": []}}

def safe_render(pid, wf):
    try:
        render(pid, wf)
    except Exception as e:   # report like ComfyUI does instead of leaving the job hanging
        history[pid] = {"outputs": {}, "status": {"status_str": "error", "completed": False,
                                                  "messages": [["execution_error", {"exception_message": repr(e)}]]}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def j(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        u = urlparse(self.path)
        if u.path == '/object_info':
            import importlib.util
            spec = importlib.util.spec_from_file_location('studio', Path(__file__).resolve().parent.parent / 'app/anime_studio.py')
            mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
            classes = set(REQUIRED) | {'FakeTTS', 'FakeText'}
            for variants in mod.STARTERS.values():
                for starter in variants.values():
                    classes.update(n['class_type'] for n in starter['wf'].values())
            return self.j(200, {c: {'input': {'required': {}}} for c in classes})
        if u.path == "/system_stats":
            return self.j(200, {"devices": [{"name": f"cuda:0 NVIDIA GeForce {gpu} : cudaMallocAsync", "vram_total": 12 * 2**30, "vram_free": 11 * 2**30}]})
        if u.path.startswith("/history/"):
            pid = u.path.rsplit("/", 1)[1]; return self.j(200, {pid: history[pid]} if pid in history else {})
        if u.path == "/queue":
            return self.j(200, {"queue_running": [], "queue_pending": []})
        if u.path == "/view":
            q = parse_qs(u.query); p = out / q.get("subfolder", [""])[0] / q["filename"][0]
            b = p.read_bytes(); self.send_response(200); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.j(404, {})
    def do_POST(self):
        u = urlparse(self.path); n = int(self.headers.get("Content-Length") or 0); body = self.rfile.read(n)
        if u.path == "/upload/image":
            m = re.search(rb'name="image"; filename="([^"]+)"\r\nContent-Type: [^\r]+\r\n\r\n', body)
            b = self.headers["Content-Type"].split("boundary=")[1].encode()
            data = body[m.end(): body.rindex(b"\r\n--" + b)]
            name = m.group(1).decode(); (inp / name).write_bytes(data)
            return self.j(200, {"name": name, "subfolder": "", "type": "input"})
        if u.path == "/prompt":
            wf = json.loads(body)["prompt"]; prompts_seen.append(wf)
            (root / f"last_{list(wf)[0]}_{len(prompts_seen)}.json").write_text(json.dumps(wf, indent=1, ensure_ascii=False))
            errs = validate(wf)
            if errs:
                return self.j(400, {"error": "invalid prompt", "node_errors": errs})
            pid = str(uuid.uuid4()); threading.Thread(target=safe_render, args=(pid, wf), daemon=True).start()
            return self.j(200, {"prompt_id": pid, "number": len(prompts_seen), "node_errors": {}})
        self.j(200, {})

ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
