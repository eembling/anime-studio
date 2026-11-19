"""Timed episode audio cues and soundtrack-only exports. No GPU work is submitted."""
import json
import math
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path


def seconds(value):
    """Accept seconds, MM:SS or HH:MM:SS, including fractional seconds."""
    parts = str(value).strip().split(':')
    if len(parts) > 3 or any(not p.strip() for p in parts):
        raise ValueError('Use seconds, MM:SS or HH:MM:SS.')
    nums = [float(p) for p in parts]
    if any(not math.isfinite(n) or n < 0 for n in nums) or (len(nums) > 1 and any(n >= 60 for n in nums[1:])):
        raise ValueError('Time must be non-negative, with seconds/minutes below 60 in timecodes.')
    total = sum(n * 60 ** i for i, n in enumerate(reversed(nums)))
    if total > 86400:
        raise ValueError('Time cannot exceed 24 hours.')
    return total


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', str(path)], capture_output=True, text=True)
    if result.returncode:
        raise ValueError('Cannot read this media file. Choose a valid audio/video file.')
    info = json.loads(result.stdout)
    duration = float(info.get('format', {}).get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('The media has no usable duration.')
    return duration, {s['codec_type'] for s in info.get('streams', [])}


class Soundtrack:
    def __init__(self, studio, ffmpeg):
        self.st, self.ffmpeg = studio, ffmpeg
        studio.db.x('''CREATE TABLE IF NOT EXISTS audio_cues(id INTEGER PRIMARY KEY, name TEXT, kind TEXT, path TEXT,
            start REAL, offset REAL, duration REAL, volume REAL, fade_in REAL, fade_out REAL, loop INTEGER, enabled INTEGER,
            source_seconds REAL)''')
        studio.db.x('''CREATE TABLE IF NOT EXISTS episode_audio_exports(id INTEGER PRIMARY KEY, title TEXT, master TEXT UNIQUE,
            output TEXT, seconds REAL, clean INTEGER, created REAL)''')

    def cues(self):
        return self.st.db.q('SELECT * FROM audio_cues ORDER BY start,id')

    def exports(self):
        return self.st.db.q('SELECT * FROM episode_audio_exports ORDER BY created DESC,id DESC')

    def save(self, body):
        old = self.st.db.one('SELECT * FROM audio_cues WHERE id=?', (body.get('id'),))
        if body.get('id') and not old:
            raise ValueError('This audio cue was removed. Refresh and try again.')
        uploaded = None
        try:
            if body.get('file'):
                uploaded = self.st.save_upload(body['file'], 'soundtrack')
                path = uploaded
            elif old:
                path = old['path']
            else:
                raise ValueError('Choose a music or sound-effect file.')
            source_duration, streams = probe(self.st.abs(path))
            if 'audio' not in streams:
                raise ValueError('This file contains no audio stream.')
            start = seconds(body.get('start', 0))
            offset = seconds(body.get('offset', 0))
            if offset >= source_duration:
                raise ValueError('Source offset must be before the end of the audio file.')
            duration = seconds(body.get('duration') or source_duration - offset)
            if duration <= 0:
                raise ValueError('Duration must be greater than zero.')
            loop = bool(body.get('loop'))
            if not loop:
                duration = min(duration, source_duration - offset)
            fade_in, fade_out = seconds(body.get('fade_in', 0)), seconds(body.get('fade_out', 0))
            if fade_in + fade_out > duration:
                raise ValueError('Combined fades cannot exceed the cue duration.')
            volume = float(body.get('volume', 1))
            if not math.isfinite(volume) or not 0 <= volume <= 4:
                raise ValueError('Volume must be between 0 and 4.')
            kind = body.get('kind', 'sfx')
            if kind not in ('music', 'sfx'):
                raise ValueError('Choose Music or Sound effect.')
            name = str(body.get('name') or (body.get('file') or {}).get('name') or (old or {}).get('name') or 'Audio cue').strip()[:160]
            vals = (name, kind, path, start, offset, duration, volume, fade_in, fade_out, int(loop), int(bool(body.get('enabled', True))), source_duration)
            with self.st.production.lock:
                if old:
                    self.st.db.x('UPDATE audio_cues SET name=?,kind=?,path=?,start=?,offset=?,duration=?,volume=?,fade_in=?,fade_out=?,loop=?,enabled=?,source_seconds=? WHERE id=?', vals + (old['id'],))
                    aid = old['id']
                else:
                    aid = self.st.db.x('INSERT INTO audio_cues(name,kind,path,start,offset,duration,volume,fade_in,fade_out,loop,enabled,source_seconds) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', vals)
            return {'ok': True, 'id': aid}
        except Exception:
            if uploaded:
                self.st.abs(uploaded).unlink(missing_ok=True)
            raise

    def delete(self, body):
        # Keep uploaded media so exports and in-flight mixes are never invalidated by editing the cue list.
        self.st.db.x('DELETE FROM audio_cues WHERE id=?', (body['id'],))
        return {'ok': True}

    def register(self, master, output, duration, clean=True):
        self.st.db.x('INSERT INTO episode_audio_exports(title,master,output,seconds,clean,created) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(master) DO UPDATE SET output=excluded.output',
                     (Path(output).name, self.st.rel(master), self.st.rel(output), duration, int(clean), time.time()))

    def discover(self):
        """Explicitly discover older app exports; never scan the user's other folders."""
        found = 0
        for p in (self.st.data / 'exports').glob('*/*.mp4'):
            if p.name.startswith(('_', 'soundtrack_')) or '_animatic_' in p.parent.name:
                continue
            if self.st.db.one('SELECT id FROM episode_audio_exports WHERE output=? OR master=?', (self.st.rel(p), self.st.rel(p))):
                continue
            master = p.parent / '_dialogue_master.mp4'
            if master.exists() and self.st.db.one('SELECT id FROM episode_audio_exports WHERE master=?', (self.st.rel(master),)):
                continue
            try:
                duration, streams = probe(master if master.exists() else p)
                if 'video' in streams:
                    self.register(master if master.exists() else p, p, duration, master.exists())
                    found += 1
            except ValueError:
                continue
        return {'ok': True, 'found': found}

    def mix(self, master, output, cues, bed='', bed_volume=.25, log=None):
        log = log if log is not None else []
        duration, streams = probe(master)
        inputs = ['-i', str(master)]
        if 'audio' in streams:
            base = '0:a'
        else:
            inputs += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
            base = '1:a'
        filters = [f'[{base}]aresample=48000,apad,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS[base]']
        labels, snapshot = ['[base]'], []
        tracks = [dict(c) for c in cues if c['enabled']]
        if bed:
            tracks.insert(0, dict(name='Whole-episode music', kind='music', path=bed, start=0, offset=0, duration=duration,
                                  volume=bed_volume, fade_in=0, fade_out=0, loop=1, enabled=1))
        input_index = 1 if 'audio' in streams else 2
        for cue in tracks:
            if cue['start'] >= duration:
                log.append(f"Skipped {cue['name']}: starts after the episode ends.")
                continue
            path = (self.st.data / cue['path']).resolve()
            if self.st.data not in path.parents or not path.is_file():
                raise ValueError('Audio source is missing: ' + cue['name'])
            length = min(float(cue['duration']), duration - float(cue['start']))
            source_seconds, kinds = probe(path)
            if 'audio' not in kinds or cue['offset'] >= source_seconds:
                raise ValueError('Audio source no longer matches cue: ' + cue['name'])
            if not cue['loop']:
                length = min(length, source_seconds - cue['offset'])
            inputs += ['-i', str(path)]
            label = 'cue' + str(input_index)
            source_length = min(length, source_seconds - cue['offset'])
            chain = f"[{input_index}:a]aresample=48000,atrim=start={cue['offset']:.6f}:duration={source_length:.6f},asetpts=PTS-STARTPTS"
            if cue['loop'] and length > source_length:
                # Loop the trimmed tail, so a skipped intro never reappears on later repetitions.
                chain += f",aloop=loop=-1:size={max(1,math.ceil(source_length*48000))},atrim=duration={length:.6f},asetpts=PTS-STARTPTS"
            chain += f",volume={cue['volume']:.6f}"
            fi, fo = min(cue['fade_in'], length), min(cue['fade_out'], length)
            if fi:
                chain += f',afade=t=in:st=0:d={fi:.6f}'
            if fo:
                chain += f',afade=t=out:st={max(0,length-fo):.6f}:d={fo:.6f}'
            chain += f",adelay={round(cue['start']*1000)}:all=1[{label}]"
            filters.append(chain)
            labels.append('[' + label + ']')
            snapshot.append(dict(cue, rendered_duration=length))
            input_index += 1
        if not snapshot:
            shutil.copyfile(master, output)
        else:
            filters.append(''.join(labels) + f'amix=inputs={len(labels)}:duration=first:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=0:latency=1[mix]')
            self.ffmpeg(inputs + ['-filter_complex', ';'.join(filters), '-map', '0:v:0', '-map', '[mix]', '-c:v', 'copy',
                                   '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', '-t', f'{duration:.6f}', str(output)])
        output.with_suffix('.audio.json').write_text(json.dumps({'master': self.st.rel(master), 'duration': duration, 'cues': snapshot}, indent=2))
        return duration

    def remix(self, body):
        with self.st.production.lock:
            if self.st.assembly['running']:
                raise ValueError('Wait for the current export to finish.')
            source = self.st.db.one('SELECT * FROM episode_audio_exports WHERE id=?', (body.get('export_id'),))
            if not source:
                raise ValueError('Choose a rendered episode, or use Find rendered episodes first.')
            master = (self.st.data / source['master']).resolve()
            if self.st.data not in master.parents or not master.is_file():
                raise ValueError('The rendered episode source is missing.')
            cues = self.cues()
            settings = self.st.settings()
            bed = settings.get('music', '') if body.get('use_music') else ''
            volume = float(body.get('music_volume', .25))
            if not math.isfinite(volume) or not 0 <= volume <= 4:
                raise ValueError('Music volume must be between 0 and 4.')
            self.st.assembly['running'] = True
            threading.Thread(target=self._export, args=(source, master, cues, bed, volume), daemon=True).start()
        return {'ok': True}

    def _export(self, source, master, cues, bed, volume):
        state = self.st.assembly
        state.update(kind='soundtrack', log=['Mixing soundtrack; video will be copied without rendering.'], output=None)
        output = master.parent / f'soundtrack_{time.strftime("%Y%m%d_%H%M%S")}_{uuid.uuid4().hex[:6]}.mp4'
        try:
            if not source['clean']:
                state['log'].append('This older source includes its existing soundtrack; it cannot be removed automatically.')
            self.mix(master, output, cues, bed, volume, state['log'])
            self.register(master, output, source['seconds'], bool(source['clean']))
            state.update(output=self.st.rel(output), folder=self.st.rel(output.parent))
            state['log'].append('Saved new soundtrack export: ' + output.name)
        except Exception as exc:
            output.unlink(missing_ok=True)
            state['log'].append('ERROR: ' + str(exc))
        finally:
            state['running'] = False
