#!/usr/bin/env bash
# Install local voice-copying text-to-speech (Qwen3-TTS) into ComfyUI, for Anime Studio's Voices tab.
# Run this ON the ComfyUI server, then restart ComfyUI.
#
#   bash install_tts.sh
#
# What it does:
#   1. saves your current Python package list (so you can roll back)
#   2. installs the ComfyUI-Qwen-TTS node pack (github.com/flybirdxx/ComfyUI-Qwen-TTS) and its requirements
#   3. downloads the Qwen3-TTS voice-cloning and voice-design models into ComfyUI/models/qwen-tts
# Safe to run again: it skips what is already installed and only fetches missing models.
# Everything runs locally; no cloud service or API key is used.
set -euo pipefail

COMFY="${COMFY:-$HOME/ComfyUI}"
if   [ -x "$COMFY/venv/bin/python" ];  then PY="$COMFY/venv/bin/python"
elif [ -x "$COMFY/.venv/bin/python" ]; then PY="$COMFY/.venv/bin/python"
else PY="${PYTHON:-python3}"; fi
echo "ComfyUI: $COMFY"
echo "Python:  $PY ($("$PY" --version 2>&1))"

# 1. Snapshot of installed packages, for rollback: pip install -r <this file>
SNAP="$HOME/comfyui-packages-before-qwen-tts-$(date +%Y%m%d-%H%M%S).txt"
"$PY" -m pip freeze > "$SNAP"
echo "Saved current package list to $SNAP"

# 2. Node pack + requirements (this upgrades transformers to 4.57 or newer)
cd "$COMFY/custom_nodes"
if [ -d ComfyUI-Qwen-TTS ]; then
  (cd ComfyUI-Qwen-TTS && git pull --ff-only)
else
  git clone https://github.com/flybirdxx/ComfyUI-Qwen-TTS
fi
"$PY" -m pip install -r ComfyUI-Qwen-TTS/requirements.txt

# The 'sox' Python package uses the sox program when it is present (optional, removes a warning)
if ! command -v sox >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  echo "Optional: sudo apt-get install -y sox"
fi

# 3. Models: shared tokenizer, the voice-cloning ("Base") models, 1.7B (better) and 0.6B (faster, smaller GPUs),
#    and VoiceDesign (makes a new voice from a written description; 1.7B only)
mkdir -p "$COMFY/models/qwen-tts"
QWEN_ROOT="$COMFY/models/qwen-tts" "$PY" - <<'EOF'
import os
from huggingface_hub import snapshot_download
root = os.environ["QWEN_ROOT"]
for repo in ("Qwen/Qwen3-TTS-Tokenizer-12Hz", "Qwen/Qwen3-TTS-12Hz-1.7B-Base", "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
             "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"):
    target = os.path.join(root, repo.split("/")[-1])
    print(f"Downloading {repo} -> {target}")
    snapshot_download(repo_id=repo, local_dir=target)
print("Models ready.")
EOF

ls -la "$COMFY/models/qwen-tts"
echo
echo "Done. Restart ComfyUI so it loads the new nodes, then check its log for 'Qwen3-TTS' errors."
echo "To roll back the Python packages: $PY -m pip install -r $SNAP"
