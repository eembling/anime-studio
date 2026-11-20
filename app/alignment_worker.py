"""Runs in the optional alignment environment; emits recognition word timestamps as JSON."""
import json,sys,subprocess
import numpy as np
from faster_whisper import WhisperModel
model=WhisperModel(sys.argv[2],device='cpu',compute_type='int8',download_root=sys.argv[3],local_files_only=True)
pcm=subprocess.run(['ffmpeg','-v','error','-i',sys.argv[1],'-t','600','-ac','1','-ar','16000','-f','f32le','-'],capture_output=True,check=True).stdout
segments,info=model.transcribe(np.frombuffer(pcm,dtype='<f4'),word_timestamps=True,beam_size=5,vad_filter=True)
words=[{'text':w.word.strip(),'start':w.start,'end':w.end,'probability':w.probability} for s in segments for w in (s.words or [])]
print(json.dumps({'words':words,'language':info.language}))
