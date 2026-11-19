"""Render queue grouping and clearly labelled estimates from successful job history."""
import math
import statistics
import time


def remember_timings(db):
    # Keep learned timings even when the user clears completed jobs; job IDs may be reused.
    db.x("""CREATE TABLE IF NOT EXISTS render_timing_history(
        job_id INTEGER, stage TEXT, draft INTEGER, instance_id INTEGER, started REAL, finished REAL,
        UNIQUE(job_id,finished))""")
    db.x("INSERT OR IGNORE INTO render_timing_history(job_id,stage,draft,instance_id,started,finished) "
         "SELECT id,stage,draft,instance_id,started,finished FROM jobs WHERE status='done' AND finished>started")
    db.x('DELETE FROM render_timing_history WHERE rowid NOT IN (SELECT rowid FROM render_timing_history ORDER BY finished DESC LIMIT 2000)')


def queue_snapshot(jobs, history, instances, health=None, readiness=None, now=None):
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
        for key in ((stage, draft, sid), (stage, draft, None)) if sid is not None else ((stage, draft, None),):
            samples.setdefault(key, []).append((job['finished'], duration))

    def typical(job, sid=None):
        key = (job['stage'], int(job.get('draft') or 0), sid)
        rows = samples.get(key) or samples.get((key[0], key[1], None)) or []
        if not rows:
            return None, 0
        rows = sorted(rows, reverse=True)[:20]
        return max(1, statistics.median(v for _, v in rows)), len(rows)

    def enrich(job):
        result = dict(job, estimated_progress=None, estimated_start_seconds=None, estimated_finish_seconds=None,
                      remaining_seconds=None, typical_seconds=None, sample_count=0, elapsed_seconds=None, overdue=False,
                      estimated_server_id=None, wait_reason='')
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
        eligible = []
        for sid, server in servers.items():
            if job['stage'] not in server.get('roles', '').split(',') or health.get(sid, {}).get('ok') is False:
                continue
            checked = readiness.get(sid)
            if checked is not None and (not checked.get(job['stage'], {}).get('ready') or (job.get('requires_flf') and not checked.get('animate_flf', {}).get('ready'))):
                continue
            eligible.append(sid)
        if not eligible:
            job['wait_reason'] = 'No enabled, available server supports this stage. Check server readiness.'
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
