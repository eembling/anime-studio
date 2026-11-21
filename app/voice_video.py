"""Queue-time voice animation controls; never alter already captured renders."""
import json
import math

DIRECTION = 'The character speaks naturally with clear mouth articulation, restrained head movement, and consistent facial features.'


def initialize(db):
    for field, default in [('voice_quality', 'standard'), ('voice_framing', 'source'),
                           ('voice_direction', DIRECTION), ('lip_sync_mode', 'keyframe'), ('voice_crop', None)]:
        if field not in {r['name'] for r in db.q('PRAGMA table_info(shots)')}:
            definition = 'TEXT' if default is None else 'TEXT DEFAULT '+json.dumps(default)
            db.x('ALTER TABLE shots ADD COLUMN '+field+' '+definition)
    if 'voice_quality' not in {r['name'] for r in db.q('PRAGMA table_info(jobs)')}:
        db.x('ALTER TABLE jobs ADD COLUMN voice_quality TEXT')


def validate(values):
    for field, choices in [('voice_quality', ('standard', 'fine')), ('voice_framing', ('source', 'closeup')),
                           ('lip_sync_mode', ('keyframe', 'existing'))]:
        if field in values and values[field] not in choices:
            raise ValueError('Invalid '+field.replace('_', ' ')+'.')
    if 'voice_direction' in values:
        values['voice_direction'] = str(values['voice_direction'] or '').strip()[:2000]


    if 'voice_crop' in values:
        raw = values['voice_crop']
        if raw is not None:
            try:
                x, y, size = (float(raw[key]) for key in ('x', 'y', 'size'))
            except (ValueError, TypeError, KeyError) as exc:
                raise ValueError('Invalid dialogue crop.') from exc
            if not all(map(math.isfinite, (x, y, size))) or not (0 <= x <= 100 and 0 <= y <= 100 and 25 <= size <= 90):
                raise ValueError('Dialogue crop position or size is outside the supported range.')
            values['voice_crop'] = json.dumps(dict(x=x, y=y, size=size))


def lip_workflow(st, shot, job=None):
    if job and job.get('_lip_workflow'):
        return job['_lip_workflow']
    captured = st.creative.workflow(job) if job else None
    if captured:
        return captured
    assignment = st.creative.assignment(shot['id'], 'lipsync')
    if assignment:
        recipe = st.creative.recipe(assignment['recipe_id'])
        return {'json': json.dumps(st.creative.patched_template(recipe, assignment['values'])),
                'fps': recipe['fps'], 'max_frames': recipe['max_frames'], 'name': recipe['name']}
    return st.wf_row('lipsync')


def connected_video(workflow):
    from anime_studio import SAVE_NODES, VIDEO_LOADERS
    seen = set()
    def visit(key):
        if key in seen or key not in workflow:
            return
        seen.add(key)
        for value in workflow[key].get('inputs', {}).values():
            if isinstance(value, list) and len(value) == 2:
                visit(str(value[0]))
    for key, node in workflow.items():
        if node['class_type'] in SAVE_NODES and not node['class_type'].startswith('SaveAudio'):
            visit(key)
    return [key for key in seen if workflow[key]['class_type'] in VIDEO_LOADERS and
            workflow[key].get('_meta', {}).get('title', '').upper() == 'VIDEO_IN']


