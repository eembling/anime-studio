"""Shared Markdown/screenplay parsing and reviewed recovery of missed dialogue."""
import hashlib
import json
import re

METADATA = {'episode conflict','resolution','season thread','running gags introduced','title','duration','camera','shot','scene','action','notes','sequence','montage','sfx','music','transition','episode','chapter'}


def dialogue_line(value):
    prefix, colon, text = value.strip().partition(':')
    if not colon or len(prefix) > 90:
        return None
    # Single-italic action paragraphs are not speaker labels.
    if prefix.startswith('*') and not prefix.startswith('**'):
        return None
    label = re.sub(r'\*\*|__|`', '', prefix).strip()
    match = re.fullmatch(r"([^\W\d_][\w .’'/-]{0,50}?)(?:\s*\(([^)]*)\))?", label)
    if not match:
        return None
    speaker = match[1].strip()
    if speaker.casefold() in METADATA:
        return None
    text = text.strip()
    for mark in ('**','__'):
        if prefix.startswith(mark) and not prefix.endswith(mark) and text.startswith(mark):
            text = text[len(mark):].lstrip()
    directions = [match[2]] if match[2] else []
    def stage(m):
        directions.append(m[1]);return ' '
    text = re.sub(r'\*\(([^)]*)\)\*', stage, text)
    # A leading parenthesis in screenplay dialogue is a delivery direction.
    text = re.sub(r'^\s*\(([^)]*)\)\s*', stage, text)
    text = re.sub(r'\*\*|__|`', '', text)
    text = ' '.join(text.split()).strip()
    if not text:
        return None
    return {'speaker':speaker,'text':text,'direction':'; '.join(d.strip() for d in directions if d.strip())}


def dialogue_entries(text):
    entries = []
    for raw in text.strip().splitlines():
        if not raw.strip():continue
        entry = dialogue_line(raw)
        if entry:
            entries.append(entry)
        elif entries:
            if raw.strip().startswith(('*','_')) and raw.strip().endswith(('*','_')):
                entries[-1]['direction'] += ('; ' if entries[-1]['direction'] else '') + raw.strip('*_ ()')
            else:
                entries[-1]['text'] += ' ' + raw.strip()
        else:
            return []
    return entries


FIELD_NAMES = {
    'keyframe': 'keyframe', 'keyframe prompt': 'keyframe', 'opening frame': 'keyframe',
    'action': 'action', 'motion': 'motion', 'motion prompt': 'motion', 'animation': 'motion',
    'direction': 'direction', 'performance': 'direction', 'blocking': 'direction',
    'camera': 'camera', 'framing': 'camera', 'shot size': 'camera',
    'location': 'location', 'setting': 'location', 'lighting': 'lighting', 'mood': 'mood',
    'duration': 'duration', 'length': 'duration', 'cast': 'cast', 'characters': 'cast',
    'dialogue': 'dialogue', 'notes': 'notes', 'sfx': 'sfx', 'sound': 'sfx', 'music': 'music',
    'kind': 'kind',
}
CAMERA_CUES = {label.casefold():label for label in (
    'Wide shot', 'Establishing shot', 'Medium shot', 'Medium close-up', 'Close-up',
    'Extreme close-up', 'Insert', 'Over the shoulder', 'Reverse angle', 'POV', 'Close on',
)}
METADATA.update(FIELD_NAMES)
METADATA.update(CAMERA_CUES)
SCENE = re.compile(r'^(?:INT\.?\s*/\s*EXT\.?|EXT\.?\s*/\s*INT\.?|I/E\.?|INT\.|EXT\.)\s+|^SCENE(?:\s+\d+)?\s*[:—–-]', re.I)
SHOT = re.compile(r'^SHOT(?:\s+([\w.-]+))?\s*(?:[:—–-]\s*(.*))?$', re.I)
SECTION = re.compile(r'^(?:episode\b|act\s+(?:[A-Z]|\d+|[IVX]+)\b|cold open\b|opening titles\b|end credits\b|teaser\b|sequence\b)', re.I)
TRANSITION = re.compile(r'^(?:(?:SMASH |HARD |MATCH )?CUT TO|DISSOLVE TO|FADE (?:IN|OUT|TO BLACK)|THE END)\s*[:.]?$', re.I)
OFFSCREEN = re.compile(r'\b(?:V\.?\s*O|O\.?\s*S|voice[ -]?over|off[ -]?screen)\b', re.I)


