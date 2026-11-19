#!/usr/bin/env bash
# Anime Studio - model downloads for the ComfyUI server. Nothing is installed: every workflow uses
# nodes built into ComfyUI. Run on the server. Already-complete files are skipped (wget -c).
set -e
cd ~/ComfyUI/models
mkdir -p diffusion_models text_encoders vae loras upscale_models audio_encoders

# ===== KEYFRAMES + CHARACTER SHEETS: Qwen-Image-Edit 2509 + 4-step Lightning LoRA (you have these) =====
wget -c -P diffusion_models https://huggingface.co/Comfy-Org/Qwen-Image-Edit_ComfyUI/resolve/main/split_files/diffusion_models/qwen_image_edit_2509_fp8_e4m3fn.safetensors

wget -c -P text_encoders https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors

wget -c -P vae https://huggingface.co/Comfy-Org/Qwen-Image_ComfyUI/resolve/main/split_files/vae/qwen_image_vae.safetensors

wget -c -P loras https://huggingface.co/lightx2v/Qwen-Image-Lightning/resolve/main/Qwen-Image-Edit-2509/Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors

# ===== ANIMATION: Wan 2.2 I2V 14B fp8 (high + low noise) + 4-step Lightning LoRAs =====
wget -c -P diffusion_models https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors

wget -c -P diffusion_models https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors

wget -c -P text_encoders https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors

wget -c -P vae https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors

wget -c -P loras https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors

wget -c -P loras https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors

# ===== UPSCALING (optional): anime upscaler =====
wget -c -P upscale_models https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.2.4/RealESRGAN_x4plus_anime_6B.pth

# ===== OPTIONAL: Wan 2.2 TI2V 5B - native 24 fps, smaller, lower quality (uncomment to use) =====
# wget -c -P diffusion_models https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors
# wget -c -P vae https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors

# ===== OPTIONAL: Wan 2.2 S2V lip sync (16.4 GB, slow on a 3060) - see README, Lip sync =====
# wget -c -P diffusion_models https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors
# wget -c -P audio_encoders https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/audio_encoders/wav2vec2_large_english_fp16.safetensors

echo
echo "Check sizes - a file of only a few KB is an error page, not a model:"
ls -lh diffusion_models text_encoders vae loras upscale_models
echo
echo "Then press R in the ComfyUI page (or restart ComfyUI) so the new models appear."
