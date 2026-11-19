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


def script_blocks(script):
    scene = ''
    blocks = []
    for paragraph in re.split(r'\n\s*\n', script):
        lines = paragraph.strip().splitlines()
        if not lines:continue
        first = re.sub(r'\*\*|__', '', lines[0]).strip()
        if re.match(r'^(#+|INT\.|EXT\.|INT/EXT|SCENE\b)', first, re.I):
            scene = re.sub(r'^#+\s*','',first)
            lines.pop(0)
        if not lines:continue
        text = '\n'.join(lines).strip()
        if re.fullmatch(r'[-*_]{3,}',text):continue
        prefix = re.sub(r'\*\*|__','',text.partition(':')[0]).strip().casefold()
        if ':' in text and prefix in METADATA:continue
        entries = dialogue_entries(text)
        canonical = '\n'.join(e['speaker']+': '+e['text'] for e in entries) if entries else ' '.join(text.split())
        blocks.append({'scene':scene,'kind':'still' if entries else 'motion','text':canonical,
                       'speaker':entries[0]['speaker'] if entries else '', 'entries':entries})
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
