"""Editorial history, episode library, scene maintenance, continuity and delivery."""
import hashlib
import json
from timeline import frames as timeline_frames, layout as timeline_layout
import math
import shutil
import sqlite3
import subprocess
import time
import uuid
import zipfile
from pathlib import Path

EDITORIAL = ('sound_library', 'settings', 'shots', 'assets', 'takes', 'voice_takes', 'voice_clips',
             'audio_cues', 'episode_audio_exports', 'production_approvals', 'production_plans', 'continuity',
             'asset_looks', 'scene_looks', 'continuity_reviews', 'shot_direction', 'generation_recipes',
             'shot_recipes', 'character_pronunciations', 'line_performances', 'performance_takes', 'creative_runs', 'shot_sections', 'section_takes')
SHARED = {'sound_library', 'assets', 'voice_clips', 'asset_looks', 'generation_recipes', 'character_pronunciations'}


def number(value, minimum=0):
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError('Enter a finite number greater than or equal to ' + str(minimum))
    return result


def timecode(seconds, comma=','):
    ms = round(seconds * 1000)
    return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}{comma}{ms%1000:03}'


class StudioTools:
    def __init__(self, studio):
        self.st = studio
        db = studio.db
        db.x('CREATE TABLE IF NOT EXISTS edit_history(id TEXT PRIMARY KEY, label TEXT, created REAL, snapshot TEXT)')
        db.x('CREATE TABLE IF NOT EXISTS episode_library(id TEXT PRIMARY KEY, title TEXT, snapshot TEXT, updated REAL)')
        db.x('CREATE TABLE IF NOT EXISTS continuity(scene TEXT PRIMARY KEY, json TEXT)')
        for table, column, sql in [('shots', 'trim_in', 'REAL DEFAULT 0'),
                                   ('audio_cues', 'anchor_shot', 'INTEGER'), ('audio_cues', 'anchor_offset', 'REAL DEFAULT 0')]:
            if column not in {r['name'] for r in db.q('PRAGMA table_info(' + table + ')')}:
                db.x(f'ALTER TABLE {table} ADD COLUMN {column} {sql}')
        (studio.data / 'history').mkdir(exist_ok=True)
        latest = db.one('SELECT created FROM edit_history ORDER BY created DESC LIMIT 1')
        if not latest or time.time() - latest['created'] > 86400:
            self.checkpoint('Automatic daily checkpoint')

    def idle(self):
        if self.st.assembly['running'] or self.st.db.one("SELECT id FROM jobs WHERE status IN ('pending','running') LIMIT 1"):
            raise ValueError('Wait for renders and exports to finish, or cancel them in Render queue first.')
        if self.st.db.one("SELECT id FROM production_plans WHERE status='active' LIMIT 1"):
            raise ValueError('Cancel the active preparation plan before restoring history or switching episodes.')

    def snapshot(self, shared=True):
        db = self.st.db
        with db.lock:
            result = {t: db.q('SELECT * FROM ' + t) for t in EDITORIAL if shared or t not in SHARED}
            result['settings'] = [r for r in result['settings'] if r['key'] != 'active_episode']
            # Generated shot dialogue tracks are mutable; preserve their exact bytes too.
            media = {}
            for shot in result['shots']:
                path = shot.get('audio')
                if path and path.startswith('voices/') and self.st.abs(path).is_file():
                    data = self.st.abs(path).read_bytes()
                    saved = self.st.data / 'history' / (hashlib.sha256(data).hexdigest() + '.audio')
                    if not saved.exists():
                        saved.write_bytes(data)
                    media[path] = self.st.rel(saved)
            result['_audio'] = media
            return result

    def checkpoint(self, label):
        ident = uuid.uuid4().hex
        snapshot = json.dumps(self.snapshot())
        self.st.db.x('INSERT INTO edit_history VALUES(?,?,?,?)',
                     (ident, str(label)[:160], time.time(), snapshot))
        (self.st.data/'history'/(ident+'.json')).write_text(snapshot)
        if str(label).startswith('Automatic daily'):
            with self.st.db.lock, sqlite3.connect(self.st.data/'history'/('automatic-'+time.strftime('%Y-%m-%d')+'.sqlite')) as copied:
                self.st.db.conn.backup(copied)
        return ident

    def apply(self, snapshot):
        db = self.st.db
        snapshot = dict(snapshot) if isinstance(snapshot, dict) else snapshot
        if not isinstance(snapshot, dict) or 'shots' not in snapshot or 'settings' not in snapshot:
            raise ValueError('Invalid project checkpoint')
        from timeline import upgrade_legacy_fps
        settings=upgrade_legacy_fps({row['key']:row['value'] for row in snapshot['settings']})
        snapshot['settings']=[dict(key=key,value=value) for key,value in settings.items()]
        for table in ('scene_looks','continuity_reviews','shot_direction','shot_recipes','line_performances','performance_takes','creative_runs','shot_sections','section_takes'):
            snapshot.setdefault(table, [])
        if 'assets' in snapshot:
            for table in ('asset_looks','character_pronunciations'):
                snapshot.setdefault(table, [])
        # Validate every table/column before changing anything; keep jobs and server configuration intact.
        for table, rows in snapshot.items():
            if table.startswith('_'):
                continue
            if table not in EDITORIAL:
                raise ValueError('Unexpected checkpoint table')
            columns = {c['name'] for c in db.q('PRAGMA table_info(' + table + ')')}
            if any(not isinstance(r, dict) or not set(r).issubset(columns) for r in rows):
                raise ValueError('Incompatible checkpoint columns')
        paths = {'sound_library': ('path',), 'shots': ('audio',), 'assets': ('image',), 'takes': ('path',),
                 'voice_takes': ('path',), 'voice_clips': ('path',), 'audio_cues': ('path',),
                 'episode_audio_exports': ('master','output')}
        for table, fields in paths.items():
            for row in snapshot.get(table, []):
                for field in fields:
                    if row.get(field): self.safe_path(row[field])
        for row in snapshot.get('settings', []):
            if row['key'] == 'music' and row['value']: self.safe_path(row['value'])
        for row in snapshot.get('shots', []):
            for line in json.loads(row.get('line_audio') or '[]'):
                for field in ('audio','path'):
                    if line.get(field): self.safe_path(line[field])
        for row in snapshot.get('continuity', []):
            for variant in json.loads(row['json']).get('variants', []):
                if variant.get('image'): self.safe_path(variant['image'])
        for dest, source in snapshot.get('_audio', {}).items():
            self.safe_path(dest)
            if not self.safe_path(source).is_file():
                raise ValueError('Checkpoint audio is missing; original project remains selected')
        with db.lock:
            try:
                db.conn.execute('BEGIN IMMEDIATE')
                for table, rows in snapshot.items():
                    if table not in EDITORIAL:
                        continue
                    db.conn.execute('DELETE FROM ' + table + (" WHERE key!='active_episode'" if table == 'settings' else ''))
                    for row in rows:
                        columns = list(row)
                        db.conn.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
                for dest, source in snapshot.get('_audio', {}).items():
                    target = self.safe_path(dest)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if not target.exists() or target.read_bytes() != self.safe_path(source).read_bytes():
                        shutil.copyfile(self.safe_path(source), target)
                db.conn.execute("UPDATE production_plans SET status='blocked', detail='Restored checkpoint: review and resume explicitly' WHERE status IN ('active','approval')")
                db.conn.commit()
            except Exception:
                db.conn.rollback()
                raise
        self.st.production.readiness = {}

    def safe_path(self, relative):
        p = (self.st.data / relative).resolve()
        if self.st.data not in p.parents:
            raise ValueError('Path must be inside the project')
        return p

    def history(self, body):
        if body.get('action') == 'restore':
            self.idle()
            row = self.st.db.one('SELECT snapshot FROM edit_history WHERE id=?', (body['id'],))
            if not row:
                raise ValueError('Checkpoint not found')
            self.checkpoint('Before restoring history')
            self.apply(json.loads(row['snapshot']))
        else:
            self.checkpoint(body.get('label') or 'Manual checkpoint')
        return {'ok': True}

    def preview_scene(self, body):
        if 'scene' not in body:
            raise ValueError('Choose a scene')
        scene = str(body['scene'])
        shots = self.st.db.q('SELECT * FROM shots WHERE scene=? ORDER BY seq,id', (scene,))
        if not shots:
            raise ValueError('Scene not found')
        ids = {s['id'] for s in shots}
        mode = body.get('mode', 'clips')
        if mode not in ('clips', 'reset'):
            raise ValueError('Choose clips or reset')
        stages = {'animate', 'continue', 'lipsync', 'upscale'} if mode == 'clips' else {'keyframe', 'animate', 'continue', 'lipsync', 'upscale'}
        takes = [t for t in self.st.db.q('SELECT * FROM takes') if t['shot_id'] in ids and t['stage'] in stages]
        voices = [t for t in self.st.db.q('SELECT * FROM voice_takes') if t['shot_id'] in ids] if mode == 'reset' else []
        busy = [j['id'] for j in self.st.db.q("SELECT * FROM jobs WHERE status IN ('pending','running')") if j['shot_id'] in ids]
        payload = {'scene': scene, 'mode': mode, 'shots': shots, 'takes': takes, 'voices': voices,
                   'continuity': self.st.db.one('SELECT * FROM continuity WHERE scene=?', (scene,)),
                   'plans': self.st.db.q('SELECT * FROM production_plans WHERE scene=?', (scene,)), 'busy': busy}
        token = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return payload, token

    def scene(self, body):
        payload, token = self.preview_scene(body)
        if not body.get('apply'):
            return {'ok': True, 'token': token, 'shots': len(payload['shots']),
                    'clips': len(payload['takes']), 'voices': len(payload['voices']), 'busy': payload['busy']}
        if body.get('token') != token:
            raise ValueError('Scene changed since preview. Review the cleanup again.')
        if payload['busy'] or self.st.assembly['running']:
            raise ValueError('Finish or cancel this scene’s renders and current export before cleanup.')
        db = self.st.db
        self.checkpoint('Before scene ' + payload['mode'] + ': ' + payload['scene'])
        with db.lock:
            try:
                db.conn.execute('BEGIN IMMEDIATE')
                for shot in payload['shots']:
                    sid = shot['id']
                    db.conn.execute("UPDATE shots SET clip_take=NULL, final_take=NULL, cont_takes='[]' WHERE id=?", (sid,))
                    if payload['mode'] == 'reset':
                        # Preserve the script, scene membership, shot order and shot kind.
                        db.conn.execute("UPDATE shots SET keyframe_take=NULL, asset_ids='[]', keyframe_prompt='', motion_prompt='', camera='', notes='', duration=?, retime='hold', ref_prev=0, end_next=0, audio=NULL, audio_mode='lines', line_audio='[]', trim_in=0 WHERE id=?", (self.st.default_duration(), sid))
                        from voice_video import DIRECTION
                        db.conn.execute("UPDATE shots SET voice_quality='standard',voice_framing='source',voice_direction=?,lip_sync_mode='keyframe' WHERE id=?", (DIRECTION, sid))
                        for table in ('shot_direction', 'shot_recipes', 'line_performances', 'performance_takes', 'continuity_reviews', 'creative_runs', 'shot_sections'):
                            db.conn.execute('DELETE FROM '+table+' WHERE shot_id=?', (sid,))
                        db.conn.execute('DELETE FROM voice_takes WHERE shot_id=?', (sid,))
                        db.conn.execute('DELETE FROM audio_cues WHERE anchor_shot=?', (sid,))
                    db.conn.execute('DELETE FROM production_approvals WHERE shot_id=?', (sid,))
                db.conn.executemany('DELETE FROM section_takes WHERE take_id=?', [(t['id'],) for t in payload['takes']])
                db.conn.executemany('DELETE FROM takes WHERE id=?', [(t['id'],) for t in payload['takes']])
                db.conn.execute("UPDATE production_plans SET status='cancelled', detail='Scene cleared by editor' WHERE scene=?", (payload['scene'],))
                if payload['mode'] == 'reset':
                    db.conn.execute('DELETE FROM continuity WHERE scene=?', (payload['scene'],))
                    db.conn.execute('DELETE FROM scene_looks WHERE scene=?', (payload['scene'],))
                db.conn.commit()
            except Exception:
                db.conn.rollback()
                raise
        # Media is retained for checkpoints and existing exports; it is removed from the active scene.
        return {'ok': True, 'removed': len(payload['takes']), 'recoverable': True}

    def timeline(self, body):
        db = self.st.db
        shots = self.st.ordered_shots()
        action = body.get('action')
        if action == 'reorder':
            ids = body.get('ids', [])
            if len(ids) != len(set(ids)) or set(ids) != {s['id'] for s in shots}:
                raise ValueError('Reorder must include every shot exactly once')
            self.checkpoint('Before timeline reorder')
            with db.lock, db.conn:
                db.conn.executemany('UPDATE shots SET seq=? WHERE id=?', [(i+1, sid) for i, sid in enumerate(ids)])
        elif action in ('trim', 'split'):
            shot = next((s for s in shots if s['id'] == body.get('id')), None)
            if not shot:
                raise ValueError('Shot not found')
            if action == 'trim':
                duration = number(body['duration'], .5)
                trim = number(body.get('trim_in', shot.get('trim_in', 0)))
                if duration > 3600 or trim > 3600:
                    raise ValueError('Shot duration and source in must be at most one hour')
                self.checkpoint('Before trimming shot')
                db.x('UPDATE shots SET duration=?, trim_in=? WHERE id=?', (duration, trim, shot['id']))
            else:
                at = number(body['at'], .5)
                if shot['duration'] - at < .5:
                    raise ValueError('Each half must be at least half a second')
                self.checkpoint('Before splitting shot')
                clone = dict(shot)
                clone.pop('id')
                clone.update(seq=shot['seq']+.5, duration=shot['duration']-at, trim_in=shot.get('trim_in', 0)+at)
                # Selections refer to the original immutable takes; duplicated options remain selectable.
                with db.lock, db.conn:
                    cursor = db.conn.execute(f"INSERT INTO shots({','.join(clone)}) VALUES({','.join('?' for _ in clone)})", list(clone.values()))
                    new_id = cursor.lastrowid
                    mapping = {}
                    for table in ('takes', 'voice_takes'):
                        for take in db.q('SELECT * FROM ' + table + ' WHERE shot_id=?', (shot['id'],)):
                            old_id = take.pop('id'); take['shot_id'] = new_id
                            cur = db.conn.execute(f"INSERT INTO {table}({','.join(take)}) VALUES({','.join('?' for _ in take)})", list(take.values()))
                            if table == 'takes':
                                mapping[old_id] = cur.lastrowid
                                for field in ('keyframe_take', 'clip_take', 'final_take'):
                                    if shot[field] and self.st.take(shot[field]) and self.st.take(shot[field])['path'] == take['path']:
                                        db.conn.execute('UPDATE shots SET '+field+'=? WHERE id=?', (cur.lastrowid, new_id))
                    db.conn.execute('UPDATE shots SET cont_takes=? WHERE id=?', (json.dumps([mapping[i] for i in json.loads(shot['cont_takes'] or '[]') if i in mapping]), new_id))
                    db.conn.execute('UPDATE shots SET duration=? WHERE id=?', (at, shot['id']))
                    self.st.creative.split_controls(shot,new_id,at/shot['duration'])
                self.st.renumber()
        else:
            raise ValueError('Unknown timeline action')
        return {'ok': True}

    def continuity(self, body):
        scene = str(body['scene'])
        if not self.st.db.one('SELECT id FROM shots WHERE scene=?', (scene,)):
            raise ValueError('Scene not found')
        variants = body.get('variants', [])
        assets = {a['id']: a for a in self.st.db.q('SELECT * FROM assets')}
        clean = []
        for item in variants:
            if item.get('asset_id') not in assets:
                raise ValueError('Cast reference not found')
            image = item.get('image') or assets[item['asset_id']]['image']
            if image and not self.safe_path(image).is_file():
                raise ValueError('Reference image is missing')
            clean.append({'asset_id': item['asset_id'], 'image': image, 'notes': str(item.get('notes', ''))[:2000]})
        if len(clean) > 3:
            raise ValueError('Use at most three scene reference overrides')
        self.checkpoint('Before continuity edit')
        for bound in self.st.db.q('SELECT s.*,l.json FROM scene_looks s JOIN asset_looks l ON l.id=s.look_id WHERE s.scene=?', (scene,)):
            image = json.loads(bound['json'])['references'][bound['view_index']]['path']
            if not any(v['asset_id']==bound['asset_id'] and v['image']==image for v in clean):
                self.st.db.x('DELETE FROM scene_looks WHERE scene=? AND asset_id=?', (scene,bound['asset_id']))
        self.st.db.x('INSERT OR REPLACE INTO continuity VALUES(?,?)', (scene, json.dumps({'lighting': str(body.get('lighting', ''))[:2000], 'notes': str(body.get('notes', ''))[:4000], 'variants': clean})))
        return {'ok': True}

    def context(self, scene):
        row = self.st.db.one('SELECT json FROM continuity WHERE scene=?', (scene,))
        return json.loads(row['json']) if row else {'lighting': '', 'notes': '', 'variants': []}

    def library(self, body):
        self.idle()
        db = self.st.db
        action = body.get('action')
        active = self.st.settings().get('active_episode')
        if active:
            db.x('UPDATE episode_library SET snapshot=?,title=?,updated=? WHERE id=?',
                 (json.dumps(self.snapshot(False)), self.st.settings()['title'], time.time(), active))
        elif action in ('new', 'duplicate', 'open'):
            active = uuid.uuid4().hex
            db.x('INSERT INTO episode_library VALUES(?,?,?,?)', (active, self.st.settings()['title'], json.dumps(self.snapshot(False)), time.time()))
        if action == 'save':
            if not active:
                active = uuid.uuid4().hex
                db.x('INSERT INTO episode_library VALUES(?,?,?,?)', (active, self.st.settings()['title'], json.dumps(self.snapshot(False)), time.time()))
        elif action in ('new', 'duplicate', 'open'):
            self.checkpoint('Before episode switch')
            if action == 'open':
                row = db.one('SELECT * FROM episode_library WHERE id=?', (body['id'],))
                if not row:
                    raise ValueError('Episode not found')
                snapshot = json.loads(row['snapshot']);active = row['id']
            else:
                snapshot = self.snapshot(False)
                if action == 'new':
                    for table in snapshot:
                        if table != 'settings': snapshot[table] = {} if table == '_audio' else []
                    snapshot['settings'] = [r for r in snapshot['settings'] if r['key'] not in ('script', 'music', 'subtitle_rows')]
                    snapshot['settings'] += [{'key':'script','value':''}, {'key':'music','value':''}]
                title = str(body.get('title') or 'New episode').strip()[:160]
                snapshot['settings'] = [r for r in snapshot['settings'] if r['key'] != 'title'] + [{'key':'title','value':title}]
                active = uuid.uuid4().hex
                db.x('INSERT INTO episode_library VALUES(?,?,?,?)', (active, title, json.dumps(snapshot), time.time()))
            self.apply(snapshot)
        else:
            raise ValueError('Unknown library action')
        db.x("INSERT OR REPLACE INTO settings VALUES('active_episode',?)", (active,))
        return {'ok': True}

    def backup(self, body):
        self.idle()
        ident = uuid.uuid4().hex
        root = self.st.data / 'backups';root.mkdir(exist_ok=True)
        target = root / ('project-' + ident + '.zip')
        temporary = root / ('db-' + ident + '.sqlite')
        with self.st.db.lock:
            with sqlite3.connect(temporary) as conn:
                self.st.db.conn.backup(conn)
        try:
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as z:
                z.write(temporary, 'studio.db')
                for folder in ('uploads', 'shots', 'cast', 'voices', 'history', 'exports'):
                    for file in (self.st.data / folder).rglob('*'):
                        if file.is_file() and not file.is_symlink():
                            z.write(file, str(file.relative_to(self.st.data)))
                z.writestr('anime-project.json', json.dumps({'format':1,'title':self.st.settings()['title']}))
        finally:
            temporary.unlink(missing_ok=True)
        return {'ok': True, 'path': self.st.rel(target), 'url': '/media/' + self.st.rel(target)}

    def restore_backup(self, body):
        self.idle()
        archive = self.st.save_upload(body['file'], 'backup-imports')
        staging = self.st.data / 'history' / ('import-' + uuid.uuid4().hex)
        staging.mkdir()
        try:
            with zipfile.ZipFile(self.safe_path(archive)) as z:
                names = z.namelist()
                if 'anime-project.json' not in names or 'studio.db' not in names:
                    raise ValueError('Choose an Anime Studio project archive')
                if sum(i.file_size for i in z.infolist()) > 100*1024**3:
                    raise ValueError('Archive exceeds the 100 GB import limit')
                for info in z.infolist():
                    dest = (staging/info.filename).resolve()
                    if staging not in dest.parents or (info.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Unsafe archive entry')
                z.extractall(staging)
            with sqlite3.connect(staging/'studio.db') as conn:
                conn.row_factory = sqlite3.Row
                if conn.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    raise ValueError('Archive database is damaged')
                available = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                snapshot = {t:[dict(r) for r in conn.execute('SELECT * FROM '+t)] for t in EDITORIAL if t in available}
                library = [dict(r) for r in conn.execute('SELECT * FROM episode_library')] if 'episode_library' in available else []
            # Namespace imported media so no existing project files are overwritten.
            prefix = self.st.rel(staging) + '/'
            def remap(value):
                if isinstance(value, dict): return {k:({remap(a):remap(b) for a,b in v.items()} if k == '_audio' else remap(v)) for k,v in value.items()}
                if isinstance(value, list): return [remap(v) for v in value]
                if isinstance(value, str):
                    if len(value) < 1024 and value.startswith(('uploads/','shots/','cast/','voices/','history/','exports/')) and (staging/value).is_file(): return prefix+value
                    if value[:1] in ('[','{'):
                        try: return json.dumps(remap(json.loads(value)))
                        except (ValueError,TypeError): pass
                return value
            snapshot = remap(snapshot)
            self.checkpoint('Before importing project archive')
            self.apply(snapshot)
            self.st.db.x("DELETE FROM settings WHERE key='active_episode'")
            for episode in library:
                self.st.db.x('INSERT INTO episode_library VALUES(?,?,?,?)', (uuid.uuid4().hex, episode['title'], json.dumps(remap(json.loads(episode['snapshot']))), time.time()))
            return {'ok': True}
        except Exception:
            shutil.rmtree(staging)
            raise

    def qc(self, shots):
        issues = []
        for s in shots:
            problems = []
            if not s['keyframe_take']: problems.append('Missing picture')
            if s.get('draft_in_use'): problems.append('Draft video selected')
            if s.get('outdated'): problems.append('Outdated: '+', '.join(s['outdated']))
            if s['kind'] != 'still' and (s.get('remaining') is None or s['remaining'] > .25): problems.append('Animation does not cover shot')
            if s.get('audio'):
                from anime_studio import media_seconds
                if media_seconds(self.st.abs(s['audio'])) > s['duration'] + s.get('trim_in', 0) + .05: problems.append('Dialogue cut off')
            if any(not l.get('audio') for l in s.get('lines', [])): problems.append('Missing dialogue audio')
            if problems: issues.append({'id':s['id'],'seq':s['seq'],'problems':problems})
        return issues

    def subtitles(self, body):
        shots = self.st.ordered_shots()
        rows = []; start = 0
        for shot in shots:
            trim = (shot.get('trim_in') or 0) - (shot.get('dialogue_offset') or 0)
            cursor = 0
            for line in self.st.shot_lines(shot):
                from anime_studio import media_seconds
                length = media_seconds(self.st.abs(line['audio'])) if line.get('audio') else max(.5, len(line['text'])/14)
                timing = self.st.creative.timing(shot['id'], line)
                cursor += timing.get('lead_in', 0)
                left, right = max(-start,cursor-trim), min(shot['duration']+(shot.get('dialogue_tail') or 0)+(shot.get('dialogue_offset') or 0),cursor+length-trim)
                if right > left:
                    rows.append({'start':start+left,'end':start+right,'text':line['text'],'speaker':line.get('speaker','')})
                cursor += length + timing.get('pause_after', float(self.st.settings().get('line_gap', .3)))
            start += timeline_frames(shot['duration'],int(self.st.settings()['out_fps']))/int(self.st.settings()['out_fps'])
        rows = [dict(row,end=min(start,row['end'])) for row in rows if row['start']<min(start,row['end'])]
        if body.get('rows') is not None:
            rows = body['rows']
        if body.get('rows') is None and self.st.settings().get('subtitle_rows') and not body.get('regenerate'):
            rows = json.loads(self.st.settings()['subtitle_rows'])
        if not body.get('export') and not body.get('save'):
            return {'ok':True,'rows':rows}
        output=[]
        for index,row in enumerate(rows):
            left,right=number(row['start']),number(row['end'])
            if right <= left or right > start+.05: raise ValueError('Subtitle timing is outside the episode')
            text=str(row['text']).replace('\r','').strip()
            if not text: continue
            output.append(f'{index+1}\n{timecode(left)} --> {timecode(right)}\n{text}\n')
        if body.get('save'):
            self.checkpoint('Before subtitle edit')
            self.st.db.x("INSERT OR REPLACE INTO settings VALUES('subtitle_rows',?)", (json.dumps(rows),))
        if body.get('export'):
            folder=self.st.data/'exports'/'delivery';folder.mkdir(parents=True,exist_ok=True)
            file=folder/('subtitles-'+uuid.uuid4().hex[:8]+'.srt');file.write_text('\n'.join(output))
            return {'ok':True,'url':'/media/'+self.st.rel(file)}
        return {'ok':True,'rows':rows}

    def delivery(self, body):
        from anime_studio import ffmpeg
        from soundtrack import probe
        output = self.st.db.one('SELECT * FROM episode_audio_exports WHERE id=?',(body['id'],))
        if not output or not output['clean']: raise ValueError('Choose an episode with a dialogue-only master')
        folder=self.st.data/'exports'/('delivery-'+uuid.uuid4().hex[:8]);folder.mkdir()
        master=self.safe_path(output['master']);duration, _=probe(master)
        recipe=json.loads(output.get('build_json') or '{}').get('mix_recipe')
        recipe_source='saved'
        if not recipe:
            recipe_source='current'
            recipe={'cues':self.st.soundtrack.cues(),'bed':self.st.settings().get('music',''),
                    'volume':float(body.get('music_volume',.25)),'mixer':self.st.finishing.mixer(),'engine':'block-rms-v1'}
        mixed=folder/'mix.mp4'
        self.st.finishing.mix_audio(master,mixed,recipe['cues'],recipe['bed'],recipe['volume'],duration,
                                   stem_dir=folder,mixer_override=recipe['mixer'],engine=recipe.get('engine','legacy-sidechain'))
        mixed.unlink();mixed.with_suffix('.audio.json').unlink(missing_ok=True)
        if not any(cue['kind']=='ambience' for cue in recipe['cues']):
            (folder/'ambience.wav').unlink()
        from export_runtime import run
        analysis=run(['ffmpeg','-hide_banner','-i',str(self.safe_path(output['output'])),'-af','loudnorm=I=-16:TP=-1.5:LRA=11:print_format=json','-f','null','-'])
        if analysis.returncode: raise ValueError('Loudness analysis failed: '+analysis.stderr[-1200:])
        (folder/'loudness.txt').write_text(analysis.stderr)
        return {'ok':True,'recipe_source':recipe_source,'files':{f.name:'/media/'+self.st.rel(f) for f in folder.iterdir() if f.suffix in ('.wav','.txt')}}

    def state(self):
        db=self.st.db
        return {'history':db.q('SELECT id,label,created FROM edit_history ORDER BY created DESC LIMIT 40'),
                'episodes':db.q('SELECT id,title,updated FROM episode_library ORDER BY updated DESC'),
                'continuity':{r['scene']:json.loads(r['json']) for r in db.q('SELECT * FROM continuity')}}
