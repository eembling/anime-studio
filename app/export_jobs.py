"""Persistent local exports rendered from isolated SQLite and media snapshots."""
import json
import shutil
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from export_runtime import LOCAL, ExportCancelled


class ExportJobs:
    def __init__(self,studio):
        self.st=studio
        self.events={}
        self.lock=threading.RLock()
        self.root=studio.data/'jobs'/'exports'
        self.root.mkdir(parents=True,exist_ok=True)
        studio.db.x('''CREATE TABLE IF NOT EXISTS export_jobs(
            id TEXT PRIMARY KEY, spec TEXT, status TEXT, operations INTEGER, result TEXT,
            error TEXT, created REAL, updated REAL)''')
        # Resume is explicit so an interrupted encode never restarts unexpectedly.
        studio.db.x("UPDATE export_jobs SET status='interrupted',error='App closed during export. Resume this captured export.' WHERE status IN ('queued','preparing','running')")
        studio.assembly['running']=False

    def rows(self):
        return [dict(row,spec=json.loads(row['spec']),result=json.loads(row['result'] or '{}'))
                for row in self.st.db.q('SELECT * FROM export_jobs ORDER BY created DESC LIMIT 40')]

    def status(self,ident,status,**values):
        fields={'status':status,'updated':time.time(),**values}
        self.st.db.x('UPDATE export_jobs SET '+','.join(key+'=?' for key in fields)+' WHERE id=?',
                     (*fields.values(),ident))

    def submit(self,spec):
        with self.lock:
            if self.st.assembly['running'] or self.st.db.one("SELECT id FROM export_jobs WHERE status IN ('queued','preparing','running') LIMIT 1"):raise ValueError('Wait for or cancel the current export.')
            for stamp in spec.get('expected_sources',[]):
                if stamp and self.st.production.file_stamp(stamp[0])!=stamp:
                    raise ValueError('Source changed while preparing preview. Retry from the current edit.')
            ident=uuid.uuid4().hex
            folder=self.root/ident
            folder.mkdir()
            self.st.db.x("INSERT INTO export_jobs VALUES(?,?,'queued',0,NULL,NULL,?,?)",
                         (ident,json.dumps(spec),time.time(),time.time()))
            # SQLite backup is short. File copies and media encoding happen after
            # the request releases the production lock.
            with self.st.db.lock, sqlite3.connect(folder/'studio.db') as connection:
                self.st.db.conn.backup(connection)
            with sqlite3.connect(folder/'studio.db') as connection:
                paths=self.media(connection,spec)
            (folder/'media.json').write_text(json.dumps([self.st.production.file_stamp(path) for path in paths]))
            self.launch(ident,spec)
            return {'ok':True,'job_id':ident,'cached':False}

    def launch(self,ident,spec):
        event=threading.Event()
        self.events[ident]=event
        self.st.assembly.update(running=True,output=None,job_id=ident,kind='animatic' if spec.get('preview') else spec['kind'],log=['Preparing captured export…'])
        threading.Thread(target=self.work,args=(ident,spec,event),daemon=True).start()

    def action(self,body):
        ident=body['id']
        job=self.st.db.one('SELECT * FROM export_jobs WHERE id=?',(ident,))
        if not job:raise ValueError('Export job no longer exists.')
        if body['action']=='cancel':
            event=self.events.get(ident)
            if event:
                from export_runtime import stop
                stop(event)
            return {'ok':True}
        if body['action']=='resume':
            with self.lock:
                if self.st.assembly['running'] or self.st.db.one("SELECT id FROM export_jobs WHERE status IN ('queued','preparing','running') LIMIT 1"):raise ValueError('Wait for the current export.')
                if job['status'] not in ('interrupted','error','cancelled'):raise ValueError('This export cannot be resumed.')
                self.status(ident,'queued',error=None)
                self.launch(ident,json.loads(job['spec']))
            return {'ok':True,'job_id':ident}
        raise ValueError('Unknown export job action.')

    def media(self,connection,spec):
        connection.row_factory=sqlite3.Row
        shots=[dict(row) for row in connection.execute('SELECT * FROM shots ORDER BY seq,id')]
        selected=[s for s in shots if (not spec.get('shot_ids') or s['id'] in spec['shot_ids']) and
                  (spec.get('scene') is None or s['scene']==spec['scene'])]
        takes={row['id']:dict(row) for row in connection.execute('SELECT * FROM takes')}
        from timeline import layout
        settings=dict(connection.execute('SELECT key,value FROM settings').fetchall())
        timing={row['id']:row for row in layout(shots,int(settings['out_fps']))}
        left=min((timing[shot['id']]['start'] for shot in selected),default=0)
        right=max((timing[shot['id']]['start']+timing[shot['id']]['duration'] for shot in selected),default=0)
        paths=set()
        for shot in shots:
            begin=timing[shot['id']]['start']+float(shot.get('dialogue_offset') or 0)
            end=begin+timing[shot['id']]['duration']+float(shot.get('dialogue_tail') or 0)
            if shot['audio'] and begin<right and end>left:paths.add(shot['audio'])
        for shot in selected:
            for tid in [shot['keyframe_take'],shot['clip_take'],shot['final_take'],*json.loads(shot['cont_takes'] or '[]')]:
                if tid in takes:paths.add(takes[tid]['path'])
        if spec.get('use_timed',True) or spec['kind'] in ('stems','soundtrack'):
            starts={shot['id']:timing[shot['id']]['start'] for shot in shots}
            for row in connection.execute('SELECT * FROM audio_cues WHERE enabled=1'):
                start=row['start']
                if row['anchor_shot']:
                    if row['anchor_shot'] not in starts:continue
                    start=starts[row['anchor_shot']]+row['anchor_offset']
                if spec['kind'] in ('stems','soundtrack') or start<right and start+row['duration']>left:
                    paths.add(row['path'])
        music=connection.execute("SELECT value FROM settings WHERE key='music'").fetchone()
        if music and music[0] and (spec.get('use_music') or spec['kind'] in ('stems','soundtrack')):paths.add(music[0])
        if spec['kind'] in ('stems','soundtrack'):
            record=connection.execute('SELECT * FROM episode_audio_exports WHERE id=?',(spec['id'],)).fetchone()
            if not record:raise ValueError('Rendered episode no longer exists.')
            paths.update([record['master'],record['output']])
            def visit(value):
                if isinstance(value,dict):
                    for item in value.values():visit(item)
                elif isinstance(value,list):
                    for item in value:visit(item)
                elif isinstance(value,str) and value.startswith(('uploads/','voices/','shots/','exports/','cache/')):
                    paths.add(value)
            visit(json.loads(record['build_json'] or '{}'))
        return sorted(paths)

    def work(self,ident,spec,event):
        from anime_studio import Studio,assemble
        folder=self.root/ident
        snapshot=folder/'project'
        clone=None
        try:
            self.status(ident,'preparing')
            manifest_file=folder/'media.json'
            if not manifest_file.exists():
                with sqlite3.connect(folder/'studio.db') as connection:
                    paths=self.media(connection,spec)
                manifest=[self.st.production.file_stamp(path) for path in paths]
                manifest_file.write_text(json.dumps(manifest))
            else:manifest=json.loads(manifest_file.read_text())
            snapshot.mkdir(exist_ok=True)
            for stamp in manifest:
                if event.is_set():raise ExportCancelled('Export cancelled.')
                path=stamp[0];target=(snapshot/path).resolve()
                if snapshot.resolve() not in target.parents:raise ValueError('Invalid captured media path.')
                if target.is_file():continue
                if self.st.production.file_stamp(path)!=stamp:raise ValueError('Captured source changed before copying: '+path)
                source=self.st.creative.path(path)
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary=target.with_suffix(target.suffix+'.copying')
                shutil.copy2(source,temporary)
                if self.st.production.file_stamp(path)!=stamp:
                    temporary.unlink(missing_ok=True)
                    raise ValueError('Captured source changed during copying: '+path)
                temporary.replace(target)
            # Keep the original database untouched for retries.
            shutil.copy2(folder/'studio.db',snapshot/'studio.db')
            clone=Studio(snapshot,export_worker=True)
            # Publish logs, but keep partial output and running state private
            # until all files have been moved into the live project.
            self.st.assembly['log']=clone.assembly['log']
            LOCAL.cancel=event
            def progress():
                self.st.assembly['log']=list(clone.assembly['log'])
                self.st.db.x('UPDATE export_jobs SET operations=operations+1,updated=? WHERE id=?',(time.time(),ident))
            LOCAL.progress=progress
            self.status(ident,'running')
            if spec['kind']=='stems':
                result=clone.editor.delivery({'id':spec['id'],'music_volume':spec.get('music_volume',.25)})
            elif spec['kind']=='soundtrack':
                source=clone.db.one('SELECT * FROM episode_audio_exports WHERE id=?',(spec['id'],))
                clone.soundtrack._export(source,clone.abs(source['master']),clone.soundtrack.cues(),
                                        clone.settings().get('music','') if spec.get('use_music') else '',
                                        spec.get('music_volume',.25),bool(spec.get('use_music')))
                result=None
            else:
                assemble(clone,bool(spec.get('use_music')),spec.get('music_volume',.25),
                         bool(spec.get('preview')),spec.get('scene'),bool(spec.get('use_timed',True)),spec.get('shot_ids'))
                result=None
            if event.is_set():raise ExportCancelled('Export cancelled.')
            if result is None:
                output=clone.assembly.get('output')
                if not output:raise RuntimeError('\n'.join(clone.assembly['log'][-5:]))
                if spec.get('cache'):
                    destination=self.st.data/'cache'/spec['cache']
                    temporary=destination.with_suffix('.tmp')
                    shutil.copy2(snapshot/output,temporary);temporary.replace(destination)
                    output=self.st.rel(destination)
                    self.st.assembly.update(output=output,folder='cache')
                else:
                    source_folder=clone.abs(clone.assembly['folder'])
                    destination=self.st.data/'exports'/('job-'+ident)
                    if destination.exists():shutil.rmtree(destination)
                    shutil.copytree(source_folder,destination)
                    old=source_folder.relative_to(snapshot).as_posix()
                    output=(destination/(Path(output).relative_to(old))).relative_to(self.st.data).as_posix()
                    self.st.assembly.update(output=output,folder=self.st.rel(destination))
                    record=clone.db.one('SELECT * FROM episode_audio_exports WHERE output=?',(clone.assembly['output'],))
                    if record and not spec.get('preview'):
                        build=json.loads(record['build_json'] or '{}') or None
                        self.st.soundtrack.register(destination/Path(record['master']).name,self.st.abs(output),
                                                    record['seconds'],bool(record['clean']),build)
                result={'output':output,'url':'/media/'+output}
            else:
                destination=self.st.data/'exports'/('stems-'+ident)
                destination.mkdir(exist_ok=True)
                files={}
                for name,url in result['files'].items():
                    source=clone.abs(url.removeprefix('/media/'))
                    shutil.copy2(source,destination/name);files[name]='/media/'+self.st.rel(destination/name)
                result=dict(result,files=files)
            shutil.rmtree(folder)
            self.status(ident,'done',result=json.dumps(result),error=None)
        except ExportCancelled as exc:
            self.status(ident,'cancelled',error=str(exc));self.st.assembly['log'].append(str(exc))
        except Exception as exc:
            self.status(ident,'error',error=str(exc));self.st.assembly['log'].append('ERROR: '+str(exc))
        finally:
            if clone:clone.db.conn.close()
            LOCAL.cancel=None;LOCAL.progress=None
            self.events.pop(ident,None)
            self.st.assembly['running']=False

    def shutdown(self):
        from export_runtime import stop
        for event in list(self.events.values()):stop(event)