def clean_script_text(value):
    return re.sub(r'\*\*|__|(?<!\w)\*|\*(?!\w)|`', '', value).strip()


def join_details(*values):
    return '. '.join(dict.fromkeys(value.strip().rstrip('.') for value in values if value and value.strip())).strip()


def screenplay_cue(raw, next_line):
    plain = clean_script_text(raw)
    if not next_line or next_line.startswith(('#', '*')) or ':' in plain or TRANSITION.fullmatch(plain):
        return None
    name = plain.partition('(')[0].strip()
    if not name.isupper() or name.endswith('.') or not re.fullmatch(r"[^\W\d_][\w .’'-]{0,46}", name) or len(name.split()) > 5:
        return None
    if SECTION.match(name) or SCENE.match(name) or SHOT.fullmatch(name) or name.casefold() in METADATA:
        return None
    if dialogue_line(next_line) or SCENE.match(clean_script_text(next_line)):
        return None
    return plain


def script_blocks(script, default_duration=4.0):
    """Read screenplay beats and labeled shot briefs with shared, editable field defaults."""
    scene, title, context, previous_picture = '', '', {}, ''
    blocks, fields, actions, entries = [], {}, [], []
    explicit, dialogue_open, field_open = False, False, None
    pending_cue, pending_direction = None, []

    def flush():
        nonlocal title, previous_picture, fields, actions, entries, dialogue_open, field_open
        action = join_details(*fields.get('action', []), *actions)
        visual = join_details(*fields.get('keyframe', []))
        motion = join_details(*fields.get('motion', []))
        direction = join_details(*fields.get('direction', []))
        if not (entries or action or visual or motion or direction):
            titled = SHOT.fullmatch(title) if title else None
            if not titled or not titled[2]:
                return
            action = titled[2]
        dialogue = '\n'.join(f"{entry['speaker']}: {entry['text']}" for entry in entries)
        voiced = list(dict.fromkeys(entry['speaker'] for entry in entries if not OFFSCREEN.search(entry['direction'])))
        visible = ' and '.join(voiced)
        opening = visual or (re.split(r'(?<=[.!?])\s+|\s+(?:then|followed by)\s+', action, maxsplit=1, flags=re.I)[0] if action else '')
        if not opening:
            opening = previous_picture if entries else ''
        if visible and not visual:
            opening = join_details(f'{visible} in frame, expression poised before speaking', opening)
        surroundings = join_details(*(join_details(*(fields.get(key) or context.get(key, []))) for key in ('location', 'lighting', 'mood')))
        scene_hint = f'Scene: {scene}' if scene else ''
        keyframe = join_details(opening or title, surroundings, scene_hint)
        if motion:
            performance = motion
        elif action:
            performance = action
        else:
            performance = ''
        if visible:
            delivery = join_details(*(f"{entry['speaker']}: {entry['direction']}" for entry in entries
                                       if entry['direction'] and not OFFSCREEN.search(entry['direction'])))
            if not motion:
                performance = join_details(performance, f'{visible} speaks with natural mouth movement', delivery)
        if entries and not visible and not performance:
            performance = 'Hold the opening composition during off-screen dialogue'
        animation = join_details(performance or 'Hold the opening composition', direction,
                                 'Maintain character appearance and the established lighting')
        camera = join_details(*fields.get('camera', [])) or ('Medium close-up, steady camera' if visible and not visual else '')
        notes = '\n'.join(f'{label}: {note}' for name, label in (('notes','Note'), ('sfx','Sound effect'), ('music','Music'))
                          for note in fields.get(name, []))
        requested = ' '.join(fields.get('duration', []))
        match = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:s|sec(?:onds?)?\.?)?', requested, re.I)
        words = sum(len(entry['text'].split()) for entry in entries)
        inferred = round(max(2, words/2.6 + .8 + max(0, len(entries)-1)*.3), 1) if entries else default_duration
        duration = min(3600, max(.5, float(match[1]))) if match else inferred
        warnings = []
        if requested and not match:
            warnings.append(f'Could not read duration “{requested}”. Used {duration:g} s.')
        if entries and match and duration < inferred:
            warnings.append(f'Dialogue may need about {inferred:g} s at a natural pace.')
        kind = ' '.join(fields.get('kind', [])).casefold()
        if kind not in ('still', 'motion'):
            kind = 'motion'
        cast = [name.strip() for value in fields.get('cast', []) for name in re.split(r'[,;]', value) if name.strip()]
        text = dialogue or action or visual or motion or direction
        blocks.append({'scene':scene, 'title':title, 'kind':kind, 'text':text, 'dialogue':dialogue,
                       'speaker':entries[0]['speaker'] if entries else '', 'entries':entries,
                       'keyframe_prompt':keyframe, 'motion_prompt':animation, 'camera':camera, 'notes':notes,
                       'duration':duration, 'cast':cast,
                       'cast_text':join_details(action, visual, motion, direction, surroundings, scene),
                       'warnings':warnings})
        if visual or action:
            previous_picture = visual or opening
        title, fields, actions, entries = '', {}, [], []
        dialogue_open, field_open = False, None

    lines = script.splitlines()
    for index, raw in enumerate(lines):
        raw = raw.strip()
        if not raw:
            if not explicit and not pending_cue:
                flush()
            dialogue_open, field_open = False, None
            continue
        plain = clean_script_text(raw)
        heading = re.match(r'^#{1,6}\s+(.+)', plain)
        label = heading[1].strip() if heading else plain
        shot = SHOT.fullmatch(label) if not raw.startswith('*') or raw.startswith('**') else None
        if shot or heading or SCENE.match(label) or TRANSITION.fullmatch(label) or re.fullmatch(r'[-*_]{3,}', raw):
            flush()
            title, fields = '', {}
            explicit = bool(shot)
            dialogue_open, field_open = False, None
            pending_cue, pending_direction = None, []
            if shot:
                title = label
            elif (heading or SCENE.match(label)) and not SECTION.match(label):
                scene = re.sub(r'\s*\(\d+:\d+[^)]*\)\s*$', '', label)
                context, previous_picture = {}, ''
            continue

        prefix, colon, value = plain.partition(':')
        key = prefix.casefold().strip()
        if colon and key in CAMERA_CUES and not raw.startswith('* '):
            if not explicit:
                flush()
            fields.setdefault('camera', []).append(CAMERA_CUES[key])
            if value.strip():
                actions.append(value.strip())
            continue
        field = FIELD_NAMES.get(key) if colon else None
        if field and (not raw.startswith('*') or raw.startswith('**')):
            pending_cue, pending_direction = None, []
            if field == 'dialogue':
                raw = value.strip()
                if not raw:
                    field_open = 'dialogue'
                    dialogue_open = False
                    continue
            else:
                if not explicit and entries:
                    flush()
                dialogue_open = False
                field_open = field
                target = context if field in ('location', 'lighting', 'mood') and not explicit and not (fields or actions) else fields
                target.setdefault(field, []).append(value.strip())
                continue
        elif colon and prefix.casefold().strip() in METADATA and not raw.startswith('* '):
            continue

        entry = dialogue_line(raw)
        if pending_cue and not entry:
            if raw.startswith('(') and raw.endswith(')'):
                pending_direction.append(raw[1:-1])
                continue
            entry = dialogue_line(f'{pending_cue}: {raw}')
            if entry and pending_direction:
                entry['direction'] = '; '.join(filter(None, [*pending_direction, entry['direction']]))
        pending_cue, pending_direction = None, []
        if entry:
            if actions and not explicit and not fields:
                flush()
            entries.append(entry)
            dialogue_open, field_open = True, None
            continue
        italic_action = raw.startswith('*') and not raw.startswith('**')
        following = lines[index+1].strip() if index+1 < len(lines) else ''
        cue = screenplay_cue(raw, following) if not italic_action and (field_open in (None, 'dialogue')) else None
        if dialogue_open and not italic_action and not cue:
            if raw.startswith('(') and raw.endswith(')'):
                entries[-1]['direction'] = '; '.join(filter(None, (entries[-1]['direction'], raw[1:-1])))
            else:
                entries[-1]['text'] += ' ' + plain
            continue
        if italic_action and entries and not explicit:
            flush()
        if cue:
            if actions and not explicit and not fields:
                flush()
            pending_cue = cue
        elif field_open and field_open in fields and not italic_action:
            fields[field_open].append(plain)
        elif not SECTION.match(label) or not raw.startswith('*'):
            actions.append(plain)
        dialogue_open = False
    flush()
    return blocks


