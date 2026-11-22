"""Break a screenplay into editable panels and scene-aware pages."""
import re
from uuid import uuid4
from script_dialogue import dialogue_line, METADATA
from lettering import balloon, kind_for


def uid():
    return uuid4().hex


def asset(name, kind="character"):
    return dict(id=uid(), name=name, kind=kind, description="", lora="", strength=1,
                refs=[], selected="")


def paginate(script, limit, assets):
    if not 1 <= limit <= 9:
        raise ValueError("Choose one to nine panels per page.")
    pages, panels = [], []
    scene = "Opening"
    pending = None

    def finish_panel():
        nonlocal pending
        if pending:
            if len(panels) == limit:
                finish_page()
            panels.append(pending)
            pending = None

    def finish_page():
        if panels:
            pages.append(dict(id=uid(), scene=scene, layout="feature", lighting=context.get("lighting",""), continuity=context.get("continuity",""),
                              direction_notes=" · ".join(filter(None,[context.get("location"),context.get("mood")])), panels=panels.copy()))
            panels.clear()

    def panel(action, raw):
        return dict(id=uid(), prompt=action, lettering="", balloons=[], cast=[], source=raw,
                    takes=[], selected="", treatment="auto")

    explicit = False
    context = {}
    last_field = ""
    screenplay_speaker = ""
    camera_labels = {"wide shot","close-up","medium close-up","extreme close-up","establishing shot","over the shoulder","pov","insert"}

    def cast_member(name, kind="character"):
        match = next((a for a in assets if a["name"].casefold() == name.casefold()), None)
        if match is None:
            match = asset(name,kind)
            assets.append(match)
        if match["id"] not in pending["cast"]:
            pending["cast"].append(match["id"])
        return match

    lines = script.splitlines()
    for index,raw in enumerate(lines):
        line = re.sub(r"^\s*\d+[.)]\s+", "", raw.strip())
        clean = line.replace("**","").strip("*_ ")
        heading = clean.lstrip("# ").strip()
        if not clean or re.fullmatch(r"[-*_]{3,}",clean):
            screenplay_speaker = last_field = ""
            continue
        page_break = re.fullmatch(r"PAGE(?:\s+\d+)?(?:\s*[:—–-]\s*.*)?",heading,re.I)
        marker = re.fullmatch(r"(?:PANEL|SHOT)(?:\s+\d+)?(?:\s*[:—–-]\s*(.*))?",heading,re.I)
        if marker:
            finish_panel()
            pending = panel(marker.group(1) or "",raw)
            explicit = True
            screenplay_speaker = last_field = ""
            continue
        if page_break or re.match(r"^(?:#+\s|(?:INT\.?/EXT\.?|INT\.|EXT\.|SCENE\b))",clean,re.I):
            finish_panel()
            finish_page()
            explicit = False
            screenplay_speaker = last_field = ""
            if not page_break:
                scene = heading
                context = {}
            continue
        label, sep, content = clean.partition(":")
        key = label.strip().casefold() if sep else ""
        content = content.strip()
        if key in ("lighting","mood","location","continuity") and not pending:
            context[key] = content
            continue
        cue = (not sep and not line.startswith(("*","#")) and
               re.fullmatch(r"[A-Z][A-Z .'’-]{0,44}(?:\s*\([^)]*\))?",clean) and
               index+1<len(lines) and lines[index+1].strip() and ":" not in lines[index+1])
        if cue:
            screenplay_speaker = clean
            last_field = ""
            continue
        if screenplay_speaker and not sep and clean.startswith("(") and clean.endswith(")"):
            screenplay_speaker = screenplay_speaker.partition("(")[0].strip()+" "+clean
            continue
        if screenplay_speaker and not sep and last_field=="lettering" and pending:
            pending["lettering"] += " "+clean
            if pending["balloons"]:
                pending["balloons"][-1]["text"] += " "+clean
            pending["source"] += "\n"+raw
            continue
        if screenplay_speaker and not sep:
            line = screenplay_speaker+": "+clean
        elif sep:
            screenplay_speaker = ""
        fields = {"keyframe":"prompt","keyframe prompt":"prompt","art":"prompt","illustration":"prompt","action":"prompt",
                  "camera":"camera","framing":"camera","direction":"director","notes":"director","blocking":"director",
                  "lighting":"director","location":"director","mood":"director","continuity":"director","cast":"cast","characters":"cast"}
        if key in fields or key in camera_labels:
            if not explicit and pending and pending["lettering"]:
                finish_panel()
            if not pending:
                pending = panel("","")
            pending["source"] = (pending["source"]+"\n"+raw).strip()
            target = fields.get(key,"prompt")
            if target=="cast":
                for name in re.split("[,;]",content):
                    if name.strip():
                        cast_member(name.strip())
            else:
                if key in camera_labels:
                    pending["camera"] = label
                pending[target] = " ".join(filter(None,[pending.get(target),content]))
            last_field = target
            continue
        if key in METADATA - {"sfx","dialogue"}:
            continue
        entry = dialogue_line(content if key=="dialogue" else line)
        if key in ("caption","sfx"):
            entry = dict(speaker=key.upper(),text=content,direction="")
        if entry:
            if pending and pending["lettering"] and not explicit:
                finish_panel()
            if not pending:
                pending = panel("","")
            speaker = entry["speaker"]
            pending["lettering"] = (pending["lettering"]+"\n"+speaker+": "+entry["text"]).strip()
            if entry["direction"]:
                pending["director"] = " ".join(filter(None,[pending.get("director"),speaker+" ("+entry["direction"]+")"]))
            kind = kind_for(speaker, entry["direction"])
            if speaker.upper() not in ("CAPTION","SFX"):
                member = cast_member(speaker)
                if member["kind"] != "character":
                    kind = "caption"
                pending["balloons"].append(balloon(kind, member["id"] if kind != "caption" else "", entry["text"]))
            else:
                pending["balloons"].append(balloon(kind, "", entry["text"]))
            last_field = "lettering"
        else:
            if not explicit:
                finish_panel()
            if not pending:
                pending = panel("","")
            target = last_field if explicit and last_field in ("camera","director") else "prompt"
            pending[target] = " ".join(filter(None,[pending.get(target),clean]))
            last_field = target
        pending["source"] = (pending["source"]+"\n"+raw).strip()
    finish_panel()
    finish_page()
    if not pages:
        raise ValueError("Add action or dialogue before planning pages.")
    for page in pages:
        for item in page["panels"]:
            for a in assets:
                if a["id"] not in item["cast"] and re.search(r"(?<!\w)" + re.escape(a["name"]) + r"(?!\w)", (" ".join([item["prompt"],page["scene"],page.get("direction_notes","")]) if a["kind"]=="location" else item["prompt"]), re.I):
                    item["cast"].append(a["id"])
    stage(pages, assets)
    return pages


