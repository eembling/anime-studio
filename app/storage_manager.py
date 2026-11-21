"""Reviewed, reversible cleanup. References, history and exports are protected."""
import json,os,shutil,time,uuid
from pathlib import Path
from creative import digest

class StorageManager:
    def __init__(self,studio):self.st=studio
    def idle(self):
        self.st.editor.idle()
        if self.st.db.one("SELECT id FROM alignment_jobs WHERE status='running'"):raise ValueError('Wait for speech alignment to finish before cleanup.')
    def references(self):
        refs=set();root=self.st.data
        def visit(value):
            if isinstance(value,dict):
                for k,v in value.items():visit(k);visit(v)
            elif isinstance(value,list):
                for v in value:visit(v)
            elif isinstance(value,str):
                if value[:1] in ('{','['):
                    try:visit(json.loads(value))
                    except (ValueError,TypeError):pass
                if len(value)<4096 and value.startswith(('uploads/','shots/','cast/','voices/','history/','exports/','cache/','storage-trash/')):
                    path=(root/value).resolve()
                    if root in path.parents:refs.add(str(path.relative_to(root)))
        for table in self.st.db.q("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            name=table['name']
            if not name.replace('_','').isalnum():continue
            for row in self.st.db.q('SELECT * FROM '+name):visit(row)
        return refs
    def files(self,folder):
        root=self.st.data/folder
        if not root.exists() or root.is_symlink():return
        for current,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(current)/d).is_symlink()]
            for name in files:
                p=Path(current)/name
                if p.is_symlink() or not p.is_file():continue
                try:stat=p.stat()
                except OSError:continue
                yield {'path':self.st.rel(p),'bytes':stat.st_size,'stamp':stat.st_mtime_ns}
    def scan(self):
        refs=self.references();groups=[];candidates=[]
        for folder,label in [('shots','Generated picture'),('voices','Dialogue audio'),('uploads','References & render inputs'),('cast','Cast'),('cache','Rebuildable cache'),('history','Recovery history'),('exports','Exports'),('storage-trash','Cleanup trash'),('backups','Portable backups'),('backup-imports','Imported archives'),('jobs','Render collection work')]:
            files=list(self.files(folder) or []);protected=folder in ('history','exports','storage-trash','backups','backup-imports','jobs')
            groups.append({'name':label,'folder':folder,'bytes':sum(f['bytes'] for f in files),'files':len(files)})
            for f in files:
                if not protected and f['path'] not in refs:
                    candidates.append(dict(f,category='cache' if folder=='cache' else 'unused'))
        known={g['folder'] for g in groups};other=[]
        for p in self.st.data.iterdir():
            if p.is_symlink() or p.name in known:continue
            if p.is_file():other.append({'bytes':p.stat().st_size})
            elif p.is_dir():other.extend(self.files(p.name) or [])
        groups.append({'name':'Database, logs & other files','folder':'other','bytes':sum(f['bytes'] for f in other),'files':len(other)})
        candidates.sort(key=lambda f:f['path'])
        batches=self.batches();payload={'files':candidates,'references':sorted(refs)}
        return {'ok':True,'groups':groups,'candidates':candidates,'token':digest(payload),'batches':batches,'protected_note':'Selected media, saved episodes, render inputs, history and exports stay protected. Trash still uses disk space until permanently deleted.'}
    def batches(self):
        root=self.st.data/'storage-trash'
        if not root.exists() or root.is_symlink():return []
        result=[]
        for p in root.glob('*/manifest.json'):
            try:
                if p.parent.is_symlink():continue
                data=json.loads(p.read_text(encoding='utf-8'));result.append({'id':p.parent.name,'created':data['created'],'files':len(data['files']),'bytes':sum(f['bytes'] for f in data['files'])})
            except (ValueError,KeyError):continue
        return sorted(result,key=lambda b:b['created'],reverse=True)
    def safe(self,path):
        p=(self.st.data/path).resolve()
        if self.st.data not in p.parents:raise ValueError('Invalid cleanup path.')
        return p
    def action(self,body):
        action=body['action']
        if action=='scan':return self.scan()
        self.idle()
        if (self.st.data/'storage-trash').is_symlink():raise ValueError('Cleanup trash must be a real project folder, not a symlink.')
        if action=='cleanup':
            scan=self.scan()
            if body.get('token')!=scan['token']:raise ValueError('Project files changed. Review cleanup again.')
            selected=set(body.get('paths') or []);available={f['path']:f for f in scan['candidates']}
            if not selected or selected-set(available):raise ValueError('Choose files from the reviewed cleanup list.')
            ident=uuid.uuid4().hex;root=self.st.data/'storage-trash'/ident;root.mkdir(parents=True)
            files=[available[p] for p in sorted(selected)];moved=[]
            try:
                for f in files:
                    target=root/f['path'];target.parent.mkdir(parents=True,exist_ok=True);self.safe(f['path']).rename(target);moved.append(f)
                (root/'manifest.json').write_text(json.dumps({'created':time.time(),'files':files}), encoding='utf-8')
            except Exception:
                for f in reversed(moved):(root/f['path']).rename(self.safe(f['path']))
                shutil.rmtree(root,ignore_errors=True);raise
            return {'ok':True,'id':ident,'files':len(files),'bytes':sum(f['bytes'] for f in files)}
        ident=str(body.get('id') or '')
        if len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Choose a cleanup batch.')
        raw_root=self.st.data/'storage-trash'/ident
        if raw_root.is_symlink():raise ValueError('Invalid cleanup batch.')
        root=raw_root.resolve()
        if root.parent!=(self.st.data/'storage-trash').resolve():raise ValueError('Invalid cleanup batch.')
        manifest=root/'manifest.json'
        if not manifest.is_file():raise ValueError('Cleanup batch no longer exists.')
        data=json.loads(manifest.read_text(encoding='utf-8'));files=data['files']
        if any(Path(f['path']).parts[0] not in ('cache','uploads','shots','voices','cast') for f in files):raise ValueError('Invalid cleanup manifest.')
        actual=sorted(self.files('storage-trash/'+ident) or [],key=lambda f:f['path'])
        expected={'storage-trash/'+ident+'/'+f['path'] for f in files}|{'storage-trash/'+ident+'/manifest.json'}
        if set(f['path'] for f in actual)!=expected:raise ValueError('Cleanup batch changed. Inspect or restore it before deleting.')
        review_token=digest([data,actual])
        for f in files:
            original=self.safe(f['path']);stored=(root/f['path']).resolve()
            if root not in stored.parents or stored.is_symlink():raise ValueError('Invalid cleanup manifest.')
        if action=='restore':
            for f in files:
                if self.safe(f['path']).exists():raise ValueError('A restored filename is already in use. Nothing was overwritten.')
            for f in files:
                target=self.safe(f['path']);target.parent.mkdir(parents=True,exist_ok=True);(root/f['path']).rename(target)
            shutil.rmtree(root);return {'ok':True,'restored':len(files)}
        if action=='purge':
            if body.get('confirm') is not True or body.get('token')!=review_token:raise ValueError('Review and confirm permanent trash deletion first.')
            refs=self.references()
            if any(p.startswith('storage-trash/'+ident+'/') for p in refs):raise ValueError('This batch contains referenced media. Restore it instead.')
            if any(f['path'] in refs or self.st.rel(root/f['path']) in refs for f in files):raise ValueError('This batch now contains referenced media. Restore it instead.')
            shutil.rmtree(root);return {'ok':True,'freed_bytes':sum(f['bytes'] for f in files)}
        if action=='review_purge':return {'ok':True,'token':review_token,'files':len(files),'bytes':sum(f['bytes'] for f in files)}
        raise ValueError('Unknown storage action.')