def recovery_preview(studio):
    names = {a['name'].strip().casefold() for a in studio.db.q("SELECT name FROM assets WHERE kind='character'")}
    rows = []
    for shot in studio.ordered_shots():
        if (shot['dialogue'] or '').strip() or shot['audio_mode'] == 'file':continue
        for field in ('keyframe_prompt','motion_prompt'):
            source = shot[field] or ''
            entries = dialogue_entries(source)
            if not entries:continue
            # Recover unknown speakers only from explicit bold labels, avoiding character-description prompts.
            if not source.strip().startswith(('**','__')) and any(e['speaker'].casefold() not in names for e in entries):continue
            rows.append({'id':shot['id'],'seq':shot['seq'],'scene':shot['scene'],'source':field,'original':source,
                         'dialogue':'\n'.join(e['speaker']+': '+e['text'] for e in entries),'entries':entries,
                         'new_speakers':sorted({e['speaker'] for e in entries if e['speaker'].casefold() not in names})})
            break
    token = hashlib.sha256(json.dumps([rows,studio.db.q('SELECT id,dialogue,audio_mode FROM shots ORDER BY id')],sort_keys=True).encode()).hexdigest()
    return rows,token


def recover_dialogue(studio, body):
    rows,token = recovery_preview(studio)
    if not body.get('apply'):
        return {'ok':True,'rows':rows,'token':token}
    if body.get('token') != token:raise ValueError('Shots changed since preview. Review dialogue recovery again.')
    ids = set(body.get('ids',[]))
    if not ids.issubset({r['id'] for r in rows}):raise ValueError('Select only reviewed dialogue rows')
    if not ids:return {'ok':True,'recovered':0}
    studio.editor.checkpoint('Before recovering Markdown dialogue')
    names = {a['name'].strip().casefold():a['id'] for a in studio.db.q("SELECT id,name FROM assets WHERE kind='character'")}
    with studio.db.lock, studio.db.conn:
        for row in rows:
            if row['id'] not in ids:continue
            shot=studio.db.one('SELECT asset_ids FROM shots WHERE id=?',(row['id'],))
            cast=json.loads(shot['asset_ids'] or '[]')
            for entry in row['entries']:
                key=entry['speaker'].casefold()
                if key not in names:
                    cur=studio.db.conn.execute("INSERT INTO assets(kind,name) VALUES('character',?)",(entry['speaker'].title(),))
                    names[key]=cur.lastrowid
                if names[key] not in cast:cast.append(names[key])
            saved=[{'speaker':e['speaker'],'text':e['text'],'direction':e['direction'],'path':None,'src':None,'clip_id':None} for e in row['entries']]
            studio.db.conn.execute('UPDATE shots SET dialogue=?,asset_ids=?,line_audio=? WHERE id=?',
                                   (row['dialogue'],json.dumps(cast),json.dumps(saved),row['id']))
    return {'ok':True,'recovered':len(ids)}


