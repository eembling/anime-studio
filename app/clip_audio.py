"""Pin selected shot dialogue at queue time and embed it in generated takes."""
import shutil

VIDEO_STAGES = ('animate', 'continue', 'lipsync', 'upscale')


def capture(st, job, folder, fps):
    if job['stage'] not in VIDEO_STAGES or st.creative.meta(job).get('kind')=='repair_video':
        return None
    shot = st.db.one('SELECT * FROM shots WHERE id=?', (job['shot_id'],))
    if not shot or not shot['audio']:
        return None
    source = st.creative.path(shot['audio'],required=False)
    if not source.is_file():
        raise ValueError('The selected shot dialogue is missing. Generate or choose a voice before generating the clip.')
    target = folder/('selected-dialogue'+source.suffix)
    shutil.copy2(source, target)
    offset = 0
    if job['stage'] == 'continue':
        chain = st.chain(shot)[:int(job.get('segment') or 0)+1]
        offset = max(0, st.chain_seconds(chain)-1/float(fps or 16))
    return dict(path=st.rel(target), source_offset=offset,
                dialogue_offset=float(shot.get('dialogue_offset') or 0), fade=float(shot.get('dialogue_fade') or 0))


def embed(st, job, video):
    """Replace embedded sound with the captured selected voice, without retiming picture."""
    from anime_studio import ffmpeg, media_seconds, record_import_file
    pinned = st.snapshots.meta(job).get('clip_audio')
    if not pinned:
        return video
    voice = st.creative.path(pinned['path'])
    if not voice.is_file():
        raise ValueError('Captured dialogue is missing. Generate a new clip from the current shot.')
    duration = media_seconds(video)
    if pinned.get('duration'):
        duration = min(duration,float(pinned['duration']))
    seconds = media_seconds(voice)
    if duration <= 0 or seconds <= 0:
        raise ValueError('Cannot read the generated clip or its captured dialogue.')
    filters = voice_filter(pinned,seconds,duration)
    frame_limit=['-frames:v',str(max(1,round(duration*float(pinned['fps']))))] if pinned.get('fps') else []
    output = video.with_name(video.stem+'-with-voice.mp4')
    record_import_file(output)
    try:
        ffmpeg(['-i',str(video),'-i',str(voice),'-filter_complex','[1:a]'+filters+'[voice]',
                '-map','0:v:0','-map','[voice]','-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-ac','2',
                *frame_limit,'-t',str(duration),'-movflags','+faststart',str(output)])
    except Exception:
        output.unlink(missing_ok=True)
        raise
    video.unlink()
    return output


def voice_filter(pinned, seconds, duration):
    start = float(pinned['source_offset'])-float(pinned['dialogue_offset'])
    fade = float(pinned.get('fade') or 0)
    filters = 'aresample=48000,aformat=channel_layouts=stereo'
    if fade:
        filters += f',afade=t=in:d={fade},afade=t=out:st={max(0,seconds-fade)}:d={fade}'
    filters += f',atrim=start={max(0,start)},asetpts=PTS-STARTPTS'
    if start < 0:
        filters += f',adelay={round(-start*1000)}:all=1'
    filters += f',apad,atrim=duration={duration}'
    return filters


def audio_inputs(workflow):
    """Find audio bound to a visual conditioning node on the saved picture path."""
    from anime_studio import SAVE_NODES

    def ancestors(roots):
        pending = list(roots)
        seen = set()
        while pending:
            key = pending.pop()
            if key in seen or key not in workflow:
                continue
            seen.add(key)
            node = workflow[key]
            for field, value in node.get('inputs', {}).items():
                if field == 'audio' and node['class_type'] in ('CreateVideo', 'VHS_VideoCombine', 'SaveVideo'):
                    continue
                if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                    pending.append(str(value[0]))
        return seen

    visual_path = ancestors(key for key, node in workflow.items()
                            if node['class_type'] in SAVE_NODES and not node['class_type'].startswith('SaveAudio'))
    conditioning = []
    for key in visual_path:
        node = workflow[key]
        if node['class_type'] == 'WanSoundImageToVideo':
            fields = ('audio_encoder_output',)
        elif (node.get('_meta') or {}).get('title', '').strip().upper() == 'AUDIO_CONDITIONING':
            fields = ('audio', 'audio_encoder_output', 'audio_features', 'audio_embeds')
        else:
            continue
        for field in fields:
            value = node.get('inputs', {}).get(field)
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                conditioning.append(str(value[0]))
    return [key for key in sorted(ancestors(conditioning))
            if workflow[key]['class_type'] == 'LoadAudio' or
            (workflow[key].get('_meta') or {}).get('title', '').strip().upper() == 'AUDIO_IN']


