"""Windows desktop entry point and private backend entry point for frozen builds."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import threading
import urllib.request

ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[2]/'app'))
sys.path.insert(0,str(ROOT))
os.environ['PATH']=str(ROOT/'tools')+os.pathsep+os.environ.get('PATH','')


def request(port,path,body=None):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    data=json.dumps(body).encode() if body is not None else None
    with opener.open(urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,headers={'Content-Type':'application/json'}),timeout=2) as response:
        return json.load(response)


def main():
    if '--backend' in sys.argv:
        sys.argv.remove('--backend')
        if sys.stdout is None:
            destination=Path(os.environ.get('ANIME_STUDIO_CONFIG',os.environ.get('LOCALAPPDATA','.')))
            destination.mkdir(parents=True,exist_ok=True)
            sys.stdout=sys.stderr=(destination/'app.log').open('a',encoding='utf-8')
        import anime_studio
        anime_studio.main();return
    from platform_runtime import config_directory
    from project_storage import resolve_storage
    config=Path(os.environ.get('ANIME_STUDIO_CONFIG',str(config_directory())))
    os.environ['ANIME_STUDIO_CONFIG']=str(config)
    data=resolve_storage(config,override=os.environ.get('ANIME_STUDIO_DATA'))
    port=int(os.environ.get('ANIME_STUDIO_PORT','8700'))
    with socket.socket() as probe:probe.bind(('127.0.0.1',port))
    args=[sys.executable,'--backend','--host','127.0.0.1','--port',str(port),'--data',str(data)]
    if not getattr(sys,'frozen',False):args.insert(1,str(Path(__file__).resolve()))
    with (config/'app.log').open('a',encoding='utf-8') as log:
        backend=subprocess.Popen(args,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for _ in range(120):
                if backend.poll() is not None:raise RuntimeError('Anime Studio server exited. See '+str(config/'app.log'))
                try:
                    ping=request(port,'/api/ping')
                    if ping.get('app')=='anime-studio':break
                except Exception:time.sleep(.25)
            else:raise RuntimeError('Anime Studio server did not become ready. See '+str(config/'app.log'))
            import webview
            window=webview.create_window('Anime Studio',f'http://127.0.0.1:{port}',width=1440,height=960,min_size=(900,600))
            def loaded():
                if os.environ.get('ANIME_STUDIO_SMOKE')=='1':
                    title=window.evaluate_js('document.title')
                    if not title:raise RuntimeError('Desktop UI failed to load')
                    (config/'window-ready.json').write_text(json.dumps({'title':title,'version':ping['version']}), encoding='utf-8')
                    def finish_smoke():
                        for _ in range(180):
                            if (config/'smoke-complete').exists():break
                            time.sleep(.5)
                        window.destroy()
                    threading.Thread(target=finish_smoke,daemon=True).start()
            window.events.loaded+=loaded
            webview.start(gui='edgechromium',private_mode=False,storage_path=str(config/'WebView'))
        finally:
            if backend.poll() is None:
                try:request(port,'/api/quit',{})
                except Exception:backend.terminate()
                try:backend.wait(timeout=8)
                except subprocess.TimeoutExpired:backend.kill();backend.wait()


if __name__=='__main__':
    try:main()
    except Exception as error:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None,str(error),'Anime Studio',0x10)
        raise
