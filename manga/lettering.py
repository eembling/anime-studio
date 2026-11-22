"""Speech balloons: the per-panel data model, script conversion and placement.

A panel's lettering is a list of balloons in reading order:

    {"id", "kind", "speaker", "text", "x", "y", "tail_x", "tail_y", "off_panel"}

kind      speech | thought | shout | whisper | caption | sfx
speaker   cast id of a character, or "" for captions and sound effects
x, y      balloon centre as a fraction of the panel, or None for automatic placement
tail_*    tail tip as a fraction of the panel, or None to aim at the speaker side
"""
import re
from uuid import uuid4

KINDS = ("speech", "thought", "shout", "whisper", "caption", "sfx")
SPOKEN = ("speech", "thought", "shout", "whisper")
MAX_BALLOONS = 12
MAX_TEXT = 600

SHOUT = re.compile(r"\b(shout|shouts|shouting|yell|yells|yelling|scream|screams|screaming|roar|roars)\b", re.I)
WHISPER = re.compile(r"\b(whisper|whispers|whispering|quietly|softly|under (?:her|his|their) breath)\b", re.I)
THOUGHT = re.compile(r"\b(thinks?|thinking|thought|internal|v\.?o\.?|inner)\b", re.I)


def uid():
    return uuid4().hex


def kind_for(speaker, direction=""):
    label = (speaker or "").strip().upper()
    if label == "CAPTION":
        return "caption"
    if label == "SFX":
        return "sfx"
    if THOUGHT.search(direction or ""):
        return "thought"
    if SHOUT.search(direction or ""):
        return "shout"
    if WHISPER.search(direction or ""):
        return "whisper"
    return "speech"


def balloon(kind="speech", speaker="", text="", **extra):
    item = dict(id=uid(), kind=kind, speaker=speaker, text=text, x=None, y=None, tail_x=None, tail_y=None, off_panel=False)
    item.update(extra)
    return item


def from_lettering(text, assets):
    """Turn legacy 'NAME: line' lettering into balloons, matching names to cast entries."""
    from script_dialogue import dialogue_line
    by_name = {a["name"].casefold(): a for a in assets if a.get("kind", "character") == "character"}
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        entry = dialogue_line(line)
        label = line.partition(":")[0].strip().upper()
        if label in ("CAPTION", "SFX"):
            out.append(balloon(label.lower(), "", line.partition(":")[2].strip()))
        elif entry:
            match = by_name.get(entry["speaker"].casefold())
            out.append(balloon(kind_for(entry["speaker"], entry.get("direction", "")), match["id"] if match else "", entry["text"]))
        elif out and out[-1]["kind"] in SPOKEN:
            out[-1]["text"] += " " + line          # continuation of the previous line
        else:
            out.append(balloon("caption", "", line))
    return out


def plain(balloons, assets):
    """Readable 'NAME: line' text for previews and the planning review."""
    names = {a["id"]: a["name"] for a in assets}
    return "\n".join((b["kind"].upper() if b["kind"] in ("caption", "sfx") else names.get(b["speaker"], "?")) + ": " + b["text"]
                     for b in balloons)


def _fraction(value, name, index):
    if value is None or value == "":
        return None
    value = float(value)
    if not -0.5 <= value <= 1.5:
        raise ValueError(f"Balloon {index}: {name} is outside the panel.")
    return round(value, 4)


