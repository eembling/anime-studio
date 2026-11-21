"""On-demand, captured-input MP4 previews: one native transport for picture and sound."""
import copy
import json
import shutil
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from export_runtime import LOCAL, stop


class ShotPreviews:
    def __init__(self, studio):
        self.st = studio
        self.lock = threading.RLock()
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='shot-preview')
        self.jobs = {}
        self.closed = False

    def request(self, body):
        st = self.st
        shot = st.db.one('SELECT * FROM shots WHERE id=?', (body['shot_id'],))
        take = st.take(body['take_id'])
        if not shot or not take or take['shot_id'] != shot['id'] or take['stage'] not in ('animate', 'lipsync', 'continue', 'upscale'):
            raise ValueError('Choose a video take from this shot.')
        shots = st.ordered_shots()
        from timeline import layout
        timing = next(row for row in layout(shots, int(st.settings()['out_fps'])) if row['id'] == shot['id'])
        prior = []
        if take['stage'] == 'continue':
            ids = [shot['clip_take'], *json.loads(shot['cont_takes'] or '[]')[:int(take['segment'] or 0)]]
            prior = [st.take(ident) for ident in ids]
            if not all(prior):
                raise ValueError('Choose the preceding clip parts before previewing this continuation.')
        spec = dict(version=1, take=take, prior=prior, audio=shot['audio'],
                    offset=float(shot.get('dialogue_offset') or 0), fade=float(shot.get('dialogue_fade') or 0),
                    timeline=timing['start']-float(shot.get('trim_in') or 0),
                    cues=st.soundtrack.cues(), mixer=st.finishing.mixer())
        paths = {take['path'], *(item['path'] for item in prior), *(cue['path'] for cue in spec['cues'])}
        if shot['audio']:
            paths.add(shot['audio'])
        spec['sources'] = [st.production.file_stamp(path) for path in sorted(paths)]
        token = st.soundtrack.digest(spec)
        output = st.data/'cache'/('shot-playback-'+token+'.mp4')
        metadata = output.with_suffix('.json')
        with self.lock:
            if self.closed:
                raise ValueError('The app is closing.')
            if output.is_file() and metadata.is_file():
                return dict(ok=True, status='ready', url='/media/'+st.rel(output), **json.loads(metadata.read_text()))
            existing = self.jobs.get(token)
            if existing and existing['status'] == 'ready':
                del self.jobs[token]
                existing = None
            if existing and (existing['status'] != 'error' or not body.get('retry')):
                return dict(ok=True, **{key:value for key,value in existing.items() if key!='event'})
            if sum(job['status'] in ('queued', 'preparing') for job in self.jobs.values()) >= 8:
                raise ValueError('Several previews are preparing. Try again when one finishes.')
            if len(self.jobs) > 100:
                self.jobs = {key:job for key,job in self.jobs.items() if job['status'] in ('queued','preparing')}
            event = threading.Event()
            self.jobs[token] = dict(status='queued')
            self.pool.submit(self.work, token, copy.deepcopy(spec), output, event)
            self.jobs[token]['event'] = event
            return dict(ok=True, status='queued')

    def work(self, token, spec, output, event):
        from anime_studio import ffmpeg
        from soundtrack import probe
        st = self.st
        LOCAL.cancel = event
        try:
            with self.lock:
                self.jobs[token]['status'] = 'preparing'
            with tempfile.TemporaryDirectory(prefix='shot-playback-', dir=st.data/'cache') as temporary:
                folder = Path(temporary)
                captured = {}
                def capture(path):
                    if path in captured:
                        return captured[path]
                    stamp = next(item for item in spec['sources'] if item[0] == path)
                    if st.production.file_stamp(path) != stamp:
                        raise ValueError('Preview source changed. Press Retry to use the current edit.')
                    source = st.creative.path(path)
                    target = folder/(str(len(captured))+source.suffix)
                    shutil.copy2(source, target)
                    if st.production.file_stamp(path) != stamp:
                        raise ValueError('Preview source changed while copying. Press Retry.')
                    captured[path] = target
                    return target
                video = capture(spec['take']['path'])
                duration, streams = probe(video)
                if 'video' not in streams:
                    raise ValueError('This take has no video stream.')
                source_offset = 0
                for index, prior in enumerate(spec['prior']):
                    seconds, _ = probe(capture(prior['path']))
                    source_offset += seconds-(1/float(prior['fps'] or 16) if index else 0)
                if spec['prior']:
                    source_offset = max(0, source_offset-1/float(spec['take']['fps'] or 16))
                tracks = []
                audio_label = 'Clip audio' if 'audio' in streams else 'No audio assigned'
                if spec['audio']:
                    voice = capture(spec['audio'])
                    seconds, voice_streams = probe(voice)
                    if 'audio' not in voice_streams:
                        raise ValueError('The selected dialogue file has no audio stream.')
                    tracks = st.finishing.window_tracks([dict(name='Shot dialogue', kind='dialogue', path=st.rel(voice),
                        start=spec['offset'], offset=0, duration=seconds, volume=1, fade_in=spec['fade'],
                        fade_out=spec['fade'], loop=0, enabled=1)], source_offset, duration)
                    audio_label = 'Dialogue' if tracks else 'Dialogue outside this part'
                cues = st.finishing.window_tracks(spec['cues'], spec['timeline']+source_offset, duration)
                for cue in cues:
                    cue['path'] = st.rel(capture(cue['path']))
                tracks += cues
                if cues:
                    audio_label += ' + timed sounds'
                picture = folder/'picture.mp4'
                # Preserve elapsed time and the source frame rate. AAC + H.264
                # provides a single seekable, audible native WebKit media asset.
                audio_args = ['-an'] if spec['audio'] else ['-map','0:a?','-c:a','aac','-ar','48000','-ac','2']
                ffmpeg(['-i',str(video),'-map','0:v:0','-vf',"scale=w='trunc(min(1280,iw)/2)*2':h=-2,setsar=1",
                        '-c:v','libx264','-preset','fast','-crf','20','-pix_fmt','yuv420p',*audio_args,
                        '-movflags','+faststart',str(picture)])
                rendered = folder/'preview.mp4'
                st.finishing.mix_audio(picture, rendered, tracks, '', 0, duration, mixer_override=spec['mixer'])
                # Move only complete files into the persistent cache.
                optimized = folder/'ready.mp4'
                ffmpeg(['-i',str(rendered),'-c','copy','-movflags','+faststart',str(optimized)])
                optimized.replace(output)
                active = {track['kind'] for track in tracks}
                if not spec['audio'] and 'audio' in streams:
                    active.add('dialogue')
                solo = any(bus['solo'] for bus in spec['mixer'].values() if isinstance(bus,dict))
                details = dict(audio_label=audio_label, seconds=duration, source_offset=source_offset,
                               mixer_silenced=bool(active) and all(spec['mixer'][name]['mute'] or
                                   (solo and not spec['mixer'][name]['solo']) or
                                   spec['mixer'][name]['volume']==0 for name in active))
                metadata = output.with_suffix('.json.tmp')
                metadata.write_text(json.dumps(details))
                metadata.replace(output.with_suffix('.json'))
            with self.lock:
                self.jobs[token] = dict(status='ready', url='/media/'+st.rel(output), **details)
        except Exception as exc:
            with self.lock:
                self.jobs[token] = dict(status='error', error=str(exc))
        finally:
            LOCAL.cancel = None

    def shutdown(self):
        with self.lock:
            self.closed = True
            for job in self.jobs.values():
                if job.get('event'):
                    stop(job['event'])
        self.pool.shutdown(wait=True)
