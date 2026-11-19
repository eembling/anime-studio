#!/usr/bin/env python3
"""
Batch-process a folder of images through a ComfyUI workflow using its HTTP API.

Usage:
    pip install requests
    python comfy_batch.py --workflow my_workflow_api.json --input ./in --output ./out

The workflow must be exported in *API format* (ComfyUI menu: Workflow -> Export (API)).
For each image, the script:
  1. uploads it to the server (POST /upload/image)
  2. points the workflow's LoadImage node at it
  3. queues the job (POST /prompt)
  4. waits for it to finish (GET /history/<prompt_id>)
  5. downloads the results (GET /view)
"""
import argparse
import copy
import json
import random
import sys
import time
import uuid
from pathlib import Path

import requests

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
SEED_KEYS = ("seed", "noise_seed")


def find_node(wf, class_type, node_id=None):
    """Return the id of the node to patch: the one given, or the first of class_type."""
    if node_id:
        if node_id not in wf:
            sys.exit(f"Node id {node_id} is not in the workflow.")
        return node_id
    ids = [k for k, v in wf.items() if v.get("class_type") == class_type]
    if not ids:
        return None
    if len(ids) > 1:
        print(f"Note: {len(ids)} {class_type} nodes found ({ids}); using {ids[0]}. "
              f"Pass a node id to pick another.")
    return ids[0]


def upload_image(server, path, subfolder):
    with open(path, "rb") as f:
        r = requests.post(
            f"{server}/upload/image",
            files={"image": (path.name, f)},
            data={"overwrite": "true", "subfolder": subfolder, "type": "input"},
            timeout=120,
        )
    r.raise_for_status()
    j = r.json()
    # LoadImage accepts "subfolder/name" relative to ComfyUI's input dir
    return f"{j['subfolder']}/{j['name']}" if j.get("subfolder") else j["name"]


def queue_prompt(server, wf, client_id):
    r = requests.post(f"{server}/prompt", json={"prompt": wf, "client_id": client_id}, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"Queue failed ({r.status_code}): {r.text}")
    return r.json()["prompt_id"]


def wait_for(server, prompt_id, timeout):
    start = time.time()
    while True:
        r = requests.get(f"{server}/history/{prompt_id}", timeout=30)
        r.raise_for_status()
        hist = r.json()
        if prompt_id in hist:
            entry = hist[prompt_id]
            status = entry.get("status", {})
            if status.get("completed") or status.get("status_str") in ("success", "error"):
                return entry
        if time.time() - start > timeout:
            raise TimeoutError(f"Timed out waiting for {prompt_id}")
        time.sleep(1)


def download_outputs(server, entry, out_dir, stem, include_temp):
    saved = []
    n = 0
    for node_out in entry.get("outputs", {}).values():
        for img in node_out.get("images", []) + node_out.get("gifs", []):
            if img.get("type") != "output" and not include_temp:
                continue
            r = requests.get(f"{server}/view", params={
                "filename": img["filename"],
                "subfolder": img.get("subfolder", ""),
                "type": img.get("type", "output"),
            }, timeout=120)
            r.raise_for_status()
            ext = Path(img["filename"]).suffix or ".png"
            dest = out_dir / (f"{stem}{ext}" if n == 0 else f"{stem}_{n}{ext}")
            dest.write_bytes(r.content)
            saved.append(dest)
            n += 1
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--server", default="http://172.16.2.70:8188")
    ap.add_argument("--workflow", required=True, help="Workflow JSON exported in API format")
    ap.add_argument("--input", required=True, help="Folder of source images")
    ap.add_argument("--output", required=True, help="Folder to save results into")
    ap.add_argument("--load-node", help="Id of the LoadImage node to patch (default: first LoadImage)")
    ap.add_argument("--save-node", help="Id of the SaveImage node (default: first SaveImage)")
    ap.add_argument("--random-seed", action="store_true", help="Randomize every seed/noise_seed per image")
    ap.add_argument("--prompt-node", help="Id of a text-encode node whose 'text' to override")
    ap.add_argument("--prompt-text", help="Text to put in --prompt-node ({name} = image file stem)")
    ap.add_argument("--subfolder", default="batch_in", help="Server input subfolder for uploads")
    ap.add_argument("--timeout", type=int, default=1800, help="Seconds to wait per image")
    ap.add_argument("--include-temp", action="store_true", help="Also download PreviewImage outputs")
    args = ap.parse_args()

    server = args.server.rstrip("/")
    base_wf = json.loads(Path(args.workflow).read_text())
    if "nodes" in base_wf and "links" in base_wf:
        sys.exit("That's a UI-format workflow. Re-export it with Workflow -> Export (API).")

    load_id = find_node(base_wf, "LoadImage", args.load_node)
    if not load_id:
        sys.exit("No LoadImage node found; pass --load-node with the right node id.")
    save_id = find_node(base_wf, "SaveImage", args.save_node)

    in_dir, out_dir = Path(args.input), Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(p for p in in_dir.iterdir() if p.suffix.lower() in IMG_EXTS)
    if not images:
        sys.exit(f"No images found in {in_dir}")

    client_id = str(uuid.uuid4())
    jobs = []

    # Phase 1: upload + queue everything (ComfyUI runs its queue in order)
    for i, img in enumerate(images, 1):
        wf = copy.deepcopy(base_wf)
        wf[load_id]["inputs"]["image"] = upload_image(server, img, args.subfolder)
        if save_id:
            wf[save_id]["inputs"]["filename_prefix"] = f"batch/{img.stem}"
        if args.random_seed:
            for node in wf.values():
                for k in SEED_KEYS:
                    if isinstance(node.get("inputs", {}).get(k), int):
                        node["inputs"][k] = random.randint(0, 2**53)
        if args.prompt_node and args.prompt_text is not None:
            wf[args.prompt_node]["inputs"]["text"] = args.prompt_text.format(name=img.stem)
        pid = queue_prompt(server, wf, client_id)
        jobs.append((img, pid))
        print(f"[{i}/{len(images)}] queued {img.name} -> {pid}")

    # Phase 2: collect results
    failed = 0
    for i, (img, pid) in enumerate(jobs, 1):
        try:
            entry = wait_for(server, pid, args.timeout)
            if entry.get("status", {}).get("status_str") == "error":
                raise RuntimeError(json.dumps(entry["status"].get("messages", []))[:500])
            saved = download_outputs(server, entry, out_dir, img.stem, args.include_temp)
            print(f"[{i}/{len(jobs)}] {img.name}: saved {', '.join(p.name for p in saved) or 'nothing'}")
        except Exception as e:
            failed += 1
            print(f"[{i}/{len(jobs)}] {img.name}: FAILED - {e}")

    print(f"Done. {len(jobs) - failed} ok, {failed} failed.")


if __name__ == "__main__":
    main()
