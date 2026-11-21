"""CI-only packaged startup, media and native-window checks with temporary data."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    parser=argparse.ArgumentParser();parser.add_argument('platform',choices=['windows','macos']);parser.add_argument('path');args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='Anime Studio CI ') as temporary:
        root=Path(temporary);config=root/'config';data=root/'project';data.mkdir();config.mkdir()
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        env=dict(os.environ,ANIME_STUDIO_CONFIG=str(config),ANIME_STUDIO_DATA=str(data),ANIME_STUDIO_PORT=str(port),ANIME_STUDIO_SMOKE='1')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if args.platform=='windows':
            command=[args.path]
        else:
            # Launch Services, never the inner native binary. These environment
            # overrides are isolated to this CI process's launchd session.
            for key in ('ANIME_STUDIO_DATA','ANIME_STUDIO_PORT'):
                subprocess.run(['launchctl','setenv',key,env[key]],check=True)
            command=['open','-n',args.path]
        process=subprocess.Popen(command,env=env)
        try:
            for _ in range(240):
                try:
                    with opener.open(f'http://127.0.0.1:{port}/api/ping',timeout=1) as r:ping=json.load(r)
                    break
                except Exception:time.sleep(.5)
            else:raise RuntimeError('Packaged backend did not start')
            sys.path.insert(0,str(Path(__file__).parent));from version import version
            assert ping['version']==version() and ping['app']=='anime-studio'
            for route in ('/','/voice_video.js','/studio_audio.js'):
                with opener.open(f'http://127.0.0.1:{port}'+route,timeout=5) as r:assert len(r.read())>100
            if args.platform=='windows':
                for _ in range(120):
                    if (config/'window-ready.json').exists():break
                    time.sleep(.5)
                else:raise RuntimeError('Windows desktop WebView did not load')
                assert json.loads((config/'window-ready.json').read_text(encoding='utf-8'))['version']==version()
                (config/'smoke-complete').write_text('verified', encoding='utf-8')
                process.wait(timeout=15);assert process.returncode==0
                tools=Path(args.path).parent/'_internal/tools'
                subprocess.run([str(tools/'ffmpeg.exe'),'-v','error','-f','lavfi','-i','sine=duration=0.2',str(root/'tone.wav')],check=True)
                subprocess.run([str(tools/'ffprobe.exe'),'-v','error',str(root/'tone.wav')],check=True)
            else:
                # Verify a native window process owns the app backend, without
                # relying on screenshots or accessibility automation.
                listing=subprocess.check_output(['ps','-axo','pid,ppid,command'],text=True)
                assert str(Path(args.path)/'Contents/MacOS/AnimeStudioWindow') in listing
            assert (data/'studio.db').exists()
            (data/'keep-project.txt').write_text('preserve user project on uninstall', encoding='utf-8')
            print('Packaged '+args.platform+' startup, resources and version verified.')
        finally:
            try:opener.open(urllib.request.Request(f'http://127.0.0.1:{port}/api/quit',data=b'{}',headers={'Content-Type':'application/json'}),timeout=2).close()
            except Exception:pass
            if args.platform=='windows' and process.poll() is None:process.terminate();process.wait(timeout=10)
            if args.platform=='macos':
                for key in ('ANIME_STUDIO_DATA','ANIME_STUDIO_PORT'):subprocess.run(['launchctl','unsetenv',key],check=False)

if __name__=='__main__':main()
