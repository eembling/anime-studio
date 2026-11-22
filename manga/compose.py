"""Lay out selected art and letter the balloons for proofs and export."""
import io
import math
import re
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps

from lettering import auto_positions

HERE = Path(__file__).resolve().parent
FONTS = HERE / "fonts"
WIDTH, HEIGHT = 1200, 1800

# Trim size in inches; pixels follow the chosen resolution. "web" keeps the original 1200 x 1800 page.
PRESETS = {
    "web": dict(label="Web / screen (1200 × 1800 px)", size=None),
    "b5": dict(label="B5 tankōbon (182 × 257 mm)", size=(7.165, 10.118)),
    "a5": dict(label="A5 (148 × 210 mm)", size=(5.827, 8.268)),
    "us": dict(label="US comic (6.625 × 10.25 in)", size=(6.625, 10.25)),
}
DPIS = (300, 600)
BLEED_IN = 0.125                                   # 3 mm
LAYOUTS = ("feature", "grid", "strips", "three", "tall", "splash")
TONES = ("color", "gray", "screentone")
MAX_PANELS = 9


def page_size(preset="web", dpi=300):
    """(width, height, bleed) in pixels for the trimmed page; bleed is added on every side when printing."""
    spec = PRESETS.get(preset or "web") or PRESETS["web"]
    if not spec["size"]:
        return WIDTH, HEIGHT, 0
    w, h = spec["size"]
    return round(w * dpi), round(h * dpi), round(BLEED_IN * dpi)