PROMPT_FIELDS = ('keyframe_prompt', 'motion_prompt', 'camera', 'notes', 'duration', 'kind')


def script_preview_token(st, text):
    fields = ('id', 'seq', 'scene', 'dialogue', 'asset_ids', 'script_source', *PROMPT_FIELDS)
    shots = [[shot[key] for key in fields] for shot in st.ordered_shots()]
    inputs = [text, st.default_duration(), st.settings().get('active_episode'), shots,
              st.db.q('SELECT id,name,kind FROM assets ORDER BY id')]
    return hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()


def cast_key(name):
    return ' '.join(name.split()).casefold()


def prepare_script_cast(api, blocks):
    names = {cast_key(row['name']) for row in api.st.db.q("SELECT name FROM assets WHERE kind='character'")}
    for block in blocks:
        for name in [*block['cast'], *(entry['speaker'] for entry in block['entries'])]:
            key = cast_key(name)
            if key not in names:
                api.asset_save({'kind':'character', 'name':name.title() if name.isupper() else name})
                names.add(key)


def apply_script_block(api, block, previous=None):
    """Merge new script fields while preserving values edited since the last import."""
    st = api.st
    old = json.loads(previous.get('script_source') or '{}') if previous else {}
    assets = st.db.q('SELECT id,name,kind FROM assets ORDER BY id')
    names = {cast_key(asset['name']): asset['id'] for asset in assets if asset['kind'] == 'character'}
    cast = [names[cast_key(name)] for name in block['cast'] if cast_key(name) in names]
    cast += [names[cast_key(entry['speaker'])] for entry in block['entries'] if not OFFSCREEN.search(entry['direction'])]
    text = block['cast_text'].casefold()
    mentions = []
    for asset in assets:
        name, ident = cast_key(asset['name']), asset['id']
        if not name:
            continue
        match = re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text)
        if match:
            mentions.append((match.start(), ident))
    cast += [ident for _, ident in sorted(mentions)]
    cast = list(dict.fromkeys(cast))[:8]
    values = {key: block[key] for key in PROMPT_FIELDS}
    values.update(scene=block['scene'], dialogue=block['dialogue'])
    if previous:
        values['id'] = previous['id']
        for key in PROMPT_FIELDS:
            if key in old:
                if previous[key] != old[key]:
                    values.pop(key)
            elif previous[key] not in ('', None):
                values.pop(key)
        # Cast explicitly selected by the editor stays attached, alongside any new speakers.
        old_cast = old.get('asset_ids', [])
        manually_added = [ident for ident in json.loads(previous['asset_ids'] or '[]') if ident not in old_cast]
        values['asset_ids'] = list(dict.fromkeys([*cast, *manually_added]))[:8]
    else:
        values['asset_ids'] = cast
    if previous:
        old_lines = {line['line_id']: line for line in st.shot_lines(previous)}
        if values['dialogue'] == previous['dialogue']:
            values.pop('dialogue')
        if values['asset_ids'] == json.loads(previous['asset_ids'] or '[]'):
            values.pop('asset_ids')
    else:
        old_lines = {}
    sid = api.shot_save(values)['id']
    if block['entries']:
        lines = st.shot_lines(st.db.one('SELECT * FROM shots WHERE id=?', (sid,)))
        previous_delivery = {}
        for entry in old.get('entries', []):
            key = (cast_key(entry['speaker']), entry['text'])
            previous_delivery.setdefault(key, []).append(entry['direction'])
        for line, entry in zip(lines, block['entries']):
            matching = previous_delivery.get((cast_key(entry['speaker']), entry['text']), [])
            previous_direction = matching.pop(0) if matching else ''
            prior = old_lines.get(line['line_id'])
            manual = (prior.get('direction') or '') if prior else None
            line['direction'] = manual if manual is not None and manual != previous_direction else entry['direction']
            if not prior or not prior.get('character_id'):
                line['character_id'] = names[cast_key(entry['speaker'])]
        st.save_lines(sid, lines)
    stamp = dict(block, asset_ids=cast)
    st.db.x('UPDATE shots SET script_source=? WHERE id=?', (json.dumps(stamp), sid))
    return sid
