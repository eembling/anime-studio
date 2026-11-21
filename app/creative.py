"""Versioned production assets, directed shots, repairs, performances and recipe experiments.

Everything is project-local. Generation uses the existing worker/queue; no server
installation or model downloads are performed here.
"""
import array
import base64
import copy
import hashlib
import json
import math
import re
import shutil
import statistics
import struct
import subprocess
import time
import uuid
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def number(value, low=0, high=86400):
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f'Enter a number between {low:g} and {high:g}.')
    return result


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Cannot read the selected media file.')
    info = json.loads(result.stdout)
    return info, float(info.get('format', {}).get('duration') or 0)


class CreativeStudio:
    def __init__(self, studio):
        self.st = studio
        self.readiness = {}
        for sql in (
            'CREATE TABLE IF NOT EXISTS asset_looks(id TEXT PRIMARY KEY,asset_id INTEGER,name TEXT,version INTEGER,json TEXT,created REAL)',
            'CREATE TABLE IF NOT EXISTS scene_looks(scene TEXT,asset_id INTEGER,look_id TEXT,view_index INTEGER DEFAULT 0,PRIMARY KEY(scene,asset_id))',
            'CREATE TABLE IF NOT EXISTS continuity_reviews(shot_id INTEGER PRIMARY KEY,signature TEXT,notes TEXT)',
            'CREATE TABLE IF NOT EXISTS shot_direction(shot_id INTEGER PRIMARY KEY,json TEXT)',
            'CREATE TABLE IF NOT EXISTS generation_recipes(id TEXT PRIMARY KEY,name TEXT,version INTEGER,stage TEXT,json TEXT,created REAL)',
            'CREATE TABLE IF NOT EXISTS shot_recipes(shot_id INTEGER,stage TEXT,recipe_id TEXT,values_json TEXT,PRIMARY KEY(shot_id,stage))',
            'CREATE TABLE IF NOT EXISTS character_pronunciations(asset_id INTEGER PRIMARY KEY,json TEXT)',
            'CREATE TABLE IF NOT EXISTS line_performances(shot_id INTEGER,line_id TEXT,json TEXT,PRIMARY KEY(shot_id,line_id))',
            'CREATE TABLE IF NOT EXISTS performance_takes(id TEXT PRIMARY KEY,shot_id INTEGER,line_id TEXT,json TEXT,created REAL)',
            'CREATE TABLE IF NOT EXISTS creative_runs(id TEXT PRIMARY KEY,job_id INTEGER,shot_id INTEGER,recipe_id TEXT,kind TEXT,status TEXT,started REAL,finished REAL,instance_id INTEGER,json TEXT,error TEXT)',
        ):
            studio.db.x(sql)
        if 'creative_json' not in {r['name'] for r in studio.db.q('PRAGMA table_info(jobs)')}:
            studio.db.x('ALTER TABLE jobs ADD COLUMN creative_json TEXT')
        self.seed_recipes()

    def path(self, value, required=True):
        if not isinstance(value, str) or not value:
            if not required:
                return None
            raise ValueError('Choose an existing project media file.')
        path = (self.st.data / value).resolve()
        if self.st.data not in path.parents or (required and not path.is_file()):
            raise ValueError('Media must exist inside this project.')
        return path

    def shot(self, ident):
        row = self.st.db.one('SELECT * FROM shots WHERE id=?', (int(ident),))
        if not row:
            raise ValueError('Shot not found.')
        return row

    def checkpoint(self, label):
        self.st.editor.checkpoint(label)

    def png(self, file, folder='uploads/creative'):
        raw = base64.b64decode(file.get('data', '').split(',')[-1], validate=True)
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or len(raw) < 33 or len(raw) > 16*1024*1024:
            raise ValueError('Choose a PNG image smaller than 16 MB.')
        width, height = struct.unpack('>II', raw[16:24])
        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise ValueError('PNG dimensions must be between 1 and 8192 pixels.')
        dest = self.st.data / folder / (uuid.uuid4().hex + '.png')
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(raw)
        return self.st.rel(dest)

    def action(self, body):
        handlers = {
            'look_save': self.look_save, 'look_bind': self.look_bind, 'review': self.review,
            'direction_save': self.direction_save, 'direction_preview': self.direction_preview,
            'recipe_save': self.recipe_save, 'recipe_assign': self.recipe_assign,
            'recipe_check': self.recipe_check, 'experiment': self.experiment, 'use_result': self.use_result,
            'repair': self.repair, 'performance': self.performance, 'performance_save': self.performance_save,
            'performance_apply': self.performance_apply, 'phrase_generate': self.phrase_generate,
            'pronunciations': self.pronunciations,
        }
        handler = handlers.get(body.get('action'))
        if not handler:
            raise ValueError('Unknown creative tool.')
        return handler(body)

    # Immutable asset look versions; scene assignments never rewrite another episode's asset.
    def look_save(self, body):
        asset = self.st.db.one('SELECT * FROM assets WHERE id=?', (int(body['asset_id']),))
        if not asset:
            raise ValueError('Cast entry not found.')
        name = str(body.get('name') or '').strip()[:100]
        if not name:
            raise ValueError('Name this reference collection.')
        refs = list(body.get('references') or [])
        if len(refs) + len(body.get('files') or []) > 12:
            raise ValueError('Use at most 12 reference images in a collection.')
        for ref in refs:
            path = self.path(ref['path'])
            if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
                raise ValueError('References must be image files.')
        for file in body.get('files') or []:
            refs.append({'path': self.png(file), 'label': str(file.get('name') or 'Reference')[:100]})
        if not refs and asset.get('image'):
            self.path(asset['image'])
            refs = [{'path': asset['image'], 'label': 'Current reference'}]
        if not refs:
            raise ValueError('Add a reference image in Cast or upload a PNG here.')
        refs = [{'path': r['path'], 'label': str(r.get('label') or 'Reference')[:100]} for r in refs]
        data = {'references': refs, 'notes': str(body.get('notes') or '')[:4000],
                'description': asset.get('description') or '', 'lora': asset.get('lora') or '',
                'lora_strength': number(asset.get('lora_strength') or 1, -4, 4)}
        self.checkpoint('Before reference collection version')
        version = self.st.db.one('SELECT COALESCE(MAX(version),0)+1 n FROM asset_looks WHERE asset_id=? AND name=?', (asset['id'], name))['n']
        ident = uuid.uuid4().hex
        self.st.db.x('INSERT INTO asset_looks VALUES(?,?,?,?,?,?)', (ident, asset['id'], name, version, json.dumps(data), time.time()))
        return {'ok': True, 'id': ident, 'version': version}

    def look_bind(self, body):
        scene = str(body['scene'])
        if not self.st.db.one('SELECT id FROM shots WHERE scene=?', (scene,)):
            raise ValueError('Choose an existing scene.')
        aid = int(body['asset_id'])
        look = self.st.db.one('SELECT * FROM asset_looks WHERE id=? AND asset_id=?', (body.get('look_id'), aid))
        context = self.st.editor.context(scene)
        variants = [v for v in context['variants'] if v['asset_id'] != aid]
        if body.get('look_id'):
            if not look or not self.st.db.one('SELECT id FROM assets WHERE id=?', (aid,)):
                raise ValueError('Reference collection no longer exists.')
            data = json.loads(look['json']); index = int(body.get('view_index', 0))
            if not 0 <= index < len(data['references']):
                raise ValueError('Choose a collection reference.')
            image = data['references'][index]['path']; self.path(image)
            variants.append({'asset_id': aid, 'image': image, 'notes': data['notes']})
            if len(variants) > 3:
                raise ValueError('A scene supports up to three reference overrides. Remove another override first.')
        self.checkpoint('Before scene reference collection')
        with self.st.db.lock, self.st.db.conn:
            if look:
                self.st.db.conn.execute('INSERT OR REPLACE INTO scene_looks VALUES(?,?,?,?)', (scene, aid, look['id'], index))
            else:
                self.st.db.conn.execute('DELETE FROM scene_looks WHERE scene=? AND asset_id=?', (scene, aid))
            context['variants'] = variants
            self.st.db.conn.execute('INSERT OR REPLACE INTO continuity VALUES(?,?)', (scene, json.dumps(context)))
        return {'ok': True}

    def scene_assets(self, scene, assets):
        versions = {r['asset_id']: json.loads(r['json']) for r in self.st.db.q('SELECT l.asset_id,l.json FROM scene_looks s JOIN asset_looks l ON l.id=s.look_id WHERE s.scene=?', (scene,))}
        return [dict(a, description=versions[a['id']]['description'], lora=versions[a['id']]['lora'], lora_strength=versions[a['id']]['lora_strength']) if a['id'] in versions else a for a in assets]

    def review_signature(self, shot):
        return digest([shot['keyframe_take'], self.st.production.signature(shot, 'keyframe')])

    def review(self, body):
        shot = self.shot(body['shot_id']); signature = self.review_signature(shot)
        if not shot['keyframe_take']:
            raise ValueError('Select a keyframe before reviewing continuity.')
        if body.get('signature') != signature:
            raise ValueError('This shot changed. Review its current references and keyframe again.')
        self.checkpoint('Before continuity review')
        self.st.db.x('INSERT OR REPLACE INTO continuity_reviews VALUES(?,?,?)', (shot['id'], signature, str(body.get('notes') or '')[:2000]))
        return {'ok': True}

    # Director data is model-independent. Extra conditioning is sent only to explicit workflow slots.
    def direction(self, sid):
        row = self.st.db.one('SELECT json FROM shot_direction WHERE shot_id=?', (sid,))
        return json.loads(row['json']) if row else {}

    def direction_save(self, body):
        shot = self.shot(body['shot_id']); old = self.direction(shot['id'])
        boxes = body.get('boxes') or []
        if len(boxes) > 12:
            raise ValueError('Use at most 12 composition subjects.')
        clean = []
        for b in boxes:
            aid = int(b['asset_id'])
            if not self.st.db.one('SELECT id FROM assets WHERE id=?', (aid,)):
                raise ValueError('Composition cast entry no longer exists.')
            clean.append({'asset_id': aid, 'x': number(b['x'], 0, 1), 'y': number(b['y'], 0, 1),
                          'width': number(b.get('width', .2), .02, 1), 'height': number(b.get('height', .6), .02, 1)})
        camera = {}
        for key in ('start', 'end'):
            c = (body.get('camera') or {}).get(key) or {'x': .5, 'y': .5, 'zoom': 1}
            camera[key] = {'x': number(c['x'], 0, 1), 'y': number(c['y'], 0, 1), 'zoom': number(c['zoom'], 1, 4)}
        data = {'boxes': clean, 'camera': camera, 'notes': str(body.get('notes') or '')[:4000],
                'joints': body.get('joints') or [], 'conditioning': bool(body.get('conditioning'))}
        if len(data['joints']) > 64 or any(len(p) != 2 for p in data['joints']):
            raise ValueError('Invalid pose points.')
        data['joints'] = [[number(x, 0, 1), number(y, 0, 1)] for x, y in data['joints']]
        for slot in ('pose', 'depth', 'layout'):
            value = old.get(slot)
            if body.get(slot + '_file'):
                value = self.png(body[slot + '_file'])
            if body.get('clear_' + slot):
                value = None
            if value:
                self.path(value)
            data[slot] = value
        if data['conditioning'] and not any(data.get(slot) for slot in ('pose','depth','layout')):
            raise ValueError('Choose a pose, depth or layout input before enabling conditioning.')
        self.checkpoint('Before shot direction')
        self.st.db.x('INSERT OR REPLACE INTO shot_direction VALUES(?,?)', (shot['id'], json.dumps(data)))
        return {'ok': True}

    def camera_filter(self, shot, width, height, fps):
        data = self.direction(shot['id'])
        if not data.get('camera'):
            return None
        a, b = data['camera']['start'], data['camera']['end']
        frames = max(1, round(float(shot['duration']) * fps) - 1)
        z = f"({a['zoom']}+({b['zoom']}-{a['zoom']})*min(on/{frames},1))"
        x = f"max(0,min(iw-iw/zoom,iw*({a['x']}+({b['x']}-{a['x']})*min(on/{frames},1))-iw/zoom/2))"
        y = f"max(0,min(ih-ih/zoom,ih*({a['y']}+({b['y']}-{a['y']})*min(on/{frames},1))-ih/zoom/2))"
        return f"scale={width*2}:{height*2}:force_original_aspect_ratio=increase,crop={width*2}:{height*2},zoompan=z='{z}':x='{x}':y='{y}':d=1:s={width}x{height}:fps={fps},setsar=1"

    def direction_preview(self, body):
        from anime_studio import ffmpeg
        shot = self.shot(body['shot_id']); image = self.st.take_path(shot['keyframe_take'])
        if not image:
            raise ValueError('Select a keyframe to preview the camera move.')
        vf = self.camera_filter(shot, 640, 360, int(self.st.settings()['out_fps']))
        if not vf:
            raise ValueError('Save the shot direction first.')
        path = self.st.data/'cache'/('camera-'+uuid.uuid4().hex+'.mp4')
        ffmpeg(['-loop', '1', '-i', str(image), '-vf', vf, '-t', str(min(30, float(shot['duration']))), '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)])
        return {'ok': True, 'url': '/media/'+self.st.rel(path)}

    # Recipe versions contain API graphs, exposed scalar controls and manual dependency notes.
    def seed_recipes(self):
        if self.st.db.one('SELECT id FROM generation_recipes LIMIT 1'):
            return
        from anime_studio import STARTERS, DEFAULT_STARTER
        key = copy.deepcopy(STARTERS['keyframe'][DEFAULT_STARTER['keyframe']]['wf'])
        saves = [k for k, n in key.items() if n['class_type'] == 'SaveImage']
        if saves:
            output = key[saves[0]]['inputs']['images']
            key['9001'] = {'class_type': 'LoadImage', 'inputs': {'image': 'source.png'}, '_meta': {'title': 'SOURCE'}}
            key['9002'] = {'class_type': 'LoadImage', 'inputs': {'image': 'mask.png'}, '_meta': {'title': 'MASK'}}
            key['9003'] = {'class_type': 'ImageCompositeMasked', 'inputs': {'destination': ['9001', 0], 'source': output, 'mask': ['9002', 1], 'x': 0, 'y': 0, 'resize_source': True}}
            key[saves[0]]['inputs']['images'] = ['9003', 0]
            self.insert_recipe('Masked keyframe repair', 'keyframe', {'workflow': key, 'fps': 0, 'max_frames': 0, 'controls': [], 'purpose': 'image_repair', 'requirements': 'Uses the shipped Qwen image-edit models and the core ImageCompositeMasked node.'})
        video = STARTERS['animate_flf'][DEFAULT_STARTER['animate_flf']]
        self.insert_recipe('Boundary-guided video retake', 'animate', {'workflow': video['wf'], 'fps': video['fps'], 'max_frames': video['max_frames'], 'controls': [], 'purpose': 'video_repair', 'requirements': 'Uses the shipped Wan first/last-frame models. Review the repaired interval and cut boundaries before accepting.'})

    def insert_recipe(self, name, stage, data):
        ident = uuid.uuid4().hex
        version = self.st.db.one('SELECT COALESCE(MAX(version),0)+1 n FROM generation_recipes WHERE name=? AND stage=?', (name, stage))['n']
        self.st.db.x('INSERT INTO generation_recipes VALUES(?,?,?,?,?,?)', (ident, name, version, stage, json.dumps(data), time.time()))
        return {'ok': True, 'id': ident, 'version': version}

    def recipe(self, ident):
        row = self.st.db.one('SELECT * FROM generation_recipes WHERE id=?', (ident,))
        if not row:
            raise ValueError('Recipe version not found.')
        return dict(row, **json.loads(row['json']))

    def recipe_save(self, body):
        from anime_studio import check_workflow
        stage = body.get('stage')
        if stage not in ('keyframe', 'animate', 'lipsync', 'upscale', 'voice'):
            raise ValueError('Choose a supported generation stage.')
        name = str(body.get('name') or '').strip()[:100]
        if not name:
            raise ValueError('Name this recipe.')
        base = self.recipe(body['base_id']) if body.get('base_id') else None
        row = self.st.wf_row(stage)
        workflow = body.get('workflow') or (base['workflow'] if base else json.loads(row['json']) if row else None)
        errors = check_workflow(workflow, stage)
        if errors:
            raise ValueError(' '.join(errors))
        if len(workflow) > 1000:
            raise ValueError('Recipe is too large.')
        controls = body.get('controls') or []
        if len(controls) > 24:
            raise ValueError('Expose at most 24 controls.')
        cleaned = []; seen = set()
        for c in controls:
            node, field = str(c['node']), str(c['field'])
            default = workflow.get(node, {}).get('inputs', {}).get(field)
            if default is None or isinstance(default, (dict, list)) or (node, field) in seen or '.' in node or '.' in field:
                raise ValueError('Controls must identify distinct existing scalar inputs.')
            title = workflow[node].get('_meta', {}).get('title', '').upper()
            if field in ('image', 'audio', 'video', 'file', 'seed', 'noise_seed', 'filename_prefix', 'width', 'height', 'length', 'ref_text', 'instruct') or (title in ('PROMPT', 'NEGATIVE', 'REF_TEXT') and field in ('text','target_text','prompt','text_g')):
                raise ValueError('Media, dialogue, seed and dimensions are managed by the shot, not exposed recipe controls.')
            seen.add((node, field))
            cleaned.append({'node': node, 'field': field, 'label': str(c.get('label') or field)[:100], 'default': default,
                            'type': 'boolean' if isinstance(default, bool) else 'number' if isinstance(default, (int, float)) else 'text'})
        fps = number(body.get('fps', base['fps'] if base else row['fps'] if row else 16), 0, 120)
        max_frames = int(number(body.get('max_frames', base['max_frames'] if base else row['max_frames'] if row else 81), 0, 4097))
        if stage in ('animate', 'lipsync') and (fps <= 0 or max_frames < 1):
            raise ValueError('Video recipes need model fps and a frame limit.')
        purpose = body.get('purpose', base.get('purpose', 'generation') if base else 'generation')
        if purpose not in ('generation', 'image_repair', 'video_repair') or (purpose == 'image_repair' and stage != 'keyframe') or (purpose == 'video_repair' and stage != 'animate'):
            raise ValueError('Repair recipes must use the matching image/video stage.')
        self.checkpoint('Before generation recipe version')
        return self.insert_recipe(name, stage, {'workflow': workflow, 'controls': cleaned, 'fps': fps, 'max_frames': max_frames,
                                  'purpose': purpose,
                                  'requirements': str(body.get('requirements') or '')[:8000]})

    def control_values(self, recipe, incoming):
        result = {}
        allowed = {c['node']+'.'+c['field']: c for c in recipe['controls']}
        if not isinstance(incoming, dict) or set(incoming)-set(allowed):
            raise ValueError('Unknown recipe control.')
        for key, c in allowed.items():
            value = incoming.get(key, c['default'])
            if c['type'] == 'number':
                value = number(value, -1e12, 1e15)
                if isinstance(c['default'], int):
                    if value != int(value): raise ValueError(c['label']+' needs a whole number.')
                    value = int(value)
            elif c['type'] == 'boolean':
                if not isinstance(value, bool): raise ValueError(c['label']+' needs a checkbox value.')
            else:
                value = str(value)
                if len(value) > 20000: raise ValueError('Recipe text is too long.')
            result[key] = value
        return result

    def patched_template(self, recipe, values):
        result = copy.deepcopy(recipe['workflow'])
        for key, value in self.control_values(recipe, values).items():
            node, field = key.split('.', 1); result[node]['inputs'][field] = value
        return result

    def assignment(self, sid, stage):
        key = 'animate' if stage == 'continue' else stage
        row = self.st.db.one('SELECT * FROM shot_recipes WHERE shot_id=? AND stage=?', (sid, key))
        return {'recipe_id': row['recipe_id'], 'values': json.loads(row['values_json']), 'missing':not bool(self.st.db.one('SELECT id FROM generation_recipes WHERE id=?',(row['recipe_id'],)))} if row else None

    def recipe_assign(self, body):
        shot = self.shot(body['shot_id']); stage = body['stage']
        if stage not in ('keyframe', 'animate', 'lipsync', 'upscale', 'voice'):
            raise ValueError('Choose a generation stage.')
        values = {}; recipe = None
        if body.get('recipe_id'):
            recipe = self.recipe(body['recipe_id'])
            if recipe['stage'] != stage or recipe.get('purpose') != 'generation':
                raise ValueError('Choose a generation recipe for this stage.')
            values = self.control_values(recipe, body.get('values') or {})
        self.checkpoint('Before shot recipe assignment')
        if recipe:
            self.st.db.x('INSERT OR REPLACE INTO shot_recipes VALUES(?,?,?,?)', (shot['id'], stage, recipe['id'], json.dumps(values)))
        else:
            self.st.db.x('DELETE FROM shot_recipes WHERE shot_id=? AND stage=?', (shot['id'], stage))
        return {'ok': True}

    def signature_data(self, shot, stage, override=None):
        assignment = override if override is not None else self.assignment(shot['id'], stage)
        result = {}
        if assignment and assignment.get('missing'):
            result['missing_recipe'] = assignment['recipe_id']
        elif assignment and assignment.get('recipe_id'):
            recipe = self.recipe(assignment['recipe_id'])
            result['recipe'] = [recipe['id'], digest(recipe['workflow']), assignment['values']]
        if stage == 'keyframe':
            result['looks'] = self.st.db.q('SELECT s.asset_id,s.look_id,s.view_index,l.json FROM scene_looks s JOIN asset_looks l ON l.id=s.look_id WHERE scene=? ORDER BY s.asset_id', (shot['scene'],))
            direction = self.direction(shot['id'])
            driven = {'notes':direction.get('notes')} if direction.get('notes') else {}
            if direction.get('conditioning'):
                driven['inputs'] = {slot:self.st.production.file_stamp(direction[slot]) for slot in ('pose','depth','layout') if direction.get(slot)}
            if driven: result['direction'] = driven
        if stage == 'voice':
            ids = {l['character_id'] for l in self.st.shot_lines(shot) if l.get('character_id')}
            result['pronunciations'] = [r for r in self.st.db.q('SELECT * FROM character_pronunciations ORDER BY asset_id') if r['asset_id'] in ids]
        return result if assignment or any(result.values()) else None

    def meta(self, job):
        return json.loads(job.get('creative_json') or '{}')

    def compatible(self, job, inst, cached_only=False):
        meta = self.meta(job)
        if not meta:
            return False
        inspected = self.inspect_recipe(meta['recipe'], meta.get('values') or {}, inst, cached_only=cached_only)
        return None if inspected is None else not inspected['problems']

    def inspect_recipe(self, recipe, values, inst, refresh=False, cached_only=False):
        template = self.patched_template(recipe, values)
        key = digest([inst['url'], template])
        cached = self.readiness.get(key)
        if cached and not refresh and time.time()-cached['checked'] < 60:
            return cached
        if cached_only:
            return None
        problems = []
        try:
            info = self.st.production.http('GET', inst['url'].rstrip('/')+'/object_info', timeout=8)
            for n in template.values():
                cls = n['class_type']; schema = info.get(cls)
                if schema is None:
                    problems.append('Missing node: '+cls); continue
                inputs = schema.get('input', {})
                specs = dict(inputs.get('required', {}), **inputs.get('optional', {}))
                for field, value in n.get('inputs', {}).items():
                    spec = specs.get(field, [])
                    if field not in ('image','audio','video','file') and spec and isinstance(spec[0], list) and isinstance(value, str) and value not in spec[0]:
                        problems.append(f'{cls}: unsupported {field}: {value}')
                    if len(spec)>1 and isinstance(spec[1],dict) and isinstance(value,(int,float)) and not isinstance(value,bool):
                        bounds=spec[1]
                        if value < bounds.get('min',-math.inf) or value > bounds.get('max',math.inf):
                            problems.append(f'{cls}: {field} is outside the supported range.')
        except Exception as exc:
            problems.append('Cannot inspect server: '+str(exc)[:250])
        result = {'id': inst['id'], 'name': inst['name'], 'checked': time.time(), 'problems': sorted(set(problems))}
        self.readiness[key] = result
        return result

    def recipe_check(self, body):
        recipe = self.recipe(body['recipe_id']); values = self.control_values(recipe, body.get('values') or {})
        servers = [self.inspect_recipe(recipe, values, inst, True) for inst in self.st.db.q('SELECT * FROM instances WHERE enabled=1') if recipe['stage'] in inst['roles'].split(',')]
        return {'ok': True, 'servers': servers, 'ready': any(not s['problems'] for s in servers), 'requirements': recipe['requirements']}

    def owned(self, body):
        sid = body.get('shot_id'); shot = self.shot(sid)
        return shot

    def wants_recipe(self, body):
        return bool(body.get('recipe_id') or any(self.assignment(sid, body['stage']) for sid in body.get('shot_ids') or []) or
                    any(self.assignment(t['shot_id'], 'voice') for t in body.get('lines') or []))

    def generate(self, body):
        with self.st.production.lock:
            before = self.st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
            try:
                return self._generate_recipes(body)
            except Exception:
                jobs = self.st.db.q("SELECT id FROM jobs WHERE id>? AND status='pending'", (before,))
                for job in jobs:
                    self.st.db.x('DELETE FROM creative_runs WHERE job_id=?', (job['id'],))
                    self.st.db.x('DELETE FROM production_inputs WHERE job_id=?', (job['id'],))
                    self.st.db.x('DELETE FROM jobs WHERE id=?', (job['id'],))
                raise

    def _generate_recipes(self, body):
        """Queue standard jobs, then freeze their workflow and complete composed inputs."""
        from anime_studio import API, Worker
        stage = body['stage']; targets = body.get('shot_ids') or sorted({t['shot_id'] for t in body.get('lines') or []})
        if not targets:
            raise ValueError('Choose a shot.')
        prepared = []
        for sid in targets:
            shot = self.shot(sid)
            assignment = ({'recipe_id': body['recipe_id'], 'values': body.get('values') or {}} if body.get('recipe_id') else self.assignment(sid, stage))
            if assignment:
                recipe = self.recipe(assignment['recipe_id']); values = self.control_values(recipe, assignment['values'])
                if recipe['stage'] != ('animate' if stage == 'continue' else stage):
                    raise ValueError('Recipe does not match this stage.')
                self.require_ready(recipe, values)
                directed = self.direction(sid)
                if recipe['stage'] == 'keyframe' and directed.get('conditioning'):
                    for slot in ('pose', 'depth', 'layout'):
                        if directed.get(slot): self.require_slot(recipe, slot.upper())
            else:
                recipe, values = None, {}
                if not self.st.production.ready(stage, [sid]):
                    raise ValueError('No ready server for '+stage+'. Run readiness checks.')
            prepared.append((shot, recipe, values))
        made = []; skipped = []
        # Validate everything before creating any of this request's jobs.
        for shot, recipe, values in prepared:
            request = dict(body, shot_ids=[shot['id']], _creative_batch=bool(body.get('batch')))
            lip_workflow = None
            if recipe and stage == 'lipsync':
                lip_workflow = {'json':json.dumps(self.patched_template(recipe, values)), 'fps':recipe['fps'], 'max_frames':recipe['max_frames']}
                request['_lip_workflow'] = lip_workflow
            if body.get('lines'):
                request['lines'] = [t for t in body['lines'] if t['shot_id'] == shot['id']]
                request['shot_ids'] = []
            before = self.st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
            result = API(self.st)._generate(request); skipped.extend(result.get('skipped') or [])
            jobs = self.st.db.q('SELECT * FROM jobs WHERE id>?', (before,))
            for job in jobs:
                if body.get('seed') is not None:
                    job['seed'] = int(number(body['seed'], 1, 2**48)) + len(made)
                    self.st.db.x('UPDATE jobs SET seed=? WHERE id=?', (job['seed'], job['id']))
                if recipe:
                    key, p = Worker(self.st).compose(dict(job, _lip_workflow=lip_workflow) if lip_workflow else job)
                    self.freeze(job, recipe, values, key, p, manual=bool(body.get('manual')), batch=body.get('batch'))
                made.append(job['id'])
        return {'ok': True, 'queued': len(made), 'job_ids': made, 'skipped': skipped}

    def require_ready(self, recipe, values):
        servers = self.st.db.q('SELECT * FROM instances WHERE enabled=1')
        checks = [self.inspect_recipe(recipe, values, s) for s in servers if recipe['stage'] in s['roles'].split(',')]
        if not any(not c['problems'] for c in checks):
            problems = '; '.join(p for c in checks for p in c['problems']) or 'No enabled server accepts '+recipe['stage']+' jobs.'
            raise ValueError('Recipe is not ready. '+problems+' '+recipe.get('requirements', ''))

    def freeze(self, job, recipe, values, key, params, **extras):
        p = copy.deepcopy(params)
        if recipe['stage'] == 'animate' and extras.get('kind') not in ('repair_video','continuation'):
            wants_end = any(n.get('_meta', {}).get('title', '').upper() == 'END_IMAGE' for n in recipe['workflow'].values())
            if wants_end and not p['files'].get('end'):
                nxt = self.st.neighbor(self.shot(job['shot_id']), 1)
                end = self.st.take_path(nxt['keyframe_take']) if nxt else None
                if not end: raise ValueError('This recipe needs the next shot’s selected keyframe.')
                p['files']['end'] = end
            if key == 'animate_flf' and not wants_end:
                raise ValueError('End-on-next requires a recipe with an END_IMAGE input.')
            key = 'animate_flf' if wants_end else 'animate'
        p['files'] = {slot: self.st.rel(path) for slot, path in p['files'].items()}
        if recipe['stage'] in ('animate', 'lipsync') and p.get('length') and extras.get('kind') != 'continuation':
            shot = self.shot(job['shot_id'])
            requested = extras['span']['end']-extras['span']['start'] if extras.get('span') else float(shot['duration'])
            seconds = min(requested, (int(recipe['max_frames'])-1)/float(recipe['fps']))
            p['length'] = min(int(recipe['max_frames']), max(1, 4*math.ceil(seconds*float(recipe['fps'])/4)+1))
        direction = self.direction(job['shot_id'])
        if recipe['stage'] == 'keyframe' and recipe.get('purpose') == 'generation':
            for slot in ('pose', 'depth', 'layout'):
                if direction.get('conditioning') and direction.get(slot):
                    self.require_slot(recipe, slot.upper())
                    p['files']['creative_'+slot.upper()] = direction[slot]
        if recipe['stage'] == 'voice' and extras.get('kind') == 'phrase':
            p['prompt'] = self.spoken(job.get('voice_character_id'), p.get('prompt', ''))
        run_id = uuid.uuid4().hex
        meta = {'run_id': run_id, 'recipe': recipe, 'values': values, 'key': key, 'params': p, 'kind': 'recipe', **extras}
        self.st.db.x('UPDATE jobs SET creative_json=? WHERE id=?', (json.dumps(meta), job['id']))
        shot = self.shot(job['shot_id'])
        override = {'recipe_id': recipe['id'], 'values': values} if recipe['id'] else None
        signature = self.st.production.signature(shot, job['stage'], job.get('segment'), override)
        self.st.db.x('INSERT OR REPLACE INTO production_inputs VALUES(?,?)', (job['id'], signature))
        self.st.db.x('INSERT INTO creative_runs VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?,NULL)', (run_id, job['id'], job['shot_id'], recipe['id'], meta['kind'], 'pending', json.dumps(meta)))
        return run_id

    def patch_controls(self, job, workflow):
        meta = self.meta(job)
        if meta:
            for key, value in meta['values'].items():
                node, field = key.split('.', 1)
                if node not in workflow: raise ValueError('An exposed control belongs to a removed node.')
                workflow[node]['inputs'][field] = value
        return workflow

    def require_slot(self, recipe, tag):
        from anime_studio import SAVE_NODES
        workflow = recipe['workflow']
        candidates = {key for key, node in workflow.items() if node.get('_meta', {}).get('title', '').upper() == tag and node['class_type'] == 'LoadImage'}
        seen = set()
        def visit(key):
            if key in seen or key not in workflow: return
            seen.add(key)
            for value in workflow[key].get('inputs', {}).values():
                if isinstance(value, list) and len(value) == 2: visit(str(value[0]))
        for key, node in workflow.items():
            if node['class_type'] in SAVE_NODES: visit(key)
        if not candidates & seen:
            raise ValueError(f'This recipe needs a connected LoadImage node titled {tag}. Use a compatible API workflow or turn off conditioning.')

    def composed(self, job):
        meta = self.meta(job); p = copy.deepcopy(meta['params'])
        p['files'] = {slot: self.path(path) for slot, path in p['files'].items()}
        p['seed'] = job['seed']
        return meta['key'], p

    def workflow(self, job):
        meta = self.meta(job)
        return {'json': json.dumps(self.patched_template(meta['recipe'], meta['values'])), 'fps': meta['recipe']['fps'], 'max_frames': meta['recipe']['max_frames']} if meta else None

    def experiment(self, body):
        with self.st.production.lock:
            before = self.st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
            try: return self._experiment(body)
            except Exception:
                for row in self.st.db.q("SELECT id FROM jobs WHERE id>? AND status='pending'", (before,)):
                    for table, field in [('creative_runs','job_id'),('production_inputs','job_id'),('jobs','id')]:
                        self.st.db.x('DELETE FROM '+table+' WHERE '+field+'=?', (row['id'],))
                raise

    def _experiment(self, body):
        recipe = self.recipe(body['recipe_id'])
        if recipe.get('purpose') != 'generation':
            raise ValueError('Use Repair for repair recipes.')
        values = self.control_values(recipe, body.get('values') or {})
        variants = body.get('variants') or [{}]
        if not isinstance(variants, list) or not 1 <= len(variants) <= 8:
            raise ValueError('Compare one to eight variants at a time.')
        clean = [self.control_values(recipe, dict(values, **v)) for v in variants]
        for v in clean: self.require_ready(recipe, v)
        shot = self.shot(body['shot_id']); batch = uuid.uuid4().hex
        seed = int(number(body.get('seed', 1), 1, 2**48))
        ids = []
        if recipe['stage'] == 'voice':
            line = next((l for l in self.st.shot_lines(shot) if l['line_id'] == body.get('line_id')), None)
            if not line: raise ValueError('Choose a saved dialogue line for a voice experiment.')
            targets = {'lines': [{'shot_id': shot['id'], 'index': line['index']}]}
        else:
            targets = {'shot_ids': [shot['id']]}
        for i, v in enumerate(clean):
            r = self.generate({'stage': recipe['stage'], 'recipe_id': recipe['id'], 'values': v, **targets, 'takes': 1,
                               'seed': seed+i if body.get('vary_seed', True) else seed, 'manual': True, 'batch': batch})
            ids.extend(r['job_ids'])
        return {'ok': True, 'queued': len(ids), 'job_ids': ids, 'batch': batch}

    def started(self, job, inst):
        meta = self.meta(job)
        if not meta: return
        if not meta.get('server'):
            meta['server'] = {'id':inst['id'],'name':inst['name'],'address':inst['url'],
                              'hardware':self.st.health.get(inst['id'],{}).get('hardware',[])}
            self.st.db.x('UPDATE jobs SET creative_json=? WHERE id=?',(json.dumps(meta),job['id']))
            self.st.db.x('UPDATE creative_runs SET json=? WHERE id=?',(json.dumps(meta),meta['run_id']))

    def completed(self, job, status, error=None):
        meta = self.meta(job)
        if meta:
            self.st.db.x('UPDATE creative_runs SET status=?,started=?,finished=?,instance_id=?,error=? WHERE id=?',
                         (status, job.get('started'), time.time(), job.get('instance_id'), error, meta['run_id']))

    def use_result(self, body):
        from anime_studio import API
        run = self.st.db.one('SELECT * FROM creative_runs WHERE id=?', (body['run_id'],))
        if not run or run['status'] != 'done':
            raise ValueError('This result is not complete.')
        meta = json.loads(run['json']); shot = self.shot(run['shot_id'])
        live = self.st.db.one('SELECT * FROM jobs WHERE id=?', (run['job_id'],))
        if not live or self.meta(live).get('run_id') != run['id']:
            raise ValueError('This queue record was cleared. Select the retained take in Shots or Voices.')
        table = 'voice_takes' if meta['recipe']['stage'] == 'voice' else 'takes'
        take = self.st.db.one('SELECT * FROM '+table+' WHERE id=? AND job_id=? AND shot_id=?', (body['take_id'], run['job_id'], shot['id']))
        if not take: raise ValueError('The result was removed from this shot.')
        if meta['kind'].startswith('repair_'):
            source = self.st.db.one('SELECT * FROM takes WHERE id=? AND shot_id=?', (meta['source_id'], shot['id']))
            if not source: raise ValueError('The original repair source was removed.')
            token = digest([source['id'], self.st.production.file_stamp(source['path']), self.st.production.signature(shot, source['stage'], source.get('segment'))])
            if token != meta['source_token']: raise ValueError('Shot inputs changed. Review this repair in Shots before selecting it.')
        elif meta['kind'] != 'recipe':
            raise ValueError('Use the performance editor to apply a phrase retake.')
        if table == 'voice_takes':
            line = next((l for l in self.st.shot_lines(shot) if l['line_id'] == take['line_id'] and l['text'] == take['text'] and l['character_id'] == take['character_id']), None)
            if not line: raise ValueError('The dialogue line changed. This take cannot replace it.')
        if meta['kind'] == 'recipe' and meta['recipe'].get('purpose') == 'generation':
            self.recipe_assign({'shot_id': shot['id'], 'stage': meta['recipe']['stage'], 'recipe_id': run['recipe_id'], 'values': meta['values']})
        if table == 'voice_takes':
            return API(self.st).line_set({'shot_id': shot['id'], 'index': line['index'], 'line_id': line['line_id'], 'src': 'take', 'take_id': take['id']})
        self.checkpoint('Before creative take selection')
        return API(self.st).approve({'take_id': take['id']})

    def current_recipe(self, stage, sid=None):
        assignment = self.assignment(sid, stage) if sid else None
        if assignment:
            return self.recipe(assignment['recipe_id']), assignment['values']
        row = self.st.wf_row(stage)
        if not row:
            raise ValueError('Configure the '+stage+' workflow first.')
        return {'id': None, 'name': 'Current '+stage+' workflow', 'version': 0, 'stage': stage, 'workflow': json.loads(row['json']),
                'fps': row['fps'], 'max_frames': row['max_frames'], 'controls': [], 'purpose': 'generation', 'requirements': ''}, {}

    def repair(self, body):
        from anime_studio import Worker, ffmpeg, media_seconds
        shot = self.shot(body['shot_id'])
        source = self.st.db.one('SELECT * FROM takes WHERE id=? AND shot_id=?', (body['take_id'], shot['id']))
        if not source:
            raise ValueError('Choose an existing take from this shot.')
        path = self.path(source['path']); image = source['stage'] == 'keyframe'
        recipe = self.recipe(body['recipe_id'])
        expected = 'image_repair' if image else 'video_repair'
        if recipe.get('purpose') != expected:
            raise ValueError('Choose a compatible '+expected.replace('_', ' ')+' recipe.')
        prompt = str(body.get('prompt') or '').strip()[:10000]
        if not prompt:
            raise ValueError('Describe the repair or retake.')
        self.require_ready(recipe, {})
        if not image and source['stage'] not in ('animate', 'continue', 'lipsync', 'upscale'):
            raise ValueError('Choose a keyframe or video take.')
        token = digest([source['id'], self.st.production.file_stamp(source['path']), self.st.production.signature(shot, source['stage'], source.get('segment'))])
        if body.get('source_token') != token:
            raise ValueError('The source or shot changed. Reopen Repair and review it again.')
        if image:
            if not body.get('mask'):
                raise ValueError('Paint the area to repair.')
            self.require_slot(recipe, 'SOURCE'); self.require_slot(recipe, 'MASK')
            mask = self.png(body['mask'])
            p = {'files': {'ref1': path, 'creative_SOURCE': path, 'creative_MASK': self.path(mask)}, 'prompt': prompt,
                 'negative': self.st.settings().get('keyframe_negative', ''), 'width': self.st.settings()['kf_width'], 'height': self.st.settings()['kf_height'], 'loras': []}
            key = 'keyframe'; span = None
        else:
            info, total = probe(path)
            video = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
            if not video:
                raise ValueError('Source has no video stream.')
            start, end = number(body.get('start'), 0, total), number(body.get('end'), 0, total)
            if end-start < .15:
                raise ValueError('Choose a repair interval at least 0.15 seconds long.')
            maximum = (recipe['max_frames']-1)/recipe['fps']
            if end-start > maximum+.001:
                raise ValueError(f'This recipe can repair at most {maximum:.2f} seconds at a time.')
            folder = self.st.data/'cache'/('repair-'+uuid.uuid4().hex); folder.mkdir()
            first, last = folder/'first.png', folder/'last.png'
            ffmpeg(['-ss', str(start), '-i', str(path), '-frames:v', '1', str(first)])
            ffmpeg(['-ss', str(min(end, max(0, total-.1))), '-i', str(path), '-frames:v', '1', str(last)])
            p = {'files': {'start': first, 'end': last}, 'prompt': prompt, 'negative': self.st.settings().get('video_negative', ''),
                 'width': int(video['width']), 'height': int(video['height']), 'length': min(recipe['max_frames'], 4*math.ceil((end-start)*recipe['fps']/4)+1)}
            if any(n.get('_meta', {}).get('title', '').upper() == 'VIDEO_IN' for n in recipe['workflow'].values()):
                clip = folder/'interval.mp4'
                ffmpeg(['-ss', str(start), '-i', str(path), '-t', str(end-start), '-an', '-c:v', 'libx264', str(clip)])
                p['files']['creative_VIDEO_IN'] = clip
            key = 'animate_flf' if any(n.get('_meta', {}).get('title', '').upper() == 'END_IMAGE' for n in recipe['workflow'].values()) else 'animate'
            span = {'start': start, 'end': end, 'total': total, 'width': int(video['width']), 'height': int(video['height']), 'fps': float(source.get('fps') or recipe['fps'])}
        # Queuing a repair is a deliberate, single candidate; never replace the source here.
        self.checkpoint('Before repair request')
        jid = self.st.db.x("INSERT INTO jobs(shot_id,stage,status,seed,created,progress,segment) VALUES(?,?,'pending',?,?,'waiting',?)",
                           (shot['id'], recipe['stage'], int(number(body.get('seed', 1), 1, 2**48)), time.time(), source.get('segment')))
        job = self.st.db.one('SELECT * FROM jobs WHERE id=?', (jid,))
        p.update(seed=job['seed'], prefix=f'anime/repair_{jid}')
        try:
            self.freeze(job, recipe, {}, key, p, kind='repair_image' if image else 'repair_video', manual=True,
                        source_id=source['id'], source_token=token, source_path=source['path'], source_stage=source['stage'], span=span)
            self.st.db.x('INSERT OR REPLACE INTO production_inputs VALUES(?,?)', (jid, self.st.production.signature(shot, source['stage'], source.get('segment'))))
        except Exception:
            self.st.db.x('DELETE FROM jobs WHERE id=?', (jid,)); raise
        return {'ok': True, 'queued': 1, 'job_ids': [jid]}

    def finish_special(self, job, key, local):
        from anime_studio import ffmpeg, media_seconds, stitch_frames, VID_EXT, AUD_EXT, IMG_EXT, record_import_file
        meta = self.meta(job)
        if meta.get('kind') == 'phrase':
            audio = next((p for p in local if p.suffix.lower() in AUD_EXT), None)
            if not audio:
                raise ValueError('The phrase workflow produced no audio.')
            dest = self.st.data/'voices'/'lines'/('phrase-'+uuid.uuid4().hex+'.wav'); dest.parent.mkdir(exist_ok=True)
            record_import_file(dest)
            ffmpeg(['-i', str(audio), '-ar', '48000', '-ac', '2', str(dest)])
            payload = {'path': self.st.rel(dest), 'seconds': media_seconds(dest), 'base_audio': meta['base_audio'],
                       'identity': meta['line_identity'], 'input_signature':(self.st.db.one('SELECT signature FROM production_inputs WHERE job_id=?',(job['id'],)) or {}).get('signature'), 'range': meta['range'], 'phrase': meta['phrase'], 'job_id': job['id']}
            self.st.db.x('INSERT INTO performance_takes VALUES(?,?,?,?,?)', (uuid.uuid4().hex, job['shot_id'], job['voice_line_id'], json.dumps(payload), time.time()))
            return True
        if meta.get('kind') != 'repair_video':
            return False
        span = meta['span']; source = self.path(meta['source_path'])
        video = next((p for p in local if p.suffix.lower() in VID_EXT), None)
        if not video:
            frames = [p for p in local if p.suffix.lower() in IMG_EXT]
            if not frames: raise ValueError('The interval recipe produced no video or frames.')
            video = self.st.data/'cache'/('repair-generated-'+uuid.uuid4().hex+'.mp4')
            record_import_file(video)
            stitch_frames(frames, meta['recipe']['fps'], video)
        length = span['end']-span['start']
        if media_seconds(video)+1/span['fps'] < length:
            raise ValueError('Generated video is shorter than the repair interval. Use a longer compatible recipe.')
        base = f"fps={span['fps']},scale={span['width']}:{span['height']},setsar=1"
        graph = []; parts = []
        if span['start'] > .001:
            graph.append(f"[0:v]trim=end={span['start']},setpts=PTS-STARTPTS,{base}[before]"); parts.append('[before]')
        graph.append(f'[1:v]trim=duration={length},setpts=PTS-STARTPTS,{base}[middle]'); parts.append('[middle]')
        if span['total']-span['end'] > .001:
            graph.append(f"[0:v]trim=start={span['end']},setpts=PTS-STARTPTS,{base}[after]"); parts.append('[after]')
        graph.append(''.join(parts)+f'concat=n={len(parts)}:v=1:a=0[v]')
        folder = self.st.data/'shots'/f"{job['shot_id']:04d}"; folder.mkdir(exist_ok=True)
        dest = folder/('repaired-'+uuid.uuid4().hex+'.mp4')
        record_import_file(dest)
        ffmpeg(['-i', str(source), '-i', str(video), '-filter_complex', ';'.join(graph), '-map', '[v]', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-t', str(span['total']), str(dest)])
        signature = self.st.db.one('SELECT signature FROM production_inputs WHERE job_id=?', (job['id'],))['signature']
        self.st.db.x('INSERT INTO takes(shot_id,stage,path,fps,seed,job_id,created,frames,segment,input_signature) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (job['shot_id'], meta['source_stage'], self.st.rel(dest), span['fps'], job['seed'], job['id'], time.time(), round(span['total']*span['fps']), job.get('segment'), signature))
        return True

    # Performance timing: imported or edited word alignment is exact user data;
    # automatic energy-based suggestions are explicitly estimates, never labelled forced alignment.
    def identity(self, line):
        return digest([line['line_id'], line['text'], line['character_id'], line.get('direction') or ''])

    def line(self, body):
        shot = self.shot(body['shot_id'])
        line = next((l for l in self.st.shot_lines(shot) if l['line_id'] == body.get('line_id')), None)
        if not line:
            raise ValueError('Dialogue changed. Reopen its performance editor.')
        return shot, line

    def performance_row(self, sid, line_id):
        row = self.st.db.one('SELECT json FROM line_performances WHERE shot_id=? AND line_id=?', (sid, line_id))
        return json.loads(row['json']) if row else {}

    def timing(self, sid, line):
        row = self.performance_row(sid, line['line_id'])
        return row if row.get('identity') == self.identity(line) else {}

    def performance_token(self, shot, line):
        return digest([self.identity(line), self.st.production.file_stamp(line.get('audio')), self.performance_row(shot['id'], line['line_id'])])

    def estimated_words(self, text, samples, rate, seconds):
        words = text.split()
        windows = [max((abs(v) for v in samples[i:i+320]), default=0) for i in range(0, len(samples), 320)]
        threshold = max(200, max(windows, default=0)*.08)
        active = [i for i, peak in enumerate(windows) if peak >= threshold] or list(range(max(1, len(windows))))
        weights = [max(1, len(re.sub(r'\W', '', word))) for word in words]; total = sum(weights); cursor = 0
        result = []
        def boundary(weight):
            position = weight/total*len(active)
            if position >= len(active): return min(seconds, (active[-1]+1)*.02)
            index = int(position)
            return min(seconds, (active[index]+position-index)*.02)
        for word, weight in zip(words, weights):
            start = boundary(cursor); cursor += weight
            end = boundary(cursor)
            result.append({'text': word, 'start': round(start, 4), 'end': round(end, 4)})
        return result

    def performance(self, body):
        shot, line = self.line(body)
        if not line.get('audio'):
            raise ValueError('Select or upload a performance before editing timing.')
        path = self.path(line['audio']); _, seconds = probe(path)
        if seconds > 600:
            raise ValueError('Edit performances up to ten minutes per line.')
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-t', '600', '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True)
        if raw.returncode: raise ValueError('Could not decode this performance.')
        samples = array.array('h'); samples.frombytes(raw.stdout)
        chunk = max(1, math.ceil(len(samples)/512))
        peaks = [round(max((abs(v) for v in samples[i:i+chunk]), default=0)/32768, 4) for i in range(0, len(samples), chunk)]
        saved = self.timing(shot['id'], line)
        aligned = saved.get('base_audio') == line['audio'] and bool(saved.get('words'))
        return {'ok': True, 'shot_id': shot['id'], 'line_id': line['line_id'], 'text': line['text'], 'character_id': line['character_id'],
                'token': self.performance_token(shot, line), 'audio_url': '/media/'+line['audio'], 'seconds': seconds, 'peaks': peaks,
                'words': saved['words'] if aligned else self.estimated_words(line['text'], samples, 16000, seconds),
                'source': saved.get('source', 'estimated') if aligned else 'estimated', 'lead_in': saved.get('lead_in', 0),
                'pause_after': saved.get('pause_after', float(self.st.settings().get('line_gap') or .3)),
                'candidates': [dict(id=r['id'], **json.loads(r['json'])) for r in self.st.db.q('SELECT * FROM performance_takes WHERE shot_id=? AND line_id=? ORDER BY created DESC', (shot['id'], line['line_id']))]}

    def performance_save(self, body):
        shot, line = self.line(body)
        if body.get('token') != self.performance_token(shot, line):
            raise ValueError('Performance changed. Reopen it before saving timing.')
        if not line.get('audio'):
            raise ValueError('Choose audio before editing its timing.')
        _, seconds = probe(self.path(line['audio']))
        words = body.get('words')
        if not isinstance(words, list) or [str(w.get('text')) for w in words] != line['text'].split():
            raise ValueError('Word timing must contain each saved spoken word, in order.')
        clean = []; last = 0
        for w in words:
            a, b = number(w['start'], 0, seconds), number(w['end'], 0, seconds)
            if b <= a or a < last-.001:
                raise ValueError('Word ranges must be positive, ordered and non-overlapping.')
            clean.append({'text': w['text'], 'start': a, 'end': b}); last = b
        source = body.get('source', 'edited')
        if source not in ('edited', 'imported', 'estimated','speech'): raise ValueError('Invalid alignment source.')
        data = {'identity': self.identity(line), 'base_audio': line['audio'], 'words': clean, 'source': source,
                'lead_in': number(body.get('lead_in', 0), 0, 30), 'pause_after': number(body.get('pause_after', .3), 0, 30)}
        terms = self.pronunciation_terms(line['character_id'], body['terms']) if line['character_id'] and 'terms' in body else None
        self.checkpoint('Before performance timing')
        previous = self.st.db.one('SELECT * FROM line_performances WHERE shot_id=? AND line_id=?', (shot['id'],line['line_id']))
        prior_terms = self.st.db.one('SELECT * FROM character_pronunciations WHERE asset_id=?', (line['character_id'],))
        self.st.db.x('INSERT OR REPLACE INTO line_performances VALUES(?,?,?)', (shot['id'], line['line_id'], json.dumps(data)))
        if terms is not None: self.st.db.x('INSERT OR REPLACE INTO character_pronunciations VALUES(?,?)', (line['character_id'],json.dumps(terms)))
        try: self.st.rebuild_audio(shot['id'])
        except Exception:
            if previous: self.st.db.x('INSERT OR REPLACE INTO line_performances VALUES(?,?,?)',(previous['shot_id'],previous['line_id'],previous['json']))
            else: self.st.db.x('DELETE FROM line_performances WHERE shot_id=? AND line_id=?',(shot['id'],line['line_id']))
            if terms is not None:
                if prior_terms: self.st.db.x('INSERT OR REPLACE INTO character_pronunciations VALUES(?,?)',(prior_terms['asset_id'],prior_terms['json']))
                else: self.st.db.x('DELETE FROM character_pronunciations WHERE asset_id=?',(line['character_id'],))
            raise
        return {'ok': True, 'token': self.performance_token(shot, line)}

    def pronunciation_terms(self, aid, terms):
        if not self.st.db.one("SELECT id FROM assets WHERE id=? AND kind='character'", (aid,)):
            raise ValueError('Choose a speaking character.')
        if not isinstance(terms, dict) or len(terms) > 100 or any(not isinstance(k, str) or not k.strip() or len(k)>100 or not isinstance(v, str) or not v.strip() or len(v)>500 for k, v in terms.items()):
            raise ValueError('Use up to 100 nonempty pronunciation pairs (word → spoken spelling).')
        return terms

    def pronunciations(self, body):
        aid = int(body['asset_id']); terms = self.pronunciation_terms(aid, body.get('terms') or {})
        self.checkpoint('Before pronunciation dictionary')
        self.st.db.x('INSERT OR REPLACE INTO character_pronunciations VALUES(?,?)', (aid, json.dumps(terms)))
        return {'ok': True}

    def spoken(self, aid, text):
        row = self.st.db.one('SELECT json FROM character_pronunciations WHERE asset_id=?', (aid,))
        terms = json.loads(row['json']) if row else {}
        if not terms: return text
        mapping = {k.casefold(): v for k, v in terms.items()}
        pattern = r'(?<!\w)(?:'+'|'.join(re.escape(k) for k in sorted(terms, key=len, reverse=True))+r')(?!\w)'
        return re.sub(pattern, lambda m: mapping[m[0].casefold()], text, flags=re.I)

    def phrase_generate(self, body):
        from anime_studio import Worker
        shot, line = self.line(body)
        if body.get('token') != self.performance_token(shot, line):
            raise ValueError('Performance changed. Save/reopen timing before requesting a phrase retake.')
        timing = self.timing(shot['id'], line)
        if timing.get('base_audio') != line.get('audio') or not timing.get('words'):
            raise ValueError('Review and save word timings before generating a phrase.')
        first, last = int(body['first']), int(body['last']); words = timing['words']
        if not 0 <= first <= last < len(words): raise ValueError('Choose a contiguous word range.')
        recipe, values = self.current_recipe('voice', shot['id']); self.require_ready(recipe, values)
        character = self.st.line_character(line); ref = self.st.ref_clip(character['id']) if character else None
        if not ref or not self.path(ref['path']).is_file(): raise ValueError('This character needs a reference voice.')
        phrase = ' '.join(w['text'] for w in words[first:last+1]); span = {'start': words[first]['start'], 'end': words[last]['end']}
        if self.st.db.one("SELECT id FROM jobs WHERE shot_id=? AND stage='voice' AND voice_line_id=? AND status IN ('pending','running')", (shot['id'], line['line_id'])):
            raise ValueError('This line already has pending voice work. Finish or cancel it before a phrase retake.')
        jid = self.st.db.x("INSERT INTO jobs(shot_id,stage,status,seed,created,progress,segment,note,speaker,voice_character_id,voice_line_id,voice_ref_path,voice_ref_text,voice_direction) VALUES(?,'voice','pending',?,?,'waiting',?,?,?,?,?,?,?,?)",
                           (shot['id'], int(number(body.get('seed', 1), 1, 2**48)), time.time(), line['index'], line['text'], line['speaker'], character['id'], line['line_id'], ref['path'], ref.get('transcript') or '', line.get('direction') or ''))
        job = self.st.db.one('SELECT * FROM jobs WHERE id=?', (jid,))
        try:
            key, p = Worker(self.st).compose(job); p['prompt'] = phrase
            self.freeze(job, recipe, values, key, p, kind='phrase', manual=True, base_audio=line['audio'],
                        line_identity=self.identity(line), range=span, phrase=phrase)
        except Exception:
            self.st.db.x('DELETE FROM jobs WHERE id=?', (jid,)); raise
        return {'ok': True, 'queued': 1, 'job_ids': [jid]}

    def performance_apply(self, body):
        from anime_studio import ffmpeg, media_seconds
        shot, line = self.line(body)
        row = self.st.db.one('SELECT * FROM performance_takes WHERE id=? AND shot_id=? AND line_id=?', (body['take_id'], shot['id'], line['line_id']))
        if not row: raise ValueError('Phrase take not found for this line.')
        take = json.loads(row['json'])
        if line.get('audio') != take['base_audio'] or self.identity(line) != take['identity'] or (take.get('input_signature') and take['input_signature'] != self.st.production.signature(shot,'voice')):
            raise ValueError('The selected performance or dialogue changed. This retake cannot overwrite it.')
        source, replacement = self.path(line['audio']), self.path(take['path'])
        start, end = take['range']['start'], take['range']['end']; total = media_seconds(source)
        length = end-start; new_length = media_seconds(replacement)
        if new_length <= 0: raise ValueError('Retake has no audio.')
        tempo = ''
        if body.get('fit', True):
            ratio = new_length/length; factors = []
            while ratio > 2: factors.append(2); ratio /= 2
            while ratio < .5: factors.append(.5); ratio *= 2
            factors.append(ratio); tempo = ','.join('atempo='+str(v) for v in factors)+','
            new_length = length
        graph = []; parts = []
        if start > .001:
            graph.append(f'[0:a]atrim=end={start},asetpts=PTS-STARTPTS[a]'); parts.append('[a]')
        graph.append(f'[1:a]{tempo}apad,atrim=duration={new_length},asetpts=PTS-STARTPTS,afade=t=in:d=0.005,afade=t=out:st={max(0,new_length-.005)}:d=0.005[b]'); parts.append('[b]')
        if total-end > .001:
            graph.append(f'[0:a]atrim=start={end},asetpts=PTS-STARTPTS[c]'); parts.append('[c]')
        graph.append(''.join(parts)+f'concat=n={len(parts)}:v=0:a=1[out]')
        dest = self.st.data/'voices'/'lines'/('performance-'+uuid.uuid4().hex+'.wav')
        ffmpeg(['-i', str(source), '-i', str(replacement), '-filter_complex', ';'.join(graph), '-map', '[out]', '-ar', '48000', '-ac', '2', str(dest)])
        self.checkpoint('Before phrase retake selection')
        signature = self.st.production.signature(shot, 'voice')
        tid = self.st.db.x('INSERT INTO voice_takes(shot_id,text,speaker,path,seconds,created,input_signature,line_index,character_id,line_id) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (shot['id'], line['text'], line['speaker'], self.st.rel(dest), media_seconds(dest), time.time(), signature, line['index'], line['character_id'], line['line_id']))
        lines = self.st.shot_lines(shot); lines[line['index']].update(audio=self.st.rel(dest), src='tts', clip_id=None)
        self.st.save_lines(shot['id'], lines); self.st.rebuild_audio(shot['id'])
        return {'ok': True, 'take_id': tid}

    def sync_runs(self):
        for job in self.st.db.q('SELECT * FROM jobs WHERE creative_json IS NOT NULL'):
            meta = self.meta(job)
            self.st.db.x('UPDATE creative_runs SET status=?,started=?,finished=?,instance_id=?,error=? WHERE id=?',
                         (job['status'],job.get('started'),job.get('finished'),job.get('instance_id'),job.get('error'),meta['run_id']))

    def state(self):
        db = self.st.db
        looks = [dict(r, **json.loads(r['json'])) for r in db.q('SELECT * FROM asset_looks ORDER BY asset_id,name,version DESC')]
        for look in looks:
            look.pop('json', None)
            for ref in look['references']:
                ref['url'] = '/media/'+ref['path']; ref['available'] = self.path(ref['path'], False).is_file()
        recipes = []
        for row in db.q('SELECT * FROM generation_recipes ORDER BY name,version DESC'):
            item = dict(row, **json.loads(row['json'])); item.pop('json', None)
            # Editing a recipe is an explicit clone; existing versions are never mutated.
            item['nodes'] = len(item['workflow'])
            recipes.append(item)
        runs = []
        for row in db.q('SELECT * FROM creative_runs ORDER BY rowid DESC LIMIT 150'):
            meta = json.loads(row.pop('json')); item = dict(row, name=meta['recipe']['name'], version=meta['recipe']['version'], values=meta.get('values', {}), batch=meta.get('batch'),
                                                        source_id=meta.get('source_id'), phrase=meta.get('phrase'), seed=meta['params'].get('seed'),server=meta.get('server',{}))
            live = db.one('SELECT * FROM jobs WHERE id=?', (row['job_id'],))
            if live and self.meta(live).get('run_id') == row['id']:
                item.update(status=live['status'], started=live.get('started'), finished=live.get('finished'), instance_id=live.get('instance_id'), error=live.get('error'))
            item['seconds'] = round(item['finished']-item['started'], 2) if item.get('finished') and item.get('started') else None
            item['takes'] = [dict(t, url='/media/'+t['path']) for t in db.q('SELECT * FROM takes WHERE job_id=? AND shot_id=?', (row['job_id'], row['shot_id']))]
            item['voice_takes'] = [dict(t, url='/media/'+t['path']) for t in db.q('SELECT * FROM voice_takes WHERE job_id=? AND shot_id=?', (row['job_id'], row['shot_id']))]
            # Job IDs can be reused after clearing finished jobs. Match the captured run ownership.
            if not live or self.meta(live).get('run_id') != row['id']:
                item['takes'] = []; item['voice_takes'] = []
            runs.append(item)
        groups = {}
        for run in runs:
            if run['status'] == 'done' and run['seconds'] is not None:
                identity = digest(run['server'])
                key = (run['recipe_id'],run['instance_id'],identity)
                group = groups.setdefault(key, {'values':[], 'server':run['server']})
                group['values'].append(run['seconds'])
        benchmarks = [{'recipe_id':key[0],'instance_id':key[1], 'server_name':group['server'].get('name'), 'hardware':group['server'].get('hardware',[]),
                       'samples':len(group['values']),'median_seconds':round(statistics.median(group['values']),2), 'min_seconds':min(group['values']),'max_seconds':max(group['values'])} for key,group in groups.items()]
        return {'looks': looks, 'bindings': db.q('SELECT * FROM scene_looks'), 'directions': {r['shot_id']: json.loads(r['json']) for r in db.q('SELECT * FROM shot_direction')},
                'recipes': recipes, 'assignments': [dict(r, values=json.loads(r['values_json'])) for r in db.q('SELECT * FROM shot_recipes')],
                'pronunciations': {r['asset_id']: json.loads(r['json']) for r in db.q('SELECT * FROM character_pronunciations')},
                'runs': runs, 'benchmarks': benchmarks,
                'source_tokens': {t['id']: digest([t['id'], self.st.production.file_stamp(t['path']), self.st.production.signature(self.shot(t['shot_id']), t['stage'], t.get('segment'))]) for t in db.q("SELECT * FROM takes WHERE shot_id IS NOT NULL AND stage!='sheet'") if db.one('SELECT id FROM shots WHERE id=?', (t['shot_id'],))},
                'review_tokens': {s['id']: self.review_signature(s) for s in self.st.ordered_shots()},
                'phrase_takes': [dict(r, **json.loads(r['json'])) for r in db.q('SELECT * FROM performance_takes ORDER BY created DESC LIMIT 200')],
                'reviews': [dict(r, current=r['signature']==self.review_signature(self.shot(r['shot_id']))) for r in db.q('SELECT * FROM continuity_reviews') if db.one('SELECT id FROM shots WHERE id=?', (r['shot_id'],))]}

    def split_controls(self, shot, new_id, fraction):
        """Called inside the timeline transaction; retain per-shot tools on both halves."""
        db = self.st.db
        for table in ('shot_recipes','line_performances','shot_sections'):
            for row in db.q('SELECT * FROM '+table+' WHERE shot_id=?',(shot['id'],)):
                row['shot_id'] = new_id
                columns = list(row)
                db.conn.execute('INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(row.values()))
        direction = self.direction(shot['id'])
        if direction:
            second = copy.deepcopy(direction)
            if direction.get('camera'):
                a,b = direction['camera']['start'],direction['camera']['end']
                middle = {key:a[key]+(b[key]-a[key])*fraction for key in ('x','y','zoom')}
                direction['camera']['end'] = middle
                second['camera']['start'] = middle
            db.conn.execute('INSERT OR REPLACE INTO shot_direction VALUES(?,?)',(shot['id'],json.dumps(direction)))
            db.conn.execute('INSERT INTO shot_direction VALUES(?,?)',(new_id,json.dumps(second)))

    def delete_shot(self, sid):
        for table in ('shot_direction', 'shot_recipes', 'line_performances', 'performance_takes', 'continuity_reviews', 'creative_runs'):
            self.st.db.x('DELETE FROM '+table+' WHERE shot_id=?', (sid,))

    def delete_asset(self, aid):
        self.st.db.x('DELETE FROM asset_looks WHERE asset_id=?', (aid,))
        self.st.db.x('DELETE FROM scene_looks WHERE asset_id=?', (aid,))
        self.st.db.x('DELETE FROM character_pronunciations WHERE asset_id=?', (aid,))
