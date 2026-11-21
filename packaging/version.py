import json
from pathlib import Path
import plistlib
import re
import subprocess


def version(root=Path('.')):
    value=re.search(r'^VERSION = "([0-9]+(?:\.[0-9]+)+)"',(root/'app/anime_studio.py').read_text(),re.M).group(1)
    data=plistlib.loads((root/'mac/Info.plist').read_bytes())
    assert value==data['CFBundleVersion']==data['CFBundleShortVersionString'],'Source versions disagree'
    return value


if __name__=='__main__':print(version())