def rows_for(count, layout):
    if layout == "strips":
        return [1] * count
    if layout == "three":
        rows = [3] * (count // 3)
        if count % 3:
            rows = [count % 3] + rows
        return rows
    rows = ([1] if count % 2 else []) + [2] * (count // 2)
    if layout == "feature" and count >= 4 and count % 2 == 0:
        rows = [1] + [2] * ((count - 2) // 2) + [1]
    return rows


def frames(count, layout, direction, width=WIDTH, height=HEIGHT, weights=None):
    """Panel boxes (x, y, w, h) in reading order."""
    if not count:
        return []
    scale = width / WIDTH
    margin, gap = round(48 * scale), round(16 * scale)
    live_w, live_h = width - 2 * margin, height - 2 * margin
    rtl = direction == "rtl"
    if layout == "splash":
        boxes = [(margin, margin, live_w, live_h)]
        rest = count - 1
        if rest:
            inset_h = round(live_h * .24)
            inset_w = (live_w - gap * (rest + 1)) // rest
            y = margin + live_h - inset_h - gap
            for i in (reversed(range(rest)) if rtl else range(rest)):
                boxes.append((margin + gap + i * (inset_w + gap), y, inset_w, inset_h))
        return boxes
    if layout == "tall" and count >= 2:
        tall_w = round(live_w * .42)
        others = count - 1
        other_w = live_w - tall_w - gap
        tall_x = margin + live_w - tall_w if rtl else margin
        other_x = margin if rtl else margin + tall_w + gap
        boxes = [(tall_x, margin, tall_w, live_h)]
        unit = (live_h - gap * (others - 1)) / others
        for i in range(others):
            boxes.append((other_x, round(margin + i * (unit + gap)), other_w, round(unit)))
        return boxes
    rows = rows_for(count, "grid" if layout == "tall" else layout)
    default = [1.35 if i == 0 and layout == "feature" else 1 for i in range(len(rows))]
    if weights and len(weights) == len(rows) and all(isinstance(v, (int, float)) and v > 0 for v in weights):
        default = list(weights)
    unit = (live_h - gap * (len(rows) - 1)) / sum(default)
    boxes, y = [], margin
    for columns, weight in zip(rows, default):
        w = (live_w - gap * (columns - 1)) // columns
        for col in (reversed(range(columns)) if rtl else range(columns)):
            boxes.append((margin + col * (w + gap), round(y), w, round(unit * weight)))
        y += unit * weight + gap
    return boxes


def row_count(count, layout):
    return 0 if layout in ("splash", "tall") else len(rows_for(count, layout))


# ---- fonts ------------------------------------------------------------------------------

CJK = re.compile(r"[　-ヿ㐀-鿿가-힯＀-￯]")
_cache = {}


def font(size, text="", face="dialogue"):
    """Bundled comic faces for Latin text; a system face with CJK coverage for Japanese, Chinese or Korean."""
    cjk = bool(CJK.search(text or ""))
    key = (size, cjk, face)
    if key in _cache:
        return _cache[key]
    bundled = {"dialogue": FONTS / "ComicNeue-Bold.ttf", "sfx": FONTS / "Bangers-Regular.ttf"}[face]
    candidates = ([] if cjk else [bundled]) + [
        "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "arial.ttf"]
    for path in candidates:
        try:
            _cache[key] = ImageFont.truetype(str(path), size)
            return _cache[key]
        except OSError:
            continue
    _cache[key] = ImageFont.load_default(size=size)
    return _cache[key]


def wrap(draw, text, face, width):
    lines = []
    for paragraph in text.splitlines() or [""]:
        line = ""
        for word in paragraph.split():
            candidate = (line + " " + word).strip()
            if line and draw.textlength(candidate, font=face) > width:
                lines.append(line)
                line = ""
            if draw.textlength(word, font=face) <= width:
                line = (line + " " + word).strip()
            else:
                for char in word:
                    if line and draw.textlength(line + char, font=face) > width:
                        lines.append(line)
                        line = ""
                    line += char
        lines.append(line)
    return [l for l in lines if l] or [""]


def vertical_columns(text, rows):
    """Japanese-style vertical text: columns of characters, read right to left."""
    chars = [c for c in text if not c.isspace()]
    return [chars[i:i + rows] for i in range(0, len(chars), rows)] or [[]]


# ---- balloon geometry ---------------------------------------------------------------------

def _measure(draw, b, size, box, caps, vertical, page_w=WIDTH):
    x, y, w, h = box
    text = b["text"].upper() if caps and not CJK.search(b["text"]) else b["text"]
    if b["kind"] == "sfx":
        face = font(round(size * 2.4), text, "sfx")
        return dict(text=text, face=face, lines=[text], tw=draw.textlength(text, font=face), th=face.size, line_h=face.size)
    face = font(size, text)
    line_h = round(size * 1.22)
    if vertical and CJK.search(text):
        rows = max(3, int(h * .55 // line_h))
        cols = vertical_columns(text, rows)
        return dict(text=text, face=face, columns=cols, tw=len(cols) * line_h, th=max(len(c) for c in cols) * line_h, line_h=line_h)
    max_w = min(w * (.8 if b["kind"] == "caption" else .55), page_w * (.42 if b["kind"] == "caption" else .27))
    lines = wrap(draw, text, face, max(size * 4, max_w))
    return dict(text=text, face=face, lines=lines, tw=max(draw.textlength(l, font=face) for l in lines), th=len(lines) * line_h, line_h=line_h)


def _shape(b, m, size):
    pad = size * .7
    if b["kind"] == "caption":
        return m["tw"] + 2 * pad, m["th"] + 1.4 * pad
    if b["kind"] == "sfx":
        return m["tw"], m["th"]
    # An ellipse that holds a w x h text block needs about sqrt(2) times each side.
    return m["tw"] * 1.32 + 2 * pad, m["th"] * 1.36 + 1.6 * pad


def layout_balloons(draw, balloons, box, direction, start, size, caps=False, vertical=True, page_w=WIDTH):
    """Resolved balloon rectangles and tail tips in page pixels."""
    x, y, w, h = box
    out = []
    for b in auto_positions(balloons, direction, start):
        m = _measure(draw, b, size, box, caps, vertical, page_w)
        bw, bh = _shape(b, m, size)
        reach = 1.2 if b["kind"] == "shout" else 1.08 if b["kind"] == "thought" else 1   # spikes and bumps stick out
        ew, eh, pad = bw * reach, bh * reach, max(6, size * .5)
        cx = min(max(x + b["x"] * w, x + ew / 2 + pad), x + w - ew / 2 - pad) if ew < w - 2 * pad else x + w / 2
        cy = min(max(y + b["y"] * h, y + eh / 2 + pad), y + h - eh / 2 - pad) if eh < h - 2 * pad else y + h / 2
        if b["tail_x"] is not None and b["tail_y"] is not None:
            tail = (x + b["tail_x"] * w, y + b["tail_y"] * h)
        elif b["off_panel"]:
            tail = (cx + (bw * .9 if cx > x + w / 2 else -bw * .9), cy - bh * .2)
            tail = (x + w + size if tail[0] > x + w / 2 else x - size, tail[1])
        else:
            # Aim toward the panel's lower middle on the balloon's own side: where a speaker usually stands.
            tail = (cx + (x + w / 2 - cx) * .35, min(y + h * .62, cy + bh / 2 + h * .22, cy + bh / 2 + bh * 1.1))
        out.append(dict(b, cx=cx, cy=cy, bw=bw, bh=bh, tail=tail, m=m))
    return out


def fits(items, box):
    x, y, w, h = box
    return all(i["bw"] <= w - 8 and i["bh"] <= h - 8 for i in items) and sum(i["bw"] * i["bh"] for i in items) <= w * h * .6


# ---- drawing ------------------------------------------------------------------------------

def _ellipse_point(cx, cy, rx, ry, angle):
    return cx + rx * math.cos(angle), cy + ry * math.sin(angle)


def _tail(draw, i, line, kind):
    cx, cy, rx, ry = i["cx"], i["cy"], i["bw"] / 2, i["bh"] / 2
    tx, ty = i["tail"]
    angle = math.atan2((ty - cy) / max(ry, 1), (tx - cx) / max(rx, 1))
    dist = math.hypot(tx - cx, ty - cy)
    edge = math.hypot(*(_ellipse_point(0, 0, rx, ry, angle)))
    if dist <= edge + 4:
        return
    if kind == "thought":
        for step, r in ((.3, .26), (.6, .18), (.88, .11)):
            px = cx + (tx - cx) * (edge / dist + (1 - edge / dist) * step)
            py = cy + (ty - cy) * (edge / dist + (1 - edge / dist) * step)
            rr = ry * r
            draw.ellipse((px - rr, py - rr, px + rr, py + rr), fill="white", outline="black", width=line)
        return
    spread = .22 if kind != "shout" else .3
    a = _ellipse_point(cx, cy, rx * .92, ry * .92, angle - spread)
    c = _ellipse_point(cx, cy, rx * .92, ry * .92, angle + spread)
    # A slight curve: the tip bends toward the reading side.
    mid = ((a[0] + c[0]) / 2 + (tx - cx) * .15, (a[1] + c[1]) / 2 + (ty - cy) * .15)
    draw.polygon([a, mid, (tx, ty), c], fill="white", outline="black", width=line)


def _body(draw, i, line, dashed=False):
    cx, cy, rx, ry = i["cx"], i["cy"], i["bw"] / 2, i["bh"] / 2
    kind = i["kind"]
    if kind == "caption":
        draw.rectangle((cx - rx, cy - ry, cx + rx, cy + ry), fill="white", outline="black", width=line)
        return
    if kind == "shout":
        points = []
        spikes = max(14, int((rx + ry) / 9))
        for n in range(spikes * 2):
            angle = math.pi * n / spikes
            scale = 1.18 if n % 2 == 0 else .9
            points.append(_ellipse_point(cx, cy, rx * scale, ry * scale, angle))
        draw.polygon(points, fill="white", outline="black", width=line)
        return
    if kind == "thought":
        bumps = max(9, int((rx + ry) / 22))
        r = min(rx, ry) * .42
        for n in range(bumps):
            px, py = _ellipse_point(cx, cy, rx * .82, ry * .78, 2 * math.pi * n / bumps)
            draw.ellipse((px - r, py - r, px + r, py + r), fill="white", outline="black", width=line)
        draw.ellipse((cx - rx * .86, cy - ry * .8, cx + rx * .86, cy + ry * .8), fill="white")
        return
    draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill="white", outline=None if dashed else "black", width=line)
    if dashed:
        steps = max(24, int((rx + ry) / 6))
        for n in range(0, steps, 2):
            a0, a1 = 2 * math.pi * n / steps, 2 * math.pi * (n + 1) / steps
            draw.line([_ellipse_point(cx, cy, rx, ry, a0), _ellipse_point(cx, cy, rx, ry, a1)], fill="black", width=line)


def _text(draw, i):
    m, cx, cy = i["m"], i["cx"], i["cy"]
    if i["kind"] == "sfx":
        stroke = max(3, m["face"].size // 9)
        draw.text((cx, cy), m["text"], font=m["face"], fill="black", anchor="mm", stroke_width=stroke, stroke_fill="white")
        return
    if "columns" in m:
        total = len(m["columns"]) * m["line_h"]
        for col, chars in enumerate(m["columns"]):
            px = cx + total / 2 - (col + .5) * m["line_h"]
            for row, ch in enumerate(chars):
                draw.text((px, cy - m["th"] / 2 + (row + .5) * m["line_h"]), ch, font=m["face"], fill="black", anchor="mm")
        return
    top = cy - m["th"] / 2
    for n, line in enumerate(m["lines"]):
        anchor_x = cx - i["bw"] / 2 + i["bw"] * .07 if i["kind"] == "caption" else cx
        draw.text((anchor_x, top + (n + .5) * m["line_h"]), line, font=m["face"], fill="black", anchor="lm" if i["kind"] == "caption" else "mm")


def letter_panel(draw, items, line):
    """Tails first so every balloon body sits on top of every tail, then bodies, then words."""
    for i in items:
        if i["kind"] in ("speech", "shout", "whisper", "thought"):
            _tail(draw, i, line, i["kind"])
    for i in items:
        if i["kind"] != "sfx":
            _body(draw, i, line, dashed=i["kind"] == "whisper")
    for i in items:
        _text(draw, i)


# ---- tone -------------------------------------------------------------------------------

def tone(image, mode, dpi=300):
    if mode == "gray":
        return ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1).convert("RGB")
    if mode == "screentone":
        gray = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1)
        period = max(4, round(dpi / 50))           # about 50 lines per inch, like printed tone
        tile = Image.new("L", (period, period))
        for py in range(period):
            for px in range(period):
                d = math.hypot(px - period / 2 + .5, py - period / 2 + .5) / (period / math.sqrt(2))
                tile.putpixel((px, py), int(255 * min(1, d)))
        row = Image.new("L", (gray.width, period))
        for tx in range(0, gray.width, period):
            row.paste(tile, (tx, 0))
        threshold = Image.new("L", gray.size)
        for ty in range(0, gray.height, period):
            threshold.paste(row, (0, ty))
        dots = ImageChops.subtract(gray, threshold).point(lambda v: 255 if v > 0 else 0)
        ink = gray.point(lambda v: 0 if v < 48 else 255)          # keep solid blacks and line art
        paper = gray.point(lambda v: 255 if v > 225 else 0)       # keep clean whites
        return ImageChops.lighter(ImageChops.darker(dots, ink), paper).convert("RGB")
    return image


# ---- page ---------------------------------------------------------------------------------

def geometry(page, direction, settings=None):
    """Panel boxes and balloon rectangles as fractions of the page, for dragging in Studio."""
    s = settings or {}
    width, height, _ = page_size(s.get("page_preset", "web"), int(s.get("dpi", 300)))
    sheet = Image.new("RGB", (8, 8))
    draw = ImageDraw.Draw(sheet)
    boxes = frames(len(page["panels"]), page["layout"], direction, width, height, page.get("weights"))
    size, plans = lettering_plan(draw, page, boxes, direction, width, s)
    panels = []
    for panel, box, items in zip(page["panels"], boxes, plans):
        x, y, w, h = box
        panels.append(dict(id=panel["id"], box=[x / width, y / height, w / width, h / height],
                           balloons=[dict(id=i["id"], cx=i["cx"] / width, cy=i["cy"] / height, w=i["bw"] / width, h=i["bh"] / height,
                                          tail=[i["tail"][0] / width, i["tail"][1] / height], kind=i["kind"]) for i in items]))
    warnings, sides = [], {}
    for n, items in enumerate(plans, 1):
        for i in items:
            if i["kind"] in ("speech", "shout", "whisper", "thought") and i["speaker"] and not i["off_panel"]:
                x, _, w, _ = boxes[n - 1]
                side = "left" if i["cx"] < x + w * .45 else "right" if i["cx"] > x + w * .55 else None
                previous = sides.get(i["speaker"])
                if side and previous and previous[1] != side and previous[0] == n - 1:
                    warnings.append(dict(speaker=i["speaker"], panels=[n - 1, n],
                                         text=f"Panels {n-1} and {n}: this speaker's balloon switches sides. Keep them on one side of the conversation (the 180° rule) or move the balloon."))
                if side:
                    sides[i["speaker"]] = (n, side)
    gutters = []
    if row_count(len(page["panels"]), page["layout"]) > 1:
        tops = sorted({b[1] for b in boxes})
        gutters = [t / height for t in tops[1:]]
    return dict(width=width, height=height, size=size, panels=panels, gutters=gutters, warnings=warnings,
                rows=row_count(len(page["panels"]), page["layout"]))


def lettering_plan(draw, page, boxes, direction, width, s):
    """One type size for the whole page: the largest that fits every balloon in its panel."""
    caps, vertical = bool(s.get("lettering_caps")), s.get("vertical_cjk", True) is not False
    start_size = max(12, round(width * .024))
    minimum = max(10, round(width * .011))
    for size in range(start_size, minimum - 1, -1):
        plans = [layout_balloons(draw, p.get("balloons", []), box, direction, p.get("treatment", "auto"), size, caps, vertical, width)
                 for p, box in zip(page["panels"], boxes)]
        if all(fits(items, box) for items, box in zip(plans, boxes)):
            return size, plans
    for n, (items, box) in enumerate(zip(plans, boxes), 1):
        if not fits(items, box):
            raise ValueError(f"Panel {n} has too much lettering. Split the dialogue or choose a larger panel.")
    return minimum, plans


def compose(page, direction, media, proof=False, settings=None):
    """The lettered page as PNG bytes."""
    s = settings or {}
    dpi = int(s.get("dpi", 300))
    result = io.BytesIO()
    page_image(page, direction, media, proof, s).save(result, "PNG", dpi=(dpi, dpi))
    return result.getvalue()


def chapter_pdf(pages, direction, media, settings=None):
    s = settings or {}
    images = [page_image(p, direction, media, False, s) for p in pages]
    result = io.BytesIO()
    images[0].save(result, "PDF", save_all=True, append_images=images[1:], resolution=float(int(s.get("dpi", 300))))
    return result.getvalue()


def page_image(page, direction, media, proof=False, settings=None):
    s = settings or {}
    dpi = int(s.get("dpi", 300))
    width, height, bleed = page_size(s.get("page_preset", "web"), dpi)
    bleed = bleed if s.get("bleed") and not proof else 0
    sheet = Image.new("RGB", (width + 2 * bleed, height + 2 * bleed), "white")
    draw = ImageDraw.Draw(sheet)
    boxes = frames(len(page["panels"]), page["layout"], direction, width, height, page.get("weights"))
    size, plans = lettering_plan(draw, page, boxes, direction, width, s)
    line = max(2, round(width / 400))
    border = max(3, round(width / 300))
    for i, (panel, box) in enumerate(zip(page["panels"], boxes)):
        x, y, w, h = box
        x, y = x + bleed, y + bleed
        if panel["selected"]:
            with Image.open(media / panel["selected"]) as image:
                art = ImageOps.fit(image.convert("RGB"), (w, h), Image.Resampling.LANCZOS)
            sheet.paste(tone(art, s.get("tone", "color"), dpi), (x, y))
        elif proof:
            draw.rectangle((x, y, x + w, y + h), fill="#e4eaf0")
            draw.text((x + 22, y + 18), f"Panel {i+1}  /  Pencil plan", fill="#536272", font=font(max(14, width // 46)))
        else:
            raise ValueError(f"Choose artwork for panel {i+1} before exporting.")
        draw.rectangle((x, y, x + w - 1, y + h - 1), outline="black", width=border)
    for items in plans:
        if bleed:
            items = [dict(it, cx=it["cx"] + bleed, cy=it["cy"] + bleed, tail=(it["tail"][0] + bleed, it["tail"][1] + bleed)) for it in items]
        letter_panel(draw, items, line)
    return sheet
