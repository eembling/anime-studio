"""Structured keyframe and motion prompts for Anime Studio.

The keyframe workflow receives one positive prompt. This module decides what goes into it
and in what order, one labelled line each:

    reference map -> setting / cast -> scene -> placement -> acting -> camera ->
    direction and continuity -> style -> things to avoid (when the graph ignores negatives)
"""
import json

SPEAKING_KINDS = ('speech', 'shout', 'whisper')


def _sentence(text):
    text = ' '.join(str(text or '').split()).rstrip('.').strip()
    return text + '.' if text else ''


def asset_label(asset):
    kind = asset.get('kind') or 'character'
    desc = (asset.get('description') or '').strip().rstrip('.')
    noun = {'location': 'the location', 'prop': 'the prop'}.get(kind, 'the character')
    return f"{noun} {asset['name']}" + (f" ({desc})" if desc else '')


def placement(box):
    x, y, h = box['x'], box['y'], box.get('height', .6)
    side = 'on the left' if x < .36 else 'on the right' if x > .64 else 'in the centre'
    depth = 'close to the camera, large in frame' if h >= .75 else 'in the background, small in frame' if h <= .35 else 'in the mid-ground'
    vertical = ', high in the frame' if y < .3 else ', low in the frame' if y > .75 else ''
    return f'{side}, {depth}{vertical}'


def speakers(lines, tagged_ids):
    """[(character_id, direction)] for dialogue lines whose speaker is in the shot, first line per character."""
    seen, out = set(), []
    for line in lines:
        cid = line.get('character_id')
        if cid and cid in tagged_ids and cid not in seen and (line.get('text') or '').strip():
            seen.add(cid)
            out.append((cid, (line.get('direction') or '').strip()))
    return out


def ignores_negative(graph):
    """True when every sampler runs at CFG 1 or lower, where the negative conditioning has no effect."""
    cfgs = [n['inputs']['cfg'] for n in (graph or {}).values()
            if isinstance(n, dict) and isinstance(n.get('inputs', {}).get('cfg'), (int, float))]
    return bool(cfgs) and all(c <= 1.0 for c in cfgs)


def compose_keyframe(*, chosen, assets, shot, lines, direction, context, style, avoid=''):
    """chosen: [(ident, label, path)] sent as Picture 1..N; assets: the shot's cast after scene looks."""
    sent = {ident for ident, _, _ in chosen}
    parts = [_sentence(f'Picture {i + 1} is {label}') for i, (_, label, _) in enumerate(chosen)]
    unseen = [asset_label(a) for a in assets if f"cast-{a['id']}" not in sent and (a.get('description') or '').strip()]
    if unseen:
        parts.append(_sentence('Also in this shot: ' + '; '.join(unseen)))
    places = [a['name'] for a in assets if a.get('kind') == 'location']
    people = [a for a in assets if (a.get('kind') or 'character') == 'character']
    if places:
        parts.append(_sentence('Setting: ' + ', '.join(places)))
    if people:
        parts.append(_sentence('Characters in frame: ' + ', '.join(a['name'] for a in people)))
    parts.append(_sentence(shot.get('keyframe_prompt')))

    by_id = {a['id']: a for a in assets}
    boxes = [b for b in (direction.get('boxes') or []) if b.get('asset_id') in by_id]
    if boxes:
        parts.append(_sentence('Placement: ' + '; '.join(f"{by_id[b['asset_id']]['name']} {placement(b)}" for b in boxes)))

    talking = speakers(lines, {a['id'] for a in people})
    if talking:
        acting = [f"{by_id[cid]['name']} is speaking" + (f", {d.lower()}" if d else '') + ', mouth open mid-word' for cid, d in talking]
        done = {cid for cid, _ in talking}
        listeners = [a['name'] for a in people if a['id'] not in done]
        if listeners:
            acting.append(', '.join(listeners) + (' listens' if len(listeners) == 1 else ' listen'))
        parts.append(_sentence('Acting: ' + '; '.join(acting)))

    if shot.get('camera'):
        parts.append(_sentence('Camera: ' + shot['camera']))
    for value in (direction.get('notes'), context.get('lighting'), context.get('notes'),
                  '. '.join(v['notes'] for v in context.get('variants', []) if v.get('notes'))):
        parts.append(_sentence(value))
    parts.append(_sentence(style))
    if avoid.strip():
        parts.append(_sentence('Avoid: ' + avoid))
    return '\n'.join(p for p in parts if p)


def motion_acting(lines, tagged_ids, names):
    """Short cue for animation prompts so the right character's mouth moves."""
    who = [names[cid] for cid, _ in speakers(lines, tagged_ids) if cid in names]
    if not who:
        return ''
    return ' and '.join(who) + (' talks' if len(who) == 1 else ' talk in turn') + ', lips moving naturally'


def tagged(shot):
    ids = shot['asset_ids']
    return set(ids if isinstance(ids, list) else json.loads(ids or '[]'))