def gpu_s2v(row):
    """Keep S2V prompt encoding on the GPU; leave other workflows untouched."""
    import json
    workflow=json.loads(row['json'])
    if not any(node['class_type']=='WanSoundImageToVideo' for node in workflow.values()):
        return row
    changed=False
    for node in workflow.values():
        if node['class_type']=='CLIPLoader' and node.get('inputs',{}).get('device')=='cpu':
            node['inputs']['device']='default';changed=True
    return dict(row,json=json.dumps(workflow)) if changed else row


def choose_workflow(st, stage, key, row, allow_switch=True):
    """Prefer an audio-capable recipe, or a ready S2V default on this worker role."""
    import json
    row=gpu_s2v(row)
    workflow = json.loads(row['json'])
    reason = 'This workflow has no connected AUDIO_IN.'
    if audio_inputs(workflow):
        try:
            st.creative.require_ready(dict(stage=stage,workflow=workflow,controls=[],requirements=''),{})
            actual_key='lipsync' if any(node['class_type']=='WanSoundImageToVideo' for node in workflow.values()) else key
            return actual_key,row,True,''
        except ValueError as exc:
            reason=str(exc)
    candidate = st.wf_row('lipsync') if allow_switch else None
    if candidate and stage != 'lipsync':
        from voice_video import connected_video
        if connected_video(json.loads(candidate['json'])):
            candidate = None
    if candidate:
        candidate=gpu_s2v(candidate)
    if candidate and audio_inputs(json.loads(candidate['json'])):
        try:
            st.creative.require_ready(dict(stage=stage,workflow=json.loads(candidate['json']),controls=[],requirements=''),{})
            return 'lipsync',candidate,True,''
        except ValueError as exc:
            reason = str(exc)
    return key,row,False,'Voice will be included in the clip; audio-driven animation is unavailable. '+reason


def condition(st, job, key, params, row, folder, pinned):
    """Use the captured section of dialogue for ComfyUI as well as the saved MP4."""
    import json,math
    if not pinned or job['stage'] not in ('animate','continue','lipsync'):
        return key,params,row,False,''
    original_fps = float(row.get('fps') or 16)
    creative = st.creative.meta(job)
    allow_switch = (not creative.get('recipe',{}).get('id') and key!='animate_flf' and
                    creative.get('kind') not in ('repair_video',))
    if job['stage'] == 'lipsync':
        shot = st.db.one('SELECT * FROM shots WHERE id=?',(job['shot_id'],))
        if shot.get('lip_sync_mode') == 'existing':
            allow_switch = False
    selected,actual,conditioned,note = choose_workflow(st,job['stage'],key,row,allow_switch)
    if not conditioned:
        if job['stage'] == 'lipsync':
            raise ValueError('Voice animation needs a ready, connected AUDIO_IN workflow. '+note)
        # A voice-only fallback still needs an executable picture workflow.
        st.creative.require_ready(dict(stage=job['stage'],workflow=json.loads(row['json']),controls=[],requirements=''),{})
        return key,params,row,False,note
    if selected!=key:
        requested = max(1/original_fps,(float(params.get('length') or 1)-1)/original_fps)
        fps = float(actual['fps'])
        maximum = int(actual['max_frames'])
        if job.get('draft'):
            maximum = min(maximum,int(st.settings()['draft_max_frames']))
        params['length'] = min(maximum,max(1,4*math.ceil(requested*fps/4)+1))
    fps = float(actual.get('fps') or 16)
    if job['stage']=='continue':
        chain=st.chain(st.db.one('SELECT * FROM shots WHERE id=?',(job['shot_id'],)))[:int(job.get('segment') or 0)+1]
        pinned['source_offset']=max(0,st.chain_seconds(chain)-1/fps)
    duration = max(1/fps,float(params.get('length') or 1)/fps)
    pinned['duration'] = duration
    pinned['fps'] = fps
    # Keep the raw voice separately for the saved clip; upload only a bounded,
    # aligned WAV to the audio-driven model, including any intentional lead-in.
    from anime_studio import ffmpeg
    source = st.creative.path(pinned['path'])
    from anime_studio import media_seconds
    filters=voice_filter(pinned,media_seconds(source),duration)
    target = folder/'model-dialogue.wav'
    ffmpeg(['-i',str(source),'-af',filters,'-ar','48000','-ac','2',str(target)])
    params.setdefault('files',{})['audio'] = target
    from voice_video import prepare
    actual, quality_note = prepare(st, job, params, actual, folder)
    note = ' '.join(filter(None, (note, quality_note)))
    # Validate the effective graph, not just the image-only workflow originally
    # chosen by the Animate button.
    st.creative.require_ready(dict(stage=job['stage'],workflow=json.loads(actual['json']),controls=[],requirements=''),{})
    return selected,params,actual,True,note
