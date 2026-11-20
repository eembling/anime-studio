"""Local speech timing jobs. Recognition mismatches are exposed, never disguised as alignment."""
import difflib,json,re,subprocess,sys,threading,time,unicodedata,uuid,shutil,os,signal
from pathlib import Path

def normalized(text):return ''.join(c for c in unicodedata.normalize('NFKC',text).casefold() if c.isalnum())

def match_words(text, recognized):
    tokens=text.split();speech=[]
    for word in recognized:
        token=normalized(word.get('text',''))
        if token and float(word['end'])>float(word['start']):speech.append((token,word))
    expected=''.join(normalized(w) for w in tokens);heard=''.join(w for w,_ in speech)
    mapping={};offset=0;spans=[]
    for token,w in speech:spans.extend([w]*len(token))
    for block in difflib.SequenceMatcher(None,expected,heard,autojunk=False).get_matching_blocks():
        for i in range(block.size):mapping[block.a+i]=block.b+i
    result=[]
    for token in tokens:
        norm=normalized(token);indexes=[mapping[i] for i in range(offset,offset+len(norm)) if i in mapping];offset+=len(norm)
        ratio=len(indexes)/max(1,len(norm));matched=[spans[i] for i in indexes]
        confidence=min([float(w.get('probability',1)) for w in matched] or [0])*ratio
        result.append({'text':token,'start':min((float(w['start']) for w in matched),default=None),'end':max((float(w['end']) for w in matched),default=None),'confidence':round(confidence,3),'matched':ratio>=.9 and confidence>=.5})
    last=0;usable=bool(tokens) and len(mapping)/max(1,len(heard))>=.9
    for w in result:
        if w['start'] is None or w['end'] is None or w['end']<=w['start'] or w['start']<last or not w['matched']:usable=False
        if w['end'] is not None:last=w['end']
    return {'words':result,'usable':usable,'coverage':round(len(mapping)/max(1,len(expected)),3),'transcript':' '.join(w.get('text','') for _,w in speech)}

class Alignment:
    def __init__(self,studio):
        self.st=studio;self.root=Path.home()/'Library/Application Support/AnimeStudio/Alignment'
        self.setup_state={'status':'idle'};self.lock=threading.Lock();self.processes=set()
        studio.db.x('CREATE TABLE IF NOT EXISTS alignment_jobs(id TEXT PRIMARY KEY,shot_id INTEGER,line_id TEXT,token TEXT,status TEXT,json TEXT,error TEXT,created REAL)')
        studio.db.x("UPDATE alignment_jobs SET status='error',error='Alignment was interrupted. Run alignment again.' WHERE status='running'")
    def ready(self):return (self.root/'ready.json').is_file() and (self.root/'venv/bin/python').is_file()
    def state(self):return {'ready':self.ready(),'setup':self.setup_state,'jobs':[dict(r,result=json.loads(r.pop('json') or '{}')) for r in self.st.db.q('SELECT * FROM alignment_jobs ORDER BY created DESC LIMIT 30')]}
    def action(self,body):
        if body['action']=='setup':
            with self.lock:
                if self.setup_state['status']=='running':return {'ok':True}
                self.setup_state={'status':'running','message':'Installing local speech engine and downloading the base model…'}
                threading.Thread(target=self.install,daemon=True).start()
            return {'ok':True}
        if body['action']!='start':raise ValueError('Unknown alignment action.')
        if not self.ready():raise ValueError('Set up local speech alignment in Settings → Tools & storage first.')
        shot,line=self.st.creative.line(body)
        if body.get('token')!=self.st.creative.performance_token(shot,line):raise ValueError('Performance changed. Reopen it before aligning.')
        if self.st.db.one("SELECT id FROM alignment_jobs WHERE shot_id=? AND line_id=? AND status='running'",(shot['id'],line['line_id'])):raise ValueError('This line is already being aligned.')
        path=self.st.creative.path(line.get('audio'))
        from anime_studio import media_seconds
        if media_seconds(path)>600:raise ValueError('Align dialogue recordings up to ten minutes long.')
        ident=uuid.uuid4().hex
        self.st.db.x("INSERT INTO alignment_jobs VALUES(?,?,?,?,'running','{}',NULL,?)",(ident,shot['id'],line['line_id'],body['token'],time.time()))
        threading.Thread(target=self.run,args=(ident,path,line['text']),daemon=True).start()
        return {'ok':True,'id':ident}
    def process(self,args,timeout,input=None,log=None):
        p=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=log or subprocess.PIPE,stderr=subprocess.STDOUT if log else subprocess.PIPE,text=True,start_new_session=True)
        with self.lock:self.processes.add(p)
        try:
            out,err=p.communicate(input,timeout=timeout)
            if p.returncode:raise subprocess.CalledProcessError(p.returncode,args,out,err)
            return subprocess.CompletedProcess(args,p.returncode,out,err)
        finally:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:p.wait(2)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
            with self.lock:self.processes.discard(p)
    def shutdown(self):
        with self.lock:processes=list(self.processes)
        for p in processes:
            if p.poll() is None:
                try:os.killpg(p.pid,signal.SIGTERM)
                except ProcessLookupError:pass

    def install(self):
        try:
            script=Path(__file__).parent/'install_alignment.sh'
            if not script.exists():script=Path(__file__).parents[1]/'mac/install_alignment.sh'
            self.root.mkdir(parents=True,exist_ok=True)
            with (self.root/'setup.log').open('w') as log:
                self.process(['bash',str(script),str(self.root),shutil.which('python3.12') or ('/opt/homebrew/bin/python3.12' if Path('/opt/homebrew/bin/python3.12').exists() else sys.executable)],timeout=1200,log=log)
            (self.root/'ready.json').write_text(json.dumps({'model':'base','created':time.time()}))
            self.setup_state={'status':'done','message':'Local speech alignment is ready.'}
        except Exception as e:self.setup_state={'status':'error','message':'Setup failed. See '+str(self.root/'setup.log')+'. '+str(e)}
    def run(self,ident,path,text):
        try:
            result=self.process([str(self.root/'venv/bin/python'),str(Path(__file__).parent/'alignment_worker.py'),str(path),'base',str(self.root/'models')],timeout=600)
            data=json.loads(result.stdout);aligned=match_words(text,data['words']);aligned['language']=data.get('language')
            self.st.db.x("UPDATE alignment_jobs SET status='done',json=? WHERE id=?",(json.dumps(aligned),ident))
        except Exception as e:
            detail=(getattr(e,'stderr','') or str(e))[-2000:]
            self.st.db.x("UPDATE alignment_jobs SET status='error',error=? WHERE id=?",('Speech alignment failed: '+detail,ident))
