#!/bin/bash
# Local Mac setup only. No project audio is uploaded. Base model download is about 150 MB.
set -euo pipefail
ALIGN_ROOT="${1:-$HOME/Library/Application Support/AnimeStudio/Alignment}"
PYTHON_BIN="${2:-python3}"
mkdir -p "$ALIGN_ROOT"
"$PYTHON_BIN" -m venv "$ALIGN_ROOT/venv"
"$ALIGN_ROOT/venv/bin/python" -m pip install --upgrade pip
"$ALIGN_ROOT/venv/bin/python" -m pip install 'faster-whisper==1.2.1' 'av>=14,<16'
"$ALIGN_ROOT/venv/bin/python" - "$ALIGN_ROOT/models" <<'PY'
import sys,json,time
from pathlib import Path
from faster_whisper import WhisperModel
WhisperModel('base',device='cpu',compute_type='int8',download_root=sys.argv[1])
(Path(sys.argv[1]).parent/'ready.json').write_text(json.dumps({'model':'base','created':time.time()}))
print('Local speech alignment is ready.')
PY
