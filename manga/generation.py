"""Image-generation settings, server roles and panel direction."""
import json
from math import isqrt
from PIL import Image, ImageOps
from urllib.parse import urlsplit
from anime_studio import check_workflow, detect_slots
from compose import frames
from planner import uid
from lettering import prompt_lines
from keyframe_prompt import ignores_negative

DEFAULTS = dict(panel_size=1024, sheet_width=1280, sheet_height=768, timeout=3600,
                page_preset="web", dpi=300, bleed=False, tone="color", lettering_caps=False, vertical_cjk=True,
                negative="Blurred linework, illegible text, watermarks, distorted anatomy.")
LIMITS = dict(panel_size=(256,4096), sheet_width=(256,4096), sheet_height=(256,4096), timeout=(10,86400))


def server(name, url, roles, enabled=True):
    address = url.strip().rstrip("/")
    parsed = urlsplit(address)
    if parsed.scheme not in ("http","https") or not parsed.hostname or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise ValueError("Enter a ComfyUI HTTP or HTTPS address.")
    if not name.strip() or any(r not in ("panel","sheet") for r in roles):
        raise ValueError("Enter a server name and valid roles.")
    return dict(id=uid(), name=name.strip(), url=address, roles=list(roles), enabled=bool(enabled))


def anime_servers(path):
    if not path or not path.is_file():
        return []
    rows = json.loads(path.read_text())["servers"]
    result = []
    for row in rows:
        roles = row["roles"].split(",")
        dest = [role for stage,role in (("keyframe","panel"),("sheet","sheet")) if stage in roles]
        if dest:
            result.append(server(row["name"],row["url"],dest,row["enabled"]))
    return result


def validate_graph(graph):
    if not isinstance(graph,dict) or any(not isinstance(n,dict) or not isinstance(n.get("inputs"),dict) for n in graph.values()):
        raise ValueError("Import a ComfyUI API-format image workflow.")
    problems = check_workflow(graph,"keyframe")
    if not any(n.get("class_type")=="SaveImage" for n in graph.values()):
        problems.append("Add a SaveImage output.")
    if problems:
        raise ValueError(" ".join(problems))


def group_references(refs, slots, continuity=False):
    if not refs:
        return []
    if not slots:
        raise ValueError("Choose an image workflow with a reference input to use panel references.")
    if continuity and len(refs)>1 and slots>1:
        return group_references(refs[:-1],slots-1) + [refs[-1:]]
    count = min(slots,len(refs))
    size, extra = divmod(len(refs),count)
    groups = []
    start = 0
    for i in range(count):
        end = start + size + (i < extra)
        groups.append(refs[start:end])
        start = end
    return groups


def grid_columns(count):
    return isqrt(count-1)+1


def make_reference_sheet(media, group, path):
    columns = grid_columns(len(group))
    rows = (len(group)+columns-1)//columns
    cell = max(1,min(768,2048//columns))
    sheet = Image.new("RGB",(columns*cell,rows*cell),"white")
    for index,name in enumerate(group):
        with Image.open(media/name) as original:
            tile = ImageOps.exif_transpose(original)
            tile.thumbnail((max(1,cell-8),max(1,cell-8)), Image.Resampling.LANCZOS)
            row,col = divmod(index,columns)
            sheet.paste(tile,(col*cell+(cell-tile.width)//2,row*cell+(cell-tile.height)//2))
    sheet.save(path,"PNG")


def prompt_inputs(s, cast, parts, previous=None, additional=()):
    refs, names, lines = [], [], []
    for a in cast:
        name = a["name"] + (" ("+a["description"]+")" if a["description"] else "")
        if a["selected"]:
            refs.append(a["selected"])
            names.append(name)
        else:
            lines.append(name)
    for image, label in additional:
        refs.append(image)
        names.append(label)
    if previous:
        refs.append(previous)
        names.append("the previous panel. Match its location, lighting and outfits")
    graph = (s.get("workflow") or {}).get("graph")
    slots = len(detect_slots(graph,"keyframe",all_references=True)["refs"]) if graph else 3
    groups = group_references(refs, slots, bool(previous))
    subjects = iter(names)
    for i,group in enumerate(groups,1):
        if len(group)==1:
            lines.append(f"Picture {i} is {next(subjects)}.")
        else:
            lines.append(f"Picture {i} is a reference sheet. Use the subjects individually in the illustration.")
            for j in range(len(group)):
                row,col = divmod(j,grid_columns(len(group)))
                lines.append(f"Picture {i}, row {row+1}, column {col+1}: {next(subjects)}.")
    # The starter graph samples at CFG 1, where a negative prompt has no effect: say it in the positive prompt too.
    avoid = "Avoid: "+s["negative"].strip().rstrip(".")+"." if s.get("negative","").strip() and (ignores_negative(graph) if graph else True) else ""
    return dict(prompt="\n".join(x for x in lines+parts+[s["style"],"No speech balloons, captions, typography or lettering.",avoid] if x),
                negative=s["negative"], refs=refs, ref_groups=groups,
                loras=[[a["lora"],a["strength"]] for a in cast if a["lora"]])


def panel_inputs(s, chapter, page, index):
    panel = page["panels"][index]
    by_id = {a["id"]:a for a in s["assets"]}
    parts = [page["scene"], page.get("direction_notes",""),page.get("lighting",""),page.get("continuity",""),
             panel["prompt"],panel.get("camera",""),panel.get("director",""),"Single panel illustration."]
    balloons = panel.get("balloons") or []
    characters = [k for k in panel["cast"] if by_id.get(k, {}).get("kind") == "character"]
    names = {a["id"]: a["name"] for a in s["assets"]}
    parts += prompt_lines(balloons, characters, names, chapter["direction"], panel.get("treatment", "auto"))
    spoken = [b for b in balloons if b["kind"] != "sfx"]
    if spoken:
        # The words guide expression and staging; the lettering itself is added later, never drawn by the model.
        parts.append("Convey the meaning visually, without drawing any text: " +
                     " ".join((names.get(b["speaker"], "Caption") if b["speaker"] else "Caption") + ": " + b["text"] for b in spoken))
    previous = None
    if panel.get("previous"):
        sequence = [p for pg in chapter["pages"] for p in pg["panels"]]
        i = sequence.index(panel)
        if not i or not sequence[i-1]["selected"]:
            raise ValueError("Select artwork for the preceding panel to use it as a continuity reference.")
        previous = sequence[i-1]["selected"]
    additional = []
    for aid, image in panel.get("extra_refs",[]):
        a = by_id[aid]
        if aid in panel["cast"] and image == a["selected"]:
            continue
        ref = next(r for r in a["refs"] if r["id"]==image)
        additional.append((image,f"{a['name']} — {ref['label']}"))
    result = prompt_inputs(s,[by_id[k] for k in panel["cast"]],parts,previous,additional)
    _,_,w,h = frames(len(page["panels"]),page["layout"],chapter["direction"],weights=page.get("weights"))[index]
    scale = s["panel_size"]/max(w,h)
    result.update(width=max(16,round(w*scale/16)*16), height=max(16,round(h*scale/16)*16))
    return result
