"""Immutable inputs for every newly queued render, including ordinary jobs."""
import copy
import json
import shutil
import uuid


class JobSnapshots:
    def __init__(self, studio):
        self.st=studio
        if 'snapshot_json' not in {r['name'] for r in studio.db.q('PRAGMA table_info(jobs)')}:
            studio.db.x('ALTER TABLE jobs ADD COLUMN snapshot_json TEXT')

    def meta(self, job):
        return json.loads(job.get('snapshot_json') or '{}')

    def capture(self, job):
        from anime_studio import Worker
        if job.get('snapshot_json') or job.get('prompt_id'):return
        st=self.st;key,params=Worker(st).compose(job)
        row=st.creative.workflow(job) or st.wf_row(key)
        if not row:raise ValueError('Configure the '+key+' workflow before queuing.')
        params=copy.deepcopy(params);folder=st.data/'uploads'/'job-inputs'/uuid.uuid4().hex
        folder.mkdir(parents=True)
        try:
            from clip_audio import capture as capture_clip_audio, condition as condition_clip_audio, gpu_s2v
            pinned=capture_clip_audio(st,job,folder,row.get('fps'))
            key,params,actual,conditioned,note=condition_clip_audio(st,job,key,params,row,folder,pinned)
            switched=actual!=gpu_s2v(row);row=actual
            for slot,source in list(params.get('files',{}).items()):
                source=st.creative.path(st.rel(source));dest=folder/(slot.replace('/','_')+source.suffix)
                shutil.copyfile(source,dest);params['files'][slot]=st.rel(dest)
            meta={'key':key,'params':params,'workflow':json.loads(row['json']), 'fps':row.get('fps'),
                  'max_frames':row.get('max_frames'),'controls':{} if switched else st.creative.meta(job).get('values',{})}
            if any(node['class_type']=='WanSoundImageToVideo' for node in meta['workflow'].values()):
                meta['controls']={key:value for key,value in meta['controls'].items() if not (key.endswith('.device') and meta['workflow'].get(key.split('.',1)[0],{}).get('class_type')=='CLIPLoader')}
            meta['clip_audio']=pinned
            meta['audio_conditioned']=conditioned
            meta['audio_note']=note
            if job['stage']=='upscale':
                source=st.db.one('SELECT t.fps FROM takes t JOIN shots s ON s.clip_take=t.id WHERE s.id=?',(job['shot_id'],))
                meta['fps']=(source or {}).get('fps') or 16
            if not st.db.one('SELECT job_id FROM production_inputs WHERE job_id=?',(job['id'],)):st.production.capture(job)
            meta['signature']=(st.db.one('SELECT signature FROM production_inputs WHERE job_id=?',(job['id'],)) or {}).get('signature')
            st.db.x('UPDATE jobs SET snapshot_json=? WHERE id=?',(json.dumps(meta),job['id']))
        except Exception:
            shutil.rmtree(folder,ignore_errors=True);raise

    def composed(self, job):
        meta=self.meta(job);params=copy.deepcopy(meta['params'])
        params['files']={slot:self.st.creative.path(path) for slot,path in params.get('files',{}).items()}
        return meta['key'],params

    def workflow(self, job):
        meta=self.meta(job)
        return {'json':json.dumps(meta['workflow']),'fps':meta['fps'],'max_frames':meta['max_frames']} if meta else None

    def patch(self, job, graph):
        for key,value in self.meta(job).get('controls',{}).items():
            node,field=key.split('.',1);graph[node]['inputs'][field]=value
        return graph

    def repeat(self, ident):
        import time
        st=self.st;old=st.db.one('SELECT * FROM jobs WHERE id=?',(ident,))
        if not old or not old.get('snapshot_json'):raise ValueError('This older render has no captured inputs. Generate a new take from its shot instead.')
        if old['status'] not in ('done','error','cancelled'):raise ValueError('Wait for this render to finish before repeating it.')
        row=dict(old);row.pop('id');row.update(status='pending',created=time.time(),started=None,finished=None,instance_id=None,prompt_id=None,error=None,progress='waiting',models_json=None)
        columns=list(row);jid=st.db.x('INSERT INTO jobs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',list(row.values()))
        meta=json.loads(row['snapshot_json']);meta['params']['prefix']=f'anime/repeat_{jid}'
        st.db.x('UPDATE jobs SET snapshot_json=? WHERE id=?',(json.dumps(meta),jid))
        sig=meta.get('signature') or (st.db.one('SELECT signature FROM production_inputs WHERE job_id=?',(ident,)) or {}).get('signature')
        if sig:st.db.x('INSERT OR REPLACE INTO production_inputs VALUES(?,?)',(jid,sig))
        if row.get('creative_json'):
            creative=json.loads(row['creative_json']);creative['run_id']=uuid.uuid4().hex;creative.pop('server',None);creative['params']['prefix']=meta['params']['prefix']
            st.db.x('UPDATE jobs SET creative_json=? WHERE id=?',(json.dumps(creative),jid))
            st.db.x('INSERT INTO creative_runs VALUES(?,?,?,?,?,?,NULL,NULL,NULL,?,NULL)',(creative['run_id'],jid,old['shot_id'],creative['recipe']['id'],creative['kind'],'pending',json.dumps(creative)))
        return jid

    def compatible(self, job, instance):
        from anime_studio import build_workflow
        meta=self.meta(job);params=copy.deepcopy(meta['params'])
        # Readiness needs input slots, but must never upload files or submit a job.
        params['images']={slot:path for slot,path in params.get('files',{}).items()}
        graph=self.patch(job,build_workflow(meta['workflow'],meta['key'],params))
        recipe={'workflow':graph,'controls':[]}
        return not self.st.creative.inspect_recipe(recipe,{},instance)['problems']
