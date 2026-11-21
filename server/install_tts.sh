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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

# 2. Check out the pinned node pack and use its declared requirements.
"$PY" "$SCRIPT_DIR/recipe_assets.py" node "$COMFY" tts
"$PY" -m pip install -r "$COMFY/custom_nodes/ComfyUI-Qwen-TTS/requirements.txt"

# The 'sox' Python package uses the sox program when it is present (optional, removes a warning)
if ! command -v sox >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
  echo "Optional: sudo apt-get install -y sox"
fi

# 3. Download and verify the pinned model revisions.
"$PY" "$SCRIPT_DIR/recipe_assets.py" fetch "$COMFY" tts

ls -la "$COMFY/models/qwen-tts"
echo
echo "Done. Restart ComfyUI so it loads the new nodes, then check its log for 'Qwen3-TTS' errors."
echo "To roll back the Python packages: $PY -m pip install -r $SNAP"
