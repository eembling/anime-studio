"""Shared picture, dialogue, mixer and reusable sound finishing tools."""
import json
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
        return {
            'mixer': self.mixer(),
            'library': [dict(row, url='/media/'+row['path']) for row in
                        self.st.db.q('SELECT * FROM sound_library ORDER BY name,id')],
        }

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
            settings = st.settings()
            mixer = self.mixer()
            sources = []
            for shot in shots:
                takes = [st.take(shot[key]) for key in ('keyframe_take','clip_take','final_take')]
                takes += st.chain(shot)
                sources.extend(st.production.file_stamp(take['path']) for take in takes if take)
                sources.append(st.production.file_stamp(shot['audio']))
            cues = st.soundtrack.cues()
            sources.extend(st.production.file_stamp(cue['path']) for cue in cues)
            sources.append(st.production.file_stamp(settings.get('music')))
            token = st.soundtrack.digest([shots, indices, settings, mixer, options, cues, sources,
                                         [st.creative.direction(s['id']) for s in selected]])
            label = 'shot-'+str(int(selected[0]['seq'])).zfill(3) if body.get('shot_id') else 'scene'
            cache = st.data/'cache'/('rendered-'+label+'-'+token+'.mp4')
            if cache.is_file():
                st.assembly.update(running=False, kind='shot' if body.get('shot_id') else 'scene',
                                   output=st.rel(cache), folder='cache', log=['Using cached picture and sound preview.'])
                return {'ok': True, 'cached': True, 'url': '/media/'+st.rel(cache)}
            st.assembly.update(running=True, output=None, log=['Rendering picture and sound…'],
                               kind='shot' if body.get('shot_id') else 'scene')
            threading.Thread(target=self.render, args=(selected, cache, bool(body.get('shot_id')), options), daemon=True).start()
            return {'ok': True, 'cached': False}
        raise ValueError('Unknown finishing action.')

    def render(self, shots, cache, single, options):
        from anime_studio import assemble
        try:
            # Serialize editorial writes while source selections and mix controls
            # are read, so one preview never combines different edit revisions.
            with self.st.production.lock:
                self.st.assembly['running'] = True
                assemble(self.st, options['use_music'], options['music_volume'],
                         use_timed=options['use_timed'], shot_ids=[s['id'] for s in shots])
                output = self.st.assembly['output']
                if output:
                    source = self.st.abs(output)
                    temporary = cache.with_suffix('.tmp')
                    shutil.copyfile(source, temporary)
                    temporary.replace(cache)
                    # Scoped previews only retain the cached result, not duplicate
                    # per-shot intermediates and dialogue masters.
                    work = self.st.abs(self.st.assembly['folder'])
                    if work.parent == self.st.data/'exports':
                        shutil.rmtree(work)
                    self.st.assembly.update(output=self.st.rel(cache), folder='cache', kind='shot' if single else 'scene')
        except Exception as exc:
            self.st.assembly['log'].append('ERROR: '+str(exc))
        finally:
            self.st.assembly['running'] = False

    def dialogue_tracks(self, shots):
        from anime_studio import media_seconds
        rows, start = [], 0
        fps = int(self.st.settings()['out_fps'])
        for shot in shots:
            duration = round(shot['duration']*fps)/fps
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

    def mix_audio(self, master, output, cues, bed, bed_volume, duration, raw=False):
        from anime_studio import ffmpeg
        mixer = self.mixer()
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
        if not raw and mixer['duck']:
            filters += ['[dialogue]asplit=2[spoken][sidechain]',
                        '[music][sidechain]sidechaincompress=threshold=0.025:ratio=8:attack=15:release=350[ducked]']
            music, dialogue = 'ducked', 'spoken'
        else:
            music, dialogue = 'music', 'dialogue'
        filters.append(f'[{dialogue}][{music}][ambience][sfx]amix=inputs=4:normalize=0:duration=longest,'
                       f'alimiter=limit=0.95:level=0:latency=1,atrim=duration={duration}[mix]')
        ffmpeg(inputs+['-filter_complex', ';'.join(filters), '-map','0:v:0','-map','[mix]',
                       '-c:v','copy','-c:a','aac','-b:a','192k','-ar','48000','-ac','2','-t',str(duration),str(output)])
        output.with_suffix('.audio.json').write_text(json.dumps({'cues': tracks,'mixer':mixer},indent=2))
        return duration

    def join_picture(self, shots, segments, joined, fps):
        """Transitions begin at the cut, preserving shot and audio cue positions."""
        from anime_studio import ffmpeg
        current = segments[0]
        elapsed = round(shots[0]['duration']*fps)/fps
        for index in range(1, len(segments)):
            shot = shots[index]
            previous = shots[index-1]
            transition = previous.get('transition') or 'cut'
            duration = min(float(previous.get('transition_seconds') or .5),
                           round(shot['duration']*fps)/fps, elapsed)
            duration = max(1/fps, round(duration*fps)/fps)
            dest = joined.parent/f'join-{index}.mp4'
            if transition == 'dissolve':
                # Hold the outgoing edge while revealing the first frames of
                # the incoming shot. No timeline shortening or cue movement.
                graph = (f'[0:v]settb=AVTB,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration}[left];'
                         f'[1:v]settb=AVTB,setpts=PTS-STARTPTS[right];'
                         f'[left][right]xfade=transition=fade:duration={duration}:offset={elapsed}[v]')
            elif transition == 'black':
                half = duration/2
                graph = (f'[0:v]fade=t=out:st={max(0,elapsed-half)}:d={half},setpts=PTS-STARTPTS[left];'
                         f'[1:v]fade=t=in:st=0:d={half},setpts=PTS-STARTPTS[right];'
                         '[left][right]concat=n=2:v=1:a=0[v]')
            else:
                graph = '[0:v]setpts=PTS-STARTPTS[left];[1:v]setpts=PTS-STARTPTS[right];[left][right]concat=n=2:v=1:a=0[v]'
            elapsed += round(shot['duration']*fps)/fps
            ffmpeg(['-i',str(current),'-i',str(segments[index]),'-filter_complex_threads','1',
                    '-filter_complex',graph,'-map','[v]','-an','-c:v','libx264','-pix_fmt','yuv420p',
                    '-r',str(fps),'-t',str(elapsed),str(dest)])
            if current not in segments:
                current.unlink()
            current = dest
        if len(segments)==1:
            shutil.copyfile(current, joined)
        else:
            current.rename(joined)
