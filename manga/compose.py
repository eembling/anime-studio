"""Lay out selected art and typeset the saved dialogue for preview and export."""
import io
from PIL import Image, ImageDraw, ImageFont, ImageOps

WIDTH, HEIGHT = 1200, 1800


def frames(count, layout, direction):
    if layout == "strips":
        rows = [1] * count
    else:
        rows = ([1] if count % 2 else []) + [2] * (count // 2)
        if layout == "feature" and count >= 4 and count % 2 == 0:
            rows = [1] + [2] * ((count - 2) // 2) + [1]
    boxes = []
    margin, gap = 48, 16
    # Give the opening panel extra space in a feature layout.
    weights = [1.35 if i == 0 and layout == "feature" else 1 for i in range(len(rows))]
    unit = (HEIGHT - 2 * margin - gap * (len(rows) - 1)) / sum(weights)
    y = margin
    for columns, weight in zip(rows, weights):
        w = (WIDTH - 2 * margin - gap * (columns - 1)) // columns
        for col in (reversed(range(columns)) if direction == "rtl" else range(columns)):
            x = margin + col * (w + gap)
            boxes.append((x, round(y), w, round(unit * weight)))
        y += unit * weight + gap
    return boxes


def font(size):
    for path in ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "DejaVuSans.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def wrap(draw, text, face, width):
    lines = []
    for paragraph in text.splitlines():
        line = ""
        for word in paragraph.split():
            candidate = line + word
            if line and draw.textlength(candidate, font=face) > width:
                lines.append(line)
                line = ""
            if draw.textlength(word, font=face) <= width:
                line += word
            else:
                for char in word:
                    if line and draw.textlength(line + char, font=face) > width:
                        lines.append(line.rstrip())
                        line = ""
                    line += char
            line += " "
        lines.append(line.strip())
    return lines


def compose(page, direction, media, proof=False):
    sheet = Image.new("RGB", (WIDTH, HEIGHT), "white")
    draw = ImageDraw.Draw(sheet)
    for i, (panel, box) in enumerate(zip(page["panels"], frames(len(page["panels"]), page["layout"], direction))):
        x, y, w, h = box
        text = panel["lettering"].strip()
        treatment = panel.get("treatment", "caption")
        balloon = treatment in ("upper-left", "upper-right")
        text_width = min(w - 48, max(235, int(w * .65))) if balloon else w - 48
        lines, band = [], 0
        if text:
            for size in range(30, 13, -2):
                face = font(size)
                lines = wrap(draw, text, face, text_width)
                band = len(lines) * (size + 8) + 28
                if band < h * .62:
                    break
            if band >= h - 40:
                raise ValueError(f"Panel {i+1} has too much lettering. Split the dialogue or choose a taller layout.")
        if panel["selected"]:
            with Image.open(media / panel["selected"]) as image:
                sheet.paste(ImageOps.fit(image.convert("RGB"), (w, h if balloon else h - band)), (x, y))
        elif proof:
            draw.rectangle((x, y, x+w, y+h-(0 if balloon else band)), fill="#e4eaf0")
            draw.text((x+22, y+18), f"Panel {i+1}  /  Pencil plan", fill="#536272", font=font(26))
        else:
            raise ValueError(f"Choose artwork for panel {i+1} before exporting.")
        if lines:
            if balloon:
                balloon_w = min(w - 24, int(max(draw.textlength(line, font=face) for line in lines)) + 36)
                left = x+12 if treatment == "upper-left" else x+w-balloon_w-12
                top = y+12
                tail_x = left+balloon_w//2
                draw.polygon([(tail_x-11,top+band-3),(tail_x-16,top+band+20),(tail_x+13,top+band-3)], fill="white", outline="black", width=3)
                draw.rounded_rectangle((left,top,left+balloon_w,top+band), radius=22, fill="white", outline="black", width=3)
                text_x = left + 18
            else:
                top = y+h-band
                text_x = x+22
                draw.line((x, top, x+w, top), fill="black", width=2)
            for j, line in enumerate(lines):
                draw.text((text_x, top+10+j*(size+8)), line, fill="black", font=face)
        draw.rectangle((x,y,x+w-1,y+h-1), outline="black", width=4)
    result = io.BytesIO()
    sheet.save(result, "PNG")
    return result.getvalue()
