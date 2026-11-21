"""Shared picture, dialogue, mixer and reusable sound finishing tools."""
import json
from timeline import frames as timeline_frames, layout as timeline_layout
import math
import shutil
import threading
import time
import uuid

BUSES = ('dialogue', 'music', 'ambience', 'sfx')


def bounded(value, minimum, maximum):
    value = float(value)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(f'Enter a value between {minimum} and {maximum}.')
    return value


class Finishing:
    def __init__(self, studio):
        self.st = studio
        db = studio.db
        db.x('''CREATE TABLE IF NOT EXISTS sound_library(
            id TEXT PRIMARY KEY, name TEXT, category TEXT, tags TEXT, path TEXT, seconds REAL, created REAL)''')
        db.x("CREATE TABLE IF NOT EXISTS generated_sounds(id TEXT PRIMARY KEY, job_id INTEGER, shot_id INTEGER, cue TEXT)")
        for column, definition in (
            ('transition', "TEXT DEFAULT 'cut'"), ('transition_seconds', 'REAL DEFAULT 0.5'),
            ('dialogue_offset', 'REAL DEFAULT 0'), ('dialogue_tail', 'REAL DEFAULT 0'),
            ('dialogue_fade', 'REAL DEFAULT 0'),
        ):
            if column not in {row['name'] for row in db.q('PRAGMA table_info(shots)')}:
                db.x(f'ALTER TABLE shots ADD COLUMN {column} {definition}')

    def mixer(self):
        defaults = {name: {'volume': 1, 'pan': 0, 'mute': False, 'solo': False} for name in BUSES}
        saved = json.loads(self.st.settings().get('audio_mixer') or '{}')
        for name in BUSES:
            defaults[name].update(saved.get(name, {}))
        return dict(defaults, duck=bool(saved.get('duck', False)))

    def state(self):
        generated = {row['id']: dict(json.loads(row['cue']), job_id=row['job_id'], shot_id=row['shot_id'])
                     for row in self.st.db.q('SELECT * FROM generated_sounds')}
        return {
            'mixer': self.mixer(),
            'audio_config': __import__('audio_processing').CONFIG,
            'library': [dict(row, url='/media/'+row['path'], generation=generated.get(row['id'])) for row in
                        self.st.db.q('SELECT * FROM sound_library ORDER BY name,id')],
        }

    def save_generated_sound(self, job, audio_files):
        from anime_studio import ffmpeg, record_import_file
        from soundtrack import probe
        from render_queue import models_for_job
        if len(audio_files) != 1:
            raise ValueError('The sound recipe must save one audio file per candidate.')
        spec = json.loads(job['note'])
        ident = uuid.uuid4().hex
        st = self.st
        folder = st.data/'sound-library'
        folder.mkdir(exist_ok=True)
        target = folder/(ident+'.wav')
        record_import_file(target)
        try:
            ffmpeg(['-i', str(audio_files[0]), '-map', '0:a:0', '-ar', '48000', '-c:a', 'pcm_s24le', str(target)])
            duration, streams = probe(target)
            if not duration or 'audio' not in streams:
                raise ValueError('The sound recipe returned unreadable audio.')
            category = {'sfx':'Generated effects','ambience':'Generated ambience','music':'Generated music'}[spec['kind']]
            st.db.x('INSERT INTO sound_library VALUES(?,?,?,?,?,?,?)',
                    (ident, spec['name'], category, 'ComfyUI', st.rel(target), duration, time.time()))
            st.db.x('INSERT INTO generated_sounds VALUES(?,?,?,?)',
                    (ident, job['id'], job['shot_id'], json.dumps(dict(spec, seed=job['seed'], models=models_for_job(job)[0]))))
        except Exception:
            st.db.x('DELETE FROM sound_library WHERE id=?', (ident,))
            target.unlink(missing_ok=True)
            raise

    def action(self, body):
        st = self.st
        action = body.get('action')
        if action == 'mixer':
            data = body['mixer']
            value = {'duck': bool(data.get('duck'))}
            for name in BUSES:
                bus = data[name]
                value[name] = dict(volume=bounded(bus['volume'], 0, 4), pan=bounded(bus['pan'], -1, 1),
                                   mute=bool(bus.get('mute')), solo=bool(bus.get('solo')))
            st.editor.checkpoint('Before audio mixer edit')
            st.db.x("INSERT OR REPLACE INTO settings VALUES('audio_mixer',?)", (json.dumps(value),))
            return {'ok': True}
        if action == 'library_save':
            old = st.db.one('SELECT * FROM sound_library WHERE id=?', (body.get('id'),))
            name = str(body.get('name') or '').strip()[:160]
            if not name:
                raise ValueError('Name this sound.')
            path = old['path'] if old else None
            uploaded = None
            try:
                if body.get('file'):
                    uploaded = st.save_upload(body['file'], 'sound-library')
                    path = uploaded
                if not path:
                    raise ValueError('Choose an audio file.')
                from soundtrack import probe
                duration, streams = probe(st.abs(path))
                if 'audio' not in streams:
                    raise ValueError('Choose a file containing audio.')
                category = str(body.get('category') or 'Effects').strip()[:80]
                tags = ', '.join(tag.strip() for tag in str(body.get('tags') or '').split(',') if tag.strip())[:500]
                ident = old['id'] if old else uuid.uuid4().hex
                st.db.x('INSERT OR REPLACE INTO sound_library VALUES(?,?,?,?,?,?,?)',
                        (ident, name, category, tags, path, duration, old['created'] if old else time.time()))
                return {'ok': True, 'id': ident}
            except Exception:
                if uploaded:
                    st.abs(uploaded).unlink(missing_ok=True)
                raise
        if action == 'library_delete':
            # Retain source files used by existing cues, saved episodes and history.
            st.db.x('DELETE FROM generated_sounds WHERE id=?', (body['id'],))
            st.db.x('DELETE FROM sound_library WHERE id=?', (body['id'],))
            return {'ok': True}
        if action == 'shot':
            shot = st.db.one('SELECT * FROM shots WHERE id=?', (body['shot_id'],))
            if not shot:
                raise ValueError('Shot no longer exists.')
            transition = body.get('transition', 'cut')
            if transition not in ('cut', 'dissolve', 'black'):
                raise ValueError('Choose Cut, Dissolve or Fade through black.')
            seconds = bounded(body.get('seconds', .5), .05, min(5, shot['duration']))
            offset = bounded(body.get('offset', 0), -30, 30)
            tail = bounded(body.get('tail', 0), 0, 30)
            fade = bounded(body.get('fade', 0), 0, min(5, shot['duration']))
            st.editor.checkpoint('Before transition and dialogue timing edit')
            st.db.x('''UPDATE shots SET transition=?,transition_seconds=?,dialogue_offset=?,
                    dialogue_tail=?,dialogue_fade=? WHERE id=?''', (transition, seconds, offset, tail, fade, shot['id']))
            return {'ok': True}
        if action == 'render':
            if st.assembly['running']:
                raise ValueError('Wait for the current export to finish.')
            shots = st.ordered_shots()
            selected = [s for s in shots if s['id'] == body.get('shot_id')] if body.get('shot_id') else [
                s for s in shots if s['scene'] == body.get('scene')]
            if not selected:
                raise ValueError('Choose a shot or scene.')
            # A scene may be interrupted in the shot list. Render each continuous
            # range separately so absolute cues never jump across removed shots.
            indices = [shots.index(shot) for shot in selected]
            if indices != list(range(indices[0], indices[-1]+1)):
                raise ValueError('This scene is interrupted by other scenes. Move its shots together before rendering a scene preview.')
            options = {'use_music': bool(body.get('use_music', True)),
                       'use_timed': bool(body.get('use_timed', True)),
                       'music_volume': bounded(body.get('music_volume', .25), 0, 4)}
            settings=st.settings()
            timeline=timeline_layout(shots,int(settings['out_fps']))
            indices=[shots.index(shot) for shot in selected]
            left=timeline[indices[0]]['start']
            duration=sum(timeline[index]['duration'] for index in indices)
            cues=self.window_tracks(st.soundtrack.cues() if options['use_timed'] else [],left,duration)
            dialogue=self.window_tracks(self.dialogue_tracks(shots),left,duration)
            sources=[st.production.file_stamp(track['path']) for track in cues+dialogue]
            for shot in selected:
                for take in [st.take(shot[key]) for key in ('keyframe_take','clip_take','final_take')]+st.chain(shot):
                    if take:sources.append(st.production.file_stamp(take['path']))
            bed=settings.get('music') if options['use_music'] else ''
            sources.append(st.production.file_stamp(bed))
            picture_fields=('id','kind','duration','trim_in','keyframe_take','clip_take','final_take','cont_takes',
                            'retime','camera','transition','transition_seconds')
            picture=[[shot.get(key) for key in picture_fields] for shot in selected]
            token=st.soundtrack.digest([picture,cues,dialogue,sources,self.mixer(),options,left,
                                       [settings[key] for key in ('out_width','out_height','out_fps')],
                                       [st.creative.direction(shot['id']).get('camera') for shot in selected]])
            label = 'shot-'+str(int(selected[0]['seq'])).zfill(3) if body.get('shot_id') else 'scene'
            cache = st.data/'cache'/('rendered-'+label+'-'+token+'.mp4')
            if cache.is_file():
                st.assembly.update(running=False, kind='shot' if body.get('shot_id') else 'scene',
                                   output=st.rel(cache), folder='cache', log=['Using cached picture and sound preview.'])
                return {'ok': True, 'cached': True, 'url': '/media/'+st.rel(cache)}
            return st.exports.submit(dict(options,kind='shot' if body.get('shot_id') else 'scene',
                                          shot_ids=[shot['id'] for shot in selected],cache=cache.name,expected_sources=sources))
        raise ValueError('Unknown finishing action.')

    def dialogue_tracks(self, shots):
        from anime_studio import media_seconds
        rows, start = [], 0
        fps = int(self.st.settings()['out_fps'])
        for shot in shots:
            duration = timeline_frames(shot['duration'],fps)/fps
            if shot['audio'] and self.st.abs(shot['audio']).is_file():
                source = media_seconds(self.st.abs(shot['audio']))
                trim = float(shot.get('trim_in') or 0)
                length = min(duration+float(shot.get('dialogue_tail') or 0), max(0, source-trim))
                if length > 0:
                    rows.append(dict(name='Shot dialogue', kind='dialogue', path=shot['audio'],
                                     start=start+float(shot.get('dialogue_offset') or 0), offset=trim,
                                     duration=length, volume=1, fade_in=float(shot.get('dialogue_fade') or 0),
                                     fade_out=float(shot.get('dialogue_fade') or 0), loop=0, enabled=1))
            start += duration
        return rows

    def window_tracks(self, tracks, start, duration):
        """Clip cues to an export window, preserving phase and fade envelopes."""
        rows = []
        for source in tracks:
            if not source['enabled']:
                continue
            left = max(start, source['start'])
            right = min(start+duration, source['start']+source['duration'])
            if right <= left:
                continue
            row = dict(source)
            row.update(start=left-start, duration=right-left, window_skip=left-source['start'],
                       original_duration=source['duration'])
            rows.append(row)
        return rows

    def audio(self, master, output, tracks, duration):
        """Render a raw dialogue master without bus gain, preserving remixability."""
        self.mix_audio(master, output, tracks, '', 0, duration, raw=True)

    def mix_audio(self, master, output, cues, bed, bed_volume, duration, raw=False, stem_dir=None, mixer_override=None, engine="block-rms-v1"):
        from anime_studio import ffmpeg
        mixer = mixer_override or self.mixer()
        inputs = ['-i', str(master)]
        filters = []
        buses = {name: [] for name in BUSES}
        first_cue = 1
        if not raw:
            from soundtrack import probe
            _, streams = probe(master)
            base = '0:a'
            if 'audio' not in streams:
                inputs += ['-f','lavfi','-i',f'anullsrc=r=48000:cl=stereo:d={duration}']
                base = '1:a'
                first_cue = 2
            filters.append(f'[{base}]aresample=48000,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[dialogue0]')
            buses['dialogue'].append('[dialogue0]')
        tracks = [dict(cue) for cue in cues if cue['enabled']]
        if bed:
            tracks.append(dict(name='Music bed',kind='music',path=bed,start=0,offset=0,duration=duration,
                               volume=bed_volume,fade_in=0,fade_out=0,loop=1,enabled=1))
        for index, cue in enumerate(tracks, first_cue):
            path = self.st.creative.path(cue['path'])
            inputs += ['-i', str(path)]
            skip = float(cue.get('window_skip', 0))
            length = float(cue['duration'])
            source_length = float(cue.get('source_seconds') or 0)
            offset = float(cue['offset'])
            if cue['loop']:
                from soundtrack import probe
                source_length = source_length or probe(path)[0]
                # Trim the intro once, then loop the remaining tail.
                filters.append(f'[{index}:a]aresample=48000,atrim=start={offset}:end={source_length},asetpts=PTS-STARTPTS,aloop=loop=-1:size={max(1,round((source_length-offset)*48000))}[loop{index}]')
                source_label = f'loop{index}'
                offset = skip
            else:
                source_label = f'{index}:a'
                offset += skip
            original = float(cue.get('original_duration', length))
            fi, fo = float(cue.get('fade_in') or 0), float(cue.get('fade_out') or 0)
            envelope = str(float(cue['volume']))
            if fi:
                envelope += f'*min(1,(t+{skip})/{fi})'
            if fo:
                envelope += f'*min(1,max(0,({original}-t-{skip})/{fo}))'
            chain = (f'[{source_label}]aresample=48000,atrim=start={offset}:duration={length},'
                     f'asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,'
                     f"volume='{envelope}':eval=frame,adelay={round(cue['start']*1000)}:all=1,"
                     f'apad,atrim=duration={duration}[cue{index}]')
            filters.append(chain)
            buses[cue['kind']].append(f'[cue{index}]')
        any_solo = any(mixer[name]['solo'] for name in BUSES)
        for name, labels in buses.items():
            if labels:
                filters.append(''.join(labels)+f'amix=inputs={len(labels)}:normalize=0:duration=longest[sum{name}]')
            else:
                filters.append(f'anullsrc=r=48000:cl=stereo,atrim=duration={duration}[sum{name}]')
            bus = mixer[name]
            volume = 1 if raw else (0 if bus['mute'] or any_solo and not bus['solo'] else bus['volume'])
            pan = 0 if raw else bus['pan']
            # Match the stereo panner used by Studio, including folding the far channel.
            cross, keep = math.sin(abs(pan)*math.pi/2), math.cos(abs(pan)*math.pi/2)
            coefficients = (f'c0=c0+{cross}*c1|c1={keep}*c1' if pan < 0 else
                            f'c0={keep}*c0|c1=c1+{cross}*c0')
            filters.append(f'[sum{name}]volume={volume},pan=stereo|{coefficients}[{name}]')
        import tempfile
        from audio_processing import write_envelope
        with tempfile.TemporaryDirectory(prefix='audio-buses-',dir=self.st.data/'cache') as temporary:
            folder=__import__('pathlib').Path(temporary)
            # Render post-fader buses once; stems use these same files.
            outputs=[]
            for name in BUSES:
                outputs += ['-map',f'[{name}]','-c:a','pcm_f32le','-ar','48000','-ac','2',
                            '-t',str(duration),str(folder/(name+'.wav'))]
            ffmpeg(inputs+['-filter_complex',';'.join(filters)]+outputs)
            music=folder/'music.wav'
            if not raw and mixer['duck']:
                ducked=folder/'ducked.wav'
                if engine=='legacy-sidechain':
                    ffmpeg(['-i',str(music),'-i',str(folder/'dialogue.wav'),'-filter_complex',
                            '[0:a][1:a]sidechaincompress=threshold=0.025:ratio=8:attack=15:release=350[out]',
                            '-map','[out]','-c:a','pcm_f32le','-t',str(duration),str(ducked)])
                else:
                    gain=folder/'duck.wav'
                    write_envelope(folder/'dialogue.wav',gain)
                    ffmpeg(['-i',str(music),'-i',str(gain),'-filter_complex',
                            '[1:a]aresample=48000,aformat=channel_layouts=stereo[g];[0:a][g]amultiply[out]',
                            '-map','[out]','-c:a','pcm_f32le','-t',str(duration),str(ducked)])
                music=ducked
            files=[folder/'dialogue.wav',music,folder/'ambience.wav',folder/'sfx.wav']
            if stem_dir:
                for name,file in zip(BUSES,files):
                    shutil.copyfile(file,stem_dir/(name+'.wav'))
            bus_inputs=['-i',str(master)]
            for file in files:bus_inputs+=['-i',str(file)]
            ffmpeg(bus_inputs+['-filter_complex',
                    '[1:a][2:a][3:a][4:a]amix=inputs=4:normalize=0:duration=longest,'
                    'alimiter=limit=0.95:level=0:latency=1[out]',
                    '-map','0:v:0','-map','[out]','-c:v','copy','-c:a','aac','-b:a','192k',
                    '-ar','48000','-ac','2','-t',str(duration),str(output)])
        output.with_suffix('.audio.json').write_text(json.dumps({'cues': tracks,'mixer':mixer},indent=2), encoding='utf-8')
        return duration

    def join_picture(self, shots, segments, joined, fps):
        """Render each affected shot once, then copy-concatenate its picture."""
        from anime_studio import ffmpeg
        files=[]
        for index,(shot,source) in enumerate(zip(shots,segments)):
            duration=timeline_frames(shot['duration'],fps)/fps
            previous=shots[index-1] if index else None
            incoming=(previous or {}).get('transition') or 'cut'
            outgoing=shot.get('transition') or 'cut' if index<len(shots)-1 else 'cut'
            filters=['[0:v]setpts=PTS-STARTPTS']
            if incoming=='black':
                fade=min(float(previous.get('transition_seconds') or .5),duration)/2
                filters[0]+=f',fade=t=in:st=0:d={fade}'
            if outgoing=='black':
                fade=min(float(shot.get('transition_seconds') or .5),duration)/2
                filters[0]+=f',fade=t=out:st={duration-fade}:d={fade}'
            inputs=['-i',str(source)]
            if incoming=='dissolve':
                last=joined.parent/f'edge-{index}.png'
                ffmpeg(['-sseof',str(-1/fps),'-i',str(segments[index-1]),'-frames:v','1',str(last)])
                fade=max(1/fps,min(float(previous.get('transition_seconds') or .5),duration))
                inputs+=['-loop','1','-framerate',str(fps),'-i',str(last)]
                filters[0]+='[picture]'
                filters+= [f'[1:v]format=yuva420p,fade=t=out:st=0:d={fade}:alpha=1[edge]',
                           f"[picture][edge]overlay=shortest=1:enable='lt(t,{fade})'[v]"]
            else:
                filters[0]+='[v]'
            if incoming=='cut' and outgoing=='cut':
                files.append(source)
                continue
            target=joined.parent/f'transition-{index}.mp4'
            ffmpeg(inputs+['-filter_complex_threads','1','-filter_complex',';'.join(filters),
                           '-map','[v]','-an','-c:v','libx264','-crf','16','-pix_fmt','yuv420p',
                           '-r',str(fps),'-t',str(duration),str(target)])
            files.append(target)
        listing=joined.parent/'picture-concat.txt'
        listing.write_text(''.join(f"file '{file.relative_to(joined.parent).as_posix()}'\n" for file in files), encoding='utf-8')
        ffmpeg(['-f','concat','-safe','0','-i',str(listing),'-map','0:v:0','-an','-c','copy',str(joined)])
        listing.unlink()
