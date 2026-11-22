"""Panel prompting and speech-bubble tagging for manga-style keyframes.

Everything here is model-independent text composition. The keyframe workflow still
receives one positive prompt; this module decides what goes into it and in what order:

    reference map -> who/where -> action -> placement -> dialogue acting -> camera ->
    direction/continuity -> lettering plan -> style

Bubbles are tagged per shot (panel) before generation: who says it, what they say,
the balloon style and where it sits. They drive both the acting in the picture
(the speaker's expression and open mouth) and the lettering plan (clear space for
balloons, or balloons drawn by the model).
"""
import json
import uuid

BUBBLE_KINDS = ('speech', 'thought', 'shout', 'whisper', 'narration', 'sfx')
SPOKEN_KINDS = ('speech', 'thought', 'shout', 'whisper')
POSITIONS = ('auto', 'top-left', 'top', 'top-right', 'left', 'right', 'bottom-left', 'bottom', 'bottom-right')
LETTERING_MODES = ('off', 'space', 'render')
READING_DIRECTIONS = ('rtl', 'ltr')
MAX_BUBBLES = 12
MAX_TEXT = 400

DEFAULT_SETTINGS = {'lettering_mode': 'off', 'reading_direction': 'rtl'}

ACTING = {
    'speech': '{name} is speaking{emotion}, mouth open mid-word',
    'shout': '{name} is shouting{emotion}, mouth wide open, intense body language',
    'whisper': '{name} is whispering{emotion}, leaning in, mouth barely open',
    'thought': '{name} is thinking silently{emotion}, mouth closed, inward gaze',
}
BALLOON = {
    'speech': 'a white oval speech balloon with a black outline',
    'shout': 'a jagged spiky burst balloon',
    'whisper': 'a speech balloon with a dashed outline',
    'thought': 'a cloud-shaped thought balloon with small trailing bubbles',
    'narration': 'a rectangular caption box',
}
PLACE_WORDS = {'top-left': 'upper left', 'top': 'top centre', 'top-right': 'upper right', 'left': 'left side',
               'right': 'right side', 'bottom-left': 'lower left', 'bottom': 'bottom centre', 'bottom-right': 'lower right'}


def _clean(text, limit):
    return ' '.join(str(text or '').split())[:limit]


def _sentence(text):
    text = (text or '').strip().rstrip('.').strip()
    return text + '.' if text else ''


def _quote(text):
    return text.replace('"', "'")


def parse(shot):
    raw = shot.get('bubbles') if isinstance(shot, dict) else None
    if isinstance(raw, list):
        return raw
    try:
        value = json.loads(raw or '[]')
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def validate(st, shot, raw):
    """Clean a bubble list from the client. Returns (bubbles, asset_ids_to_tag)."""
    if not isinstance(raw, list):
        raise ValueError('Speech bubbles must be a list.')
    if len(raw) > MAX_BUBBLES:
        raise ValueError(f'Use at most {MAX_BUBBLES} speech bubbles in one panel.')
    tagged = shot.get('asset_ids') if isinstance(shot.get('asset_ids'), list) else json.loads(shot.get('asset_ids') or '[]')
    add, out, seen = [], [], set()
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f'Bubble {index} is not valid.')
        kind = str(item.get('kind') or 'speech')
        if kind not in BUBBLE_KINDS:
            raise ValueError(f'Bubble {index} has an unknown style.')
        position = str(item.get('position') or 'auto')
        if position not in POSITIONS:
            raise ValueError(f'Bubble {index} has an unknown position.')
        text = _clean(item.get('text'), MAX_TEXT)
        if not text:
            raise ValueError(f'Bubble {index} has no words. Remove it or write what is said.')
        character_id = item.get('character_id')
        character_id = int(character_id) if character_id not in (None, '', 0, '0') else None
        off_panel = bool(item.get('off_panel'))
        if kind in SPOKEN_KINDS and character_id is None:
            raise ValueError(f'Bubble {index}: choose who says it, or use a narration or sound-effect box.')
        if character_id is not None:
            row = st.db.one("SELECT id, kind FROM assets WHERE id=?", (character_id,))
            if not row:
                raise ValueError(f'Bubble {index}: that character was deleted.')
            if row['kind'] != 'character':
                raise ValueError(f'Bubble {index}: choose a character, not a location or prop.')
            if not off_panel and character_id not in tagged and character_id not in add:
                add.append(character_id)
        bid = str(item.get('id') or '')[:24] or uuid.uuid4().hex[:12]
        if bid in seen:
            bid = uuid.uuid4().hex[:12]
        seen.add(bid)
        out.append({'id': bid, 'kind': kind, 'character_id': character_id, 'text': text, 'position': position,
                    'emotion': _clean(item.get('emotion'), 80), 'off_panel': off_panel and character_id is not None})
    if len(tagged) + len(add) > 8:
        raise ValueError('A shot can tag at most 8 cast members and locations. Untag one, or mark the speaker off-panel.')
    return out, add


