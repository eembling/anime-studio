"""Shared offline ducking envelope with the Studio AudioWorklet constants."""
import array
import json
import math
import wave
from pathlib import Path

CONFIG = json.loads((Path(__file__).parent/'audio_mix.json').read_text(encoding='utf-8'))


def envelope(pcm, rate=48000):
    size = max(1, round(rate*CONFIG['block_seconds']))
    target = 1.0
    gains = [target]
    for start in range(0, len(pcm), size*2):
        block = pcm[start:start+size*2]
        rms = math.sqrt(sum(value*value for value in block)/max(1,len(block)))
        desired = (CONFIG['threshold']/rms)**(1-1/CONFIG['ratio']) if rms>CONFIG['threshold'] else 1
        tau = CONFIG['attack'] if desired<target else CONFIG['release']
        target = desired+(target-desired)*math.exp(-CONFIG['block_seconds']/tau)
        gains.append(target)
    return gains


def write_envelope(dialogue, output):
    import struct
    import tempfile
    from export_runtime import run, LOCAL, ExportCancelled
    # Keep episode-length PCM on disk and analyze one block at a time.
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        pcm=Path(folder)/'dialogue.f32'
        result=run(['ffmpeg','-v','error','-i',str(dialogue),'-ar','48000',
                    '-ac','2','-f','f32le',str(pcm)])
        if result.returncode:raise ValueError('Could not analyze dialogue for ducking.')
        target=1.0
        with wave.open(str(output),'wb') as stream, pcm.open('rb') as source:
            stream.setparams((1,2,round(1/CONFIG['block_seconds']),0,'NONE',''))
            stream.writeframesraw(struct.pack('<h',32767))
            while True:
                raw=source.read(round(48000*CONFIG['block_seconds'])*2*4)
                if not raw:break
                event=getattr(LOCAL,'cancel',None)
                if event and event.is_set():raise ExportCancelled('Export cancelled.')
                block=array.array('f');block.frombytes(raw)
                rms=math.sqrt(sum(value*value for value in block)/max(1,len(block)))
                desired=(CONFIG['threshold']/rms)**(1-1/CONFIG['ratio']) if rms>CONFIG['threshold'] else 1
                tau=CONFIG['attack'] if desired<target else CONFIG['release']
                target=desired+(target-desired)*math.exp(-CONFIG['block_seconds']/tau)
                stream.writeframesraw(struct.pack('<h',round(max(0,min(1,target))*32767)))
