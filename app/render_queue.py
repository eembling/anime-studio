"""Render queue grouping and clearly labelled estimates from successful job history."""
import hashlib
import math
import json
import statistics
import time


def workflow_models(workflow):
    """Model assets referenced by the submitted graph, not a guess from the stage."""
    result=[];seen=set()
    if not isinstance(workflow,dict):return result
    fields={'unet_name':'Diffusion','ckpt_name':'Checkpoint','checkpoint_name':'Checkpoint',
            'clip_name':'Text encoder','clip_name1':'Text encoder','clip_name2':'Text encoder','clip_name3':'Text encoder',
            'vae_name':'VAE','lora_name':'LoRA','audio_encoder_name':'Audio encoder',
            'model_name':'Model','model_choice':'Model','model_id':'Model','model_path':'Model'}
    for node in workflow.values():
        if not isinstance(node,dict):continue
        cls=str(node.get('class_type',''));inputs=node.get('inputs') or {}
        if not isinstance(inputs,dict):continue
        for field,kind in fields.items():
            name=inputs.get(field)
            if not isinstance(name,str) or not name.strip() or name.lower() in ('none','auto','default'):continue
            if field=='model_choice' and 'Qwen3TTS' in cls:
                kind='Voice model';name='Qwen3-TTS '+('Voice Design' if 'Design' in cls else 'Voice Clone')+' · '+name
            elif field=='model_name' and 'Upscale' in cls:kind='Upscaler'
            key=(kind,name)
            if key not in seen:result.append({'kind':kind,'name':name});seen.add(key)
    return result


def models_for_job(job):
    """Prefer the accepted graph; a frozen recipe is a safe older-job fallback."""
    try:
        if job.get('models_json') is not None:return json.loads(job['models_json']), 'submitted'
        if job.get('snapshot_json'):
            from anime_studio import build_workflow
            meta=json.loads(job['snapshot_json']);graph=build_workflow(meta['workflow'],meta['key'],meta['params'])
            for key,value in meta.get('controls',{}).items():
                node,field=key.split('.',1);graph[node]['inputs'][field]=value
            return workflow_models(graph),'captured'
        if job.get('creative_json'):
            from anime_studio import build_workflow
            meta=json.loads(job['creative_json']);recipe=meta['recipe']
            graph=build_workflow(recipe['workflow'],meta['key'],meta['params'])
            for key,value in meta.get('values',{}).items():
                node,field=key.split('.',1);graph[node]['inputs'][field]=value
            return workflow_models(graph),'captured'
    except (ValueError,KeyError,TypeError):pass
    return [],'unknown'


def prompt_graph(entry, prompt_id):
    # ComfyUI queue entries and history.prompt share this representation.
    if isinstance(entry,(list,tuple)) and len(entry)>2 and entry[1]==prompt_id and isinstance(entry[2],dict):return entry[2]
    return None


def render_workload(job):
    if 'workload' in job:
        return job['workload']
    if not job.get('snapshot_json'):
        return None
    snapshot = json.loads(job['snapshot_json'])
    params = snapshot.get('params', {})
    data = [snapshot.get('key'), snapshot.get('workflow'), snapshot.get('controls'),
            params.get('width'), params.get('height'), params.get('length'),
            params.get('voice_video', {}).get('steps')]
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def remember_timings(db):
    # Keep learned timings even when the user clears completed jobs; job IDs may be reused.
    db.x("""CREATE TABLE IF NOT EXISTS render_timing_history(
        job_id INTEGER, stage TEXT, draft INTEGER, instance_id INTEGER, started REAL, finished REAL,
        UNIQUE(job_id,finished))""")
    if 'workload' not in {row['name'] for row in db.q('PRAGMA table_info(render_timing_history)')}:
        db.x('ALTER TABLE render_timing_history ADD COLUMN workload TEXT')
    for job in db.q("SELECT j.* FROM jobs j WHERE j.status='done' AND j.finished>j.started AND NOT EXISTS "
                    "(SELECT 1 FROM render_timing_history h WHERE h.job_id=j.id AND h.finished=j.finished)"):
        db.x("INSERT OR IGNORE INTO render_timing_history(job_id,stage,draft,instance_id,started,finished,workload) "
             "VALUES(?,?,?,?,?,?,?)",
             (job['id'], job['stage'], job['draft'], job['instance_id'], job['started'], job['finished'],
              render_workload(job)))
    db.x('DELETE FROM render_timing_history WHERE rowid NOT IN (SELECT rowid FROM render_timing_history ORDER BY finished DESC LIMIT 2000)')


def eligible_server(job, instance, health, readiness, compatibility=None):
    if not instance.get('enabled') or health.get('ok') is False:
        return False
    if job.get('prompt_id') or job.get('submission_client_id'):
        return job.get('instance_id') == instance['id']
    if job['stage'] not in {role.strip() for role in (instance.get('roles') or '').split(',')}:
        return False
    if job.get('snapshot_json') or job.get('creative_json'):
        return compatibility(job, instance) if compatibility else None
    if readiness is not None and not readiness.get(job['stage'], {}).get('ready'):
        return False
    if job.get('requires_flf') and not (readiness or {}).get('animate_flf', {}).get('ready'):
        return False
    return True