def effective(st, shot, lines=None):
    """The shot's bubbles, or its dialogue lines treated as speech bubbles when none are tagged yet."""
    saved = parse(shot)
    if saved:
        return saved, 'tagged'
    lines = st.shot_lines(shot) if lines is None else lines
    derived = [{'id': 'line-' + str(line.get('line_id') or i), 'kind': 'speech', 'character_id': line.get('character_id'),
                'text': line['text'], 'position': 'auto', 'emotion': _clean(line.get('direction'), 80), 'off_panel': False}
               for i, line in enumerate(lines) if (line.get('text') or '').strip()]
    return derived, 'dialogue' if derived else 'none'


def asset_label(asset):
    kind = asset.get('kind') or 'character'
    desc = (asset.get('description') or '').strip().rstrip('.')
    noun = {'location': 'the location', 'prop': 'the prop'}.get(kind, 'the character')
    return f"{noun} {asset['name']}" + (f" ({desc})" if desc else '')


def _placement(box):
    x, y, h = box['x'], box['y'], box.get('height', .6)
    side = 'on the left' if x < .36 else 'on the right' if x > .64 else 'in the centre'
    depth = 'close to the camera, large in frame' if h >= .75 else 'in the background, small in frame' if h <= .35 else 'in the mid-ground'
    vertical = ', high in the frame' if y < .3 else ', low in the frame' if y > .75 else ''
    return f'{side}, {depth}{vertical}'


def _side(box):
    if not box:
        return None
    return 'left' if box['x'] < .45 else 'right' if box['x'] > .55 else ''


def auto_positions(bubbles, boxes, reading):
    """Place balloons in reading order: first balloon where the eye enters the panel."""
    order = ['top-right', 'top-left', 'right', 'left', 'bottom-right', 'bottom-left'] if reading == 'rtl' else \
            ['top-left', 'top-right', 'left', 'right', 'bottom-left', 'bottom-right']
    used, out = {b['position'] for b in bubbles if b['position'] != 'auto'}, []
    for bubble in bubbles:
        position = bubble['position']
        if position == 'auto':
            if bubble['kind'] == 'narration':
                position = order[0] if order[0] not in used else next((p for p in order if p not in used), 'top')
            else:
                side = _side(boxes.get(bubble.get('character_id')))
                wanted = f'top-{side}' if side else None
                position = wanted if wanted and wanted not in used else next((p for p in order if p not in used), 'top')
            used.add(position)
        out.append(dict(bubble, position=position))
    return out