def prepare(st, job, params, row, folder):
    """Customize native S2V only. Imported recipes retain their sampling controls."""
    from anime_studio import ffmpeg, media_seconds
    shot = st.db.one('SELECT * FROM shots WHERE id=?', (job['shot_id'],))
    workflow = json.loads(row['json'])
    native = [key for key, node in workflow.items() if node['class_type'] == 'WanSoundImageToVideo']
    direction = shot.get('voice_direction') or ''
    if direction:
        params['prompt'] = ', '.join(filter(None, (params.get('prompt'), direction)))
    if not native:
        return row, ''
    if abs(float(row['fps'])-16) > .001:
        raise ValueError('Native Wan S2V requires 16 fps. Correct this workflow’s model timing before generating.')
    row = dict(row)
    # Explicit versioned recipes own their sampler settings.
    creative = st.creative.meta(job)
    recipe = creative.get('recipe', {})
    builtin = row.get('name') == 'wan22_s2v_14b_fp8' or (recipe.get('name') == 'Voice-driven continuation' and not recipe.get('id'))
    if builtin and not recipe.get('id') and not any('Lora' in node['class_type'] for node in workflow.values()):
        quality = job.get('voice_quality') or shot.get('voice_quality') or 'standard'
        for node in workflow.values():
            if node['class_type'] == 'KSampler':
                node['inputs'].update(steps=30 if quality == 'fine' else 20, cfg=6.0)
    note = 'Wide framing can make mouth movement hard to read. For dialogue, choose a close-up keyframe or Dialogue close-up framing.'
    if job['stage'] == 'continue':
        chain = st.chain(shot)[:int(job.get('segment') or 0)+1]
        source = st.abs(chain[-1]['path'])
        seconds = media_seconds(source)
        motion = folder/'previous-motion.mp4'
        # Native S2V consumes at most 73 previous motion frames. Decode only
        # this bounded tail, at the model cadence, without its old audio.
        ffmpeg(['-ss', str(max(0, seconds-73/16)), '-i', str(source), '-an',
                '-vf', 'fps=16', '-frames:v', '73', '-c:v', 'libx264', '-crf', '14',
                '-pix_fmt', 'yuv420p', str(motion)])
        load, components = 'voice_motion_load', 'voice_motion_components'
        while load in workflow or components in workflow:
            load += '_'; components += '_'
        workflow[load] = {'class_type': 'LoadVideo', 'inputs': {'file': 'previous-motion.mp4'}, '_meta': {'title': 'VIDEO_IN'}}
        workflow[components] = {'class_type': 'GetVideoComponents', 'inputs': {'video': [load, 0]}}
        for key in native:
            workflow[key]['inputs']['ref_motion'] = [components, 0]
        params['files']['video'] = motion
        # Keep identity anchored to the original reference, rather than
        # repeatedly encoding a degraded generated boundary as identity.
        reference = st.take_path(shot['keyframe_take'])
        root = chain[0]
        root_job = st.db.one('SELECT * FROM jobs WHERE id=?', (root.get('job_id'),)) if root.get('job_id') else None
        captured = st.snapshots.meta(root_job).get('params', {}).get('files', {}).get('start') if root_job else None
        if captured and st.abs(captured).is_file():
            reference = st.abs(captured)
        if reference:
            params['files']['start'] = reference
        note = 'Continuation preserves the previous clip’s motion context at 16 fps.'
    if shot.get('voice_framing') == 'closeup' and not (job['stage'] == 'lipsync' and shot.get('lip_sync_mode') == 'existing'):
        # Crop the reference only for the initial generation; continuation
        # motion and identity must use the same framing as its predecessor.
        if job['stage'] != 'continue':
            source = params['files']['start']; target = folder/'dialogue-closeup.png'
            geometry = json.loads(shot['voice_crop']) if shot.get('voice_crop') else {'x': 50, 'y': 100/9, 'size': 55}
            scale = geometry['size'] / 100
            left, top = (geometry[axis] / 100 * (1-scale) for axis in ('x', 'y'))
            ffmpeg(['-i', str(source), '-vf',
                    f'crop=iw*{scale}:ih*{scale}:iw*{left}:ih*{top}', '-frames:v', '1', str(target)])
            params['files']['start'] = target
            params['prompt'] += ', dialogue close-up, face clearly visible, steady camera'
        note += ' Review the selected close-up framing before approving the take.'
    params['voice_video'] = {'framing': shot.get('voice_framing'), 'motion_context': job['stage'] == 'continue',
                            'steps': next((n['inputs'].get('steps') for n in workflow.values() if n['class_type'] == 'KSampler'), None)}
    row['json'] = json.dumps(workflow)
    return row, note
