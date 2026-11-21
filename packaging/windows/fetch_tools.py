"""Download a checksum-pinned FFmpeg build and retain its distribution notices."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

URL='https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/download/autobuild-2026-11-21-12-37/ffmpeg-N-129166-gae9f4540cb-win64-gpl.zip'
SHA='4f7e54b17b37ee7d8c539129efb839291929f27998f00f812828aeed1be9ce14'
ROOT=Path(__file__).parent/'tools'
if __name__=='__main__':
    ROOT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        archive=Path(directory)/'ffmpeg.zip'
        urllib.request.urlretrieve(URL,archive)
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==SHA,'FFmpeg checksum mismatch'
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                path=Path(name)
                if path.name in ('ffmpeg.exe','ffprobe.exe') or path.suffix.lower() in ('.txt','.md'):
                    if name.endswith('/'):continue
                    (ROOT/path.name).write_bytes(z.read(name))
    (ROOT/'FFMPEG-SOURCE.txt').write_text('FFmpeg GPL distribution: '+URL+'\nBuild source and dependency recipes: https://github.com/FFmpeg-Builds/FFmpeg-Builds\nFFmpeg corresponding revision: https://github.com/FFmpeg/FFmpeg/tree/ae9f4540cb\nLicense: https://www.gnu.org/licenses/gpl-3.0.txt\n', encoding='utf-8')