WIDE, MEDIUM, CLOSE = "Wide establishing shot", "Medium shot", "Close-up"


def stage(pages, assets):
    """Give dialogue-only panels a drawable beat and suggest varied framing where the script names none."""
    names = {a["id"]: a["name"] for a in assets}
    scene, last_action, recent = None, "", []
    for page in pages:
        if page["scene"] != scene:
            scene, last_action, recent, first = page["scene"], "", [], True
        else:
            first = False
        for index, item in enumerate(page["panels"]):
            speakers = [names[b["speaker"]] for b in item.get("balloons", []) if b["speaker"] in names and not b["off_panel"]]
            if item["prompt"].strip():
                last_action = item["prompt"]
            elif speakers:
                who = " and ".join(dict.fromkeys(speakers))
                item["prompt"] = f"{who}, reacting in the moment" + (f". Continuing from: {last_action}" if last_action else f", {scene}")
            else:
                item["prompt"] = f"An establishing view of {scene}" + (f". {last_action}" if last_action else "")
            if not item.get("camera"):
                if first and index == 0:
                    choice = WIDE
                elif speakers and not item["prompt"] == last_action:
                    choice = CLOSE
                else:
                    choice = MEDIUM
                if recent[-2:] == [choice, choice]:          # three identical framings in a row read flat
                    choice = MEDIUM if choice != MEDIUM else CLOSE
                item["camera"] = choice
                item["camera_suggested"] = True
            recent.append(item["camera"])
    return pages