def validate(raw, assets, existing=()):
    if not isinstance(raw, list):
        raise ValueError("Balloons must be a list.")
    if len(raw) > MAX_BALLOONS:
        raise ValueError(f"Use at most {MAX_BALLOONS} balloons in one panel.")
    by_id = {a["id"]: a for a in assets}
    old = {b["id"]: b for b in existing}
    out, seen = [], set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Balloon {index} is not valid.")
        kind = item.get("kind") or "speech"
        if kind not in KINDS:
            raise ValueError(f"Balloon {index} has an unknown style.")
        text = " ".join(str(item.get("text") or "").split())[:MAX_TEXT] if kind != "caption" else str(item.get("text") or "").strip()[:MAX_TEXT]
        if not text:
            raise ValueError(f"Balloon {index} has no words. Remove it or write what is said.")
        speaker = item.get("speaker") or ""
        if kind in SPOKEN:
            if not speaker:
                raise ValueError(f"Balloon {index}: choose who says it, or make it a caption or sound effect.")
            if speaker not in by_id:
                raise ValueError(f"Balloon {index}: that cast entry no longer exists.")
            if by_id[speaker]["kind"] != "character":
                raise ValueError(f"Balloon {index}: choose a character, not a location or prop.")
        else:
            speaker = ""
        bid = str(item.get("id") or "")
        if not re.fullmatch(r"[a-f0-9]{32}", bid) or bid in seen:
            bid = uid()
        seen.add(bid)
        previous = old.get(bid, {})
        b = balloon(kind, speaker, text, id=bid, off_panel=bool(item.get("off_panel")) and kind in SPOKEN)
        for key in ("x", "y", "tail_x", "tail_y"):
            b[key] = _fraction(item[key], key, index) if key in item else previous.get(key)
        out.append(b)
    return out


def speakers_needing_cast(balloons, cast):
    return [b["speaker"] for b in balloons if b["speaker"] and not b["off_panel"] and b["speaker"] not in cast]


# ---- placement -------------------------------------------------------------------------

def slot_order(direction, start="auto"):
    """Balloon centres in reading order, as panel fractions."""
    right = [(.73, .17), (.27, .17), (.75, .4), (.25, .4), (.5, .1), (.73, .64), (.27, .64)]
    left = [(1 - x, y) for x, y in right]
    first_right = start == "upper-right" if start in ("upper-right", "upper-left") else direction == "rtl"
    return right if first_right else left


def side_words(x, y):
    horizontal = "left" if x < .4 else "right" if x > .6 else "centre"
    vertical = "upper" if y < .35 else "lower" if y > .65 else "middle"
    return f"{vertical} {horizontal}".replace("middle centre", "centre")


def auto_positions(balloons, direction, start="auto"):
    """Fill in missing x/y in reading order without stacking two balloons on one spot."""
    order = slot_order(direction, start)
    taken = [(b["x"], b["y"]) for b in balloons if b["x"] is not None and b["y"] is not None]
    out, cursor = [], 0
    for b in balloons:
        b = dict(b)
        if b["kind"] == "caption" and b["x"] is None:
            corner = order[0]
            b["x"], b["y"] = (.5 + (corner[0] - .5) * 1.25, .08)
        elif b["x"] is None or b["y"] is None:
            while cursor < len(order) - 1 and any(abs(order[cursor][0] - x) < .15 and abs(order[cursor][1] - y) < .12 for x, y in taken):
                cursor += 1
            b["x"], b["y"] = order[min(cursor, len(order) - 1)]
            cursor += 1
        taken.append((b["x"], b["y"]))
        out.append(b)
    return out


def prompt_lines(balloons, cast_ids, names, direction, start="auto"):
    """Acting and clear-space instructions for the illustration prompt."""
    lines, done = [], []
    for b in balloons:
        who = b["speaker"]
        if b["kind"] not in SPOKEN or not who or b["off_panel"] or who not in cast_ids or who in done or who not in names:
            continue
        done.append(who)
        verb = {"speech": "is speaking, mouth open mid-word", "shout": "is shouting, mouth wide open, intense body language",
                "whisper": "is whispering, leaning in, mouth barely open", "thought": "is lost in thought, mouth closed"}[b["kind"]]
        lines.append(f"{names[who]} {verb}")
    listeners = [names[c] for c in cast_ids if c in names and c not in done]
    if lines and listeners:
        lines.append(", ".join(listeners) + (" listens and reacts" if len(listeners) == 1 else " listen and react"))
    out = []
    if lines:
        out.append("Acting: " + "; ".join(lines) + ".")
    off = sorted({names[b["speaker"]] for b in balloons if b["off_panel"] and b["speaker"] in names})
    if off:
        out.append(", ".join(off) + " speaks from outside the frame and is not shown.")
    placed = [b for b in auto_positions(balloons, direction, start) if b["kind"] != "sfx"]
    if placed:
        areas = list(dict.fromkeys(side_words(b["x"], b["y"]) for b in placed))
        out.append("Leave the " + ", ".join(areas) + " of the panel as calm, uncluttered background with no faces or important detail there.")
    return out
