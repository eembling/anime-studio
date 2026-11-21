#!/usr/bin/env bash
# Run on the ComfyUI server after updating ComfyUI manually.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/recipe_assets.py" fetch "${COMFY:-$HOME/ComfyUI}" lipsync
printf '\nReload model discovery, select the Wan S2V recipe in Studio, and run readiness checks.\n'
