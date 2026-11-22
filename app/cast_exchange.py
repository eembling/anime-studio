"""Portable visual cast libraries shared by Anime Studio and Manga Studio."""
import hashlib
import json
import math
import re
import zipfile
from pathlib import Path

FORMAT = "anime-studio-cast"
KINDS = ("character", "location")
MAX_ARCHIVE = 256 * 1024 * 1024
MAX_IMAGE = 32 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024


def export_cast(studio, output, ids=None):
    """Write a ZIP with content-addressed images and no project-specific paths."""
    entries, written, warnings = [], set(), []
    total = 0
    with zipfile.ZipFile(output, "w") as archive:
        def reference(path, label, collection):
            nonlocal total
            file = (studio.data / path).resolve()
            if studio.data not in file.parents or not file.is_file():
                warnings.append(f"{row['name']} · {label} is unavailable.")
                return None
            if file.stat().st_size > MAX_IMAGE:
                raise ValueError(f"{row['name']} has a reference larger than 32 MB.")
            raw = file.read_bytes()
            key = hashlib.sha256(raw).hexdigest()
            if key not in written:
                total += len(raw)
                if total > MAX_ARCHIVE:
                    raise ValueError("Select fewer entries to fit this library in 256 MB.")
                archive.writestr("images/" + key, raw)
                written.add(key)
            return dict(image=key, label=label, collection=collection)

        with studio.db.lock:
            rows = studio.db.q("SELECT * FROM assets WHERE kind IN ('character','location') ORDER BY id")
            if ids is not None:
                rows = [row for row in rows if row["id"] in ids]
                if len(rows) != len(ids):
                    raise ValueError("A selected entry is unavailable. Reopen the export dialog.")
            if not rows:
                raise ValueError("Choose at least one character or location.")
            for row in rows:
                refs = []
                for sheet in studio.db.q("SELECT id,path FROM takes WHERE asset_id=? AND stage='sheet' ORDER BY id", (row["id"],)):
                    if sheet["path"]:
                        refs.append((sheet["path"], f"Sheet {sheet['id']}", "Turnarounds"))
                for look in studio.db.q("SELECT name,version,json FROM asset_looks WHERE asset_id=? ORDER BY created,id", (row["id"],)):
                    group = f"{look['name']} · v{look['version']}"
                    refs.extend((r["path"], r["label"], group) for r in json.loads(look["json"])["references"])
                current = row["image"]
                if current and not any(path == current for path, _, _ in refs):
                    refs.insert(0,(current,"Current reference","Model references"))
                selected, images = "", []
                for path, label, group in refs:
                    ref = reference(path, label, group)
                    if ref:
                        images.append(ref)
                        if path == current:
                            selected = ref["image"]
                entries.append(dict(name=row["name"], kind=row["kind"], description=row["description"] or "",
                                    lora=row["lora"] or "", strength=row["lora_strength"] if row["lora_strength"] is not None else 1,
                                    refs=images, selected=selected))
        manifest = json.dumps(dict(format=FORMAT, version=1, entries=entries, warnings=warnings)).encode()
        if len(manifest) > MAX_MANIFEST:
            raise ValueError("Select fewer entries to reduce the library manifest size.")
        archive.writestr("manifest.json", manifest)
    if output.tell() > MAX_ARCHIVE:
        raise ValueError("Select fewer entries to fit this library in 256 MB.")


def read_manifest(archive):
    files = archive.infolist()
    if len({f.filename for f in files}) != len(files) or sum(f.file_size for f in files) > MAX_ARCHIVE:
        raise ValueError("Invalid or oversized cast library.")
    names = {f.filename for f in files}
    if "manifest.json" not in names or archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
        raise ValueError("Choose a cast library exported by Anime Studio.")
    for f in files:
        if f.filename != "manifest.json" and (not re.fullmatch(r"images/[0-9a-f]{64}", f.filename) or f.file_size > MAX_IMAGE):
            raise ValueError("Unexpected file in cast library.")
        if f.flag_bits & 1 or f.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("Unsupported cast library encoding.")
    data = json.loads(archive.read("manifest.json"))
    if not isinstance(data, dict) or data.get("format") != FORMAT or data.get("version") != 1:
        raise ValueError("Unsupported cast library format or version.")
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("This cast library has no entries.")
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("kind") not in KINDS:
            raise ValueError("Unknown cast entry type.")
        if not all(isinstance(entry.get(k), str) for k in ("name","description","lora","selected")) or not entry["name"].strip():
            raise ValueError("Invalid cast entry details.")
        if not isinstance(entry.get("strength"), (float,int)) or not math.isfinite(entry["strength"]):
            raise ValueError("Invalid LoRA strength.")
        refs = entry.get("refs")
        if not isinstance(refs, list):
            raise ValueError("Invalid reference list.")
        for ref in refs:
            if not isinstance(ref, dict) or not all(isinstance(ref.get(k), str) for k in ("image","label","collection")) or "images/"+ref["image"] not in names:
                raise ValueError("A referenced image is missing from the library.")
        if entry["selected"] and not any(r["image"] == entry["selected"] for r in refs):
            raise ValueError("The active reference is missing from the library.")
    if not isinstance(data.get("warnings", []), list) or not all(isinstance(w,str) for w in data.get("warnings", [])):
        raise ValueError("Invalid library warnings.")
    return data
