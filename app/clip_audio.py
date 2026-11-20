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
    from anime_studio import ffmpeg, media_seconds
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
    """Only connected audio inputs count; an unused LoadAudio cannot drive a render."""
    from anime_studio import SAVE_NODES
    seen = set()
    def visit(key):
        if key in seen or key not in workflow:
            return
        seen.add(key)
        for value in workflow[key].get('inputs', {}).values():
            if isinstance(value,list) and len(value)==2:
                visit(str(value[0]))
    for key,node in workflow.items():
        if node['class_type'] in SAVE_NODES and not node['class_type'].startswith('SaveAudio'):
            visit(key)
    return [key for key in seen if workflow[key]['class_type']=='LoadAudio' or
            workflow[key].get('_meta',{}).get('title','').upper()=='AUDIO_IN']


def choose_workflow(st, stage, key, row, allow_switch=True):
    """Prefer an audio-capable recipe, or a ready S2V default on this worker role."""
    import json
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
    selected,actual,conditioned,note = choose_workflow(st,job['stage'],key,row,allow_switch)
    if not conditioned:
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
    # Validate the effective graph, not just the image-only workflow originally
    # chosen by the Animate button.
    st.creative.require_ready(dict(stage=job['stage'],workflow=json.loads(actual['json']),controls=[],requirements=''),{})
    return selected,params,actual,True,note
