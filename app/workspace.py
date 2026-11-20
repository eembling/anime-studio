"""Timeline waveform and cut-frame inspection; no GPU work."""
import array,hashlib,json,math,subprocess

class Workspace:
    def __init__(self,studio):self.st=studio;self.waves={}
    def action(self,body):
        st=self.st;shot=st.creative.shot(body['shot_id'])
        if body['action']=='waveform':
            if not shot.get('audio'):return {'ok':True,'peaks':[],'seconds':0}
            path=st.creative.path(shot['audio']);key=tuple(st.production.file_stamp(shot['audio']))
            if key not in self.waves:
                from anime_studio import media_seconds
                seconds=media_seconds(path)
                if seconds>1200:raise ValueError('Waveform previews support shot audio up to twenty minutes.')
                raw=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-ac','1','-ar','8000','-f','s16le','-'],capture_output=True,check=True).stdout
                samples=array.array('h');samples.frombytes(raw);step=max(1,math.ceil(len(samples)/512))
                self.waves[key]={'ok':True,'seconds':seconds,'peaks':[round(max((abs(x) for x in samples[i:i+step]),default=0)/32768,4) for i in range(0,len(samples),step)]}
                if len(self.waves)>64:self.waves.pop(next(iter(self.waves)))
            return self.waves[key]
        if body['action']=='cut':
            next_shot=st.neighbor(shot,1)
            if not next_shot:raise ValueError('Choose a shot with a following cut.')
            return {'ok':True,'left':self.frame(shot,float(shot['duration'])-1/(int(st.settings()['out_fps']) or 24)),'right':self.frame(next_shot,0),'left_seq':shot['seq'],'right_seq':next_shot['seq']}
        raise ValueError('Unknown timeline action.')
    def frame(self,shot,local):
        from anime_studio import ffmpeg,media_seconds
        st=self.st;root=st.take(shot['final_take']) or st.take(shot['clip_take'])
        if not root or shot['kind']=='still' and root['stage'] not in ('lipsync','upscale'):
            key=st.take(shot['keyframe_take']);return '/media/'+key['path'] if key else None
        chain=[root] if root['stage']=='upscale' else st.chain(shot);offset=max(0,float(shot.get('trim_in') or 0)+local)
        for i,take in enumerate(chain):
            fps=float(take.get('fps') or 16);skip=1/fps if i else 0;duration=st.take_frames(take)/fps-skip
            if offset<duration or i==len(chain)-1:
                path=st.creative.path(take['path']);at=min(offset+skip,max(0,media_seconds(path)-1/fps))
                digest=hashlib.sha256(json.dumps([st.production.file_stamp(take['path']),at]).encode()).hexdigest()[:24]
                dest=st.data/'cache'/('cut-'+digest+'.png')
                if not dest.exists():ffmpeg(['-ss',str(at),'-i',str(path),'-frames:v','1',str(dest)])
                return '/media/'+st.rel(dest)
            offset-=duration
        return None
