"""Explicit macOS WebKit acceptance using a disposable project and temporary test window."""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'app'))
from anime_studio import Studio,API,ffmpeg


def main():
    if sys.platform!='darwin':
        raise SystemExit('This explicit acceptance check requires macOS and Xcode command line tools.')
    with tempfile.TemporaryDirectory(prefix='anime-native-playback-') as temporary:
        folder=Path(temporary)
        studio=Studio(folder/'project');api=API(studio)
        studio.db.x('UPDATE instances SET enabled=0')
        sid=api.shot_save({'scene':'Fixture','kind':'motion','duration':4})['id']
        video=studio.data/'shots/clip.mp4'
        ffmpeg(['-f','lavfi','-i','color=blue:s=128x72:r=16:d=4','-c:v','libx264',str(video)])
        ffmpeg(['-f','lavfi','-i','sine=frequency=440:duration=4','-af','volume=0.1',str(studio.data/'voices/test.wav')])
        tid=studio.db.x("INSERT INTO takes(shot_id,stage,path,fps,frames) VALUES(?,'animate','shots/clip.mp4',16,64)",(sid,))
        studio.db.x("UPDATE shots SET clip_take=?,audio='voices/test.wav',audio_mode='file' WHERE id=?",(tid,sid))
        studio.db.conn.close()
        executable=folder/'native-check'
        env=dict(os.environ,CLANG_MODULE_CACHE_PATH='/tmp/anime-studio-clang-cache',
                 SWIFT_MODULECACHE_PATH='/tmp/anime-studio-swift-cache')
        subprocess.run(['xcrun','swiftc',str(ROOT/'tests/native_playback.swift'),'-o',str(executable),
                        '-framework','AppKit','-framework','WebKit'],check=True,env=env)
        process=subprocess.Popen([sys.executable,str(ROOT/'app/anime_studio.py'),'--projects','--port','8871','--data',str(folder/'project')],
                                 env=dict(os.environ,ANIME_STUDIO_CONFIG=str(folder/'config')),
                                 stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(50):
                try:
                    if opener.open('http://127.0.0.1:8871/api/ping',timeout=.5).status==200:break
                except OSError:time.sleep(.1)
            else:raise RuntimeError('Disposable app did not start.')
            subprocess.run([str(executable)],check=True,timeout=35)
        finally:
            from project_workspace import read_catalog, controller_status
            for item in read_catalog(folder/'config').values():
                status = controller_status(Path(item['path']))
                if status and status['port'] != 8871:
                    try:
                        opener.open(urllib.request.Request(f"http://127.0.0.1:{status['port']}/api/quit", data=b'{}'), timeout=2).close()
                    except OSError:
                        pass
            process.terminate()
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait()


if __name__=='__main__':
    main()
