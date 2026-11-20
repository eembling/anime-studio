"""Production planning, render provenance, preflight and non-destructive script revisions."""
import difflib
import hashlib
import json
import re
import threading
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Production:
    def __init__(self, studio, api_type, http_json):
        self.st, self.api_type, self.http = studio, api_type, http_json
        self.lock = threading.RLock()
        self.readiness = {}
        studio.db.x("CREATE TABLE IF NOT EXISTS production_inputs(job_id INTEGER PRIMARY KEY, signature TEXT)")
        studio.db.x("CREATE TABLE IF NOT EXISTS production_plans(id INTEGER PRIMARY KEY, scene TEXT, status TEXT, phase TEXT, detail TEXT, priority INTEGER, options TEXT, jobs TEXT DEFAULT '[]')")
        studio.db.x("CREATE TABLE IF NOT EXISTS production_approvals(shot_id INTEGER PRIMARY KEY, signature TEXT)")

    def file_stamp(self, path):
        if not path:
            return None
        try:
            p = self.st.abs(path)
            stat = p.stat()
            return [path, stat.st_size, stat.st_mtime_ns]
        except OSError:
            return [path, 'missing']

    def signature(self, shot, stage, segment=0, recipe_override=None):
        st = self.st
        settings = st.settings()
        assets = [st.db.one('SELECT * FROM assets WHERE id=?', (i,)) for i in (json.loads(shot['asset_ids'] or '[]') if isinstance(shot['asset_ids'], str) else shot['asset_ids'])]
        common = {'stage': stage, 'workflow': st.wf_row('animate_flf' if stage == 'continue' and shot['end_next'] else 'animate' if stage == 'continue' else stage)}
        assignment = recipe_override if recipe_override is not None else (st.creative.assignment(shot['id'], stage) if hasattr(st,'creative') else None)
        if assignment and assignment.get('recipe_id') and not assignment.get('missing'):
            pinned = st.creative.recipe(assignment['recipe_id'])
            common['workflow'] = {'id':pinned['id'],'json':pinned['workflow'],'fps':pinned['fps'],'max_frames':pinned['max_frames']}
        # Metadata timestamps do not change what a workflow renders.
        if common['workflow']:
            common['workflow'] = {k: v for k, v in common['workflow'].items() if k != 'updated'}
        if stage == 'keyframe':
            context = st.editor.context(shot['scene']) if hasattr(st, 'editor') else {'variants': []}
            common['continuity'] = context
            for variant in context['variants']:
                if variant['asset_id'] not in {a['id'] for a in assets if a}:
                    assets.append(st.db.one('SELECT * FROM assets WHERE id=?', (variant['asset_id'],)))
            overrides = {v['asset_id']: v for v in context['variants']}
            assets = [dict(a, image=overrides[a['id']]['image']) if a and a['id'] in overrides else a for a in assets]
            common.update(prompt=shot['keyframe_prompt'], camera=shot['camera'], assets=[{k: a[k] for k in ('id', 'image', 'description', 'lora', 'lora_strength')} for a in assets if a],
                          image_stamps=[self.file_stamp(a['image']) for a in assets if a],
                          settings={k: settings[k] for k in ('style_prompt', 'keyframe_negative', 'kf_width', 'kf_height')})
            if shot['ref_prev']:
                prev = st.neighbor(shot, -1)
                common['previous'] = prev and prev['keyframe_take']
        elif stage == 'voice':
            assets = [st.line_character(l) for l in st.shot_lines(shot)]
            common.update(dialogue=shot['dialogue'], characters=[a['id'] if a else None for a in assets],
                          directions=[l.get('direction') for l in st.shot_lines(shot)],
                          refs=[st.ref_clip(a['id']) for a in assets if a],
                          ref_stamps=[self.file_stamp((st.ref_clip(a['id']) or {}).get('path')) for a in assets if a],
                          settings={k: settings[k] for k in ('tts_model', 'tts_language')})
        else:
            common.update(keyframe=shot['keyframe_take'], keyframe_inputs=self.signature(shot, 'keyframe'),
                          motion=shot['motion_prompt'], camera=shot['camera'], duration=shot['duration'],
                          settings={k: settings[k] for k in ('motion_suffix', 'video_negative', 'vid_width', 'vid_height', 'draft_width', 'draft_height', 'draft_max_frames')})
            common['keyframe_file'] = self.file_stamp((st.take(shot['keyframe_take']) or {}).get('path'))
            if stage in ('animate', 'continue') and shot['end_next']:
                nxt = st.neighbor(shot, 1)
                common['next'] = nxt and nxt['keyframe_take']
                flf = st.wf_row('animate_flf')
                common['flf_workflow'] = pinned['workflow'] if assignment and assignment.get('recipe_id') and not assignment.get('missing') else flf and flf['json']
            if stage == 'continue':
                chain = st.chain(shot)
                if hasattr(st,'sections'):
                    common['section_direction'] = st.sections.row(shot['id'],int(segment or 0))
                    if common['section_direction']:
                        common['motion']=common['section_direction']['prompt']
                        common['camera']=common['section_direction']['camera'] or shot['camera']
                        common['duration']=None
                    common['source_stamps'] = [self.file_stamp(t['path']) for t in chain[:int(segment or 0)+1]]
                common['previous_parts'] = [t['id'] for t in chain[:int(segment or 0) + 1]]
                if shot['end_next']:
                    nxt = st.neighbor(shot, 1)
                    common['next'] = nxt and nxt['keyframe_take']
            if stage == 'upscale':
                common['clips'] = [t['id'] for t in st.chain(shot)]
            if stage == 'lipsync':
                common['audio'] = self.file_stamp(shot['audio'])
        creative = st.creative.signature_data(shot, stage, recipe_override) if hasattr(st, 'creative') else None
        if creative: common['creative'] = creative
        return digest(common)

    def capture(self, job):
        if job['stage'] in ('sheet', 'voice_design'):
            return
        shot = self.st.db.one('SELECT * FROM shots WHERE id=?', (job['shot_id'],))
        if shot:
            self.st.db.x('INSERT OR REPLACE INTO production_inputs VALUES(?,?)',
                         (job['id'], self.signature(shot, job['stage'], job.get('segment'))))

    def provenance(self, take, shot):
        if take['stage']=='continue' and hasattr(self.st,'sections') and self.st.sections.needs_review(take['id']):
            return 'outdated'
        row = {'signature': take.get('input_signature')} if take.get('input_signature') else None
        if not row:
            return 'unknown'
        return 'current' if row['signature'] == self.signature(shot, take['stage'], take.get('segment')) else 'outdated'

    def inspect(self, refresh=False):
        """Check node classes and model enums without downloading or submitting anything."""
        with self.lock:
            if not refresh and self.readiness and time.time() - self.readiness['checked'] < 60:
                return self.readiness
            servers = []
            for inst in self.st.db.q('SELECT * FROM instances WHERE enabled=1'):
                stages = {}
                try:
                    info = self.http('GET', inst['url'].rstrip('/') + '/object_info', timeout=8)
                    stages_to_check = inst['roles'].split(',')
                    if any(s in stages_to_check for s in ('animate', 'continue')):
                        stages_to_check.append('animate_flf')
                    for stage in stages_to_check:
                        key = 'keyframe' if stage == 'sheet' else 'animate' if stage == 'continue' else stage
                        row = self.st.wf_row(key)
                        problems = []
                        if not row:
                            problems.append('Configure a workflow in Settings.')
                        else:
                            for node in json.loads(row['json']).values():
                                cls = node['class_type']
                                if cls not in info:
                                    problems.append('Missing node: ' + cls + '. Install its node pack and restart ComfyUI.')
                                    continue
                                inputs = info[cls].get('input', {})
                                specs = dict(inputs.get('required', {}), **inputs.get('optional', {}))
                                for field, value in node.get('inputs', {}).items():
                                    spec = specs.get(field, [])
                                    # Runtime inputs are filled later; only validate concrete model names here.
                                    if ('model' in field or field in ('ckpt_name', 'unet_name', 'audio_encoder_name', 'vae_name', 'clip_name', 'lora_name')) and spec and isinstance(spec[0], list) and isinstance(value, str) and value not in spec[0]:
                                        problems.append('Missing/unsupported ' + field + ': ' + value + '. Check model downloads and refresh ComfyUI.')
                        stages[stage] = {'ready': not problems, 'problems': sorted(set(problems))}
                except Exception as exc:
                    stages = {s: {'ready': False, 'problems': ['Cannot inspect server: ' + str(exc)[:200]]} for s in inst['roles'].split(',')}
                servers.append({'id': inst['id'], 'name': inst['name'], 'stages': stages})
            self.readiness = {'checked': time.time(), 'servers': servers}
            return self.readiness

    def ready(self, stage, shot_ids=()):
        needs_flf = stage in ('animate', 'continue') and any((self.st.db.one('SELECT end_next FROM shots WHERE id=?', (sid,)) or {}).get('end_next') for sid in shot_ids)
        return any(s['stages'].get(stage, {}).get('ready') and (not needs_flf or s['stages'].get('animate_flf', {}).get('ready'))
                   for s in self.inspect()['servers'])

    def snapshot(self, shots):
        for shot in shots:
            stale = []
            for take in shot['takes']:
                take['provenance'] = self.provenance(take, shot)
                if take['provenance'] == 'outdated' and take['id'] in [shot['keyframe_take'], shot['clip_take'], shot['final_take']] + shot['cont']:
                    stale.append(take['stage'])
            for line in shot['lines']:
                for take in line['takes']:
                    take['provenance'] = self.provenance(dict(take, stage='voice'), shot)
                    if take['path'] == line['audio'] and take['provenance'] == 'outdated':
                        stale.append('voice')
            shot['outdated'] = sorted(set(stale))
            approval = self.st.db.one('SELECT signature FROM production_approvals WHERE shot_id=?', (shot['id'],))
            shot['keyframe_approved'] = bool(approval and approval['signature'] == digest([shot['keyframe_take'], self.signature(shot, 'keyframe')]))
        averages = {r['stage']: round(r['seconds']) for r in self.st.db.q("SELECT stage,AVG(finished-started) seconds FROM render_timing_history GROUP BY stage")}
        pending = self.st.db.q("SELECT * FROM jobs WHERE status='pending' ORDER BY priority DESC,id")
        servers = self.st.db.q('SELECT * FROM instances WHERE enabled=1')
        loads = {s['id']: 0 for s in servers}
        estimates = {}
        for job in self.st.db.q("SELECT * FROM jobs WHERE status='running'"):
            if job['instance_id'] in loads and job['stage'] in averages:
                remaining = max(1, averages[job['stage']] - (time.time() - (job['started'] or time.time())))
                loads[job['instance_id']] += remaining
                estimates[job['id']] = round(remaining)
        for job in pending:
            eligible = [s['id'] for s in servers if job['stage'] in s['roles'].split(',')]
            if not eligible or job['stage'] not in averages:
                continue
            sid = min(eligible, key=lambda i: loads[i])
            loads[sid] += averages[job['stage']]
            estimates[job['id']] = round(loads[sid])
        return {'plans': self.st.db.q('SELECT * FROM production_plans ORDER BY priority DESC,id'),
                'readiness': self.readiness or None, 'average_seconds': averages,
                'pending_estimate_seconds': sum(averages.get(j['stage'], 0) for j in pending),
                'job_estimates': estimates, 'estimated_finish_seconds': round(max(loads.values(), default=0)),
                'estimate_incomplete': any(j['stage'] not in averages for j in pending)}

    def approve(self, shot_id):
        shot = self.st.db.one('SELECT * FROM shots WHERE id=?', (shot_id,))
        if shot and shot['keyframe_take']:
            self.st.db.x('INSERT OR REPLACE INTO production_approvals VALUES(?,?)',
                         (shot_id, digest([shot['keyframe_take'], self.signature(shot, 'keyframe')])))
            self.st.db.x("UPDATE production_plans SET status='active' WHERE scene=? AND status='approval'", (shot['scene'],))

    def plan(self, body):
        with self.lock:
            if body.get('action') == 'cancel':
                p = self.st.db.one('SELECT * FROM production_plans WHERE id=?', (body['id'],))
                if p:
                    self.api_type(self.st).job_action({'action': 'cancel', 'ids': json.loads(p['jobs'])})
                    self.st.db.x("UPDATE production_plans SET status='cancelled' WHERE id=?", (p['id'],))
                return {'ok': True}
            if body.get('action') == 'resume':
                self.st.db.x("UPDATE production_plans SET status='active',detail='' WHERE id=? AND status='blocked'", (body['id'],))
                return {'ok': True}
            scene = body.get('scene', '')
            if not self.st.db.one('SELECT id FROM shots WHERE scene=?', (scene,)):
                raise RuntimeError('Choose a scene containing shots.')
            if self.st.db.one("SELECT id FROM production_plans WHERE scene=? AND status IN ('active','blocked','approval')", (scene,)):
                raise RuntimeError('This scene already has a production plan.')
            options = {'lipsync': bool(body.get('lipsync')), 'upscale': bool(body.get('upscale')), 'continue_shared':bool(body.get('continue_shared'))}
            pid = self.st.db.x("INSERT INTO production_plans(scene,status,phase,detail,priority,options) VALUES(?,'active','voices','',?,?)", (scene, max(-10, min(10, int(body.get('priority', 0)))), json.dumps(options)))
            return {'ok': True, 'id': pid}

    def tick(self):
        try:
            self._tick()
        except Exception as exc:
            self.st.db.x("UPDATE production_plans SET status='blocked',detail=? WHERE id=(SELECT id FROM production_plans WHERE status='active' ORDER BY priority DESC,id LIMIT 1)",
                         ('Preparation stopped: ' + str(exc)[:400],))

    def _tick(self):
        with self.lock:
            p = self.st.db.one("SELECT * FROM production_plans WHERE status='active' ORDER BY priority DESC,id LIMIT 1")
            if not p:
                return
            api, db = self.api_type(self.st), self.st.db
            owned = [db.one('SELECT * FROM jobs WHERE id=?', (i,)) for i in json.loads(p['jobs'])]
            if any(j and j['status'] in ('pending', 'running') for j in owned):
                return
            if any(not j or j['status'] != 'done' for j in owned):
                db.x("UPDATE production_plans SET status='blocked',detail='A planned job failed or was cancelled. Retry it in Render queue, then resume.' WHERE id=?", (p['id'],))
                return
            for j in owned:
                if j['stage'] == 'voice':
                    t = db.one('SELECT * FROM voice_takes WHERE job_id=? ORDER BY id DESC LIMIT 1', (j['id'],))
                    shot = db.one('SELECT * FROM shots WHERE id=?', (j['shot_id'],))
                    if t and shot and shot['audio_mode'] == 'lines':
                        lines = self.st.shot_lines(shot)
                        if int(j['segment']) < len(lines) and lines[int(j['segment'])]['text'] == j['note']:
                            api.line_set({'shot_id': shot['id'], 'index': j['segment'], 'src': 'take', 'take_id': t['id']})
                if j['stage'] in ('animate', 'continue', 'lipsync', 'upscale'):
                    t = db.one('SELECT id FROM takes WHERE job_id=? ORDER BY id LIMIT 1', (j['id'],))
                    if t:
                        full=db.one('SELECT * FROM takes WHERE id=?',(t['id'],))
                        shot=db.one('SELECT * FROM shots WHERE id=?',(j['shot_id'],))
                        if j['stage']=='continue' and self.provenance(full,shot)=='outdated':
                            db.x("UPDATE production_plans SET status='blocked',jobs='[]',detail='Continuation inputs changed during generation. Review section direction and resume.' WHERE id=?",(p['id'],));return
                        api.approve({'take_id': t['id']})
            db.x("UPDATE production_plans SET jobs='[]' WHERE id=?", (p['id'],))
            shots = [s for s in api.state()['shots'] if s['scene'] == p['scene']]
            if not shots:
                db.x("UPDATE production_plans SET status='blocked',detail='Scene no longer has shots.' WHERE id=?", (p['id'],)); return
            phase = p['phase']
            if phase in ('animation', 'lipsync', 'upscale') and any(not s['keyframe_approved'] for s in shots):
                db.x("UPDATE production_plans SET phase='keyframes',detail='Keyframe inputs changed; review required again.' WHERE id=?", (p['id'],))
                return
            tasks = []
            if phase == 'voices':
                tasks = [('voice', s['id']) for s in shots if s['audio_mode'] != 'file' and (any(not l['audio'] for l in s['lines']) or 'voice' in s['outdated'])]
                following = 'keyframes'
            elif phase == 'keyframes':
                tasks = [('keyframe', s['id']) for s in shots if not s['keyframe_take'] or 'keyframe' in s['outdated']]
                following = 'approval'
            elif phase == 'approval':
                if any(not s['keyframe_approved'] for s in shots):
                    db.x("UPDATE production_plans SET status='approval',detail='Choose and approve the keyframes below, then the plan continues automatically.' WHERE id=?", (p['id'],)); return
                following = 'animation'
            elif phase == 'animation':
                for s in shots:
                    if s['kind'] == 'still':
                        continue
                    if not s['clip_take'] or 'animate' in s['outdated']:
                        tasks.append(('animate', s['id']))
                    elif (s['remaining'] and s['remaining'] > .1) or 'continue' in s['outdated']:
                        tasks.append(('continue', s['id']))
                following = 'animation' if tasks else 'lipsync'
            elif phase in ('lipsync', 'upscale'):
                opts = json.loads(p['options'])
                tasks = [(phase, s['id']) for s in shots if opts[phase] and (s['audio'] if phase == 'lipsync' else s['clip_take'])]
                following = 'upscale' if phase == 'lipsync' else 'done'
            else:
                db.x("UPDATE production_plans SET status='done',detail='Scene production complete.' WHERE id=?", (p['id'],)); return
            for stage, sid in tasks:
                if not self.ready(stage, [sid]):
                    db.x("UPDATE production_plans SET status='blocked',detail=? WHERE id=?", ('No ready server for ' + stage + '. Run readiness checks, fix the listed problems, then resume.', p['id'])); return
            jobs = []
            for stage, sid in tasks:
                before = db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
                request={'stage': stage, 'shot_ids': [sid], 'takes': 2 if stage == 'keyframe' else 1, 'replace': True}
                if stage=='continue':
                    shot=next(s for s in shots if s['id']==sid)
                    if shot['continuation']['coverage']['needs_extension']:
                        db.x("UPDATE production_plans SET status='blocked',detail=? WHERE id=?",('Shot '+str(sid)+': extend the shot to fit dialogue before continuing.',p['id']));return
                    stale=next((t for t in self.st.chain(shot)[1:] if self.provenance(t,shot)=='outdated'),None)
                    if stale:request['segment']=stale['segment']
                    request['use_shared_prompt']=json.loads(p['options']).get('continue_shared',False)
                result = api.generate(request)
                new = [j['id'] for j in db.q('SELECT id FROM jobs WHERE id>? AND shot_id=? AND stage=?', (before, sid, stage))]
                for jid in new:
                    db.x('UPDATE jobs SET priority=? WHERE id=?', (p['priority'], jid))
                jobs.extend(new)
                if not new or result.get('ok') is False:
                    db.x("UPDATE production_plans SET status='blocked',detail=?,jobs=? WHERE id=?", ('Cannot prepare shot: ' + (result.get('error') or ' '.join(result.get('skipped', []))), json.dumps(jobs), p['id'])); return
            db.x('UPDATE production_plans SET phase=?,jobs=?,detail=? WHERE id=?',
                 (following, json.dumps(jobs), 'Waiting for ' + phase if jobs else '', p['id']))

    def revision(self, body):
        api, st = self.api_type(self.st), self.st
        current = st.ordered_shots()
        token = digest(current)
        from script_dialogue import script_blocks
        blocks = script_blocks(body.get('script', ''))
        def canonical(text):
            return re.sub(r'^([^:]+):', lambda m: m.group(1).strip().casefold() + ':', text, count=1)
        def oldkey(s):
            return (s['scene'], canonical(s['dialogue'] if s['dialogue'] else s['motion_prompt'] or s['keyframe_prompt']))
        matcher = difflib.SequenceMatcher(None, [oldkey(s) for s in current], [(b['scene'], canonical(b['text'])) for b in blocks], autojunk=False)
        rows = []
        for tag, i, j, a, z in matcher.get_opcodes():
            for offset, block in enumerate(blocks[a:z]):
                old = current[i + offset] if i + offset < j else None
                rows.append(dict(block, index=a + offset, shot_id=old['id'] if old else None, change='unchanged' if tag == 'equal' else 'changed' if old else 'added'))
        # Reordered unchanged passages keep their original shot IDs even across diff insert/delete blocks.
        claimed = {r['shot_id'] for r in rows if r['change'] == 'unchanged'}
        for row in rows:
            if row['change'] == 'unchanged':
                continue
            exact = next((shot for shot in current if shot['id'] not in claimed and oldkey(shot) == (row['scene'], canonical(row['text']))), None)
            if exact:
                row.update(shot_id=exact['id'], change='unchanged')
                claimed.add(exact['id'])
        for row in rows:
            if row['change'] != 'unchanged' and row['shot_id'] in claimed:
                row.update(shot_id=None, change='added')
        used = {r['shot_id'] for r in rows}
        removed = [{'id': s['id'], 'scene': s['scene'], 'text': oldkey(s)[1]} for s in current if s['id'] not in used]
        if not body.get('apply'):
            return {'ok': True, 'token': token, 'rows': rows, 'removed': removed, 'targets': [{'id': s['id'], 'seq': s['seq'], 'text': oldkey(s)[1]} for s in current]}
        if body.get('token') != token:
            raise RuntimeError('Shots changed since this preview. Preview the revision again.')
        selections = body.get('selections', [])
        targets = [int(r['shot_id']) for r in selections if r.get('shot_id')]
        if len(targets) != len(set(targets)) or any(i not in {s['id'] for s in current} for i in targets):
            raise RuntimeError('Each existing shot can be assigned only once.')
        indices = [int(r['index']) for r in selections]
        if len(indices) != len(set(indices)) or any(i < 0 or i >= len(blocks) for i in indices):
            raise RuntimeError('Invalid passage selection. Preview the revision again.')
        remove = [int(i) for i in body.get('remove', [])]
        if set(remove) & set(targets) or any(i not in {s['id'] for s in current} for i in remove):
            raise RuntimeError('Invalid removal selection.')
        seq, changed = [], 0
        for selection in selections:
            block = blocks[int(selection['index'])]
            sid = selection.get('shot_id')
            if not selection.get('apply'):
                if sid:
                    seq.append(int(sid))
                continue
            names = {a['name'].lower(): a['id'] for a in st.db.q('SELECT id,name FROM assets')}
            for entry in block['entries']:
                if entry['speaker'].lower() not in names:
                    names[entry['speaker'].lower()] = api.asset_save({'name':entry['speaker'].title(),'kind':'character'})['id']
            cast = [v for k, v in names.items() if re.search(r'\b' + re.escape(k) + r'\b', block['text'], re.I)]
            values = {'scene': block['scene'], 'kind': block['kind'], 'asset_ids': cast}
            if block['speaker']:
                values['dialogue'] = block['text']
                if not sid:
                    values['keyframe_prompt'] = block['speaker'].title() + ' speaking, medium close-up'
            else:
                values.update(keyframe_prompt=block['text'], motion_prompt=block['text'], dialogue='')
            if sid:
                values['id'] = int(sid)
            saved = api.shot_save(values)
            if block['entries']:
                shot = st.db.one('SELECT * FROM shots WHERE id=?', (saved['id'],))
                lines = st.shot_lines(shot)
                for line,entry in zip(lines,block['entries']):
                    if entry['direction']:line['direction']=entry['direction']
                st.save_lines(shot['id'],lines)
            seq.append(int(sid) if sid else saved['id']); changed += 1
        if remove:
            api.shot_delete({'ids': remove})
        seq += [s['id'] for s in current if s['id'] not in seq and s['id'] not in remove]
        for n, sid in enumerate(seq, 1):
            st.db.x('UPDATE shots SET seq=? WHERE id=?', (n, sid))
        api.settings({'settings': {'script': body.get('script', '')}})
        return {'ok': True, 'changed': changed}