def queue_snapshot(jobs, history, instances, health=None, readiness=None, now=None, compatibility=None):
    now = time.time() if now is None else now
    health, readiness = health or {}, readiness or {}
    servers = {i['id']: i for i in instances if i.get('enabled')}
    availability = {sid: 0.0 for sid in servers}
    samples = {}
    for job in history:
        if job.get('status') != 'done' or not job.get('started') or not job.get('finished'):
            continue
        duration = job['finished'] - job['started']
        if duration <= 0 or not math.isfinite(duration):
            continue
        stage, draft, sid = job['stage'], int(job.get('draft') or 0), job.get('instance_id')
        workload = render_workload(job)
        for server in (sid, None) if sid is not None else (None,):
            key = (stage, draft, server, workload)
            samples.setdefault(key, []).append((job['finished'], duration))

    def typical(job, sid=None):
        key = (job['stage'], int(job.get('draft') or 0), sid, render_workload(job))
        rows = samples.get(key) or samples.get((key[0], key[1], None, key[3])) or []
        if not rows:
            return None, 0
        rows = sorted(rows, reverse=True)[:20]
        return max(1, statistics.median(v for _, v in rows)), len(rows)

    def enrich(job):
        result = dict(job, estimated_progress=None, estimated_start_seconds=None, estimated_finish_seconds=None,
                      remaining_seconds=None, typical_seconds=None, sample_count=0, elapsed_seconds=None, overdue=False,
                      estimated_server_id=None, wait_reason='')
        result['models'],result['models_source']=models_for_job(job)
        from render_diagnostics import diagnose
        result['diagnostic']=diagnose(job) if job['status']=='error' else None
        if job.get('started') and job['status'] != 'pending':
            result['elapsed_seconds'] = max(0, round((job.get('finished') or now) - job['started']))
        return result

    active = [enrich(j) for j in jobs if j['status'] == 'running']
    queued = [enrich(j) for j in jobs if j['status'] == 'pending']
    completed = [enrich(j) for j in jobs if j['status'] == 'done']
    attention = [enrich(j) for j in jobs if j['status'] in ('error', 'cancelled')]
    active.sort(key=lambda j: (j.get('started') or now, j['id']))
    queued.sort(key=lambda j: (-int(j.get('priority') or 0), j['id']))
    completed.sort(key=lambda j: (j.get('finished') or 0, j['id']), reverse=True)
    attention.sort(key=lambda j: (j.get('finished') or 0, j['id']), reverse=True)
    for job in active:
        duration, count = typical(job, job.get('instance_id'))
        elapsed = job['elapsed_seconds'] or 0
        job.update(typical_seconds=duration, sample_count=count)
        if duration is not None:
            job['estimated_progress'] = min(95, round(elapsed / duration * 100))
            job['overdue'] = elapsed >= duration
            if not job['overdue']:
                job['remaining_seconds'] = max(1, round(duration - elapsed))
                job['estimated_finish_seconds'] = job['remaining_seconds']
        if job.get('instance_id') in availability:
            # Unknown or overdue running work must not produce invented downstream ETAs.
            availability[job['instance_id']] += job['remaining_seconds'] if job['remaining_seconds'] is not None else math.inf
    for rank, job in enumerate(queued, 1):
        job['queue_position'] = rank
        job['estimated_progress'] = 0
        if job.get('progress') == 'Another project is rendering on this server.':
            job['wait_reason'] = job['progress']
            continue
        checks = {sid: eligible_server(job, server, health.get(sid, {}), readiness.get(sid), compatibility)
                  for sid, server in servers.items()}
        eligible = [sid for sid, ready in checks.items() if ready]
        if not eligible:
            job['wait_reason'] = ('Waiting for the captured workflow compatibility check.' if None in checks.values()
                                  else 'No enabled, available server supports this stage. Check server readiness.')
            continue
        sid = min(eligible, key=lambda s: availability[s])
        job['estimated_server_id'] = sid
        duration, count = typical(job, sid)
        job.update(typical_seconds=duration, sample_count=count)
        if duration is None:
            job['wait_reason'] = 'Waiting for successful runs of this stage to learn its duration.'
            availability[sid] = math.inf
        elif not math.isfinite(availability[sid]):
            job['wait_reason'] = 'Work ahead has no reliable finish estimate yet.'
        else:
            job['estimated_start_seconds'] = round(availability[sid])
            availability[sid] += duration
            job['estimated_finish_seconds'] = round(availability[sid])
    for job in completed:
        job['estimated_progress'] = 100
    return {'active': active, 'queued': queued, 'completed': completed, 'attention': attention}
