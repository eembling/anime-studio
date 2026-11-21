import json
import shutil
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'packaging'))
from version import version

if __name__=='__main__':
    app=ROOT/'app';tools=ROOT/'packaging/windows/tools'
    assert (tools/'ffmpeg.exe').is_file() and (tools/'ffprobe.exe').is_file()
    product=version(ROOT)
    numbers=[int(n) for n in product.split('.')]
    assert len(numbers)<=4 and all(0<=n<=65535 for n in numbers)
    numeric=tuple(numbers+[0]*(4-len(numbers)))
    resource=ROOT/'build/windows-version.txt';resource.parent.mkdir(exist_ok=True)
    resource.write_text("VSVersionInfo(ffi=FixedFileInfo(filevers="+repr(numeric)+",prodvers="+repr(numeric)+",mask=0x3f,flags=0,OS=0x40004,fileType=1,subtype=0,date=(0,0)),kids=[StringFileInfo([StringTable('040904B0',[StringStruct('CompanyName','Anime Studio'),StringStruct('FileDescription','Anime Studio'),StringStruct('FileVersion',"+repr(product)+"),StringStruct('ProductName','Anime Studio'),StringStruct('ProductVersion',"+repr(product)+")])]),VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding='utf-8')
    command=[sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed','--name','AnimeStudio','--paths',str(app),
             '--distpath',str(ROOT/'dist'),'--workpath',str(ROOT/'build/windows'),'--specpath',str(ROOT/'build'),'--version-file',str(resource),
             '--add-binary',str(tools/'ffmpeg.exe')+':tools','--add-binary',str(tools/'ffprobe.exe')+':tools']
    for source in app.iterdir():
        if source.is_file() and source.suffix in ('.py','.js','.html','.css','.json'):
            command+=['--add-data',str(source)+':.']
        if source.suffix=='.py':command+=['--hidden-import',source.stem]
    command += [str(ROOT/'packaging/windows/launcher.py')]
    subprocess.run(command,cwd=ROOT,check=True)
    for notice in tools.glob('*.txt'):
        shutil.copy2(notice,ROOT/'dist/AnimeStudio'/notice.name)
    shutil.copy2(ROOT/'packaging/RELEASE.md',ROOT/'dist/AnimeStudio/RELEASE.md')
    data={'version':version(ROOT),'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'platform':'windows','architecture':'x64'}
    (ROOT/'dist/AnimeStudio/_internal/build_info.json').write_text(json.dumps(data), encoding='utf-8')
