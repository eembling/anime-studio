"""Saved section direction and queue-time continuation provenance."""
import hashlib
import json
import math
import shutil
import uuid
from creative import digest, number


class Continuations:
    def __init__(self, studio):
        self.st = studio
        studio.db.x('CREATE TABLE IF NOT EXISTS shot_sections(shot_id INTEGER,segment INTEGER,prompt TEXT,camera TEXT,fallback INTEGER DEFAULT 0,PRIMARY KEY(shot_id,segment))')
        studio.db.x('CREATE TABLE IF NOT EXISTS section_takes(take_id INTEGER PRIMARY KEY,json TEXT)')
        self.audio_cache = {}

    def row(self, sid, segment):
        return self.st.db.one('SELECT * FROM shot_sections WHERE shot_id=? AND segment=?', (sid, int(segment)))

    def audio_seconds(self, shot):
        from anime_studio import media_seconds
        if not shot.get('audio'): return 0.0
        stamp = tuple(self.st.production.file_stamp(shot['audio']))
        if stamp not in self.audio_cache:
            self.audio_cache[stamp] = media_seconds(self.st.abs(shot['audio']))
        return self.audio_cache[stamp]

    def coverage(self, shot):
        chain = self.st.chain(shot)
        covered = self.st.chain_seconds(chain)
        audio = self.audio_seconds(shot)
        trim = float(shot.get('trim_in') or 0)
        return {'audio_seconds': audio, 'audio_after_trim': max(0, audio-trim), 'target_seconds':float(shot['duration']),
                'covered_seconds':max(0, covered-trim), 'remaining_seconds':max(0,float(shot['duration'])+trim-covered),
                'needs_extension':audio-trim>float(shot['duration'])+.04}

    def details(self, sid, segment=None, boundary=False):
        shot = self.st.creative.shot(sid); chain = self.st.chain(shot)
        segment = len(chain)-1 if segment is None else int(segment)
        if segment < 0 or segment >= len(chain): raise ValueError('Select the previous section’s clip first.')
        chain = chain[:segment+1]; source = chain[-1]
        covered = self.st.chain_seconds(chain); remaining = float(shot['duration'])+float(shot.get('trim_in') or 0)-covered
        recipe, values = self.st.creative.current_recipe('animate', sid)
        key = 'animate'
        if shot.get('audio'):
            from clip_audio import choose_workflow, gpu_s2v
            row={'json':json.dumps(recipe['workflow']),'fps':recipe['fps'],'max_frames':recipe['max_frames']}
            selected,actual,conditioned,_=choose_workflow(self.st,'continue',key,row,not recipe.get('id') and not shot['end_next'])
            if conditioned and selected=='lipsync':
                if actual!=gpu_s2v(row):
                    recipe=dict(recipe,id=None,name='Voice-driven continuation',workflow=json.loads(actual['json']),fps=actual['fps'],max_frames=actual['max_frames'],controls=[])
                    values={}
                recipe=dict(recipe,workflow=json.loads(actual['json']))
                key=selected
        fps, maxf = float(recipe['fps']), int(recipe['max_frames'])
        if fps<=0 or maxf<2: raise ValueError('Set a positive animation fps and a frame limit of at least two.')
        capacity=(maxf-1)/fps
        pinned_end = any(n.get('_meta',{}).get('title','').upper()=='END_IMAGE' for n in recipe['workflow'].values())
        if pinned_end and remaining>capacity+.001:
            raise ValueError('Use an animation recipe without END_IMAGE for earlier sections. Endpoint targeting is only for the final section.')
        if shot['end_next'] and not pinned_end:
            row=self.st.wf_row('animate_flf')
            if not row: raise ValueError('Configure the first/last-frame workflow for the final section.')
            final_capacity=(int(row['max_frames'])-1)/float(row['fps'])
            if remaining<=final_capacity+.001:
                recipe={'id':None,'name':row['name'],'version':0,'stage':'animate','workflow':json.loads(row['json']),'fps':row['fps'],'max_frames':row['max_frames'],'controls':[],'purpose':'generation','requirements':''};values={}
                fps,maxf=float(recipe['fps']),int(recipe['max_frames']);capacity=final_capacity;key='animate_flf'
            elif remaining<=capacity+.001:
                capacity=max(1/fps,remaining-final_capacity)
        if pinned_end: key='animate_flf'
        saved=self.row(sid,segment)
        token=digest([shot['id'],segment,[t['id'] for t in chain],self.st.production.file_stamp(source['path']),shot['duration'],shot.get('trim_in'),self.st.production.file_stamp(shot.get('audio')),shot.get('dialogue_offset'),recipe,values])
        frame=self.st.last_frame(self.st.abs(source['path'])) if boundary else None
        return {'shot_id':sid,'segment':segment,'section':segment+2,'start':covered,'end':covered+max(0,min(remaining,capacity)),
                'remaining':max(0,remaining),'requested_seconds':max(0,min(remaining,capacity)), 'fps':fps,'max_frames':maxf,'capacity':capacity,
                'source_take':source['id'],'source_path':source['path'],'previous_parts':[t['id'] for t in chain],
                'source_token':token,'boundary_url':'/media/'+self.st.rel(frame) if frame else None,
                'prompt':saved['prompt'] if saved else '', 'camera':saved['camera'] if saved else '', 'fallback':bool(saved and saved['fallback']),
                'shared_prompt':shot['motion_prompt'] or shot['keyframe_prompt'], 'shared_camera':shot['camera'],
                'style':self.st.settings().get('motion_suffix',''),'recipe':recipe,'values':values,'key':key,'coverage':self.coverage(shot)}

    def action(self, body):
        action=body['action'];shot=self.st.creative.shot(body['shot_id'])
        if action=='extend':
            self.st.editor.checkpoint('Before extending shot to fit dialogue')
            duration=max(float(shot['duration']), self.audio_seconds(shot)-float(shot.get('trim_in') or 0))
            self.st.db.x('UPDATE shots SET duration=? WHERE id=?',(duration,shot['id']))
            return {'ok':True,'duration':duration}
        if action=='inspect':return {'ok':True,**self.details(shot['id'],body.get('segment'),True)}
        if action=='save':
            segment=int(number(body['segment'],0,999));prompt=str(body.get('prompt') or '').strip()[:10000]
            if not prompt: raise ValueError('Describe what happens in this section.')
            self.st.editor.checkpoint('Before continuation direction')
            self.st.db.x('INSERT OR REPLACE INTO shot_sections VALUES(?,?,?,?,?)',(shot['id'],segment,prompt,str(body.get('camera') or '')[:2000],int(bool(body.get('fallback')))))
            return {'ok':True}
        raise ValueError('Unknown continuation action.')

    def compose(self, job):
        d=self.details(job['shot_id'],job.get('segment'));shot=self.st.creative.shot(job['shot_id']);saved=self.row(shot['id'],d['segment'])
        if not saved: raise ValueError('Add direction for section '+str(d['section'])+' in Continue shot before generating.')
        if d['remaining']<1/d['fps']:raise ValueError('This shot is already covered. Extend its editorial duration if needed.')
        # A selected predecessor with known stale provenance must be reviewed first.
        source=self.st.take(d['source_take'])
        if source['stage']=='continue' and self.st.production.provenance(source,shot)=='outdated': raise ValueError('The previous section needs review or regeneration before continuing.')
        S=self.st.settings();draft=bool(job.get('draft'));frame=self.st.last_frame(self.st.abs(d['source_path']))
        prompt=', '.join(x for x in ('the motion continues seamlessly from the first frame',saved['prompt'],saved['camera'] or shot['camera'],S.get('motion_suffix','')) if x)
        files={'start':frame}
        if d['key']=='animate_flf':
            nxt=self.st.neighbor(shot,1);end=self.st.take_path(nxt['keyframe_take']) if nxt else None
            if not end:raise ValueError('The final section needs the next shot’s selected keyframe.')
            files['end']=end
        maximum=min(d['max_frames'],int(S['draft_max_frames'])) if draft else d['max_frames']
        length=min(maximum,4*math.ceil(d['requested_seconds']*d['fps']/4)+1)
        return d['key'],{'seed':job['seed'],'prefix':f"anime/j{job['id']:06d}_continue",'files':files,'prompt':prompt,'negative':S.get('video_negative',''),
                         'width':S['draft_width'] if draft else S['vid_width'],'height':S['draft_height'] if draft else S['vid_height'],'length':length}

    def generate(self, body):
        from anime_studio import API
        st=self.st;made=[];skipped=[]
        with st.production.lock:
            for sid in body.get('shot_ids') or []:
                before=st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
                try:
                    d=self.details(sid,body.get('segment'))
                    if body.get('source_token') and body['source_token']!=d['source_token']:raise ValueError('The source or timing changed. Reopen Continue shot to review the boundary.')
                    if not self.row(sid,d['segment']):
                        if not body.get('use_shared_prompt'):raise ValueError('Add direction for section '+str(d['section'])+' in Continue shot, or explicitly choose to reuse the shared shot direction.')
                        self.action({'action':'save','shot_id':sid,'segment':d['segment'],'prompt':d['shared_prompt'],'camera':'','fallback':True})
                    if st.db.one("SELECT id FROM jobs WHERE shot_id=? AND stage='continue' AND segment=? AND status IN ('pending','running')",(sid,d['segment'])):raise ValueError('This section already has queued or active renders.')
                    st.creative.require_ready(dict(d['recipe'],stage='continue'),d['values'])
                    result=API(st)._generate(dict(body,stage='continue',shot_ids=[sid],segment=d['segment']))
                    skipped.extend(result['skipped'])
                    for job in st.db.q('SELECT * FROM jobs WHERE id>?',(before,)):
                        key,p=self.compose(job)
                        # Retain an immutable boundary and endpoint even if a source file is replaced later.
                        folder=st.data/'uploads'/'continuations';folder.mkdir(exist_ok=True)
                        for slot,path in list(p['files'].items()):
                            dest=folder/(uuid.uuid4().hex+path.suffix);shutil.copyfile(path,dest);p['files'][slot]=dest
                        row=self.row(sid,d['segment'])
                        st.creative.freeze(job,d['recipe'],d['values'],key,p,kind='continuation',source_id=d['source_take'],source_token=d['source_token'],
                                           section_direction=row,previous_parts=d['previous_parts'],requested_seconds=min(d['requested_seconds'],(p['length']-1)/d['fps']),
                                           boundary_hash=hashlib.sha256(p['files']['start'].read_bytes()).hexdigest(),manual=False)
                        made.append(job['id'])
                except (ValueError,RuntimeError) as exc:
                    for job in st.db.q("SELECT id FROM jobs WHERE id>? AND status='pending'",(before,)):
                        for table,field in [('creative_runs','job_id'),('production_inputs','job_id'),('jobs','id')]:st.db.x('DELETE FROM '+table+' WHERE '+field+'=?',(job['id'],))
                    skipped.append(f'Shot {sid}: {exc}')
            return {'ok':bool(made) or not skipped,'queued':len(made),'job_ids':made,'skipped':skipped,'error':'; '.join(skipped) if skipped and not made else None}

    def legacy_review(self, ids):
        # Old takes have no source snapshot. Record a review requirement, not invented history.
        for tid in ids:
            take=self.st.take(tid)
            if take and not take.get('input_signature'):
                self.st.db.x('INSERT OR REPLACE INTO section_takes VALUES(?,?)',(tid,json.dumps({'kind':'legacy','review_required':True})))

    def needs_review(self, tid):
        row=self.st.db.one('SELECT json FROM section_takes WHERE take_id=?',(tid,))
        return bool(row and json.loads(row['json']).get('review_required'))

    def saved_take(self, tid, job):
        meta=self.st.creative.meta(job)
        if meta.get('kind')=='continuation':self.st.db.x('INSERT OR REPLACE INTO section_takes VALUES(?,?)',(tid,json.dumps(meta)))

    def state(self, shot):
        rows=self.st.db.q('SELECT * FROM shot_sections WHERE shot_id=? ORDER BY segment',(shot['id'],))
        return {'coverage':self.coverage(shot),'sections':rows,'takes':{t['id']:json.loads(row['json']) for t in self.st.db.q("SELECT id FROM takes WHERE shot_id=? AND stage='continue'",(shot['id'],)) if (row:=self.st.db.one('SELECT json FROM section_takes WHERE take_id=?',(t['id'],)))}}