def compose_keyframe(*, chosen, assets, shot, direction, context, bubbles, settings, names):
    """Build the structured keyframe prompt.

    chosen: [(ident, label, path)] references actually sent as Picture 1..N
    assets: shot assets (characters, locations, props) after scene looks
    names: {asset_id: name} for every character a bubble can name (includes off-panel speakers)
    """
    mode = settings.get('lettering_mode') if settings.get('lettering_mode') in LETTERING_MODES else 'off'
    reading = settings.get('reading_direction') if settings.get('reading_direction') in READING_DIRECTIONS else 'rtl'
    sent = {ident for ident, _, _ in chosen}
    parts = [_sentence(f'Picture {i + 1} is {label}') for i, (_, label, _) in enumerate(chosen)]
    unseen = [asset_label(a) for a in assets if f"cast-{a['id']}" not in sent and (a.get('description') or '').strip()]
    if unseen:
        parts.append(_sentence('Also in this shot: ' + '; '.join(unseen)))
    places = [a['name'] for a in assets if a.get('kind') == 'location']
    people = [a['name'] for a in assets if (a.get('kind') or 'character') == 'character']
    if places:
        parts.append(_sentence('Setting: ' + ', '.join(places)))
    if people:
        parts.append(_sentence('Characters in frame: ' + ', '.join(people)))
    parts.append(_sentence(shot.get('keyframe_prompt')))

    boxes = {b['asset_id']: b for b in (direction.get('boxes') or [])}
    placed = [f"{names.get(aid) or next((a['name'] for a in assets if a['id'] == aid), '')} {_placement(box)}"
              for aid, box in boxes.items() if any(a['id'] == aid for a in assets)]
    if placed:
        parts.append(_sentence('Placement: ' + '; '.join(p for p in placed if p.strip())))

    in_frame = {a['id'] for a in assets}
    bubbles = [dict(b, off_panel=bool(b.get('off_panel')) or (b.get('character_id') is not None and b['character_id'] not in in_frame))
               for b in bubbles]
    speakers = [b for b in bubbles if b['kind'] in SPOKEN_KINDS and b.get('character_id') and not b.get('off_panel')]
    acting, done = [], set()
    for b in speakers:
        if b['character_id'] in done or not names.get(b['character_id']):
            continue
        done.add(b['character_id'])
        emotion = f", {b['emotion'].lower()}" if b.get('emotion') else ''
        acting.append(ACTING[b['kind']].format(name=names[b['character_id']], emotion=emotion))
    listeners = [a['name'] for a in assets if (a.get('kind') or 'character') == 'character' and a['id'] not in done] if acting else []
    if listeners:
        acting.append(', '.join(listeners) + (' listens' if len(listeners) == 1 else ' listen'))
    if acting:
        parts.append(_sentence('Acting: ' + '; '.join(acting)))
    off = sorted({names[b['character_id']] for b in bubbles if b.get('off_panel') and names.get(b.get('character_id'))})
    if off:
        parts.append(_sentence(', '.join(off) + ' speaks from outside the frame and is not shown'))

    if shot.get('camera'):
        parts.append(_sentence('Camera: ' + shot['camera']))
    for value in (direction.get('notes'), context.get('lighting'), context.get('notes'),
                  '. '.join(v['notes'] for v in context.get('variants', []) if v.get('notes'))):
        parts.append(_sentence(value))

    placed_bubbles = auto_positions(bubbles, boxes, reading) if bubbles else []
    if mode == 'space' and placed_bubbles:
        areas = list(dict.fromkeys(PLACE_WORDS[b['position']] for b in placed_bubbles))
        parts.append(_sentence('Composition for lettering: keep the ' + ', '.join(areas) +
                               ' of the frame as calm, uncluttered background with no faces or important detail there'))
    if mode == 'space' or (mode == 'render' and not placed_bubbles):
        parts.append('Do not draw any text, letters, speech balloons, captions or sound effects.')
    elif mode == 'render':
        drawn = []
        for b in placed_bubbles:
            where = PLACE_WORDS[b['position']]
            words = _quote(b['text'])
            if b['kind'] == 'sfx':
                drawn.append(f'large hand-drawn manga sound effect lettering reading "{words}" in the {where}')
                continue
            who = names.get(b.get('character_id'))
            tail = '' if b['kind'] == 'narration' or not who else \
                   f', its tail pointing toward {who} outside the frame' if b.get('off_panel') else f', its tail pointing to {who}'
            drawn.append(f'{BALLOON[b["kind"]]} in the {where}{tail}, containing the text "{words}"')
        parts.append(_sentence('Lettering: draw ' + '; '.join(drawn)))
        parts.append('Use clean, legible comic lettering, spelled exactly as quoted. No other text anywhere.')
    parts.append(_sentence(settings.get('style_prompt')))
    return '\n'.join(p for p in parts if p)


def motion_acting(st, shot):
    """Short speaking cue for animation prompts, so the right character's mouth moves."""
    bubbles, _ = effective(st, shot)
    tagged = shot['asset_ids'] if isinstance(shot['asset_ids'], list) else json.loads(shot['asset_ids'] or '[]')
    names = []
    for b in bubbles:
        if b['kind'] in ('speech', 'shout', 'whisper') and b.get('character_id') in tagged and not b.get('off_panel'):
            row = st.db.one("SELECT name FROM assets WHERE id=?", (b['character_id'],))
            if row and row['name'] not in names:
                names.append(row['name'])
    if not names:
        return ''
    return ' and '.join(names) + (' talks' if len(names) == 1 else ' talk in turn') + ', lips moving naturally'


def signature(st, shot):
    """Keyframe signature additions; empty for shots that use no manga features (keeps old takes current)."""
    settings = st.settings()
    saved = parse(shot)
    mode = settings.get('lettering_mode') or 'off'
    if not saved and mode == 'off':
        return {}
    return {'bubbles': saved, 'lettering_mode': mode, 'reading_direction': settings.get('reading_direction') or 'rtl'}
