#!/usr/bin/env bash
# Run on the ComfyUI server. Assets are pinned and checksum verified.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/recipe_assets.py" fetch "${COMFY:-$HOME/ComfyUI}" picture
printf '\nReload model discovery in ComfyUI after the files have been verified.\n'
