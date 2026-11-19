#!/usr/bin/env bash
# Download native Wan2.2 S2V models. Run ON the ComfyUI server after updating ComfyUI.
# COMFY=/path/to/ComfyUI bash install_lipsync.sh
# Does not update Git, Python packages, custom nodes or services. Restart ComfyUI yourself.
set -euo pipefail
COMFY="${COMFY:-$HOME/ComfyUI}"
if [ ! -f "$COMFY/main.py" ]; then
  echo "ComfyUI not found at $COMFY. Set COMFY to the folder containing main.py." >&2
  exit 1
fi
command -v wget >/dev/null || { echo "Install wget first (Ubuntu: sudo apt-get install wget)." >&2; exit 1; }
mkdir -p "$COMFY/models/diffusion_models" "$COMFY/models/text_encoders" "$COMFY/models/vae" "$COMFY/models/audio_encoders"
ROOT=https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files
fetch() {
  local category="$1" name="$2"
  # Continue partial transfers and skip files already downloaded to their full size.
  wget --continue --tries=3 --directory-prefix="$COMFY/models/$category" "$ROOT/$category/$name"
  test -s "$COMFY/models/$category/$name" || { echo "Download failed: $name" >&2; exit 1; }
}
fetch diffusion_models wan2.2_s2v_14B_fp8_scaled.safetensors
fetch audio_encoders wav2vec2_large_english_fp16.safetensors
fetch text_encoders umt5_xxl_fp8_e4m3fn_scaled.safetensors
fetch vae wan_2.1_vae.safetensors
printf '\nModels downloaded. Restart EVERY ComfyUI instance that will take lipsync jobs.\n'
printf 'Then reopen Anime Studio, choose Settings > Workflows > Lip sync > wan22_s2v_14b_fp8,\n'
printf 'enable lipsync on the intended server, and run Production > Check readiness.\n'
