#!/usr/bin/env python3
"""
Anime Studio - produce long-form anime with ComfyUI from one Mac app.

Runs on your Mac (Python 3.9+ standard library only). Talks to ComfyUI over its HTTP API and uses only
nodes built into ComfyUI, so nothing needs installing on the server - just model files.

  ComfyUI server : character sheets, keyframes (Qwen-Image-Edit 2509), animation (Wan 2.2 I2V,
                   first/last-frame mode), optional lip sync (Wan S2V) and upscaling
  This Mac       : script -> shot list, temporary voices (macOS `say`), 16 fps playback,
                   assembly, DaVinci Resolve export (EDL + per-shot clips)

    python3 anime_studio.py --port 8700 --data ~/Documents/AnimeStudio
"""
from script_dialogue import (apply_script_block, dialogue_line, prepare_script_cast, recovery_preview,
                             recover_dialogue, script_blocks, script_preview_token)
import hashlib
from timeline import frames as timeline_frames, layout as timeline_layout
import manga
import argparse
import base64
import copy
import difflib
import csv
import glob
import io
import json
from contextlib import contextmanager, nullcontext
import math
import mimetypes
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "2.26"
for _p in ("/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.homebrew/bin")):
    if os.path.isdir(_p) and _p not in os.environ.get("PATH", ""):
        os.environ["PATH"] = _p + os.pathsep + os.environ.get("PATH", "")

JOB_STAGES = ("sheet", "keyframe", "animate", "continue", "lipsync", "upscale", "voice", "voice_design", "sound_effect", "music_cue")
WF_KEYS = ("keyframe", "animate", "animate_flf", "lipsync", "upscale", "voice", "voice_design", "sound_effect", "music_cue")
ASSET_STAGES = ("sheet", "voice_design")   # jobs that belong to a cast member, not a shot
_ASSET_SQL = "('sheet','voice_design')"
TAKE_FIELD = {"keyframe": "keyframe_take", "animate": "clip_take", "lipsync": "clip_take", "upscale": "final_take"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}
VID_EXT = {".mp4", ".webm", ".mkv", ".mov", ".gif"}
AUD_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aiff", ".aif"}


# ======================================================================================
# Starter workflows - ComfyUI API format, core nodes only.
# Node titles mark the slots the app fills: PROMPT NEGATIVE REF1-3 START_IMAGE END_IMAGE
# VIDEO_IN AUDIO_IN SIZE SAVE. Every seed / noise_seed is randomised per take.
# ======================================================================================
def _n(cls, inputs, title=None):
    d = {"class_type": cls, "inputs": inputs}
    if title:
        d["_meta"] = {"title": title}
    return d


def _wan14b(flf=False):
    wf = {
        "1": _n("UNETLoader", {"unet_name": "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}),
        "2": _n("UNETLoader", {"unet_name": "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors", "weight_dtype": "default"}),
        "3": _n("CLIPLoader", {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}),
        "4": _n("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "5": _n("LoraLoaderModelOnly", {"model": ["1", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors", "strength_model": 1.0}),
        "6": _n("LoraLoaderModelOnly", {"model": ["2", 0], "lora_name": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors", "strength_model": 1.0}),
        "7": _n("ModelSamplingSD3", {"model": ["5", 0], "shift": 5.0}),
        "8": _n("ModelSamplingSD3", {"model": ["6", 0], "shift": 5.0}),
        "10": _n("LoadImage", {"image": "example.png"}, "START_IMAGE"),
        "11": _n("CLIPTextEncode", {"clip": ["3", 0], "text": ""}, "PROMPT"),
        "12": _n("CLIPTextEncode", {"clip": ["3", 0], "text": ""}, "NEGATIVE"),
        "20": _n("KSamplerAdvanced", {"model": ["7", 0], "positive": ["13", 0], "negative": ["13", 1], "latent_image": ["13", 2],
                                      "add_noise": "enable", "noise_seed": 1, "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                                      "scheduler": "simple", "start_at_step": 0, "end_at_step": 2, "return_with_leftover_noise": "enable"}),
        "21": _n("KSamplerAdvanced", {"model": ["8", 0], "positive": ["13", 0], "negative": ["13", 1], "latent_image": ["20", 0],
                                      "add_noise": "disable", "noise_seed": 1, "steps": 4, "cfg": 1.0, "sampler_name": "euler",
                                      "scheduler": "simple", "start_at_step": 2, "end_at_step": 10000, "return_with_leftover_noise": "disable"}),
        "30": _n("VAEDecode", {"samples": ["21", 0], "vae": ["4", 0]}),
        "40": _n("SaveImage", {"images": ["30", 0], "filename_prefix": "anime/clip"}, "SAVE"),
    }
    common = {"positive": ["11", 0], "negative": ["12", 0], "vae": ["4", 0], "start_image": ["10", 0],
              "width": 848, "height": 480, "length": 81, "batch_size": 1}
    if flf:
        wf["14"] = _n("LoadImage", {"image": "example.png"}, "END_IMAGE")
        wf["13"] = _n("WanFirstLastFrameToVideo", dict(common, end_image=["14", 0]), "SIZE")
    else:
        wf["13"] = _n("WanImageToVideo", common, "SIZE")
    return wf


STARTERS = {
    "music_cue": {"ace_step_1_5_turbo": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("CheckpointLoaderSimple", {"ckpt_name": "ace_step_1.5_turbo_aio.safetensors"}),
        "2": _n("TextEncodeAceStepAudio1.5", {"clip": ["1", 1], "tags": "", "lyrics": "", "seed": 1,
                    "bpm": 100, "duration": 20.0, "timesignature": "4", "language": "en", "keyscale": "C major",
                    "generate_audio_codes": True, "cfg_scale": 2.0, "temperature": .85, "top_p": .9, "top_k": 0,
                    "min_p": 0.0}, "PROMPT"),
        "3": _n("ConditioningZeroOut", {"conditioning": ["2", 0]}),
        "4": _n("EmptyAceStep1.5LatentAudio", {"seconds": 20.0, "batch_size": 1}, "DURATION"),
        "5": _n("ModelSamplingAuraFlow", {"model": ["1", 0], "shift": 3.0}),
        "6": _n("KSampler", {"model": ["5", 0], "positive": ["2", 0], "negative": ["3", 0],
                              "latent_image": ["4", 0], "seed": 1, "steps": 8, "cfg": 1.0,
                              "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}),
        "7": _n("VAEDecodeAudio", {"samples": ["6", 0], "vae": ["1", 2]}),
        "8": _n("SaveAudio", {"audio": ["7", 0], "filename_prefix": "anime/score"}, "SAVE"),
    }}},

    "sound_effect": {"stable_audio_open": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("CheckpointLoaderSimple", {"ckpt_name": "stable-audio-open-1.0.safetensors"}),
        "2": _n("CLIPLoader", {"clip_name": "t5-base.safetensors", "type": "stable_audio", "device": "default"}),
        "3": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "PROMPT"),
        "4": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "NEGATIVE"),
        "5": _n("EmptyLatentAudio", {"seconds": 2.0, "batch_size": 1}, "DURATION"),
        "6": _n("KSampler", {"model": ["1", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["5", 0],
                              "seed": 1, "steps": 50, "cfg": 4.98, "sampler_name": "dpmpp_3m_sde_gpu",
                              "scheduler": "exponential", "denoise": 1.0}),
        "7": _n("VAEDecodeAudio", {"samples": ["6", 0], "vae": ["1", 2]}),
        "8": _n("SaveAudio", {"audio": ["7", 0], "filename_prefix": "anime/effect"}, "SAVE"),
    }}},

    "keyframe": {"qwen_edit_2509": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("UNETLoader", {"unet_name": "qwen_image_edit_2509_fp8_e4m3fn.safetensors", "weight_dtype": "default"}),
        "2": _n("CLIPLoader", {"clip_name": "qwen_2.5_vl_7b_fp8_scaled.safetensors", "type": "qwen_image", "device": "default"}),
        "3": _n("VAELoader", {"vae_name": "qwen_image_vae.safetensors"}),
        "4": _n("LoraLoaderModelOnly", {"model": ["1", 0], "lora_name": "Qwen-Image-Edit-2509-Lightning-4steps-V1.0-bf16.safetensors", "strength_model": 1.0}),
        "5": _n("ModelSamplingAuraFlow", {"model": ["4", 0], "shift": 3.0}),
        "10": _n("LoadImage", {"image": "example.png"}, "REF1"),
        "11": _n("LoadImage", {"image": "example.png"}, "REF2"),
        "12": _n("LoadImage", {"image": "example.png"}, "REF3"),
        "20": _n("TextEncodeQwenImageEditPlus", {"clip": ["2", 0], "vae": ["3", 0], "prompt": "",
                                                 "image1": ["10", 0], "image2": ["11", 0], "image3": ["12", 0]}, "PROMPT"),
        "21": _n("TextEncodeQwenImageEditPlus", {"clip": ["2", 0], "vae": ["3", 0], "prompt": ""}, "NEGATIVE"),
        "30": _n("EmptySD3LatentImage", {"width": 1280, "height": 720, "batch_size": 1}, "SIZE"),
        "40": _n("KSampler", {"model": ["5", 0], "positive": ["20", 0], "negative": ["21", 0], "latent_image": ["30", 0],
                              "seed": 1, "steps": 4, "cfg": 1.0, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0}),
        "50": _n("VAEDecode", {"samples": ["40", 0], "vae": ["3", 0]}),
        "60": _n("SaveImage", {"images": ["50", 0], "filename_prefix": "anime/keyframe"}, "SAVE"),
    }}},
    "animate": {
        "wan22_14b_fp8": {"fps": 16, "max_frames": 81, "wf": _wan14b()},
        "wan22_5b_24fps": {"fps": 24, "max_frames": 121, "wf": {
            "1": _n("UNETLoader", {"unet_name": "wan2.2_ti2v_5B_fp16.safetensors", "weight_dtype": "default"}),
            "2": _n("CLIPLoader", {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}),
            "3": _n("VAELoader", {"vae_name": "wan2.2_vae.safetensors"}),
            "4": _n("ModelSamplingSD3", {"model": ["1", 0], "shift": 8.0}),
            "10": _n("LoadImage", {"image": "example.png"}, "START_IMAGE"),
            "11": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "PROMPT"),
            "12": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "NEGATIVE"),
            "13": _n("Wan22ImageToVideoLatent", {"vae": ["3", 0], "start_image": ["10", 0], "width": 832, "height": 480,
                                                 "length": 121, "batch_size": 1}, "SIZE"),
            "20": _n("KSampler", {"model": ["4", 0], "positive": ["11", 0], "negative": ["12", 0], "latent_image": ["13", 0],
                                  "seed": 1, "steps": 20, "cfg": 5.0, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}),
            "30": _n("VAEDecode", {"samples": ["20", 0], "vae": ["3", 0]}),
            "40": _n("SaveImage", {"images": ["30", 0], "filename_prefix": "anime/clip"}, "SAVE"),
        }},
    },
    "animate_flf": {"wan22_14b_fp8_flf": {"fps": 16, "max_frames": 81, "wf": _wan14b(flf=True)}},
    "upscale": {"realesrgan_anime_1080p": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("LoadVideo", {"file": "example.mp4"}, "VIDEO_IN"),
        "2": _n("GetVideoComponents", {"video": ["1", 0]}),
        "3": _n("UpscaleModelLoader", {"model_name": "RealESRGAN_x4plus_anime_6B.pth"}),
        "4": _n("ImageUpscaleWithModel", {"upscale_model": ["3", 0], "image": ["2", 0]}),
        "5": _n("ImageScale", {"image": ["4", 0], "upscale_method": "lanczos", "width": 1920, "height": 1080, "crop": "center"}),
        "6": _n("SaveImage", {"images": ["5", 0], "filename_prefix": "anime/upscale"}, "SAVE"),
    }}},
    # Single-chunk S2V, adapted from Comfy-Org's native Wan2.2 S2V template.
    # Save frames rather than muxing audio: the Mac stitches at 16 fps and mixes the shot's dialogue.
    "lipsync": {"wan22_s2v_14b_fp8": {"fps": 16, "max_frames": 77, "wf": {
        "1": _n("UNETLoader", {"unet_name": "wan2.2_s2v_14B_fp8_scaled.safetensors", "weight_dtype": "default"}),
        "2": _n("CLIPLoader", {"clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors", "type": "wan", "device": "default"}),
        "3": _n("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "4": _n("AudioEncoderLoader", {"audio_encoder_name": "wav2vec2_large_english_fp16.safetensors"}),
        "5": _n("ModelSamplingSD3", {"model": ["1", 0], "shift": 8.0}),
        "10": _n("LoadImage", {"image": "example.png"}, "START_IMAGE"),
        "11": _n("LoadAudio", {"audio": "example.wav"}, "AUDIO_IN"),
        "12": _n("AudioEncoderEncode", {"audio_encoder": ["4", 0], "audio": ["11", 0]}),
        "20": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "PROMPT"),
        "21": _n("CLIPTextEncode", {"clip": ["2", 0], "text": ""}, "NEGATIVE"),
        "22": _n("WanSoundImageToVideo", {"positive": ["20", 0], "negative": ["21", 0], "vae": ["3", 0],
                                            "audio_encoder_output": ["12", 0], "ref_image": ["10", 0],
                                            "width": 848, "height": 480, "length": 77, "batch_size": 1}, "SIZE"),
        "30": _n("KSampler", {"model": ["5", 0], "positive": ["22", 0], "negative": ["22", 1], "latent_image": ["22", 2],
                               "seed": 1, "steps": 20, "cfg": 6.0, "sampler_name": "uni_pc", "scheduler": "simple", "denoise": 1.0}),
        "40": _n("VAEDecode", {"samples": ["30", 0], "vae": ["3", 0]}),
        "50": _n("SaveImage", {"images": ["40", 0], "filename_prefix": "anime/lipsync"}, "SAVE"),
    }}},
    # Local voice-copying TTS: ComfyUI-Qwen-TTS (github.com/flybirdxx/ComfyUI-Qwen-TTS) + built-in Load/Save Audio
    "voice": {"qwen3_tts_clone": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("LoadAudio", {"audio": "example.wav"}, "AUDIO_IN"),
        "2": _n("FB_Qwen3TTSVoiceClone", {"target_text": "", "model_choice": "1.7B", "device": "auto", "precision": "bf16",
                                          "language": "Auto", "ref_audio": ["1", 0], "ref_text": "", "seed": 0,
                                          "max_new_tokens": 2048, "top_p": 1.0, "top_k": 50, "temperature": 0.9,
                                          "repetition_penalty": 1.05, "x_vector_only": False, "instruct": "",
                                          "attention": "auto", "unload_model_after_generate": False, "custom_model_path": ""}, "PROMPT"),
        "3": _n("SaveAudio", {"audio": ["2", 0], "filename_prefix": "anime/voice"}, "SAVE"),
    }}},
    # New voice from a written description (Qwen3-TTS VoiceDesign, 1.7B only); the result becomes a reference clip
    "voice_design": {"qwen3_tts_design": {"fps": 0, "max_frames": 0, "wf": {
        "1": _n("FB_Qwen3TTSVoiceDesign", {"text": "", "instruct": "", "model_choice": "1.7B", "device": "auto", "precision": "bf16",
                                           "language": "Auto", "seed": 0, "max_new_tokens": 2048, "top_p": 1.0, "top_k": 50,
                                           "temperature": 0.9, "repetition_penalty": 1.05, "attention": "auto",
                                           "unload_model_after_generate": False}, "PROMPT"),
        "2": _n("SaveAudio", {"audio": ["1", 0], "filename_prefix": "anime/voice_design"}, "SAVE"),
    }}},
}
DEFAULT_STARTER = {"keyframe": "qwen_edit_2509", "animate": "wan22_14b_fp8", "animate_flf": "wan22_14b_fp8_flf",
                   "upscale": "realesrgan_anime_1080p", "voice": "qwen3_tts_clone",
                   "voice_design": "qwen3_tts_design", "lipsync": "wan22_s2v_14b_fp8"}
VOICE_SAMPLE = "Hello there. It's good to finally meet you. I've been waiting a long time for this, and I won't let you down."

WAN_NEGATIVE = ("色调艳丽，过曝，静态，细节模糊不清，字幕，风格，作品，画作，画面，静止，整体发灰，最差质量，低质量，JPEG压缩残留，丑陋的，"
                "残缺的，多余的手指，画得不好的手部，画得不好的脸部，畸形的，毁容的，形态畸形的肢体，手指融合，静止不动的画面，杂乱的背景，"
                "三条腿，背景人很多，倒着走")

SHEET_PROMPT = ("Character design reference sheet of the character in Picture 1: full-body front view, side view and back view "
                "standing side by side, plus a row of four facial expressions (neutral, happy, angry, surprised). "
                "Plain white background, identical outfit, colors and proportions in every view")

DEFAULT_SETTINGS = {
    "title": "My Anime Episode",
    "script": "",
    "style_prompt": "anime style, 2D cel-shaded animation, clean line art, vibrant colors, detailed painted background, cinematic lighting",
    "keyframe_negative": "",
    "motion_suffix": "2D anime animation, smooth natural motion, consistent character design",
    "video_negative": WAN_NEGATIVE,
    "default_duration": "4",
    "draft_width": "512", "draft_height": "288", "draft_max_frames": "49",
    "kf_width": "1280", "kf_height": "720",
    "sheet_width": "1536", "sheet_height": "864",
    "vid_width": "848", "vid_height": "480",
    "out_width": "1920", "out_height": "1080", "out_fps": "16",
    "default_voice": "",
    "line_gap": "0.3",
    "tts_model": "1.7B", "tts_language": "Auto",
    "job_timeout": "3600",
    "video_min_free_vram": "0",
    "music": "",
    "lettering_mode": "off", "reading_direction": "rtl",
}
DEFAULT_INSTANCES = [("ComfyUI 172.16.2.70", "http://172.16.2.70:8188", ",".join(JOB_STAGES))]

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS instances(id INTEGER PRIMARY KEY, name TEXT, url TEXT, roles TEXT, enabled INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS workflows(stage TEXT PRIMARY KEY, json TEXT, name TEXT, fps INTEGER DEFAULT 0, max_frames INTEGER DEFAULT 0, updated REAL);
CREATE TABLE IF NOT EXISTS assets(id INTEGER PRIMARY KEY, kind TEXT DEFAULT 'character', name TEXT, description TEXT DEFAULT '',
    image TEXT, voice TEXT DEFAULT '', lora TEXT DEFAULT '', lora_strength REAL DEFAULT 1.0, voice_desc TEXT DEFAULT '',
    voice_sample TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS shots(id INTEGER PRIMARY KEY, seq REAL, scene TEXT DEFAULT '', kind TEXT DEFAULT 'motion',
    asset_ids TEXT DEFAULT '[]', keyframe_prompt TEXT DEFAULT '', motion_prompt TEXT DEFAULT '', camera TEXT DEFAULT '',
    dialogue TEXT DEFAULT '', duration REAL DEFAULT 4, retime TEXT DEFAULT 'hold', ref_prev INTEGER DEFAULT 0,
    end_next INTEGER DEFAULT 0, audio TEXT, keyframe_take INTEGER, clip_take INTEGER, final_take INTEGER, notes TEXT DEFAULT '',
    cont_takes TEXT DEFAULT '[]', line_audio TEXT DEFAULT '[]', audio_mode TEXT DEFAULT 'lines');
CREATE TABLE IF NOT EXISTS voice_takes(id INTEGER PRIMARY KEY, shot_id INTEGER, text TEXT, speaker TEXT, path TEXT, seed INTEGER,
    job_id INTEGER, seconds REAL DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS voice_clips(id INTEGER PRIMARY KEY, asset_id INTEGER, path TEXT, name TEXT, transcript TEXT DEFAULT '',
    seconds REAL DEFAULT 0, is_ref INTEGER DEFAULT 0, created REAL);
CREATE TABLE IF NOT EXISTS takes(id INTEGER PRIMARY KEY, shot_id INTEGER, asset_id INTEGER, stage TEXT, path TEXT, fps REAL DEFAULT 0,
    seed INTEGER, job_id INTEGER, created REAL, frames INTEGER, segment INTEGER, draft INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS jobs(id INTEGER PRIMARY KEY, shot_id INTEGER, asset_id INTEGER, stage TEXT, status TEXT, instance_id INTEGER,
    prompt_id TEXT, seed INTEGER, error TEXT, progress TEXT, created REAL, started REAL, finished REAL, segment INTEGER, draft INTEGER DEFAULT 0,
    note TEXT);
"""


MIGRATIONS = [  # columns added after v2.0 - existing projects get them on start
    ("instances", "shared_retired", "INTEGER NOT NULL DEFAULT 0"),
    ("shots", "cont_takes", "TEXT DEFAULT '[]'"), ("takes", "frames", "INTEGER"), ("takes", "segment", "INTEGER"),
    ("takes", "draft", "INTEGER DEFAULT 0"), ("jobs", "segment", "INTEGER"), ("jobs", "draft", "INTEGER DEFAULT 0"),
    ("shots", "line_audio", "TEXT DEFAULT '[]'"), ("shots", "audio_mode", "TEXT DEFAULT 'lines'"), ("jobs", "note", "TEXT"),
    ("jobs", "models_json", "TEXT"),
    ("jobs", "priority", "INTEGER DEFAULT 0"), ("jobs", "speaker", "TEXT DEFAULT ''"),
    ("voice_takes", "line_index", "INTEGER"),
    ("voice_takes", "character_id", "INTEGER"), ("voice_takes", "line_id", "TEXT"),
    ("jobs", "voice_character_id", "INTEGER"), ("jobs", "voice_line_id", "TEXT"),
    ("jobs", "voice_autoselect", "INTEGER DEFAULT 0"), ("jobs", "voice_previous_audio", "TEXT"),
    ("shots", "keyframe_reference_order", "TEXT"),
    ("shots", "script_source", "TEXT"),
    ("jobs", "submission_client_id", "TEXT"),
    ("jobs", "voice_ref_path", "TEXT"), ("jobs", "voice_ref_text", "TEXT"), ("jobs", "voice_direction", "TEXT"),
    ("takes", "input_signature", "TEXT"), ("voice_takes", "input_signature", "TEXT"),
    ("assets", "voice_desc", "TEXT DEFAULT ''"), ("assets", "voice_sample", "TEXT DEFAULT ''"),
    ("shots", "bubbles", "TEXT DEFAULT '[]'"),
]

# ======================================================================================
# Storage
# ======================================================================================
class ProjectDatabaseAccessError(RuntimeError):
    """macOS/file authorization denied access to the existing project, not a corrupt database."""


_IMPORT_CONTEXT = threading.local()


def record_import_file(path):
    manifest = getattr(_IMPORT_CONTEXT, 'manifest', None)
    if manifest is not None:
        with manifest.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(str(path)) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def reconcile_import(studio, manifest):
    committed = studio.db.one('SELECT token FROM render_imports WHERE token=?', (manifest.stem,))
    if not committed:
        for line in manifest.read_text(encoding='utf-8').splitlines():
            try:
                path = Path(json.loads(line)).resolve()
            except (ValueError, TypeError):
                continue
            if studio.data in path.parents:
                path.unlink(missing_ok=True)
    manifest.unlink()
    studio.db.x('DELETE FROM render_imports WHERE token=?', (manifest.stem,))


class DB:
    def __init__(self, path):
        try:
            self.conn = sqlite3.connect(str(path), check_same_thread=False)
        except sqlite3.DatabaseError as exc:
            denied = getattr(exc, 'sqlite_errorcode', None) in (23, 3) or any(
                text in str(exc).lower() for text in ('authorization denied', 'permission denied', 'not authorized', 'operation not permitted'))
            if denied:
                raise ProjectDatabaseAccessError(
                    f"ANIME_STUDIO_DATA_ACCESS_DENIED: Cannot open project database {path}. "
                    "macOS denied this app access to the project folder. In Anime Studio, choose "
                    "Choose Project Folder and select the existing AnimeStudio Data folder to grant access. "
                    "Do not delete or replace studio.db. Original SQLite error: " + str(exc)) from exc
            raise
        self.progress_writes = 0
        self._transaction_active = False
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
            for table, col, decl in MIGRATIONS:
                cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if col not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
                    if (table, col) == ("shots", "audio_mode"):   # audio from before v2.2 was one file per shot
                        self.conn.execute("UPDATE shots SET audio_mode='file' WHERE audio IS NOT NULL")
            self.conn.commit()

    @contextmanager
    def transaction(self):
        with self.lock:
            if self._transaction_active:
                raise RuntimeError('Nested database transaction')
            self._transaction_active = True
            try:
                with self.conn:
                    yield
            finally:
                self._transaction_active = False

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.q(sql, args)
        return r[0] if r else None

    def x(self, sql, args=()):
        """Execute and return lastrowid (for INSERTs)."""
        with self.lock:
            cur = self.conn.execute(sql, args)
            if not self._transaction_active:
                self.conn.commit()
            return cur.lastrowid

    def u(self, sql, args=()):
        """Execute and return the number of changed rows."""
        with self.lock:
            cur = self.conn.execute(sql, args)
            if not self._transaction_active:
                self.conn.commit()
            return cur.rowcount


LINE_RE = re.compile(r"^\s*([^:]{1,40}):\s*(.+)$")


def parse_lines(dialogue, default_speaker=""):
    """'AIKO: line' -> [{'speaker': 'AIKO', 'text': 'line'}]; lines without a name go to the shot's first cast member."""
    out = []
    for line in (dialogue or "").splitlines():
        if not line.strip():
            continue
        parsed = dialogue_line(line)
        if parsed:
            out.append(parsed)
        else:
            out.append({'speaker':default_speaker,'text':line.strip(),'direction':''})
    return out


def spoken_key(text):
    """Exact spoken words, preserving punctuation and case that affect delivery."""
    return ' '.join((text or '').split())


def norm_text(t):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", (t or "").lower())).strip()


def similarity(a, b):
    a, b = norm_text(a), norm_text(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def transcript_from_name(name, speaker=""):
    """'aiko_03_you_came_after_all.wav' -> 'you came after all'."""
    words = [w for w in re.split(r"[_\-.\s]+", Path(name or "").stem) if w]
    if words and speaker and words[0].lower() == speaker.strip().lower():
        words = words[1:]
    while words and words[0].isdigit():
        words = words[1:]
    return " ".join(words)


TTS_LANGUAGES = ["Auto", "Chinese", "English", "Japanese", "Korean", "French", "German", "Spanish", "Portuguese", "Russian", "Italian"]


def norm_choice(value, options, default):
    v = (value or "").strip().lower()
    return next((o for o in options if o.lower() == v), default)


def find_rife():
    """Optional: rife-ncnn-vulkan (https://github.com/nihui/rife-ncnn-vulkan) for optional source frame-rate conversion on the Mac."""
    hit = shutil.which("rife-ncnn-vulkan")
    if hit:
        return Path(hit)
    # Startup must not enumerate protected Downloads/Documents folders: a
    # macOS privacy check can block before the HTTP server starts serving.
    for pat in ("~/Applications/rife-ncnn-vulkan*/rife-ncnn-vulkan",
                "~/Library/Application Support/AnimeStudio/tools/rife-ncnn-vulkan*/rife-ncnn-vulkan",
                "/Applications/rife-ncnn-vulkan*/rife-ncnn-vulkan"):
        for p in glob.glob(os.path.expanduser(pat)):
            if os.access(p, os.X_OK):
                return Path(p)
    return None


class Studio:
    def __init__(self, data_dir, export_worker=False):
        self.projects = None
        self.data = Path(data_dir).expanduser().resolve()
        for sub in ("uploads", "shots", "cast", "jobs", "exports", "voices", "cache"):
            (self.data / sub).mkdir(parents=True, exist_ok=True)
        self.db = DB(self.data / "studio.db")
        self.state_session = uuid.uuid4().hex
        self.db.x("CREATE TABLE IF NOT EXISTS render_imports(token TEXT PRIMARY KEY)")
        if not export_worker:
            for manifest in (self.data / "jobs").glob("*/*.render-import"):
                reconcile_import(self, manifest)
        self.db.x("CREATE TABLE IF NOT EXISTS render_project (id INTEGER PRIMARY KEY CHECK (id=1), namespace TEXT NOT NULL)")
        self.db.x("INSERT OR IGNORE INTO render_project VALUES(1,?)", (uuid.uuid4().hex,))
        self.render_namespace = self.db.one("SELECT namespace FROM render_project WHERE id=1")["namespace"]
        if "remote_client_id" not in {row["name"] for row in self.db.q("PRAGMA table_info(jobs)")}:
            self.db.x("ALTER TABLE jobs ADD COLUMN remote_client_id TEXT")
        from voice_video import initialize as initialize_voice_video
        initialize_voice_video(self.db)
        for k, v in DEFAULT_SETTINGS.items():
            self.db.x("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
        if not export_worker:
            from timeline import upgrade_legacy_fps
            saved=self.settings()
            for key,value in upgrade_legacy_fps(saved).items():
                if saved.get(key)!=value:
                    self.db.x('INSERT OR REPLACE INTO settings VALUES(?,?)',(key,value))
        if not self.db.one("SELECT id FROM instances"):
            for name, url, roles in DEFAULT_INSTANCES:
                self.db.x("INSERT INTO instances(name,url,roles) VALUES(?,?,?)", (name, url, roles))
        for key, name in DEFAULT_STARTER.items():
            s = STARTERS[key][name]
            self.db.x("INSERT OR IGNORE INTO workflows(stage,json,name,fps,max_frames,updated) VALUES(?,?,?,?,?,?)",
                      (key, json.dumps(s["wf"]), name, s["fps"], s["max_frames"], time.time()))
        for inst in self.db.q("SELECT id, roles FROM instances"):
            roles = [r for r in (inst["roles"] or "").split(",") if r]
            add = (["continue"] if "animate" in roles and "continue" not in roles else []) + \
                  (["voice"] if "keyframe" in roles and "voice" not in roles else [])
            if ("voice" in roles or "voice" in add) and "voice_design" not in roles:
                add.append("voice_design")
            if add:   # job types added in later versions go to servers that already take the related work
                self.db.x("UPDATE instances SET roles=? WHERE id=?", (",".join(roles + add), inst["id"]))
        # Takes of deleted shots / cast must not reattach to new ones that reuse the id
        self.db.x(f"DELETE FROM takes WHERE stage NOT IN {_ASSET_SQL} AND shot_id NOT IN (SELECT id FROM shots)")
        self.db.x("DELETE FROM takes WHERE stage='sheet' AND asset_id NOT IN (SELECT id FROM assets)")
        self.db.x("DELETE FROM voice_takes WHERE shot_id NOT IN (SELECT id FROM shots)")
        self.db.x("DELETE FROM voice_clips WHERE asset_id NOT IN (SELECT id FROM assets)")
        self.db.x("UPDATE jobs SET status='cancelled' WHERE status IN ('pending','running') AND "
                  f"((stage NOT IN {_ASSET_SQL} AND shot_id NOT IN (SELECT id FROM shots)) OR (stage IN {_ASSET_SQL} AND asset_id NOT IN (SELECT id FROM assets)))")
        self.db.x("UPDATE jobs SET status='pending', progress='reconnecting after restart' WHERE status='running'")
        self.health = {}
        from production import Production
        self.production = Production(self, API, http_json)
        from soundtrack import Soundtrack
        self.soundtrack = Soundtrack(self, ffmpeg)
        from render_queue import remember_timings
        remember_timings(self.db)
        self.frame_lock = threading.Lock()
        self.assembly = {"running": False, "log": [], "output": None, "folder": None}
        self.tools_cache = None
        from creative import CreativeStudio
        self.creative = CreativeStudio(self)
        from continuations import Continuations
        self.sections = Continuations(self)
        from job_snapshots import JobSnapshots
        self.snapshots = JobSnapshots(self)
        from finishing import Finishing
        self.finishing = Finishing(self)
        from studio_tools import StudioTools
        self.editor = StudioTools(self)
        from alignment import Alignment
        from storage_manager import StorageManager
        from workspace import Workspace
        self.alignment=Alignment(self)
        self.storage=StorageManager(self)
        self.workspace=Workspace(self)
        if not export_worker:
            from export_jobs import ExportJobs
            self.exports=ExportJobs(self)
        from shot_previews import ShotPreviews
        self.shot_previews=ShotPreviews(self)

    def job_compatible(self, job, instance, cached_only=False):
        owner = self.snapshots if job.get('snapshot_json') else self.creative
        return owner.compatible(job, instance, cached_only=cached_only)

    def tools(self, refresh=False):
        if self.tools_cache is None or refresh:
            self.tools_cache = {"ffmpeg": shutil.which("ffmpeg"), "say": shutil.which("say"), "rife": str(find_rife() or "") or None,
                                "open": shutil.which("open")}
        return self.tools_cache

    def settings(self):
        settings = {r["key"]: r["value"] for r in self.db.q("SELECT key,value FROM settings")}
        if self.projects:
            settings.update(self.projects.configuration['settings'])
        return settings

    def instances(self, enabled_only=False):
        return self.db.q("SELECT id,name,url,roles,enabled FROM instances WHERE shared_retired=0"
                         + (" AND enabled=1" if enabled_only else "") + " ORDER BY id")

    def render_instances(self):
        servers = self.instances(enabled_only=True)
        if self.projects:
            # A removed or disabled server can finish jobs already submitted to its original address.
            servers += self.db.q("SELECT i.*, 1 AS resume_only FROM instances i WHERE (i.shared_retired=1 OR i.enabled=0) "
                                 "AND EXISTS (SELECT 1 FROM jobs j WHERE j.instance_id=i.id AND j.status='pending' "
                                 "AND (j.prompt_id IS NOT NULL OR j.submission_client_id IS NOT NULL))")
        return servers

    def rel(self, p):
        return Path(p).resolve().relative_to(self.data).as_posix()

    def abs(self, rel):
        return self.data / rel

    def save_upload(self, f, folder="uploads"):
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", f.get("name") or "file")[-80:]
        dest = self.data / folder / f"{uuid.uuid4().hex[:10]}_{name}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(base64.b64decode(f["data"].split(",")[-1]))
        return self.rel(dest)

    def keyframe_template(self, shot_id):
        assignment = self.creative.assignment(shot_id, 'keyframe')
        if assignment:
            recipe = self.creative.recipe(assignment['recipe_id'])
            return recipe['workflow'], assignment['values'], recipe
        row = self.wf_row('keyframe')
        if not row:
            raise ValueError('Configure the keyframe workflow first.')
        return json.loads(row['json']), {}, None

    def ordered_shots(self):
        return self.db.q("SELECT * FROM shots ORDER BY seq, id")

    def renumber(self):
        for i, s in enumerate(self.ordered_shots(), 1):
            self.db.x("UPDATE shots SET seq=? WHERE id=?", (i, s["id"]))

    def take(self, take_id):
        t = self.db.one("SELECT * FROM takes WHERE id=?", (take_id,)) if take_id else None
        return t if t and self.abs(t["path"]).exists() else None

    def take_path(self, take_id):
        t = self.take(take_id)
        return self.abs(t["path"]) if t else None

    def wf_row(self, key):
        if self.projects:
            return self.projects.configuration['workflows'].get(key)
        return self.db.one("SELECT * FROM workflows WHERE stage=?", (key,))

    def default_duration(self):
        try:
            return max(0.5, float(self.settings().get("default_duration") or 4))
        except ValueError:
            return 4.0

    def wf_fps(self, key="animate"):
        return int((self.wf_row(key) or {}).get("fps") or 16)

    def max_frames(self, key="animate", draft=False):
        maxf = int((self.wf_row(key) or {}).get("max_frames") or 81)
        if draft:
            maxf = min(maxf, int(float(self.settings().get("draft_max_frames") or 49)))
        return ((maxf - 1) // 4) * 4 + 1

    def frames_for(self, duration, key="animate", draft=False):
        fps = self.wf_fps(key)
        n = max(5, int(round(float(duration or 4) * fps)) + 1)
        n = ((n - 1 + 3) // 4) * 4 + 1          # Wan wants 4k+1 frames
        return min(n, self.max_frames(key, draft))

    def take_frames(self, t):
        """Frame count of a video take (measured once for takes made before v2.1)."""
        if t.get("frames"):
            return int(t["frames"])
        fps = float(t.get("fps") or 16)
        n = int(round(media_seconds(self.abs(t["path"])) * fps)) or 1
        self.db.x("UPDATE takes SET frames=? WHERE id=?", (n, t["id"]))
        return n

    def chain(self, shot):
        """The shot's approved animation: main clip followed by its continuation segments."""
        clip = self.take(shot["clip_take"])
        if not clip:
            return []
        out = [clip]
        for tid in json.loads(shot.get("cont_takes") or "[]"):
            t = self.take(tid)
            if not t:
                break
            out.append(t)
        return out

    def chain_seconds(self, chain):
        """Seconds of motion in a chain (continuations repeat the previous last frame, so drop one frame each)."""
        total = 0.0
        for i, t in enumerate(chain):
            n = self.take_frames(t) - (1 if i else 0)
            total += max(0, n) / float(t.get("fps") or 16)
        return total

    def speaker_asset(self, name):
        n = (name or "").strip().lower()
        return next((a for a in self.db.q("SELECT * FROM assets WHERE kind='character'") if a["name"].strip().lower() == n), None) if n else None

    def line_character(self, line):
        if 'character_id' in line:
            return self.db.one("SELECT * FROM assets WHERE id=? AND kind='character'", (line['character_id'],)) if line['character_id'] else None
        return self.speaker_asset(line['speaker'])

    def job_line(self, shot, job):
        lines = self.shot_lines(shot)
        same = lambda line: norm_text(line['text']) == norm_text(job.get('note')) and (not job.get('speaker') or line['speaker'].casefold() == job['speaker'].casefold())
        if job.get('voice_line_id'):
            return next((line for line in lines if line['line_id']==job['voice_line_id'] and spoken_key(line['text'])==spoken_key(job.get('note')) and line['character_id']==job.get('voice_character_id') and (job.get('voice_direction') is None or line.get('direction','')==job['voice_direction'])),None)
        index = job.get('segment')
        if index is not None:
            return lines[int(index)] if 0 <= int(index) < len(lines) and same(lines[int(index)]) else None
        matches = [line for line in lines if same(line)]
        return matches[0] if len(matches) == 1 else None

    def ref_clip(self, asset_id):
        return (self.db.one("SELECT * FROM voice_clips WHERE asset_id=? AND is_ref=1", (asset_id,))
                or self.db.one("SELECT * FROM voice_clips WHERE asset_id=? ORDER BY id LIMIT 1", (asset_id,)))

    def shot_lines(self, shot):
        """The shot's dialogue lines, each with the audio assigned to it (kept when lines are reordered or edited elsewhere)."""
        cast_ids=shot["asset_ids"] if isinstance(shot["asset_ids"],list) else json.loads(shot["asset_ids"] or "[]")
        cast = [self.db.one("SELECT name FROM assets WHERE id=? AND kind='character'", (i,)) for i in cast_ids]
        characters = [c for c in cast if c]
        lines = parse_lines(shot["dialogue"], characters[0]["name"] if len(characters)==1 else "")
        saved, used = json.loads(shot.get("line_audio") or "[]"), set()
        for i, l in enumerate(lines):
            l.update(index=i, audio=None, src=None, clip_id=None)
            l.setdefault("direction", "")
            character = self.speaker_asset(l['speaker'])
            l['character_id'] = character['id'] if character else None
            l['line_id'] = hashlib.sha256(json.dumps([shot['id'],i,l['speaker'],l['text']]).encode()).hexdigest()[:24]
            hits = [j for j, e in enumerate(saved) if j not in used and spoken_key(e.get("text")) == spoken_key(l["text"]) and (e.get("speaker") or "").casefold() == l["speaker"].casefold()]
            if hits:
                j = i if i in hits else hits[0]
                used.add(j)
                e = saved[j]
                l["direction"] = e.get("direction") or l.get("direction") or ""
                l['line_id'] = e.get('line_id') or l['line_id']
                if 'character_id' in e:l['character_id']=e['character_id']
                if e.get("path") and self.abs(e["path"]).exists():
                    l.update(audio=e["path"], src=e.get("src"), clip_id=e.get("clip_id"))
        return lines

    def save_lines(self, shot_id, lines):
        data = [{"speaker": l["speaker"], "text": l["text"], "path": l.get("audio"), "src": l.get("src"),
                 "clip_id": l.get("clip_id"), "direction": l.get("direction") or "",
                 **({"character_id":l["character_id"]} if "character_id" in l else {}),
                 **({"line_id":l["line_id"]} if "line_id" in l else {})} for l in lines]
        self.db.x("UPDATE shots SET line_audio=? WHERE id=?", (json.dumps(data), shot_id))

    def rebuild_audio(self, shot_id, fit=True):
        """Join the line audio into the shot's dialogue track. Lines without audio keep their place as silence."""
        s = self.db.one("SELECT * FROM shots WHERE id=?", (shot_id,))
        if not s or s["audio_mode"] == "file":
            return
        lines = self.shot_lines(s)
        self.save_lines(shot_id, lines)
        old = s["audio"]
        if not any(l["audio"] for l in lines):
            self.db.x("UPDATE shots SET audio=NULL WHERE id=?", (shot_id,))
        else:
            dest = self.data / "voices" / f"shot{shot_id}_mix_{uuid.uuid4().hex[:6]}.wav"
            record_import_file(dest)
            self.render_dialogue_track(shot_id, lines, dest)
            secs, dur = media_seconds(dest), float(s["duration"])
            if fit and secs + 0.4 > dur:
                dur = round(secs + 0.4, 1)
            self.db.x("UPDATE shots SET audio=?, duration=? WHERE id=?", (self.rel(dest), dur, shot_id))
        # Prior mixes remain available to history and archived episodes.

    def render_dialogue_track(self, shot_id, lines, dest, speaker=None):
        """Keep dialogue intervals identical when producing a speaker stem."""
        if not self.tools()["ffmpeg"]:
            raise RuntimeError("ffmpeg is needed to join dialogue lines: brew install ffmpeg")
        gap = float(self.settings().get("line_gap") or 0.3)
        ins, graph = [], ""
        for i, l in enumerate(lines):
            if l["audio"]:
                ins += ["-i", str(self.abs(l["audio"]))]
            else:
                ins += ["-f", "lavfi", "-t", f"{max(1.0, len(l['text'].split()) / 2.6):.2f}", "-i", "anullsrc=r=48000:cl=stereo"]
            timing = self.creative.timing(shot_id, l)
            pad = timing.get('pause_after', gap) if i < len(lines) - 1 else 0
            delay = timing.get('lead_in', 0)*1000
            mute = ',volume=0' if speaker is not None and l['character_id'] != speaker else ''
            graph += f"[{i}:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,adelay={delay}:all=1,apad=pad_dur={pad}{mute}[a{i}];"
        graph += "".join(f"[a{i}]" for i in range(len(lines))) + f"concat=n={len(lines)}:v=0:a=1[out]"
        ffmpeg(ins + ["-filter_complex", graph, "-map", "[out]", "-ar", "48000", str(dest)])

    def say_line(self, text, voice=""):
        if not self.tools()["say"]:
            raise RuntimeError("Temporary voices use the macOS 'say' command (Mac only).")
        (self.data / "voices" / "lines").mkdir(parents=True, exist_ok=True)
        base = self.data / "voices" / "lines" / f"temp_{uuid.uuid4().hex[:8]}"
        r = subprocess.run(["say", "-o", str(base) + ".aiff"] + (["-v", voice] if voice else []) + [text], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"say failed: {r.stderr[:300]}")
        ffmpeg(["-i", str(base) + ".aiff", "-ar", "48000", str(base) + ".wav"])
        Path(str(base) + ".aiff").unlink(missing_ok=True)
        return self.rel(Path(str(base) + ".wav"))

    def last_frame(self, path):
        """Last frame of a clip as PNG. Locked and written atomically: parallel Continue jobs share it."""
        source=Path(path).resolve();stat=source.stat()
        identity=hashlib.sha256(f"{source}:{stat.st_mtime_ns}:{stat.st_size}".encode()).hexdigest()[:20]
        out = self.data / "cache" / f"last_{identity}.png"
        with self.frame_lock:
            if not out.exists() or out.stat().st_mtime < Path(path).stat().st_mtime:
                tmp = out.with_name(f".{out.stem}_{uuid.uuid4().hex[:6]}.png")
                ffmpeg(["-sseof", "-1", "-i", str(path), "-update", "1", "-q:v", "1", str(tmp)])
                os.replace(tmp, out)
        return out

    def neighbor(self, shot, direction):
        op, order = (">", "ASC") if direction > 0 else ("<", "DESC")
        return self.db.one(f"SELECT * FROM shots WHERE seq {op} ? ORDER BY seq {order} LIMIT 1", (shot["seq"],))


# ======================================================================================
# ComfyUI client (direct - bypass any proxy, the server is on your LAN)
# ======================================================================================
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http(method, url, data=None, headers=None, timeout=30):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with _opener.open(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:3000]}") from None


NODE_PACKS = {"FB_Qwen3TTS": ("ComfyUI-Qwen-TTS", "Run install_tts.sh on the server")}


def explain_missing_nodes(text, base=""):
    """Turn ComfyUI's missing_node_type error into steps the user can follow; None for other errors."""
    if "missing_node_type" not in text:
        return None
    m = re.search(r'"class_type":\s*"([^"]+)"', text)
    cls = m.group(1) if m else "a node in this workflow"
    for prefix, (pack, fix) in NODE_PACKS.items():
        if cls.startswith(prefix):
            return (f"The ComfyUI server {base} doesn't have the {cls} node, so the {pack} node pack isn't loaded. "
                    f"{fix}, then restart ComfyUI. If you already did, look in the ComfyUI startup log for "
                    f"'IMPORT FAILED' next to {pack}; the lines above it say why.")
    return (f"The ComfyUI server {base} doesn't have the {cls} node. Install the custom node pack that provides it "
            f"(ComfyUI Manager > Install Missing Custom Nodes), then restart ComfyUI.")


def http_json(method, url, obj=None, timeout=30):
    data = json.dumps(obj).encode() if obj is not None else None
    raw = http(method, url, data, {"Content-Type": "application/json"} if data else {}, timeout)
    return json.loads(raw) if raw else None


def comfy_upload(base, path, name, progress=None):
    """Upload any input file (image, video, audio) to ComfyUI's input folder root."""
    b = uuid.uuid4().hex
    header = (f'--{b}\r\nContent-Disposition: form-data; name="overwrite"\r\n\r\ntrue\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n'
            f'--{b}\r\nContent-Disposition: form-data; name="image"; filename="{name}"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n').encode()
    trailer = f"\r\n--{b}--\r\n".encode()
    path = Path(path)
    size = path.stat().st_size

    def chunks():
        yield header
        uploaded = 0
        with path.open('rb') as stream:
            while chunk := stream.read(1 << 20):
                if progress:
                    progress(uploaded, size)
                yield chunk
                uploaded += len(chunk)
        if progress:
            progress(uploaded, size)
        yield trailer

    headers = {"Content-Type": f"multipart/form-data; boundary={b}",
               "Content-Length": str(len(header) + size + len(trailer))}
    response = json.loads(http("POST", base + "/upload/image", chunks(), headers, 600))
    return f"{response['subfolder']}/{response['name']}" if response.get("subfolder") else response["name"]


def comfy_download(url, destination, progress=None):
    """Publish only a fully downloaded output on the local filesystem."""
    partial = destination.with_name(destination.name + '.part')
    try:
        with _opener.open(url, timeout=300) as response, partial.open('wb') as stream:
            length = response.headers.get('Content-Length')
            expected = int(length) if length is not None else None
            received = 0
            if progress:
                progress(received, expected)
            while chunk := response.read(1 << 20):
                received += len(chunk)
                if progress:
                    progress(received, expected)
                stream.write(chunk)
            if expected is not None and received != expected:
                raise RuntimeError(f"ComfyUI output transfer ended after {received} of {expected} bytes.")
            if not received:
                raise RuntimeError("ComfyUI returned an empty output.")
        partial.replace(destination)
    finally:
        partial.unlink(missing_ok=True)


# ======================================================================================
# Workflow slots
# ======================================================================================
TEXT_ENCODERS = {"CLIPTextEncode", "TextEncodeQwenImageEditPlus", "TextEncodeQwenImageEdit", "TextEncodeQwenImage21"}
SAVE_NODES = {"SaveImage", "SaveVideo", "VHS_VideoCombine", "SaveWEBM", "SaveAnimatedWEBP", "SaveAnimatedPNG",
              "SaveAudio", "SaveAudioMP3", "SaveAudioOpus", "SaveAudioAdvanced"}
VIDEO_LOADERS = {"LoadVideo": "file", "VHS_LoadVideo": "video"}
MODEL_CONSUMERS = {"KSampler", "KSamplerAdvanced", "SamplerCustom", "BasicGuider", "CFGGuider", "DualCFGGuider"}


def _nid(k):
    try:
        return (0, int(k))
    except ValueError:
        return (1, str(k))


def _title(n):
    return ((n.get("_meta") or {}).get("title") or "").strip()


def _tagged(wf, tag):
    return sorted([k for k, n in wf.items() if _title(n).upper() == tag], key=_nid)


def visual_prompt_slots(wf, tag):
    """Inspect conditioning socket ancestry rather than graph order."""
    pending = [key for key, node in wf.items()
               if node['class_type'] in SAVE_NODES and not node['class_type'].startswith('SaveAudio')]
    seen, bindings = set(), set()
    socket = 'positive' if tag == 'PROMPT' else 'negative'
    while pending:
        key = pending.pop()
        if key in seen or key not in wf:
            continue
        seen.add(key)
        for field, value in wf[key].get('inputs', {}).items():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                if field == socket:
                    bindings.add(str(value[0]))
                pending.append(str(value[0]))

    # Trace upstream from the relevant conditioning sockets. Stop at the
    # encoder so shared CLIP loaders do not cross positive/negative paths.
    pending, seen, encoders = list(bindings), set(), set()
    while pending:
        key = pending.pop()
        if key in seen or key not in wf:
            continue
        seen.add(key)
        node = wf[key]
        if node['class_type'] in TEXT_ENCODERS or _title(node).upper() == tag:
            encoders.add(key)
            continue
        for field, value in node.get('inputs', {}).items():
            if field in ('positive', 'negative') and field != socket:
                continue
            if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                pending.append(str(value[0]))
    tagged = _tagged(wf, tag)
    if tagged:
        return [key for key in tagged if key in encoders]
    return sorted(encoders, key=_nid)


def detect_slots(wf, key, *, all_references=False):
    keys = sorted(wf.keys(), key=_nid)
    cls = lambda k: wf[k].get("class_type") or ""
    ins = lambda k: wf[k].get("inputs", {})
    encs = [k for k in keys if cls(k) in TEXT_ENCODERS]
    loads = [k for k in keys if cls(k) == "LoadImage" and _title(wf[k]).upper() not in ("SOURCE", "MASK", "POSE", "DEPTH", "LAYOUT")]
    m = {"prompt": (_tagged(wf, "PROMPT") or [k for k in encs if "neg" not in _title(wf[k]).lower()])[:1],
         "negative": (_tagged(wf, "NEGATIVE") or [k for k in encs if "neg" in _title(wf[k]).lower()])[:1],
         "save": [k for k in keys if cls(k) in SAVE_NODES],
         "seeds": [[k, f] for k in keys for f in ("seed", "noise_seed") if isinstance(ins(k).get(f), int)],
         "refs": [], "start_image": [], "end_image": [], "video_in": [], "audio_in": [], "size": [], "ref_text": []}
    if key in ("keyframe", "animate", "animate_flf", "lipsync"):
        m["prompt"] = visual_prompt_slots(wf, "PROMPT")
        m["negative"] = visual_prompt_slots(wf, "NEGATIVE")
    if key == "keyframe":
        m["refs"] = [k for i in (1, 2, 3) for k in _tagged(wf, f"REF{i}")] or loads[:3]
        if all_references:
            numbered = [(int(match[1]), k) for k in loads
                        if (match := re.fullmatch(r"REF([1-9][0-9]*)", _title(wf[k]).upper()))]
            if numbered:
                m["refs"] = [k for _, k in sorted(numbered)]
        m["size"] = _tagged(wf, "SIZE") or [k for k in keys if "Latent" in cls(k) and {"width", "height"} <= set(ins(k))][:1]
    elif key in ("animate", "animate_flf", "lipsync"):
        m["start_image"] = (_tagged(wf, "START_IMAGE") or loads)[:1]
        if key == "animate_flf":
            m["end_image"] = (_tagged(wf, "END_IMAGE") or [k for k in loads if k not in m["start_image"]])[:1]
        from clip_audio import audio_inputs
        connected_audio = set(audio_inputs(wf))
        m["audio_in_2"] = [k for k in _tagged(wf, "AUDIO_IN_2") if k in connected_audio]
        m["audio_in"] = [k for k in _tagged(wf, "AUDIO_IN") if k in connected_audio] or [
            k for k in keys if k in connected_audio and cls(k) == "LoadAudio" and k not in m["audio_in_2"]]
        from voice_video import connected_video
        m["video_in"] = connected_video(wf)
        m["size"] = _tagged(wf, "SIZE") or [k for k in keys if {"width", "height", "length"} <= set(ins(k))][:1]
    elif key == "upscale":
        m["video_in"] = (_tagged(wf, "VIDEO_IN") or [k for k in keys if cls(k) in VIDEO_LOADERS])[:1]
    elif key in ("sound_effect", "music_cue"):
        m["audio_duration"] = [[k, field] for k in keys for field in ("seconds_total", "seconds", "duration")
                               if isinstance(ins(k).get(field), (int, float))
                               and (field == "seconds_total" or k in _tagged(wf, "DURATION") or (k in m["prompt"] and field == "duration"))]
        m["tempo"] = [k for k in m["prompt"] if isinstance(ins(k).get("bpm"), (int, float))]
        m["picture_in"] = [k for k in _tagged(wf, "PICTURE_IN") if cls(k) == "LoadImage"][:1]
        m["save"] = [k for k in m["save"] if cls(k).startswith("SaveAudio")]
    elif key == "voice_design":
        if not m["prompt"]:
            m["prompt"] = [k for k in keys if isinstance(ins(k).get("text"), str)][:1]
    elif key == "voice":
        m["audio_in"] = (_tagged(wf, "AUDIO_IN") or [k for k in keys if cls(k) == "LoadAudio"])[:1]
        m["ref_text"] = _tagged(wf, "REF_TEXT")[:1]
        if not m["prompt"]:
            m["prompt"] = [k for k in keys if isinstance(ins(k).get("text"), str) and cls(k) not in ("LoadAudio",)][:1]
    return m


def check_workflow(wf, key):
    if not isinstance(wf, dict) or not wf:
        return ["Not a workflow."]
    if "nodes" in wf and "links" in wf:
        return ["This is the normal (UI) format. In ComfyUI use Workflow > Export (API) and upload that file."]
    if any(not isinstance(n, dict) or "class_type" not in n for n in wf.values()):
        return ["Some entries have no class_type - not an API-format workflow."]
    m, p = detect_slots(wf, key), []
    if key in ("keyframe", "animate", "animate_flf", "lipsync") and not m["prompt"]:
        p.append("No prompt node (title a text-encode node PROMPT).")
    if key in ("keyframe", "animate", "animate_flf", "lipsync"):
        for tag, slot in (("PROMPT", "prompt"), ("NEGATIVE", "negative")):
            if set(_tagged(wf, tag)) - set(m[slot]):
                p.append(f"Tagged {tag} node does not feed its matching visual conditioning input.")
        if set(m["prompt"]) & set(m["negative"]):
            p.append("Positive and negative conditioning share a text encoder. Bind separate prompt nodes.")
    if key in ("animate", "animate_flf", "lipsync") and not m["start_image"] and not (key == 'lipsync' and m['video_in']):
        p.append("No start image (title a Load Image node START_IMAGE).")
    if key == "animate_flf" and not m["end_image"]:
        p.append("No end image (title a Load Image node END_IMAGE).")
    if key == "lipsync" and not m["audio_in"]:
        p.append("No audio input reaches visual conditioning. Use native Wan S2V or tag the custom visual node AUDIO_CONDITIONING and the loader AUDIO_IN.")
    if key == "voice" and not m["prompt"]:
        p.append("No text input for the line (title the text node PROMPT).")
    if key == "voice_design" and not m["prompt"]:
        p.append("No text input for the sample line (title the voice design node PROMPT).")
    if key == "voice_design" and not any("instruct" in n.get("inputs", {}) for n in wf.values()):
        p.append("No voice description input (a node with an 'instruct' field, such as Qwen3-TTS Voice Design).")
    if key == "voice" and not m["audio_in"]:
        p.append("No reference voice input (Load Audio node, title AUDIO_IN).")
    if key == "upscale" and not m["video_in"]:
        p.append("No video input (Load Video node, title VIDEO_IN).")
    if key in ("sound_effect", "music_cue"):
        if not m["prompt"]:
            p.append("Tag the sound description encoder PROMPT.")
        if not m["audio_duration"]:
            p.append("The sound workflow needs a writable seconds_total or a DURATION node.")
    if not m["save"]:
        p.append("No save node (Save Image / Save Video).")
    return p


def _remove_node(wf, key):
    wf.pop(key, None)
    for n in wf.values():
        inp = n.get("inputs", {})
        for name, v in list(inp.items()):
            if isinstance(v, list) and len(v) == 2 and str(v[0]) == str(key):
                del inp[name]


def _set_text(node, text):
    inp = node["inputs"]
    for f in ("prompt", "text", "text_g", "target_text", "tags"):
        if f in inp and not isinstance(inp[f], list):
            inp[f] = text
            return
    inp["text"] = text


def inject_loras(wf, loras):
    """Insert LoraLoaderModelOnly nodes between the model source and every sampler/guider."""
    loras = [(n, s) for n, s in loras if n]
    if not loras:
        return
    next_id = max([int(k) for k in wf if str(k).isdigit()] + [0]) + 1000
    chains = {}
    for k in list(wf):
        n = wf[k]
        src = n.get("inputs", {}).get("model")
        if n.get("class_type") in MODEL_CONSUMERS and isinstance(src, list):
            sk = (str(src[0]), src[1])
            if sk not in chains:
                cur = src
                for name, strength in loras:
                    nid = str(next_id)
                    next_id += 1
                    wf[nid] = _n("LoraLoaderModelOnly", {"model": cur, "lora_name": name, "strength_model": float(strength)})
                    cur = [nid, 0]
                chains[sk] = cur
            n["inputs"]["model"] = chains[sk]


def build_workflow(template, key, p, *, all_references=False):
    wf = copy.deepcopy(template)
    m = detect_slots(wf, key, all_references=all_references)
    for k in m["prompt"]:
        _set_text(wf[k], p.get("prompt", ""))
    if p.get("negative") is not None:
        for k in m["negative"]:
            _set_text(wf[k], p["negative"])
    imgs = p.get("images", {})
    if key == "keyframe":
        for i, k in enumerate(m["refs"]):
            if imgs.get(f"ref{i + 1}"):
                wf[k]["inputs"]["image"] = imgs[f"ref{i + 1}"]
            else:
                _remove_node(wf, k)
        inject_loras(wf, p.get("loras", []))
    if key in ("sound_effect", "music_cue"):
        for k, field in m["audio_duration"]:
            original = wf[k]["inputs"][field]
            value = p["audio_seconds"]
            wf[k]["inputs"][field] = max(1, round(value)) if isinstance(original, int) else float(value)
        for k in m["tempo"]:
            if p.get("tempo") is not None:
                wf[k]["inputs"]["bpm"] = p["tempo"]
        for k in m["picture_in"]:
            if not imgs.get("picture"):
                raise ValueError("The sound workflow needs an approved keyframe for PICTURE_IN.")
            wf[k]["inputs"]["image"] = imgs["picture"]
    for slot, field_name in (("start_image", "start"), ("end_image", "end")):
        for k in m[slot]:
            wf[k]["inputs"]["image"] = imgs[field_name]
    for k in m["audio_in"]:
        if imgs.get("audio"):
            wf[k]["inputs"]["audio"] = imgs["audio"]
        elif key in ("voice","lipsync"):
            raise ValueError("This workflow requires an audio input.")
    for k in m.get("audio_in_2", []):
        if not imgs.get("audio2"):
            raise ValueError("This two-speaker workflow requires two assigned dialogue tracks.")
        wf[k]["inputs"]["audio"] = imgs["audio2"]
    for k in m.get("ref_text", []):
        _set_text(wf[k], p.get("ref_text", ""))
    if key in ("voice", "voice_design"):   # fill well-known TTS inputs wherever a node has them
        for n in wf.values():
            inp = n.get("inputs", {})
            for field, val in (("ref_text", p.get("ref_text")), ("instruct", p.get("direction")),
                               ("language", p.get("language")), ("model_choice", p.get("tts_model"))):
                if val is not None and field in inp and isinstance(inp[field], str):
                    inp[field] = val
    for k in m["video_in"]:
        wf[k]["inputs"][VIDEO_LOADERS.get(wf[k]["class_type"], "file")] = imgs["video"]
    for k, f in m["seeds"]:
        if k in wf:
            wf[k]["inputs"][f] = p["seed"]
    for k in m["size"]:
        inp, step = wf[k]["inputs"], 32 if wf[k]["class_type"] == "Wan22ImageToVideoLatent" else 16
        for f in ("width", "height", "length"):
            if p.get(f) and f in inp and not isinstance(inp[f], list):
                v = int(p[f])
                inp[f] = max(step, int(round(v / step)) * step) if f != "length" else v
    if key == 'lipsync':
        for n in wf.values():
            if n.get('class_type') == 'WanSoundImageToVideo' and isinstance(n['inputs'].get('length'), (int, float)):
                n['inputs']['length'] = max(73, int(n['inputs']['length']))
    for k in m["save"]:
        if "filename_prefix" in wf[k]["inputs"]:
            wf[k]["inputs"]["filename_prefix"] = p["prefix"]
    # Explicit creative inputs never rely on the positional reference fallback.
    for tag, value in imgs.items():
        if tag.startswith('creative_'):
            for node in wf.values():
                if _title(node).upper() == tag[9:]:
                    field = VIDEO_LOADERS.get(node['class_type'], 'image')
                    node['inputs'][field] = value
    return wf


def collect_outputs(entry, stage, graph=None):
    """Pick a declared final saver or require one unambiguous output group."""
    audio = stage in ('voice', 'voice_design', 'sound_effect', 'music_cue')
    still = stage in ('keyframe', 'sheet')
    extensions = AUD_EXT if audio else IMG_EXT if still else IMG_EXT | VID_EXT
    groups = {}
    for key, outputs in entry.get('outputs', {}).items():
        files = [file for items in outputs.values() if isinstance(items, list)
                 for file in items if isinstance(file, dict) and file.get('type', 'output') == 'output'
                 and Path(file.get('filename') or '').suffix.lower() in extensions]
        if files:
            groups[key] = files
    if graph:
        tagged = _tagged(graph, 'FINAL_OUTPUT')
        if not tagged:
            tagged = _tagged(graph, 'SAVE')
        if len(tagged) > 1:
            raise ValueError('Tag exactly one workflow saver FINAL_OUTPUT.')
        if tagged:
            groups = {key: files for key, files in groups.items() if key in tagged}
    if not groups:
        raise ValueError('Workflow saved no final output of the expected media type.')
    if len(groups) > 1:
        raise ValueError('Several nodes saved matching outputs. Tag the intended saver FINAL_OUTPUT and queue a new render.')
    files = next(iter(groups.values()))
    if not audio and not still:
        videos = [file for file in files if Path(file['filename']).suffix.lower() in VID_EXT]
        if len(videos) > 1:
            raise ValueError('The final saver returned multiple videos. Configure it to save one video or one frame sequence.')
        if videos:
            return videos
    return files


# ======================================================================================
# Media helpers (run on the Mac)
# ======================================================================================
def ffmpeg(args):
    from export_runtime import run
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + args)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg: " + (r.stderr or "")[-1500:])


def stitch_frames(frames, fps, out):
    tmp = Path(str(out) + ".frames")
    tmp.mkdir(exist_ok=True)
    for i, f in enumerate(sorted(frames, key=lambda p: p.name), 1):
        f.rename(tmp / f"f_{i:06d}{f.suffix.lower()}")
    ext = sorted(tmp.iterdir())[0].suffix
    ffmpeg(["-framerate", str(fps), "-i", str(tmp / f"f_%06d{ext}"), "-c:v", "libx264", "-crf", "14",
            "-preset", "medium", "-pix_fmt", "yuv420p", str(out)])
    shutil.rmtree(tmp, ignore_errors=True)


def media_seconds(path):
    """Duration of any audio/video file (wav natively, everything else via ffmpeg)."""
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        pass
    if not shutil.which("ffmpeg"):
        return 0.0
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0


def validate_render_cadence(path, fps):
    """Read presentation timestamps and preserve the recipe's real-time cadence."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_frames",
         "-show_entries", "frame=best_effort_timestamp_time", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=300,
    )
    try:
        if result.returncode or result.stderr:
            raise ValueError(result.stderr)
        timestamps = [float(frame["best_effort_timestamp_time"])
                      for frame in json.loads(result.stdout)["frames"]]
        origin = timestamps[0]
        if any(not math.isfinite(value) for value in timestamps):
            raise ValueError("Non-finite timestamp")
    except (ValueError, KeyError, IndexError) as exc:
        raise ValueError("The generated file does not contain readable video timestamps.") from exc
    # Allow millisecond container quantization, such as WebM's 62/63 ms
    # spacing at 16 fps. Compare against the first presentation timestamp.
    if any(abs(value - origin - index / fps) > .002
           for index, value in enumerate(timestamps)):
        raise ValueError(f"The generated video does not use the recipe’s {fps:g} fps cadence. "
                         "Set the workflow output frame rate to match the recipe and generate a new take.")
    return len(timestamps)


def retime(st, take, mode, out_fps, log=None):
    """Return a file at out_fps. hold = repeat frames, blend = ffmpeg motion interpolation, rife = rife-ncnn-vulkan."""
    src, fps = st.abs(take["path"]), float(take["fps"] or 0)
    if not fps or abs(fps - out_fps) < 0.01:
        return src
    rife = st.tools().get("rife")
    if mode == "rife" and not rife:
        mode = "blend"
        if log is not None:
            log.append("  (rife-ncnn-vulkan not found - using blend)")
    stamp = hashlib.sha256((str(src) + str(src.stat().st_mtime_ns)).encode()).hexdigest()[:12]
    dst = st.data / "cache" / f"take{take['id']}_{stamp}_{mode}{out_fps}_v2.mp4"
    if dst.exists() and dst.stat().st_mtime >= src.stat().st_mtime:
        return dst
    enc = ["-c:v", "libx264", "-crf", "14", "-preset", "medium", "-pix_fmt", "yuv420p", "-an"]
    if mode == "hold":
        ffmpeg(["-i", str(src), "-vf", f"fps={out_fps}"] + enc + [str(dst)])
        return dst

    # Use the video stream's duration. Workflow metadata can disagree with
    # the encoded file, and the container may contain a longer audio track.
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(src)],
        capture_output=True, text=True,
    )
    try:
        source_duration = float(probe.stdout.strip())
    except ValueError:
        source_duration = media_seconds(src)
    if not 0 < source_duration < float("inf"):
        raise ValueError(f"Cannot determine video duration for {src.name}.")
    target = max(1, int(source_duration * out_fps + 0.5))
    if mode == "blend":
        # Interpolation looks ahead and drops the final frames without padding.
        # Clone the edge for lookahead, then cap the output at source duration.
        filters = f"tpad=stop_mode=clone:stop=3,minterpolate=fps={out_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        ffmpeg(["-i", str(src), "-vf", filters, "-frames:v", str(target)] + enc + [str(dst)])
    elif target == 1:
        ffmpeg(["-i", str(src), "-vf", f"fps={out_fps}", "-frames:v", "1"] + enc + [str(dst)])
    else:
        work = st.data / "cache" / f"rife_{take['id']}"
        shutil.rmtree(work, ignore_errors=True)
        (work / "in").mkdir(parents=True)
        (work / "out").mkdir()
        ffmpeg(["-i", str(src), str(work / "in" / "%08d.png")])
        rp = Path(rife)
        model = next((m for m in ("rife-v4.6", "rife-v4") if (rp.parent / m).is_dir()), None)
        cmd = [str(rp), "-i", str(work / "in"), "-o", str(work / "out"), "-n", str(target)] + (["-m", model] if model else [])
        r = subprocess.run(cmd, cwd=str(rp.parent), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("rife-ncnn-vulkan failed: " + (r.stderr or r.stdout)[-800:])
        frames = sorted((work / "out").glob("*.png"))
        stitch_frames(frames, out_fps, dst)
        shutil.rmtree(work, ignore_errors=True)
    return dst


def list_voices():
    if not shutil.which("say"):
        return []
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    voices = []
    for line in out.splitlines():
        m = re.match(r"^(.+?)\s{2,}([a-z]{2,3}[_-][A-Za-z0-9]+)", line)
        if m:
            voices.append({"name": m.group(1).strip(), "lang": m.group(2)})
    return voices


# ======================================================================================
# Job worker: one job at a time per ComfyUI server, matched by role
# ======================================================================================
def video_gpu_wait(job, health, minimum_gib, now):
    if job['stage'] not in ('animate', 'continue', 'lipsync', 'upscale') or job.get('prompt_id') or job.get('submission_client_id') or not minimum_gib:
        return ''
    free = [device['vram_free'] for device in health.get('hardware', [])
            if isinstance(device.get('vram_free'), (int, float))]
    if not free or now - health.get('t', 0) > 30:
        return 'Waiting for current free GPU memory telemetry.'
    available = max(free) / 2**30
    if available < minimum_gib:
        return f'Waiting for GPU memory: {available:.1f} GiB free, {minimum_gib:g} GiB configured.'
    return ''


def find_remote_submission(base, client_id):
    """Match the unique client ID recorded in ComfyUI's queue/history metadata."""
    queued = http_json("GET", base + "/queue", timeout=8)
    history = http_json("GET", base + "/history?max_items=1000", timeout=15)
    entries = queued.get("queue_running", []) + queued.get("queue_pending", [])
    entries += [entry.get("prompt") for entry in history.values()]
    matches = {entry[1] for entry in entries
               if isinstance(entry, (list, tuple)) and len(entry) > 3
               and isinstance(entry[3], dict) and entry[3].get("client_id") == client_id}
    if len(matches) > 1:
        raise RuntimeError("Several ComfyUI renders match this submission. Verify the server queue before repeating it.")
    return next(iter(matches), None)


class Worker(threading.Thread):
    daemon = True

    def __init__(self, st):
        super().__init__()
        self.st, self.last_health = st, 0

    def run(self):
        while True:
            try:
                if self.st.projects:
                    self.st.projects.refresh_configuration()
                if time.time() - self.last_health > 15:
                    self.refresh_health()
                self.st.production.tick()
                self.dispatch()
            except Exception:
                traceback.print_exc()
            time.sleep(2)

    def refresh_health(self):
        self.last_health = time.time()
        servers = {inst['id']:inst for inst in self.st.instances()}
        servers.update({inst['id']:inst for inst in self.st.render_instances() if inst.get('resume_only')})
        for inst in servers.values():
            threading.Thread(target=self.check, args=(inst,), daemon=True).start()

    def check(self, inst):
        try:
            j = http_json("GET", inst["url"].rstrip("/") + "/system_stats", timeout=4)
            parts = []
            for d in j.get("devices", []):
                nm = re.sub(r"^cuda:\d+\s*", "", d.get("name", "?")).split(" : ")[0]
                parts.append(f"{nm} - {d.get('vram_free', 0) / 2**30:.1f} of {d.get('vram_total', 0) / 2**30:.1f} GB free")
            self.st.health[inst["id"]] = {"ok": True, "info": "; ".join(parts), "t": time.time(), "hardware":[{k:d.get(k) for k in ("name","vram_total","vram_free")} for d in j.get("devices",[])]}
        except Exception as e:
            self.st.health[inst["id"]] = {"ok": False, "info": str(e)[:200], "t": time.time()}

    def dispatch(self):
        with self.st.production.lock:
            return self._dispatch()

    def _dispatch(self):
        db = self.st.db
        if db.one("SELECT id FROM jobs WHERE status='pending' LIMIT 1"):
            self.st.production.inspect()
        minimum_gib = float(self.st.settings().get("video_min_free_vram") or 0)
        memory_waits = {}
        busy = {r["instance_id"] for r in db.q("SELECT instance_id FROM jobs WHERE status='running'")}
        for inst in self.st.render_instances():
            if inst["id"] in busy or not self.st.health.get(inst["id"], {}).get("ok"):
                continue
            roles = [] if inst.get('resume_only') else [r.strip() for r in (inst["roles"] or "").split(",") if r.strip() in JOB_STAGES]
            inspected = self.st.production.readiness.get('servers', [])
            readiness = next((s['stages'] for s in inspected if s['id'] == inst['id']), None)
            resumable = db.q("SELECT * FROM jobs WHERE status='pending' AND (prompt_id IS NOT NULL OR submission_client_id IS NOT NULL) AND instance_id=? ORDER BY id", (inst['id'],))
            if not roles and not resumable:
                continue
            candidates = resumable
            if roles:
                candidates += db.q(f"SELECT * FROM jobs WHERE status='pending' AND stage IN ({','.join('?' * len(roles))}) AND prompt_id IS NULL AND submission_client_id IS NULL ORDER BY priority DESC,id", roles)
            job = None
            from render_queue import eligible_server
            for candidate in candidates:
                if not (candidate.get('prompt_id') or candidate.get('submission_client_id')) and not (candidate.get('snapshot_json') or candidate.get('creative_json')):
                    shot = db.one('SELECT end_next FROM shots WHERE id=?', (candidate['shot_id'],))
                    candidate['requires_flf'] = bool(candidate['stage'] in ('animate', 'continue') and shot and shot['end_next'])
                if not eligible_server(candidate, inst, self.st.health[inst['id']], readiness, self.st.job_compatible):
                    continue
                wait = video_gpu_wait(candidate, self.st.health[inst['id']], minimum_gib, time.time())
                if wait:
                    memory_waits[candidate['id']] = wait
                    continue
                job = candidate
                break
            if not job:
                continue
            from platform_runtime import ControllerBusy
            lease = self.st.projects.server_lock(inst['url']) if self.st.projects else nullcontext()
            try:
                lease.__enter__()
            except ControllerBusy:
                memory_waits[job['id']] = 'Another project is rendering on this server.'
                continue
            try:
                if db.u("UPDATE jobs SET status='running', instance_id=?, started=?, progress='starting', error=NULL "
                        "WHERE id=? AND status='pending'", (inst["id"], job.get("started") or time.time(), job["id"])):
                    busy.add(inst["id"])
                    threading.Thread(target=self.execute_leased, args=(job["id"], inst, lease), daemon=True).start()
                    continue
            except BaseException:
                lease.__exit__(*sys.exc_info())
                raise
            lease.__exit__(None, None, None)

        for job in db.q("SELECT id,progress FROM jobs WHERE status='pending'"):
            wait = memory_waits.get(job['id'])
            if wait:
                self._progress_wait(job, wait)
            elif (job.get('progress') or '').startswith(('Waiting for GPU memory:', 'Waiting for current free GPU', 'Another project is rendering')):
                self._progress_wait(job, 'waiting')

    def execute_leased(self, job_id, instance, lease):
        try:
            self.execute(job_id, instance)
        finally:
            lease.__exit__(None, None, None)

    def _progress_wait(self, job, value):
        if job['progress'] != value:
            self.st.db.x("UPDATE jobs SET progress=? WHERE id=? AND status='pending'", (value, job['id']))

    def _progress(self, job_id, text):
        db = self.st.db
        with db.lock:
            before = db.conn.total_changes
            db.x("UPDATE jobs SET progress=? WHERE id=? AND status='running' AND progress IS NOT ?",
                 (text, job_id, text))
            db.progress_writes += db.conn.total_changes - before

    def compose(self, job):
        """Returns (workflow_key, params)."""
        st, S = self.st, self.st.settings()
        if job.get("snapshot_json"):
            return st.snapshots.composed(job)
        if job.get("creative_json"):
            return st.creative.composed(job)
        stage = job["stage"]
        p = {"seed": job["seed"], "prefix": f"anime/j{job['id']:06d}_{stage}", "files": {}}
        if stage in ("sound_effect", "music_cue"):
            spec = json.loads(job['note'])
            row = st.wf_row(stage)
            if not row:
                raise ValueError("Import a sound effects workflow in Settings first.")
            if row['name']=='stable_audio_open' and spec['duration'] > 47.6:
                raise ValueError("Stable Audio Open supports effects up to 47.6 seconds.")
            slots = detect_slots(json.loads(row['json']), stage)
            p.update(prompt=spec['prompt'], negative=spec['negative'], audio_seconds=spec['duration'], tempo=spec.get('tempo'))
            if slots['picture_in']:
                shot = st.db.one("SELECT keyframe_take FROM shots WHERE id=?", (job['shot_id'],))
                selected = st.take_path(shot['keyframe_take']) if shot else None
                if not selected:
                    raise ValueError("Choose a shot keyframe for this sound recipe.")
                p['files']['picture'] = selected
            return stage, p
        if stage == "sheet":
            a = st.db.one("SELECT * FROM assets WHERE id=?", (job["asset_id"],))
            if not a or not a["image"] or not st.abs(a["image"]).exists():
                raise RuntimeError("Character needs a reference image first.")
            desc = f" ({a['description']})" if a["description"] else ""
            p["prompt"] = f"{SHEET_PROMPT}. Picture 1 is {a['name']}{desc}. {S.get('style_prompt', '')}"
            p["negative"] = S.get("keyframe_negative", "")
            p["files"] = {"ref1": st.abs(a["image"])}
            p["loras"] = [(a["lora"], a["lora_strength"])]
            p["width"], p["height"] = S["sheet_width"], S["sheet_height"]
            return "keyframe", p
        if stage == "voice_design":
            a = st.db.one("SELECT * FROM assets WHERE id=?", (job["asset_id"],))
            if not a:
                raise RuntimeError("That cast member was deleted.")
            if not (a.get("voice_desc") or "").strip():
                raise RuntimeError(f"{a['name']} has no voice description. Write one in Cast.")
            p["prompt"], p["negative"] = job.get("note") or VOICE_SAMPLE, None
            p["direction"] = a["voice_desc"].strip()
            p["language"] = norm_choice(S.get("tts_language"), TTS_LANGUAGES, "Auto")
            return "voice_design", p
        shot = st.db.one("SELECT * FROM shots WHERE id=?", (job["shot_id"],))
        if not shot:
            raise RuntimeError("Shot was deleted.")
        if stage == "voice":
            line = st.job_line(shot, job)
            if not line:
                raise RuntimeError("That dialogue line was changed or removed.")
            sp = st.line_character(line)
            ref = ({'path':job['voice_ref_path'],'transcript':job.get('voice_ref_text') or ''} if job.get('voice_ref_path') else st.ref_clip(sp['id']) if sp else None)
            if not ref or not st.abs(ref["path"]).exists():
                raise RuntimeError(f"{line['speaker'] or 'The speaker'} has no voice clip to copy. Add one in Cast.")
            p["files"] = {"audio": st.abs(ref["path"])}
            p["prompt"], p["negative"], p["ref_text"] = st.creative.spoken(line["character_id"], line["text"]), None, ref["transcript"] or ""
            S = st.settings()
            p["direction"] = job.get("voice_direction") if job.get("voice_direction") is not None else line.get("direction") or ""
            p["language"] = norm_choice(S.get("tts_language"), TTS_LANGUAGES, "Auto")
            p["tts_model"] = norm_choice(S.get("tts_model"), ["0.6B", "1.7B"], "1.7B")
            return "voice", p
        if stage == "keyframe":
            ids = json.loads(shot["asset_ids"] or "[]")
            assets = [a for a in (st.db.one("SELECT * FROM assets WHERE id=?", (i,)) for i in ids) if a]
            context = st.editor.context(shot['scene'])
            overrides = {v['asset_id']: v for v in context['variants']}
            for v in context['variants']:
                if v['asset_id'] not in {a['id'] for a in assets}:
                    a = st.db.one('SELECT * FROM assets WHERE id=?', (v['asset_id'],))
                    if a: assets.append(a)
            assets = [dict(a, image=overrides[a['id']]['image']) if a['id'] in overrides else a for a in assets]
            assets = st.creative.scene_assets(shot['scene'], assets)
            graph = job.get('_keyframe_template')
            if graph is None:
                graph, _, _ = st.keyframe_template(shot['id'])
            limit = len(detect_slots(graph, 'keyframe')['refs'])
            references = []
            for asset in assets:
                if asset['image'] and st.abs(asset['image']).is_file():
                    references.append((f"cast-{asset['id']}", manga.asset_label(asset), st.abs(asset['image'])))
            if shot['ref_prev']:
                previous = st.neighbor(shot, -1)
                image = st.take_path(previous['keyframe_take']) if previous else None
                if image and image.is_file():
                    references.append(('previous', 'the previous shot: keep the same location, lighting and outfits', image))
            order = job.get('reference_order', json.loads(shot.get('keyframe_reference_order') or '[]'))
            priority = {ident: index for index, ident in enumerate(order)}
            references.sort(key=lambda ref: priority.get(ref[0], len(priority)))
            chosen = references[:limit]
            bubbles, _ = manga.effective(st, shot)
            names = {a['id']: a['name'] for a in assets if (a.get('kind') or 'character') == 'character'}
            for bubble in bubbles:
                cid = bubble.get('character_id')
                if cid and cid not in names:
                    row = st.db.one("SELECT name FROM assets WHERE id=?", (cid,))
                    if row:
                        names[cid] = row['name']
            loras = [(asset['lora'], asset['lora_strength']) for asset in assets if asset['lora']]
            p['review_references'] = [{'id': ident, 'label': label, 'path': st.rel(path), 'applied': index < limit}
                                      for index, (ident, label, path) in enumerate(references)]
            p['omitted_loras'] = [name for name, _ in loras[4:]]
            p["prompt"] = manga.compose_keyframe(chosen=chosen, assets=assets, shot=shot, direction=st.creative.direction(shot["id"]),
                                                 context=context, bubbles=bubbles, settings=S, names=names)
            p["bubbles"] = bubbles
            p["negative"] = S.get("keyframe_negative", "")
            p["files"] = {f"ref{i + 1}": ref[2] for i, ref in enumerate(chosen)}
            p["loras"] = loras[:4]
            p["width"], p["height"] = S["kf_width"], S["kf_height"]
            return "keyframe", p
        kf = st.take_path(shot["keyframe_take"])
        text = ", ".join(x for x in (shot["motion_prompt"] or shot["keyframe_prompt"], manga.motion_acting(st, shot) if stage == "animate" else "", shot["camera"], S.get("motion_suffix", "")) if x)
        draft = bool(job.get("draft"))
        size = (S["draft_width"], S["draft_height"]) if draft else (S["vid_width"], S["vid_height"])

        def end_on_next(seconds_needed, key_now):
            """Use first-last-frame mode only when this clip reaches the end of the shot."""
            if not shot["end_next"]:
                return key_now
            length = st.frames_for(seconds_needed, "animate_flf", draft)
            if (length - 1) / float(st.wf_fps("animate_flf")) + 0.25 < seconds_needed:
                return key_now     # more segments follow - the last one will land on the next keyframe
            nxt = st.neighbor(shot, 1)
            end = st.take_path(nxt["keyframe_take"]) if nxt else None
            if not end:
                raise RuntimeError("'End on the next shot' is on, but the next shot has no chosen keyframe.")
            p["files"]["end"] = end
            return "animate_flf"

        if stage in ("animate", "lipsync"):
            existing = stage == 'lipsync' and shot.get('lip_sync_mode') == 'existing'
            if not kf and not existing:
                raise RuntimeError("Shot has no approved keyframe.")
            key = stage
            p["files"] = {"start": kf} if kf else {}
            if stage == "animate":
                key = end_on_next(float(shot["duration"]), key)
            if stage == "lipsync":
                if not shot["audio"] or not st.abs(shot["audio"]).exists():
                    raise RuntimeError("Lip sync needs dialogue audio on the shot.")
                p["files"]["audio"] = st.abs(shot["audio"])
                if existing:
                    from voice_video import connected_video, lip_workflow
                    row = lip_workflow(st, shot, job)
                    if not row or not connected_video(json.loads(row['json'])):
                        raise ValueError('Lip-syncing an existing clip requires a tested video-conditioned workflow with a connected VIDEO_IN and AUDIO_IN. The built-in Wan S2V starter generates a new clip from a keyframe.')
                    clip = st.take_path(shot['clip_take'])
                    if not clip:
                        raise ValueError('Choose the existing clip to lip-sync first.')
                    p['files']['video'] = clip
            p["prompt"], p["negative"] = text, S.get("video_negative", "")
            p["width"], p["height"] = size
            p["length"] = st.frames_for(shot["duration"], key, draft)
            return key, p
        if stage == "continue":
            return st.sections.compose(job)
        if stage == "upscale":
            clip = st.take_path(shot["clip_take"])
            if not clip:
                raise RuntimeError("Shot has no approved clip.")
            p["files"] = {"video": clip}
            return "upscale", p
        raise RuntimeError(f"Unknown stage {stage}")

    def execute(self, job_id, inst):
        st, db = self.st, self.st.db
        base, pid = inst["url"].rstrip("/"), None
        events, submitted_graph = None, None
        try:
            job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
            st.creative.started(job,inst)
            job = db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
            pid = job.get("prompt_id")
            client_id = job.get("submission_client_id")
            if client_id and not pid:
                try:
                    pid = find_remote_submission(base, client_id)
                except Exception as exc:
                    raise RuntimeError("Submission unconfirmed. Reconnect to check ComfyUI again. " + str(exc)) from exc
                if not pid:
                    raise RuntimeError("Submission unconfirmed. Check the original ComfyUI queue before repeating this render. "
                                       "Reconnect checks the existing submission without sending it again.")
                db.x("UPDATE jobs SET prompt_id=? WHERE id=?", (pid, job_id))
            from render_queue import workflow_models, prompt_graph
            if not pid:
                db.x("UPDATE jobs SET models_json=NULL WHERE id=?",(job_id,))
            elif job.get('models_json') is None:
                try:
                    queued=http_json('GET',base+'/queue',timeout=8)
                    for entry in queued.get('queue_running',[])+queued.get('queue_pending',[]):
                        graph=prompt_graph(entry,pid)
                        if graph is not None:
                            db.x('UPDATE jobs SET models_json=? WHERE id=?',(json.dumps(workflow_models(graph)),job_id));break
                except Exception:
                    pass  # Older servers may not expose their queued API graphs.
            key = 'animate' if job['stage'] == 'continue' else job['stage']
            if not pid:
                if not db.one("SELECT job_id FROM production_inputs WHERE job_id=?",(job_id,)):
                    st.production.capture(job)
                key, p = self.compose(job)
                row = st.snapshots.workflow(job) or st.creative.workflow(job) or st.wf_row(key)
                if not row:
                    raise RuntimeError(f"No '{key}' workflow set. Upload one in Settings > Workflows.")
                tpl = json.loads(row["json"])
                problems = check_workflow(tpl, key)
                if problems:
                    raise RuntimeError(f"'{key}' workflow: " + " ".join(problems))
            from comfy_events import ProgressEvents
            def progress_event(event):
                data = event.get('data', {})
                if data.get('prompt_id') not in (None, pid):
                    return
                if event['type'] == 'progress' and data.get('max'):
                    self._progress(job_id, f"Sampler {data.get('value',0)}/{data['max']} (current node)")
                elif data.get('node'):
                    self._progress(job_id, 'Executing node ' + str(data['node']))
            # Already accepted jobs may predate namespaced submissions.
            client_id = client_id or job.get("remote_client_id") or f"anime-studio-{job_id}"
            if not pid:
                client_id = f"anime-studio-{st.render_namespace}-{uuid.uuid4().hex}"
            events = ProgressEvents(base, client_id, progress_event).start()
            def transfer_progress(label, current, total):
                row = db.one("SELECT status FROM jobs WHERE id=?", (job_id,))
                if not row or row['status'] != 'running':
                    raise RuntimeError("Render transfer cancelled.")
                length = f" / {total:,}" if total is not None else ""
                self._progress(job_id, f"{label}: {current:,}{length} bytes")

            if not pid:
                self._progress(job_id, "uploading inputs")
                attempt = client_id.removeprefix("anime-studio-")
                p["prefix"] = f"anime/{st.render_namespace}/{attempt}_{job['stage']}"
                p["images"] = {slot: comfy_upload(base, path, f"anime_j{job_id}_{attempt}_{slot}{Path(path).suffix.lower()}",
                                                  lambda current, total: transfer_progress("Uploading " + slot, current, total))
                               for slot, path in p["files"].items()}
                wf = build_workflow(tpl, key, p)
                wf = st.snapshots.patch(job,wf) if job.get("snapshot_json") else st.creative.patch_controls(job, wf)
                submitted_graph = wf
                # After this write a restart must reconcile, even if it happened just before send.
                if not db.u("UPDATE jobs SET submission_client_id=? WHERE id=? AND status='running'", (client_id, job_id)):
                    return
                try:
                    res = http_json("POST", base + "/prompt", {"prompt": wf, "client_id": client_id}, 60)
                except RuntimeError as e:
                    if str(e).startswith("HTTP 400 "):
                        db.x("UPDATE jobs SET submission_client_id=NULL WHERE id=?", (job_id,))
                        raise RuntimeError(explain_missing_nodes(str(e), base) or str(e)) from None
                    raise RuntimeError("Submission unconfirmed. Reconnect to find the original render. " + str(e)) from e
                except OSError as exc:
                    raise RuntimeError("Submission unconfirmed. Reconnect to find the original render. " + str(exc)) from exc
                if not res.get("prompt_id") and res.get("node_errors"):
                    db.x("UPDATE jobs SET submission_client_id=NULL WHERE id=?", (job_id,))
                    raise RuntimeError("ComfyUI rejected the workflow: " + json.dumps(res["node_errors"])[:2500])
                pid = res.get("prompt_id")
                if not pid:
                    raise RuntimeError("Submission unconfirmed. ComfyUI did not return a prompt ID.")
                db.x("UPDATE jobs SET prompt_id=?, models_json=?, remote_client_id=?, progress='queued on ComfyUI' WHERE id=?", (pid, json.dumps(workflow_models(wf)), client_id, job_id))
            t0, timeout, fails, entry = time.time(), int(float(st.settings().get("job_timeout") or 3600)), 0, None
            while True:
                if (db.one("SELECT status FROM jobs WHERE id=?", (job_id,)) or {}).get("status") != "running":
                    self._interrupt(base, pid)
                    return
                try:
                    hist = http_json("GET", f"{base}/history/{pid}", timeout=30)
                    fails = 0
                except Exception:
                    fails += 1
                    if fails > 30:
                        raise
                    hist = {}
                if pid in hist:
                    entry = hist[pid]
                    if (db.one('SELECT models_json FROM jobs WHERE id=?',(job_id,)) or {}).get('models_json') is None:
                        graph=prompt_graph(entry.get('prompt'),pid)
                        if graph is not None:db.x('UPDATE jobs SET models_json=? WHERE id=?',(json.dumps(workflow_models(graph)),job_id))
                    s = entry.get("status", {})
                    if s.get("completed") or s.get("status_str") in ("success", "error"):
                        break
                if time.time() - t0 > timeout:
                    self._interrupt(base, pid)
                    raise RuntimeError(f"Timed out after {timeout}s")
                current = db.one('SELECT progress FROM jobs WHERE id=?', (job_id,)) or {}
                if not current.get('progress', '').startswith(('Sampler ', 'Executing node ')):
                    self._progress(job_id, f"rendering {int(time.time() - t0)}s")
                time.sleep(2)
            if entry.get("status", {}).get("status_str") == "error":
                db.x("UPDATE jobs SET prompt_id=NULL,submission_client_id=NULL WHERE id=?", (job_id,))
                msgs = [m for m in entry["status"].get("messages", []) if m and m[0] == "execution_error"]
                raise RuntimeError("ComfyUI error: " + json.dumps(msgs[-1][1] if msgs else entry["status"])[:2500])
            graph = submitted_graph or prompt_graph(entry.get('prompt'), pid)
            if graph is None:
                captured = st.snapshots.workflow(job) or st.creative.workflow(job)
                graph = json.loads(captured['json']) if captured else None
            files = collect_outputs(entry, job['stage'], graph)
            self._progress(job_id, f"downloading {len(files)} file(s)")
            out_dir = st.data / "jobs" / str(job_id)
            out_dir.mkdir(parents=True, exist_ok=True)
            local = []
            for i, f in enumerate(files):
                q = urllib.parse.urlencode({"filename": f["filename"], "subfolder": f.get("subfolder", ""), "type": f.get("type", "output")})
                dest = out_dir / f"{i:06d}_{Path(f['filename']).name}"
                comfy_download(f"{base}/view?{q}", dest,
                               lambda current, total: transfer_progress(f"Downloading {i+1}/{len(files)}", current, total))
                local.append(dest)
            with st.production.lock:
                if (db.one("SELECT status FROM jobs WHERE id=?", (job_id,)) or {}).get("status") != "running":
                    return
                manifest = out_dir / (uuid.uuid4().hex + '.render-import')
                manifest.touch()
                _IMPORT_CONTEXT.manifest = manifest
                try:
                    with db.transaction():
                        if not st.creative.finish_special(job, key, local):
                            self.finish(job, key, local)
                        db.x("UPDATE jobs SET status='done', finished=?, progress='done' WHERE id=?", (time.time(), job_id))
                        st.creative.completed(db.one('SELECT * FROM jobs WHERE id=?', (job_id,)), 'done')
                        db.x('INSERT INTO render_imports VALUES(?)', (manifest.stem,))
                finally:
                    _IMPORT_CONTEXT.manifest = None
                    reconcile_import(st, manifest)
            from render_queue import remember_timings
            remember_timings(db)
            shutil.rmtree(out_dir, ignore_errors=True)
        except Exception as e:
            traceback.print_exc()
            db.x("UPDATE jobs SET status='error', error=?, finished=?, progress='' WHERE id=? AND status='running'",
                 (str(e)[:4000], time.time(), job_id))
            failed = db.one('SELECT * FROM jobs WHERE id=?', (job_id,))
            if failed: st.creative.completed(failed, 'error', str(e)[:4000])

        finally:
            if events:
                events.close()

    def _interrupt(self, base, pid):
        if not pid:
            return
        try:
            http_json("POST", base + "/queue", {"delete": [pid]}, 10)
            q = http_json("GET", base + "/queue", timeout=10)
            if any(item[1] == pid for item in q.get("queue_running", [])):
                http_json("POST", base + "/interrupt", {}, 10)
        except Exception:
            pass

    def finish(self, job, key, local):
        st, stage = self.st, job["stage"]
        output_id = uuid.uuid4().hex[:12]
        signature = (st.db.one("SELECT signature FROM production_inputs WHERE job_id=?", (job["id"],)) or {}).get("signature")
        owner = (st.db.one("SELECT id FROM assets WHERE id=?", (job["asset_id"],)) if stage in ASSET_STAGES
                 else st.db.one("SELECT id FROM shots WHERE id=?", (job["shot_id"],)))
        if not owner:
            return   # deleted while rendering
        imgs = [f for f in local if f.suffix.lower() in IMG_EXT]
        vids = [f for f in local if f.suffix.lower() in VID_EXT]
        if stage in ("sound_effect", "music_cue"):
            st.finishing.save_generated_sound(job, [path for path in local if path.suffix.lower() in AUD_EXT])
            return
        if stage == "voice_design":
            auds = [f for f in local if f.suffix.lower() in AUD_EXT]
            if not auds:
                raise RuntimeError("The voice design workflow saved no audio file.")
            a = st.db.one("SELECT * FROM assets WHERE id=?", (job["asset_id"],))
            folder = st.data / "voices" / "reference"
            folder.mkdir(parents=True, exist_ok=True)
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", a["name"]).strip("_") or f"cast{a['id']}"
            dest = folder / f"{safe}_designed_{job['id']}_{output_id}.wav"
            record_import_file(dest)
            ffmpeg(["-i", str(auds[0]), "-ar", "48000", str(dest)])
            has_ref = st.db.one("SELECT id FROM voice_clips WHERE asset_id=? AND is_ref=1", (a["id"],))
            st.db.x("INSERT INTO voice_clips(asset_id,path,name,transcript,seconds,is_ref,created) VALUES(?,?,?,?,?,?,?)",
                    (a["id"], st.rel(dest), "Designed: " + (a.get("voice_desc") or "")[:80], job.get("note") or "",
                     round(media_seconds(dest), 2), 0 if has_ref else 1, time.time()))
            return
        if stage == "voice":
            auds = [f for f in local if f.suffix.lower() in AUD_EXT]
            if not auds:
                raise RuntimeError("The voice workflow saved no audio file.")
            (st.data / "voices" / "lines").mkdir(parents=True, exist_ok=True)
            dest = st.data / "voices" / "lines" / f"tts_j{job['id']}_{output_id}.wav"
            record_import_file(dest)
            ffmpeg(["-i", str(auds[0]), "-ar", "48000", str(dest)])
            shot = st.db.one("SELECT * FROM shots WHERE id=?", (job["shot_id"],))
            if not shot:
                return
            lines = st.shot_lines(shot)
            line = st.job_line(shot, job)
            if line:
                line = lines[line["index"]]
            st.db.x("INSERT INTO voice_takes(shot_id,text,speaker,path,seed,job_id,seconds,created,input_signature,line_index,character_id,line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (shot["id"], job.get("note"), (line or {}).get("speaker", ""), st.rel(dest), job["seed"], job["id"],
                     round(media_seconds(dest), 2), time.time(), signature, line["index"] if line else job.get("segment"), job.get("voice_character_id"), job.get("voice_line_id")))
            if not st.creative.meta(job).get("manual") and line and (not line["audio"] or (job.get("voice_autoselect") and line["audio"]==job.get("voice_previous_audio"))) and (not signature or signature==st.production.signature(shot,"voice")):
                line.update(audio=st.rel(dest), src="tts", clip_id=None)
                st.save_lines(shot["id"], lines)
                st.rebuild_audio(shot["id"])
            return
        if stage == "sheet":
            folder = st.data / "cast" / f"{job['asset_id']:04d}"
        else:
            folder = st.data / "shots" / f"{job['shot_id']:04d}"
        folder.mkdir(parents=True, exist_ok=True)
        made = []
        video_frames = None
        if stage in ("sheet", "keyframe"):
            if job.get("creative_json") and not imgs: raise ValueError("The recipe saved no image candidate.")
            for i, f in enumerate(imgs or local):
                ffmpeg(["-xerror", "-i", str(f), "-frames:v", "1", "-f", "null", "-"])
                dest = folder / f"{stage}_j{job['id']}_{output_id}_{i}{f.suffix.lower()}"
                record_import_file(dest)
                shutil.move(str(f), dest)
                made.append((dest, 0))
        else:
            if stage == "upscale" and not job.get("snapshot_json"):
                src = st.db.one("SELECT t.fps FROM takes t JOIN shots s ON s.clip_take=t.id WHERE s.id=?", (job["shot_id"],))
                fps = (src or {}).get("fps") or 16
            else:
                fps = (st.snapshots.workflow(job) or st.creative.workflow(job) or {}).get("fps") or st.wf_fps(key)
            dest = folder / f"{stage}_j{job['id']}_{output_id}.mp4"
            if vids:
                validate_render_cadence(vids[0], fps)
                dest = dest.with_suffix(vids[0].suffix.lower())
                record_import_file(dest)
                shutil.move(str(vids[0]), dest)
            else:
                if not st.tools()["ffmpeg"]:
                    raise RuntimeError("ffmpeg is needed on this Mac to turn frames into video: brew install ffmpeg")
                self._progress(job["id"], f"stitching {len(imgs)} frames at {fps} fps")
                record_import_file(dest)
                stitch_frames(imgs, fps, dest)
            from clip_audio import embed as embed_clip_audio
            dest = embed_clip_audio(st, job, dest)
            video_frames = validate_render_cadence(dest, fps)
            made.append((dest, fps))
        for path, fps in made:
            tid = st.db.x("INSERT INTO takes(shot_id,asset_id,stage,path,fps,seed,job_id,created,frames,segment,draft,input_signature) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                          (job["shot_id"], job["asset_id"], stage, st.rel(path), fps, job["seed"], job["id"], time.time(),
                           video_frames if fps else None, job.get("segment"), 1 if job.get("draft") else 0, signature))
            st.sections.saved_take(tid,job)
            if st.creative.meta(job).get("manual"):
                continue
            if stage in TAKE_FIELD:
                current=st.db.one("SELECT * FROM shots WHERE id=?",(job["shot_id"],))
                if current and signature and signature!=st.production.signature(current,stage,job.get("segment")):continue
                f = TAKE_FIELD[stage]
                st.db.x(f"UPDATE shots SET {f}=? WHERE id=? AND {f} IS NULL", (tid, job["shot_id"]))
            elif stage == "continue":
                shot = st.db.one("SELECT * FROM shots WHERE id=?", (job["shot_id"],))
                cont = [t["id"] for t in st.chain(shot)[1:]] if shot else []
                if shot and len(cont) == int(job.get("segment") or 0) and (not signature or signature==st.production.signature(shot,"continue",job.get("segment"))):
                    st.db.x("UPDATE shots SET cont_takes=? WHERE id=?", (json.dumps(cont + [tid]), shot["id"]))


# ======================================================================================
# Assembly + DaVinci Resolve export (on the Mac)
# ======================================================================================
def tc(frames, fps, hour=0):
    f = int(frames)
    return f"{hour + f // (fps * 3600):02d}:{f // (fps * 60) % 60:02d}:{f // fps % 60:02d}:{f % fps:02d}"


def assemble(st, use_music, music_volume, preview=False, scene=None, use_timed=True, shot_ids=None):
    A = st.assembly
    A.update(running=True, log=[], output=None, folder=None, kind='animatic' if preview else 'episode')
    log = A["log"]
    cues = st.soundtrack.cues() if use_timed and not preview else []
    try:
        if not st.tools(refresh=True)["ffmpeg"]:
            raise RuntimeError("ffmpeg not found. Install it with: brew install ffmpeg")
        S = st.settings()
        W, H, FPS = int(S["out_width"]), int(S["out_height"]), int(S["out_fps"])
        if preview:
            W, H = 960, 540
        all_shots = st.ordered_shots()
        shots = [s for s in all_shots if (scene is None or s['scene']==scene) and (shot_ids is None or s['id'] in shot_ids)]
        start = 0
        for shot in all_shots:
            if shots and shot['id']==shots[0]['id']:break
            start += timeline_frames(shot['duration'],FPS)/FPS
        if not shots:
            raise RuntimeError("No shots yet.")
        bed = S.get('music', '') if use_music else ''
        build = {
            'picture': st.soundtrack.picture_signature(shots, S), 'mix': st.soundtrack.mix_signature(cues, bed),
            'width': W, 'height': H, 'fps': FPS, 'music': bool(use_music), 'timed': bool(use_timed),
            'volume': float(music_volume),
        } if not preview and scene is None and shot_ids is None else None
        name = re.sub(r"[^A-Za-z0-9_-]+", "_", S.get("title") or "episode").strip("_") or "episode"
        name += "_animatic" if preview else ""
        out = st.data / "exports" / f"{name}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        (out / "shots").mkdir(parents=True)
        venc = ["-c:v", "libx264", "-crf", "16", "-preset", "medium", "-pix_fmt", "yuv420p", "-r", str(FPS)]
        aenc = ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
        fill = f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,crop={W}:{H},setsar=1"
        segs, edl, rec = [], [f"TITLE: {name}", "FCM: NON-DROP FRAME", ""], 0
        for i, s in enumerate(shots, 1):
            dur = max(0.5, float(s["duration"] or 4))
            trim_in = float(s.get("trim_in") or 0)
            nfr = timeline_frames(dur, FPS)
            seg = out / "shots" / f"{i:04d}_shot.mp4"
            chain = st.chain(s)
            take = st.take(s["final_take"]) or (chain[0] if chain else None)
            if take and s["kind"] == "still" and take["stage"] not in ("lipsync", "upscale"):
                take, chain = None, []   # still shots use the keyframe pan unless they were lip-synced
            if preview:
                take, chain = None, []
            kf = st.take_path(s["keyframe_take"])
            audio = st.abs(s["audio"]) if s["audio"] and st.abs(s["audio"]).exists() else None
            ain = ["-i", str(audio)] if audio else ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            if audio:
                spoken = media_seconds(audio)
                if spoken > dur + 0.05:
                    log.append(f"  WARNING shot {i}: dialogue is {spoken:.1f}s but the shot is {dur:.1f}s, so the end is cut off")
            if take:
                mode = s["retime"] or "hold"
                parts = [retime(st, take, mode, FPS, log)]
                for t in chain[1:]:
                    source_stamp = hashlib.sha256(t["path"].encode()).hexdigest()[:12]
                    trimmed = st.data / "cache" / f"take{t['id']}_{source_stamp}_trim.mp4"
                    if not trimmed.exists():
                        ffmpeg(["-i", str(st.abs(t["path"])), "-vf", "trim=start_frame=1,setpts=PTS-STARTPTS",
                                "-c:v", "libx264", "-crf", "14", "-pix_fmt", "yuv420p", "-an", str(trimmed)])
                    parts.append(retime(st, dict(t, id=f"{t['id']}t", path=st.rel(trimmed)), mode, FPS, log))
                vin = sum((["-i", str(x)] for x in parts), [])
                n = len(parts)
                vf = "".join(f"[{k}:v]fps={FPS},{fill}[p{k}];" for k in range(n))
                vf += "".join(f"[p{k}]" for k in range(n)) + f"concat=n={n}:v=1:a=0,trim=start={trim_in},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={dur}[v]"
                motion = st.chain_seconds(chain) if chain else 0
                how = f"clip ({take['stage']}{' + ' + str(n - 1) + ' continued' if n > 1 else ''}, {mode})"
                if any(t.get("draft") for t in [take] + chain):
                    how += " DRAFT"
                if motion and dur - motion > 0.25:
                    how += f", last frame held {dur - motion:.1f}s"
                    log.append(f"  WARNING shot {i}: incomplete motion coverage; final frame will be held. Continue the shot for full animation.")
            elif kf:
                how, vin = "still pan", ["-loop", "1", "-framerate", str(FPS), "-i", str(kf)]
                vf = (f"[0:v]scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
                      f"zoompan=z='min(1+0.0010*on,1.2)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={FPS},setsar=1[v]")
                directed = st.creative.camera_filter(s, W, H, FPS)
                if directed: vf = '[0:v]'+directed+'[v]'
                if not directed and preview and (s['camera'] or '').strip().lower() in ('static', 'hold', 'locked'):
                    vf = f"[0:v]{fill}[v]"
            else:
                how, vin = "MISSING - black", ["-f", "lavfi", "-i", f"color=c=black:s={W}x{H}:r={FPS}"]
                vf = "[0:v]setsar=1[v]"
            a_idx = (len(vin) // 2) if take else 1
            ffmpeg(vin + ain + ["-filter_complex", vf + f";[{a_idx}:a]atrim=start={trim_in},asetpts=PTS-STARTPTS,aresample=48000,apad[a]", "-map", "[v]", "-map", "[a]",
                                "-frames:v", str(nfr), "-t", f"{nfr / FPS:.3f}"] + venc + aenc + [str(seg)])
            segs.append(seg)
            edl += [f"{i:03d}  AX       AA/V  C        {tc(0, FPS)} {tc(nfr, FPS)} {tc(rec, FPS, 1)} {tc(rec + nfr, FPS, 1)}",
                    f"* FROM CLIP NAME: {seg.name}", f"* COMMENT: {(s['scene'] or '')[:60]} | {(s['keyframe_prompt'] or '')[:80]}", ""]
            rec += nfr
            log.append(f"Shot {i}: {how}, {dur:.1f}s" + (" + dialogue" if audio else ""))
        (out / "timeline.edl").write_text("\n".join(edl), encoding='utf-8')
        (out / "concat.txt").write_text("".join(f"file 'shots/{p.name}'\n" for p in segs), encoding='utf-8')
        joined, final = out / "_joined.mp4", out / f"{name}.mp4"
        ffmpeg(["-f", "concat", "-safe", "0", "-i", str(out / "concat.txt"), "-c", "copy", str(joined)])
        master = out / '_dialogue_master.mp4'
        if not preview:
            if any((shot.get('transition') or 'cut')!='cut' for shot in shots[:-1]):
                joined.unlink()
                st.finishing.join_picture(shots, segs, joined, FPS)
            duration = rec / FPS
            dialogue = st.finishing.window_tracks(st.finishing.dialogue_tracks(all_shots), start, duration)
            st.finishing.audio(joined, master, dialogue, duration)
            joined.unlink()
            cues = st.finishing.window_tracks(cues, start, duration)
            # Keep a music bed at the same source phase as the whole episode.
            if bed:
                from soundtrack import probe
                seconds, _ = probe(st.abs(bed))
                cues.append(dict(name='Music bed',kind='music',path=bed,start=0,offset=0,duration=duration,
                                 source_seconds=seconds,window_skip=start,original_duration=start+duration,
                                 volume=music_volume,fade_in=0,fade_out=0,loop=1,enabled=1))
                bed = ''
        else:
            joined.rename(master)
        log.append('Mixing episode audio...' if cues or use_music else 'Saving dialogue-only episode...')
        if build is not None:
            build['mix_recipe']={'cues':cues,'bed':bed,'volume':float(music_volume),'mixer':st.finishing.mixer(),'engine':'block-rms-v1'}
        st.soundtrack.mix(master, final, cues, bed, float(music_volume), log)
        if not preview and scene is None and shot_ids is None:
            st.soundtrack.register(master, final, rec / FPS, build=build)
        (out / "concat.txt").unlink()
        (out / "shotlist.csv").write_text(API(st).csv_export(), encoding='utf-8')
        A["output"], A["folder"] = st.rel(final), st.rel(out)
        log.append(f"Done: {final.name}  ({int(rec / FPS // 60)}:{int(rec / FPS % 60):02d})")
        log.append("For DaVinci Resolve: File > Import > Timeline > timeline.edl, and point it at the 'shots' folder.")
    except Exception as e:
        traceback.print_exc()
        log.append("ERROR: " + str(e))
    finally:
        A["running"] = False


# ======================================================================================
# HTTP API
# ======================================================================================
CSV_FIELDS = ["seq", "scene", "kind", "cast", "keyframe_prompt", "motion_prompt", "camera", "dialogue", "duration",
              "retime", "ref_prev", "end_next", "notes"]
SHOT_FIELDS = {"scene", "kind", "keyframe_prompt", "motion_prompt", "camera", "dialogue", "duration", "retime", "ref_prev", "end_next", "notes", "voice_quality", "voice_framing", "voice_crop", "voice_speakers", "voice_direction", "lip_sync_mode"}
TRUE = (1, True, "1", "true", "yes", "y", "on")


class API:
    def __init__(self, st):
        self.st = st

    def media(self, p):
        return ("/media/" + urllib.parse.quote(p)) if p else None

    def queue_state(self, insts=None):
        st, db = self.st, self.st.db
        if insts is None:
            insts = st.instances()
        if st.projects:
            insts = [*insts, *(server for server in st.render_instances() if server.get('resume_only'))]
        jobs = db.q("SELECT * FROM jobs WHERE status IN ('pending','running') "
                    "OR id IN (SELECT id FROM jobs WHERE status='done' ORDER BY finished DESC,id DESC LIMIT 100) "
                    "OR id IN (SELECT id FROM jobs WHERE status IN ('error','cancelled') ORDER BY finished DESC,id DESC LIMIT 100) ORDER BY id DESC")
        counts = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        from render_queue import queue_snapshot
        history = db.q("SELECT stage,draft,instance_id,'done' AS status,started,finished,workload FROM render_timing_history ORDER BY finished DESC LIMIT 1000")
        readiness = {server['id']: server['stages'] for server in st.production.readiness.get('servers', [])}
        end_next = {shot['id'] for shot in db.q('SELECT id FROM shots WHERE end_next=1')}
        for job in jobs:
            job['requires_flf'] = job['stage'] in ('animate','continue') and job['shot_id'] in end_next
        queue = queue_snapshot(jobs, history, insts, st.health, readiness,
                               compatibility=lambda job, inst: st.job_compatible(job, inst, cached_only=True))
        return {'jobs': jobs, 'job_counts': counts, 'render_queue': queue}

    def queue_poll(self):
        data = self.queue_state()
        jobs = data.pop('jobs')
        # Limit the live view to titles for the rows it can display.
        labels = {}
        for table, key, field in (('shots', 'shot_id', 'seq'), ('assets', 'asset_id', 'name')):
            ids = sorted({row[key] for row in jobs if row.get(key) is not None})
            marks = ','.join('?' for _ in ids)
            labels[table] = {row['id']: row[field] for row in self.st.db.q(
                f'SELECT id,{field} FROM {table} WHERE id IN ({marks})', ids)} if ids else {}
        labels['servers'] = {row['id']: row['name'] for row in self.st.db.q('SELECT id,name FROM instances')}
        for rows in data['render_queue'].values():
            for row in rows:
                row['snapshot_json'] = bool(row.get('snapshot_json'))
                for field in ('creative_json', 'models_json'):
                    row.pop(field, None)
        data['queue_labels'] = labels
        return data

    def state_etag(self, queue_only=False):
        st, db = self.st, self.st.db
        with db.lock:
            changes = db.conn.total_changes - (0 if queue_only else db.progress_writes)
            version = db.conn.execute('PRAGMA data_version').fetchone()[0]
            active = db.one("SELECT id FROM jobs WHERE status='running' LIMIT 1") if queue_only else None
        # Full state includes filesystem availability and transient tool state.
        # Refresh periodically even if nothing wrote to the project database.
        interval = 4 if active else 60
        health = [(key, value.get('ok'), value.get('info')) for key, value in sorted(st.health.copy().items())]
        shared = st.projects.configuration_revision if st.projects else None
        value = (st.state_session, changes, version, shared, int(time.time() / interval), health, st.alignment.setup_state, (st.data/'.keep-rendering').is_file())
        return '"' + hashlib.sha256(repr(value).encode()).hexdigest() + '"'

    def state(self, _=None):
        st, db = self.st, self.st.db
        takes, asset_takes, active, asset_active = {}, {}, {}, {}
        voice_video = {j['id']:json.loads(j['snapshot_json']).get('params',{}).get('voice_video') for j in db.q("SELECT id,snapshot_json FROM jobs WHERE snapshot_json IS NOT NULL")}
        for t in db.q("SELECT * FROM takes ORDER BY id"):
            t["url"] = self.media(t["path"])
            if voice_video.get(t.get('job_id')):
                t['voice_video'] = voice_video[t['job_id']]
            if t["stage"] == "sheet":
                asset_takes.setdefault(t["asset_id"], []).append(t)
            else:
                takes.setdefault(t["shot_id"], []).append(t)
        voice_active = {}
        for j in db.q("SELECT shot_id, asset_id, stage, status FROM jobs WHERE status IN ('pending','running')"):
            if j["stage"] == "voice_design":
                voice_active.setdefault(j["asset_id"], []).append(j["status"])
            elif j["stage"] == "sheet":
                asset_active.setdefault(j["asset_id"], []).append(j["status"])
            else:
                active.setdefault(j["shot_id"], {}).setdefault(j["stage"], []).append(j["status"])
        vtakes, vjobs = {}, {}
        for t in db.q("SELECT * FROM voice_takes ORDER BY id"):
            t["url"] = self.media(t["path"])
            vtakes.setdefault((t["shot_id"], norm_text(t["text"]), (t.get("speaker") or "").casefold()), []).append(t)
        voice_jobs = db.q("SELECT * FROM jobs WHERE stage='voice' AND status IN ('pending','running')")
        for j in voice_jobs:
            vjobs.setdefault((j["shot_id"], norm_text(j["note"]), (j["speaker"] or "").casefold(), j["segment"]), []).append(j["status"])
        shots = []
        voice_recovery_rows,_ = recovery_preview(st)
        voice_suggestions={row["id"]:row["entries"] for row in voice_recovery_rows}
        for s in st.ordered_shots():
            s.pop('script_source', None)
            lines = st.shot_lines(s)
            for l in lines:
                l["url"] = self.media(l["audio"])
                key = (s["id"], norm_text(l["text"]), l['speaker'].casefold())
                exact = [t for rows in vtakes.values() for t in rows if t['shot_id']==s['id'] and t.get('line_id')==l['line_id'] and t.get('character_id')==l['character_id'] and spoken_key(t['text'])==spoken_key(l['text'])]
                legacy = [t for t in vtakes.get(key,[]) if not t.get('line_id') and spoken_key(t['text'])==spoken_key(l['text']) and t.get('line_index') in (None,l['index']) and t.get('character_id') in (None,l['character_id'])]
                l['takes'] = sorted(exact+legacy,key=lambda t:t['id'])
                character=st.line_character(l)
                l['character_name']=character['name'] if character else None
                l['pending'] = [j['status'] for j in voice_jobs if j['shot_id']==s['id'] and ((j.get('voice_line_id')==l['line_id'] and j.get('voice_character_id')==l['character_id'] and spoken_key(j['note'])==spoken_key(l['text']) and (j.get('voice_direction') is None or j['voice_direction']==l.get('direction',''))) or (not j.get('voice_line_id') and j['segment']==l['index'] and norm_text(j['note'])==key[1] and (not j.get('speaker') or j['speaker'].casefold()==l['speaker'].casefold())))]
            from shot_voices import edit_token
            s['voice_token']=edit_token(st,s)
            s['voice_suggestion']=voice_suggestions.get(s['id'],[])
            s["lines"] = lines
            s["bubbles"] = manga.parse(s)
            s["continuation"] = st.sections.state(s)
            chain = st.chain(s)
            covered = st.chain_seconds(chain) if chain else 0.0
            s.update(asset_ids=json.loads(s["asset_ids"] or "[]"), takes=takes.get(s["id"], []), active=active.get(s["id"], {}),
                     audio_url=self.media(s["audio"]), frames=st.frames_for(s["duration"]),
                     cont=[t["id"] for t in chain[1:]], covered=round(covered, 2),
                     remaining=round(max(0.0, float(s["duration"]) + float(s.get("trim_in") or 0) - covered), 2) if chain else None,
                     draft_in_use=any(t.get("draft") for t in chain))
            shots.append(s)
        clips = {}
        for c in db.q("SELECT * FROM voice_clips ORDER BY id"):
            c["url"] = self.media(c["path"])
            c["available"] = st.abs(c["path"]).is_file()
            clips.setdefault(c["asset_id"], []).append(c)
        assets = db.q("SELECT * FROM assets ORDER BY kind, name")
        for a in assets:
            a.update(url=self.media(a["image"]), sheets=asset_takes.get(a["id"], []), active=asset_active.get(a["id"], []),
                     clips=clips.get(a["id"], []), voice_active=voice_active.get(a["id"], []))
        insts = st.instances()
        for i in insts:
            h = st.health.get(i["id"])
            i["health"] = {"ok": h["ok"], "info": h["info"]} if h else None
        wfs = {}
        for key in WF_KEYS:
            w = st.wf_row(key)
            if w:
                wf = json.loads(w["json"])
                wfs[key] = {"name": w["name"], "fps": w["fps"], "max_frames": w["max_frames"], "nodes": len(wf),
                            "slots": detect_slots(wf, key), "problems": check_workflow(wf, key)}
            else:
                wfs[key] = None
        queue_data = self.queue_state(insts)
        jobs, counts, queue = (queue_data[key] for key in ('jobs', 'job_counts', 'render_queue'))
        production = st.production.snapshot(shots)
        timing = {row['id']: row for row in timeline_layout(shots, int(st.settings()['out_fps']))}
        for shot in shots:
            shot['timeline_start'] = timing[shot['id']]['start']
            shot['timeline_duration'] = timing[shot['id']]['duration']
            shot['timeline_frames'] = timing[shot['id']]['frames']
        A = dict(st.assembly, log=list(st.assembly["log"]))
        A["output_url"] = self.media(A.get("output"))
        S = st.settings()
        starters = {k: list(v.keys()) for k, v in STARTERS.items()}
        cues = st.soundtrack.cues()
        S["max_clip_seconds"] = round((st.max_frames() - 1) / float(st.wf_fps()), 2)
        try:build=json.loads((Path(__file__).parent/'build_info.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):build={'version':VERSION,'commit':'development','branch':'local source'}
        return {"project_id":hashlib.sha256(str(st.data).encode()).hexdigest()[:24],"build":build,"alignment":st.alignment.state(),"settings": S, "tts_languages": TTS_LANGUAGES, "voice_sample": VOICE_SAMPLE, "shots": shots, "assets": assets, "instances": insts, "workflows": wfs, "starters": starters,
                "jobs": jobs, "job_counts": counts, "assembly": A, "tools": st.tools(), "music_url": self.media(S.get("music")),
                "version": VERSION, "platform": sys.platform,
                "project_name": st.projects.name if st.projects else None, "background_rendering": (st.data/".keep-rendering").is_file(), "production": production, "render_queue": queue,
                "audio_cues": [dict(c, url=self.media(c['path'])) for c in cues],
                "audio_exports": [dict(e, url=self.media(e['output'])) for e in st.soundtrack.export_states(S, cues)],
                "export_jobs": st.exports.rows() if hasattr(st,'exports') else [], "finishing": st.finishing.state(), "editor": st.editor.state(), "creative": st.creative.state(), "voice_recovery_count": len(voice_recovery_rows)}

    # ---- settings / servers / workflows / tools
    def settings(self, b):
        vals = dict(b.get("settings") or {})
        if "video_min_free_vram" in vals:
            headroom = float(vals["video_min_free_vram"])
            if not math.isfinite(headroom) or headroom < 0:
                raise ValueError("Minimum free GPU memory must be zero or greater.")
            vals["video_min_free_vram"] = headroom
        if "tts_language" in vals:
            vals["tts_language"] = norm_choice(vals["tts_language"], TTS_LANGUAGES, "Auto")
        if "tts_model" in vals:
            vals["tts_model"] = norm_choice(vals["tts_model"], ["0.6B", "1.7B"], "1.7B")
        if self.st.projects:
            from project_workspace import SHARED_SETTINGS
            shared = {key:str(vals.pop(key)) for key in SHARED_SETTINGS.intersection(vals)}
            if shared:
                self.st.projects.change_configuration(lambda config: config['settings'].update(shared))
        for k, v in vals.items():
            self.st.db.x("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (k, str(v)))
        return {"ok": True}

    def instance_save(self, b):
        roles = ",".join(r for r in b.get("roles", []) if r in JOB_STAGES)
        url = b["url"].strip().rstrip("/")
        if not url.startswith("http"):
            url = "http://" + url
        if self.st.projects:
            self.st.projects.save_server(b.get('id'), dict(name=b['name'], url=url, roles=roles, enabled=int(bool(b.get('enabled', True)))))
        elif b.get("id"):
            self.st.db.x("UPDATE instances SET name=?, url=?, roles=?, enabled=? WHERE id=?",
                         (b["name"], url, roles, 1 if b.get("enabled", True) else 0, b["id"]))
        else:
            self.st.db.x("INSERT INTO instances(name,url,roles,enabled) VALUES(?,?,?,?)", (b["name"], url, roles,1 if b.get("enabled",True) else 0))
        if hasattr(self.st, "worker"): self.st.worker.last_health = 0
        return {"ok": True}

    def instance_delete(self, b):
        if self.st.projects:
            self.st.projects.save_server(b['id'], None)
        else:
            self.st.db.x("DELETE FROM instances WHERE id=?", (b["id"],))
        return {"ok": True}

    def instance_check(self, _):
        self.st.tools(refresh=True)
        self.st.worker.refresh_health()
        time.sleep(4.5)
        return {"ok": True}

    def workflow(self, b):
        key = b["stage"]
        if key not in WF_KEYS:
            raise ValueError("Unknown workflow slot")
        if b.get("remove"):
            if self.st.projects:
                self.st.projects.change_configuration(lambda config: config['workflows'].pop(key, None))
            else:
                self.st.db.x("DELETE FROM workflows WHERE stage=?", (key,))
            return {"ok": True}
        if b.get("starter"):
            s = STARTERS[key][b["starter"]]
            wf, name, fps, maxf = s["wf"], b["starter"], s["fps"], s["max_frames"]
        else:
            wf = json.loads(base64.b64decode(b["file"]["data"].split(",")[-1]))
            name, fps, maxf = b["file"].get("name", "workflow.json"), int(b.get("fps") or 16), int(b.get("max_frames") or 81)
        problems = check_workflow(wf, key)
        if problems:
            return {"ok": False, "problems": problems}
        row = dict(stage=key, json=json.dumps(wf), name=name, fps=fps, max_frames=maxf, updated=time.time())
        if self.st.projects:
            self.st.projects.change_configuration(lambda config: config['workflows'].update({key:row}))
        else:
            self.st.db.x("INSERT OR REPLACE INTO workflows(stage,json,name,fps,max_frames,updated) VALUES(?,?,?,?,?,?)",
                         tuple(row.values()))
        return {"ok": True}

    def workflow_meta(self, b):
        fps, frames, stage = int(b["fps"]), int(b["max_frames"]), b["stage"]
        if self.st.projects:
            def update(config):
                if stage not in config['workflows']:
                    raise ValueError('Configure this workflow before changing its timing.')
                config['workflows'][stage].update(fps=fps, max_frames=frames)
            self.st.projects.change_configuration(update)
        else:
            self.st.db.x("UPDATE workflows SET fps=?, max_frames=? WHERE stage=?", (fps, frames, stage))
        return {"ok": True}

    def shot_voices(self, body):
        from shot_voices import save_and_generate
        return save_and_generate(self,body)

    def voice_recover(self, body):
        return recover_dialogue(self.st, body)

    def voices(self, _):
        return {"voices": list_voices()}

    def reveal(self, b):
        p = (self.st.data / b["path"]).resolve() if b.get("path") else self.st.data
        if p != self.st.data and self.st.data not in p.parents:
            raise RuntimeError("Can only show files inside the project folder.")
        if sys.platform == "darwin":
            subprocess.Popen(["open", "-R", str(p)] if p.is_file() else ["open", str(p)])
        elif sys.platform == "win32":
            os.startfile(str(p))
        return {"ok": True}

    def background_rendering(self, body):
        marker = self.st.data / '.keep-rendering'
        if body.get('enabled'):
            marker.touch()
        else:
            marker.unlink(missing_ok=True)
        return {'ok':True}

    def quit(self, _):
        self.st.alignment.shutdown()
        self.st.exports.shutdown()
        self.st.shot_previews.shutdown()
        threading.Timer(0.5, lambda: os._exit(0)).start()
        return {"ok": True}

    # ---- script -> shot list
    def script_draft(self, b):
        st, text = self.st, b.get('script', '')
        blocks = script_blocks(text, st.default_duration())
        if b.get('preview'):
            return {'ok': True, 'rows': blocks, 'token': script_preview_token(st, text)}
        selected = b.get('indices')
        if selected is not None:
            if (not isinstance(selected, list) or any(type(index) is not int or not 0 <= index < len(blocks) for index in selected)
                    or len(selected) != len(set(selected))):
                raise ValueError('Select valid shots from the preview.')
            blocks = [blocks[index] for index in sorted(selected)]
        if not blocks:
            return {'ok': True, 'made': 0}
        with st.db.transaction():
            if b.get('token') is not None and b['token'] != script_preview_token(st, text):
                raise ValueError('The script or shot settings changed. Preview the shots again.')
            st.editor.checkpoint('Before creating shots from script')
            prepare_script_cast(self, blocks)
            for block in blocks:
                apply_script_block(self, block)
            self.settings({'settings': {'script': text}})
        return {'ok': True, 'made': len(blocks)}

    # ---- cast & locations
    def asset_save(self, b):
        db = self.st.db
        img = self.st.save_upload(b["file"]) if b.get("file") else None
        vals = (b.get("kind", "character"), b.get("name") or "Unnamed", b.get("description", ""), b.get("voice", ""),
                b.get("lora", ""), float(b.get("lora_strength") or 1.0))
        if b.get("id"):
            db.x("UPDATE assets SET kind=?, name=?, description=?, voice=?, lora=?, lora_strength=? WHERE id=?", vals + (b["id"],))
            if img:
                db.x("UPDATE assets SET image=? WHERE id=?", (img, b["id"]))
            aid = b["id"]
        else:
            aid = db.x("INSERT INTO assets(kind,name,description,voice,lora,lora_strength,image) VALUES(?,?,?,?,?,?,?)", vals + (img,))
        for f in ("voice_desc", "voice_sample"):
            if f in b:
                db.x(f"UPDATE assets SET {f}=? WHERE id=?", ((b[f] or "").strip(), aid))
        return {"ok": True, "id": aid}

    def asset_delete(self, b):
        self.st.creative.delete_asset(b["id"])
        for c in self.st.db.q("SELECT id FROM voice_clips WHERE asset_id=?", (b["id"],)):
            self.clip_delete({"id": c["id"]})
        self.st.db.x("DELETE FROM takes WHERE asset_id=? AND stage='sheet'", (b["id"],))
        self.st.db.x(f"UPDATE jobs SET status='cancelled', finished=? WHERE asset_id=? AND stage IN {_ASSET_SQL} AND status IN ('pending','running')",
                     (time.time(), b["id"]))
        self.st.db.x("DELETE FROM assets WHERE id=?", (b["id"],))
        return {"ok": True}

    def asset_use_sheet(self, b):
        t = self.st.db.one("SELECT * FROM takes WHERE id=?", (b["take_id"],))
        self.st.db.x("UPDATE assets SET image=? WHERE id=?", (t["path"], t["asset_id"]))
        return {"ok": True}

    # ---- shots
    def shot_save(self, b):
        st = self.st
        vals = {k: b[k] for k in SHOT_FIELDS if k in b}
        from voice_video import validate as validate_voice_video
        validate_voice_video(vals)
        if "duration" in vals:
            vals["duration"] = max(0.5, float(vals["duration"] or st.default_duration()))
        elif not b.get("id"):
            vals["duration"] = st.default_duration()
        for f in ("ref_prev", "end_next"):
            if f in vals:
                vals[f] = 1 if vals[f] in TRUE else 0
        if "asset_ids" in b:
            vals["asset_ids"] = json.dumps([int(x) for x in b["asset_ids"]][:8])
        if "bubbles" in b:
            current = st.db.one("SELECT * FROM shots WHERE id=?", (b["id"],)) if b.get("id") else {"asset_ids": "[]"}
            if not current:
                raise ValueError("That shot was deleted.")
            tagged = json.loads(vals.get("asset_ids") or current.get("asset_ids") or "[]")
            bubbles, add = manga.validate(st, dict(current, asset_ids=tagged), b["bubbles"])
            vals["bubbles"] = json.dumps(bubbles)
            if add:
                vals["asset_ids"] = json.dumps(tagged + add)
        if b.get("audio_file"):
            vals["audio"] = st.save_upload(b["audio_file"], "voices")
            vals["audio_mode"] = "file"
            secs = media_seconds(st.abs(vals["audio"]))
            cur = float(vals.get("duration") or (st.db.one("SELECT duration FROM shots WHERE id=?", (b.get("id"),)) or {}).get("duration") or st.default_duration())
            if secs + 0.4 > cur:
                vals["duration"] = round(secs + 0.4, 1)
        if b.get("clear_audio"):
            vals["audio"], vals["audio_mode"] = None, "lines"
        if b.get("id"):
            if vals:
                st.db.x(f"UPDATE shots SET {', '.join(k + '=?' for k in vals)} WHERE id=?", list(vals.values()) + [b["id"]])
            if b.get("clear_audio") or "dialogue" in vals or "asset_ids" in vals:
                st.rebuild_audio(b["id"])
            return {"ok": True, "id": b["id"]}
        mx = st.db.one("SELECT COALESCE(MAX(seq),0) m FROM shots")["m"]
        vals["seq"] = float(b["after_seq"]) + 0.5 if b.get("after_seq") is not None else mx + 1
        sid = st.db.x(f"INSERT INTO shots({', '.join(vals)}) VALUES({','.join('?' * len(vals))})", list(vals.values()))
        st.renumber()
        return {"ok": True, "id": sid}

    def shot_delete(self, b):
        db = self.st.db
        for sid in b["ids"]:
            self.st.db.x("DELETE FROM shot_sections WHERE shot_id=?",(sid,))
            self.st.db.x("DELETE FROM section_takes WHERE take_id IN (SELECT id FROM takes WHERE shot_id=?)",(sid,))
            self.st.creative.delete_shot(sid)
            db.x("DELETE FROM production_approvals WHERE shot_id=?", (sid,))
            db.x("DELETE FROM shots WHERE id=?", (sid,))
            db.x(f"DELETE FROM takes WHERE shot_id=? AND stage NOT IN {_ASSET_SQL}", (sid,))
            db.x("DELETE FROM voice_takes WHERE shot_id=?", (sid,))
            db.x(f"UPDATE jobs SET status='cancelled', finished=? WHERE shot_id=? AND stage NOT IN {_ASSET_SQL} AND status IN ('pending','running')",
                 (time.time(), sid))
        self.st.renumber()
        return {"ok": True}

    def shot_move(self, b):
        shots = self.st.ordered_shots()
        idx = next(i for i, s in enumerate(shots) if s["id"] == b["id"])
        j = idx + (1 if b["dir"] > 0 else -1)
        if 0 <= j < len(shots):
            shots[idx], shots[j] = shots[j], shots[idx]
            for i, s in enumerate(shots, 1):
                self.st.db.x("UPDATE shots SET seq=? WHERE id=?", (i, s["id"]))
        return {"ok": True}

    def csv_import(self, b):
        st = self.st
        raw = base64.b64decode(b["file"]["data"].split(",")[-1]).decode("utf-8-sig")
        dialect = "excel-tab" if raw.count("\t") > raw.count(",") else "excel"
        rows = list(csv.DictReader(io.StringIO(raw), dialect=dialect))
        names = {a["name"].strip().lower(): a["id"] for a in st.db.q("SELECT id,name FROM assets")}
        mx = st.db.one("SELECT COALESCE(MAX(seq),0) m FROM shots")["m"]
        for n, r in enumerate(rows, 1):
            r = {(k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in r.items()}
            ids = []
            for nm in re.split(r"[;|,]", r.get("cast", "") or r.get("characters", "")):
                nm = nm.strip()
                if nm:
                    if nm.lower() not in names:
                        names[nm.lower()] = st.db.x("INSERT INTO assets(kind,name) VALUES('character',?)", (nm,))
                    ids.append(names[nm.lower()])
            try:
                seq = mx + (float(r.get("seq") or r.get("shot") or 0) or n)
            except ValueError:
                seq = mx + n
            kind = r.get("kind", "motion").lower()
            rt = r.get("retime", "hold").lower()
            try:
                dur = float(r.get("duration") or r.get("length") or st.default_duration())
            except ValueError:
                dur = st.default_duration()
            st.db.x("INSERT INTO shots(seq,scene,kind,asset_ids,keyframe_prompt,motion_prompt,camera,dialogue,duration,retime,ref_prev,end_next,notes) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (seq, r.get("scene", ""), kind if kind in ("motion", "still") else "motion", json.dumps(ids),
                     r.get("keyframe_prompt") or r.get("description", ""), r.get("motion_prompt", ""), r.get("camera", ""),
                     r.get("dialogue", ""), dur, rt if rt in ("hold", "blend", "rife") else "hold",
                     1 if r.get("ref_prev", "").lower() in TRUE else 0, 1 if r.get("end_next", "").lower() in TRUE else 0, r.get("notes", "")))
        st.renumber()
        return {"ok": True, "imported": len(rows)}

    def csv_export(self, _=None):
        st = self.st
        names = {a["id"]: a["name"] for a in st.db.q("SELECT id,name FROM assets")}
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=CSV_FIELDS)
        w.writeheader()
        for s in st.ordered_shots():
            row = {k: s.get(k, "") for k in CSV_FIELDS if k != "cast"}
            row.update(cast=";".join(names.get(i, "") for i in json.loads(s["asset_ids"] or "[]")), seq=int(s["seq"]))
            w.writerow(row)
        return buf.getvalue()

    # ---- dialogue audio: voice clips, per-line audio, temporary voices
    def clips_upload(self, b):
        st = self.st
        a = st.db.one("SELECT * FROM assets WHERE id=?", (b["asset_id"],))
        if not a:
            raise RuntimeError("That cast member was deleted.")
        has_ref = bool(st.db.one("SELECT id FROM voice_clips WHERE asset_id=? AND is_ref=1", (a["id"],)))
        as_ref = bool(b.get("as_ref"))
        if as_ref:
            st.db.x("UPDATE voice_clips SET is_ref=0 WHERE asset_id=?", (a["id"],))
        added = 0
        for f in b["files"]:
            rel = st.save_upload(f, f"cast/voice_{a['id']:04d}")
            transcript = b["transcript"] if (as_ref and b.get("transcript") is not None) else transcript_from_name(f.get("name", ""), a["name"])
            ref = 1 if (as_ref and not added) or (not as_ref and not has_ref and not added) else 0
            st.db.x("INSERT INTO voice_clips(asset_id,path,name,transcript,seconds,is_ref,created) VALUES(?,?,?,?,?,?,?)",
                    (a["id"], rel, f.get("name", ""), transcript, round(media_seconds(st.abs(rel)), 2), ref, time.time()))
            added += 1
        matched = self.match({"asset_id": a["id"]})["matched"] if b.get("auto_match", True) else 0
        return {"ok": True, "added": added, "matched": matched}

    def clip_update(self, b):
        st = self.st
        c = st.db.one("SELECT * FROM voice_clips WHERE id=?", (b["id"],))
        if "transcript" in b:
            st.db.x("UPDATE voice_clips SET transcript=? WHERE id=?", (b["transcript"], c["id"]))
        if b.get("is_ref"):
            st.db.x("UPDATE voice_clips SET is_ref=(id=?) WHERE asset_id=?", (c["id"], c["asset_id"]))
        return {"ok": True}

    def clip_delete(self, b):
        st = self.st
        c = st.db.one("SELECT * FROM voice_clips WHERE id=?", (b["id"],))
        if not c:
            return {"ok": True}
        st.db.x("DELETE FROM voice_clips WHERE id=?", (c["id"],))
        # Retain media for recovery history and existing exports.
        for sh in st.db.q("SELECT id FROM shots WHERE line_audio LIKE ?", (f"%{c['path']}%",)):
            st.rebuild_audio(sh["id"])
        if c["is_ref"]:
            nxt = st.db.one("SELECT id FROM voice_clips WHERE asset_id=? ORDER BY id LIMIT 1", (c["asset_id"],))
            if nxt:
                st.db.x("UPDATE voice_clips SET is_ref=1 WHERE id=?", (nxt["id"],))
        return {"ok": True}

    def match(self, b):
        """Give each dialogue line without audio the voice clip whose transcript matches it best."""
        st = self.st
        threshold = float(b.get("threshold") or 0.72)
        clips = st.db.q("SELECT * FROM voice_clips")
        if b.get("asset_id"):
            clips = [c for c in clips if c["asset_id"] == b["asset_id"]]
        ids = b.get("shot_ids") or [x["id"] for x in st.db.q("SELECT id FROM shots WHERE audio_mode='lines'")]
        matched = 0
        for sid in ids:
            shot = st.db.one("SELECT * FROM shots WHERE id=?", (sid,))
            if not shot or shot["audio_mode"] == "file" or not (shot["dialogue"] or "").strip():
                continue
            lines, changed = st.shot_lines(shot), False
            for l in lines:
                if l["audio"] and not b.get("replace"):
                    continue
                sp = st.line_character(l)
                pool = [c for c in clips if c["asset_id"] == sp["id"]] if sp else []
                best = max(pool, key=lambda c: similarity(c["transcript"], l["text"]), default=None)
                if best and similarity(best["transcript"], l["text"]) >= threshold and st.abs(best["path"]).exists():
                    l.update(audio=best["path"], src="clip", clip_id=best["id"])
                    changed, matched = True, matched + 1
            if changed:
                st.save_lines(sid, lines)
                st.rebuild_audio(sid)
        return {"ok": True, "matched": matched}

    def line_set(self, b):
        """Set one line's audio: src = clip | upload | temp | none."""
        st = self.st
        shot = st.db.one("SELECT * FROM shots WHERE id=?", (b["shot_id"],))
        if not shot:
            raise ValueError('Shot not found')
        lines = st.shot_lines(shot)
        index = int(b["index"])
        if not 0 <= index < len(lines):raise ValueError("Dialogue line not found")
        l = lines[index]
        if b.get('line_id') and b['line_id']!=l['line_id']:raise ValueError('Dialogue order changed. Reopen this shot’s voice editor.')
        src = b["src"]
        if src == "none":
            l.update(audio=None, src=None, clip_id=None)
        elif src == "clip":
            c = st.db.one("SELECT * FROM voice_clips WHERE id=?", (b["clip_id"],))
            if not c or not st.abs(c["path"]).is_file():raise ValueError("Voice clip file is missing")
            character=st.line_character(l)
            if not character or c['asset_id'] != character['id']:raise ValueError('This recording belongs to another character. Choose the line character first.')
            l.update(audio=c["path"], src="clip", clip_id=c["id"])
        elif src == "take":
            t = st.db.one("SELECT * FROM voice_takes WHERE id=?", (b["take_id"],))
            if not t or t['shot_id'] != shot['id'] or spoken_key(t['text']) != spoken_key(l['text']) or (t.get('line_id') != l['line_id'] if t.get('line_id') else (t.get('speaker') or '').casefold() != l['speaker'].casefold() or t.get('line_index') not in (None,l['index'])) or (t.get('character_id') is not None and t['character_id'] != l['character_id']):
                raise ValueError('This voice take belongs to another dialogue line.')
            l.update(audio=t["path"], src="tts", clip_id=None)
        elif src == "upload":
            l.update(audio=st.save_upload(b["file"], "voices/lines"), src="upload", clip_id=None)
        elif src == "temp":
            sp = st.line_character(l)
            l.update(audio=st.say_line(l["text"], (sp or {}).get("voice") or st.settings().get("default_voice") or ""),
                     src="temp", clip_id=None)
        else:
            raise ValueError('Unknown audio source')
        if shot['audio_mode'] == 'file':
            st.db.x("UPDATE shots SET audio_mode='lines',audio=NULL WHERE id=?", (shot['id'],))
        st.save_lines(shot["id"], lines)
        st.rebuild_audio(shot["id"])
        return {"ok": True}

    def line_direction(self, b):
        st = self.st
        shot = st.db.one("SELECT * FROM shots WHERE id=?", (b["shot_id"],))
        lines = st.shot_lines(shot)
        lines[int(b["index"])]["direction"] = (b.get("direction") or "").strip()
        st.save_lines(shot["id"], lines)
        return {"ok": True}

    def voice_take_delete(self, b):
        st = self.st
        t = st.db.one("SELECT * FROM voice_takes WHERE id=?", (b["id"],))
        if not t:
            return {"ok": True}
        st.db.x("DELETE FROM voice_takes WHERE id=?", (t["id"],))
        shot = st.db.one("SELECT * FROM shots WHERE id=?", (t["shot_id"],))
        if shot and t["path"] in (shot.get("line_audio") or ""):
            lines = st.shot_lines(shot)
            for l in lines:
                if l["audio"] == t["path"]:
                    l.update(audio=None, src=None, clip_id=None)
            st.save_lines(shot["id"], lines)
        # Retain media for recovery history and existing exports.
        if shot:
            st.rebuild_audio(shot["id"])
        return {"ok": True}

    def voice(self, b):
        """Temporary voices (macOS say) for every line that has no audio yet, or every line with replace=true."""
        st = self.st
        if not st.tools()["say"]:
            raise RuntimeError("Temporary voices use the macOS 'say' command (Mac only).")
        if not st.tools()["ffmpeg"]:
            raise RuntimeError("ffmpeg is needed: brew install ffmpeg")
        default = st.settings().get("default_voice") or ""
        made, skipped = 0, 0
        for sid in b["shot_ids"]:
            s = st.db.one("SELECT * FROM shots WHERE id=?", (sid,))
            if not s or not (s["dialogue"] or "").strip():
                continue
            if s["audio_mode"] == "file":
                skipped += 1
                continue
            lines, changed = st.shot_lines(s), False
            for l in lines:
                if l["audio"] and not b.get("replace"):
                    continue
                sp = st.line_character(l)
                l.update(audio=st.say_line(l["text"], (sp or {}).get("voice") or default), src="temp", clip_id=None)
                changed, made = True, made + 1
            if changed:
                st.save_lines(sid, lines)
                st.rebuild_audio(sid, fit=b.get("fit", True))
        return {"ok": True, "made": made, "skipped": skipped}

    # ---- generation / takes / jobs
    def keyframe_review(self, body):
        st = self.st
        shot_id = int(body['shot_id'])
        order = body.get('reference_order')
        if order is not None and (not isinstance(order, list) or any(not isinstance(item, str) for item in order)):
            raise ValueError('Invalid keyframe reference order.')
        job = {'id': 0, 'shot_id': shot_id, 'seed': 0, 'stage': 'keyframe'}
        if order is not None:
            job['reference_order'] = order
        key, params = Worker(st).compose(job)
        template, controls, recipe = st.keyframe_template(shot_id)
        problems = check_workflow(template, key)
        if problems:
            raise ValueError(' '.join(problems))
        if recipe:
            st.creative.keyframe_conditioning(shot_id, recipe, params)
        params['images'] = {slot: path if isinstance(path, str) else st.rel(path)
                            for slot, path in params['files'].items()}
        graph = build_workflow(template, key, params)
        for control, value in controls.items():
            node, field = control.split('.', 1)
            graph[node]['inputs'][field] = value
        slots = detect_slots(graph, key)

        def prompt_values(kind):
            return [str(graph[node]['inputs'].get('text', graph[node]['inputs'].get('prompt', ''))) for node in slots[kind]]

        loras = [{'name': node['inputs']['lora_name'], 'strength': node['inputs'].get('strength_model')}
                 for node in graph.values() if node.get('inputs', {}).get('lora_name')]
        stamps = {slot: st.production.file_stamp(path) for slot, path in params['images'].items()}
        token = hashlib.sha256(json.dumps([graph, stamps], sort_keys=True).encode()).hexdigest()
        return {'ok': True, 'token': token, 'positive': prompt_values('prompt'), 'negative': prompt_values('negative'),
                'images': {slot: self.media(path) for slot, path in params['images'].items()}, 'loras': loras,
                'references': params['review_references'], 'omitted_loras': params['omitted_loras'],
                'bubbles': params.get('bubbles', []), 'bubble_source': manga.effective(st, st.db.one('SELECT * FROM shots WHERE id=?', (shot_id,)))[1]}

    def generate(self, b):
        with self.st.production.lock:
            if b.get('keyframe_review_token'):
                if b['stage'] != 'keyframe' or len(b.get('shot_ids', [])) != 1 or b.get('recipe_id'):
                    raise ValueError('Review a single keyframe using its assigned recipe.')
                sid = b['shot_ids'][0]
                review = self.keyframe_review({'shot_id': sid, 'reference_order': b.get('reference_order')})
                if review['token'] != b['keyframe_review_token']:
                    raise ValueError('The keyframe inputs changed. Review them again before generating.')
                if b.get('reference_order') is not None:
                    self.st.db.x('UPDATE shots SET keyframe_reference_order=? WHERE id=?',
                                 (json.dumps(b['reference_order']), sid))
            from voice_video import validate as validate_voice_video
            validate_voice_video({k: b[k] for k in ('voice_quality',) if k in b})
            before=self.st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
            try:
                result=self._generate_request(b)
                for job in self.st.db.q("SELECT * FROM jobs WHERE id>? AND status='pending'",(before,)):
                    if b.get('voice_quality'):
                        self.st.db.x('UPDATE jobs SET voice_quality=? WHERE id=?',(b['voice_quality'],job['id']))
                        job['voice_quality'] = b['voice_quality']
                    self.st.snapshots.capture(job)
                audio_jobs=[self.st.snapshots.meta(job) for job in self.st.db.q("SELECT * FROM jobs WHERE id>? AND status='pending'",(before,)) if self.st.snapshots.meta(job).get('clip_audio')]
                if b.get('require_voice_animation') and (len(audio_jobs) != result.get('queued',0) or any(not meta.get('audio_conditioned') for meta in audio_jobs)):
                    raise ValueError('Voice-driven animation is unavailable. Configure a ready audio-conditioned workflow before generating this performance.')
                result['audio_conditioned']=sum(bool(meta.get('audio_conditioned')) for meta in audio_jobs)
                result['audio_included']=len(audio_jobs)
                result['audio_notes']=list(dict.fromkeys(meta['audio_note'] for meta in audio_jobs if meta.get('audio_note')))
                return result
            except Exception:
                for job in self.st.db.q("SELECT id FROM jobs WHERE id>? AND status='pending'",(before,)):
                    for table,field in [('creative_runs','job_id'),('production_inputs','job_id'),('jobs','id')]:
                        self.st.db.x('DELETE FROM '+table+' WHERE '+field+'=?',(job['id'],))
                raise

    def _generate_request(self, b):
        if b["stage"] == "continue":
            return self.st.sections.generate(b)
        if self.st.creative.wants_recipe(b):
            with self.st.production.lock:
                return self.st.creative.generate(b)
        if b['stage'] == 'keyframe' and any(self.st.creative.direction(sid).get('conditioning') for sid in b.get('shot_ids', [])):
            return {'ok':False,'queued':0,'skipped':[],'error':'Assign a compatible keyframe recipe in Generation lab before using pose/depth/layout conditioning.'}
        voiced_animation=b['stage']=='animate' and bool(b.get('shot_ids')) and all((self.st.db.one('SELECT audio FROM shots WHERE id=?',(sid,)) or {}).get('audio') for sid in b['shot_ids'])
        if not voiced_animation and not self.st.production.ready(b['stage'], b.get('shot_ids', [])):
            return {'ok': False, 'queued': 0, 'skipped': ['No ready server for this workflow. Run Production → Check readiness for details.'],
                    'error': 'No ready server for ' + b['stage']}
        with self.st.production.lock:
            r = self._generate(b)
        takers = [i for i in self.st.instances(enabled_only=True) if b["stage"] in (i["roles"] or "").split(",")]
        if r.get("queued") and not takers:
            r["skipped"] = [f"No server is set to take {b['stage']} jobs. Tick it under Settings, ComfyUI servers."] + r["skipped"]
        return r

    def _generate(self, b):
        st, stage = self.st, b["stage"]
        takes = max(1, min(8, int(b.get("takes", 1))))
        made, skipped = 0, []
        if stage in ("sound_effect", "music_cue"):
            if len(b.get('shot_ids', [])) != 1:
                raise ValueError("Choose one shot for this sound cue.")
            sid = int(b['shot_ids'][0])
            if not st.db.one("SELECT id FROM shots WHERE id=?", (sid,)):
                raise ValueError("The cue's anchor shot was removed.")
            prompt = str(b.get('prompt') or '').strip()[:6000]
            kind = 'music' if stage == 'music_cue' else b.get('kind', 'sfx')
            if not prompt or (stage == 'sound_effect' and kind not in ('sfx','ambience')):
                raise ValueError("Enter a sound prompt and choose Effects or Ambience.")
            duration, offset = float(b.get('duration', 2)), float(b.get('anchor_offset', 0))
            minimum, maximum = (1, 240) if stage == 'music_cue' else (0.1, 120)
            if not (math.isfinite(duration) and minimum <= duration <= maximum and math.isfinite(offset) and 0 <= offset <= 7200):
                raise ValueError("Review the sound duration and cue offset.")
            seed = int(b['seed']) if b.get('seed') is not None else random.randint(1, 2**48)
            if not 0 <= seed <= 2**48:
                raise ValueError("Seed must be between 0 and 281474976710656.")
            tempo = None
            if stage == 'music_cue' and b.get('tempo') not in (None, ''):
                tempo = int(b['tempo'])
                row = st.wf_row(stage)
                if not row or not detect_slots(json.loads(row['json']), stage)['tempo']:
                    raise ValueError('The selected music recipe does not expose tempo.')
                if not 30 <= tempo <= 300:
                    raise ValueError('Enter a tempo from 30 to 300 BPM.')
            spec = dict(tempo=tempo, prompt=prompt, negative=str(b.get('negative') or '')[:6000], duration=duration,
                        name=str(b.get('name') or prompt[:80]).strip()[:160], kind=kind, anchor_offset=offset)
            jid = st.db.x("INSERT INTO jobs(shot_id,stage,status,seed,created,progress,note) VALUES(?,?,'pending',?,?,'waiting',?)",
                          (sid, stage, seed, time.time(), json.dumps(spec)))
            return {"ok": True, "queued": 1, "skipped": [], "job_ids": [jid]}
        if stage == "sheet":
            for aid in b["asset_ids"]:
                for _ in range(takes):
                    st.db.x("INSERT INTO jobs(asset_id,stage,status,seed,created,progress) VALUES(?,?,'pending',?,?,'waiting')",
                            (aid, "sheet", random.randint(1, 2**48), time.time()))
                    made += 1
            return {"ok": True, "queued": made, "skipped": []}
        if stage == "voice_design":
            for aid in b["asset_ids"]:
                a = st.db.one("SELECT * FROM assets WHERE id=?", (aid,))
                if not a:
                    continue
                if not (a.get("voice_desc") or "").strip():
                    skipped.append(f"{a['name']}: write a voice description first.")
                    continue
                for _ in range(takes):
                    st.db.x("INSERT INTO jobs(asset_id,stage,status,seed,created,progress,note) VALUES(?,?,'pending',?,?,'waiting',?)",
                            (aid, "voice_design", random.randint(1, 2**31), time.time(), (a.get("voice_sample") or "").strip() or VOICE_SAMPLE))
                    made += 1
            return {"ok": True, "queued": made, "skipped": skipped}
        if stage == "voice":
            # targets: explicit lines [{shot_id, index}] (always generate) or whole shots (lines without audio,
            # or every line with replace=true)
            targets = []
            for t in b.get("lines") or []:
                targets.append((int(t["shot_id"]), int(t["index"]), True))
            for sid in b.get("shot_ids") or []:
                targets.append((int(sid), None, bool(b.get("replace"))))
            no_voice = set()
            seen = set()
            for sid, idx, force in targets:
                s = st.db.one("SELECT * FROM shots WHERE id=?", (sid,))
                if not s or s["audio_mode"] == "file":
                    continue
                for l in st.shot_lines(s):
                    if idx is not None and l["index"] != idx:
                        continue
                    if l["audio"] and not force:
                        continue
                    target = (sid, l['index'])
                    if target in seen:continue
                    seen.add(target)
                    sp = st.line_character(l)
                    if not b.get('_creative_batch') and st.db.one("SELECT id FROM jobs WHERE shot_id=? AND stage='voice' AND status IN ('pending','running') AND ((voice_line_id=? AND voice_character_id IS ? AND note=? AND voice_direction=?) OR (voice_line_id IS NULL AND segment=? AND note=? AND speaker=? COLLATE NOCASE)) LIMIT 1", (sid,l['line_id'],sp['id'] if sp else None,l['text'],l.get('direction') or '',l['index'],l['text'],l['speaker'])):
                        skipped.append(f"Shot {int(s['seq'])}, line {l['index']+1}: voice generation already queued or running.")
                        continue
                    if not sp or not st.ref_clip(sp["id"]) or not st.abs(st.ref_clip(sp["id"])["path"]).is_file():
                        no_voice.add(l["speaker"] or "A speaker")
                        continue
                    for take_number in range(takes):
                        jid = st.db.x("INSERT INTO jobs(shot_id,stage,status,seed,created,progress,segment,note,speaker,voice_character_id,voice_line_id,voice_ref_path,voice_ref_text,voice_direction) VALUES(?,?,'pending',?,?,'waiting',?,?,?,?,?,?,?,?)",
                                (sid, "voice", random.randint(1, 2**48), time.time(), l["index"], l["text"], l["speaker"], sp["id"], l["line_id"], st.ref_clip(sp["id"])["path"], st.ref_clip(sp["id"])["transcript"] or "", l.get("direction") or ""))
                        if b.get("select_generated") and take_number==0:
                            st.db.x("UPDATE jobs SET voice_autoselect=1,voice_previous_audio=? WHERE id=?",(l.get("audio"),jid))
                        st.production.capture(st.db.one("SELECT * FROM jobs WHERE id=?",(jid,)))
                        made += 1
            skipped += [f"{n} has no reference voice (add one in Cast)" for n in sorted(no_voice)]
            return {"ok": True, "queued": made, "skipped": skipped}
        for sid in b["shot_ids"]:
            s = st.db.one("SELECT * FROM shots WHERE id=?", (sid,))
            if not s:
                continue
            label = f"shot {int(s['seq'])}"
            draft = 1 if (b.get("draft") and stage in ("animate", "continue")) else 0
            segment = None
            if stage == "continue":
                chain = st.chain(s)
                if not chain:
                    skipped.append(f"{label}: animate it first")
                    continue
                segment = int(b["segment"]) if b.get("segment") is not None else len(chain) - 1
                if segment > len(chain) - 1:
                    skipped.append(f"{label}: choose the previous part first")
                    continue
                if float(s["duration"]) + float(s.get("trim_in") or 0) - st.chain_seconds(chain[:segment + 1]) < 1/max(1,st.wf_fps()):
                    skipped.append(f"{label}: already animated for its full length")
                    continue
            if stage != "keyframe" and s["kind"] == "still" and stage != "lipsync" and not (stage == "upscale" and s["clip_take"]):
                skipped.append(f"{label}: still shot")
                continue
            if stage in ("animate", "lipsync") and not st.take_path(s["keyframe_take"]) and not (stage == 'lipsync' and s.get('lip_sync_mode') == 'existing'):
                skipped.append(f"{label}: no approved keyframe")
                continue
            if stage == 'lipsync':
                from voice_video import lip_workflow
                row = b.get('_lip_workflow') or lip_workflow(st, s)
                native_s2v = row and any(n.get('class_type') == 'WanSoundImageToVideo' for n in json.loads(row['json']).values())
                capacity = float(row['max_frames']) / float(row['fps']) if row and row['fps'] else 0
                if native_s2v and float(s['duration']) > capacity:
                    skipped.append(f"{label}: S2V preset supports at most {capacity:.2f}s. Split the shot or upload a longer S2V workflow.")
                    continue
            if stage == "lipsync" and not s["audio"]:
                skipped.append(f"{label}: no dialogue audio")
                continue
            if stage == "upscale" and not st.take_path(s["clip_take"]):
                skipped.append(f"{label}: no approved clip")
                continue
            n = 1 if (stage == "upscale" or b.get("seed")) else takes
            for _ in range(n):
                seed = int(b["seed"]) if b.get("seed") else random.randint(1, 2**48)
                st.db.x("INSERT INTO jobs(shot_id,stage,status,seed,created,progress,segment,draft) VALUES(?,?,'pending',?,?,'waiting',?,?)",
                        (sid, stage, seed, time.time(), segment, draft))
                made += 1
        return {"ok": True, "queued": made, "skipped": skipped}

    def approve(self, b):
        st = self.st
        t = st.db.one("SELECT * FROM takes WHERE id=?", (b["take_id"],))
        if t["stage"] == "continue":
            shot = st.db.one("SELECT * FROM shots WHERE id=?", (t["shot_id"],))
            cont = [x["id"] for x in st.chain(shot)[1:]]
            seg = int(t["segment"] or 0)
            if seg > len(cont):
                raise RuntimeError("Choose a take for the earlier part of this shot first.")
            if seg<len(cont) and cont[seg]!=t["id"]: st.sections.legacy_review(cont[seg+1:])
            # Retain later selections for review; their predecessor signatures become outdated.
            st.db.x("UPDATE shots SET cont_takes=?, final_take=NULL WHERE id=?", (json.dumps(cont[:seg] + [t["id"]] + cont[seg+1:]), t["shot_id"]))
        elif t["stage"] == "lipsync" or t["stage"] == "animate":
            shot=st.db.one("SELECT * FROM shots WHERE id=?",(t["shot_id"],))
            if shot and shot["clip_take"]!=t["id"]: st.sections.legacy_review(json.loads(shot["cont_takes"] or "[]"))
            st.db.x("UPDATE shots SET clip_take=?, final_take=NULL WHERE id=? AND (clip_take IS NULL OR clip_take<>?)",
                    (t["id"], t["shot_id"], t["id"]))
        else:
            self.st.db.x(f"UPDATE shots SET {TAKE_FIELD[t['stage']]}=? WHERE id=?", (t["id"], t["shot_id"]))
        if t['stage'] == 'keyframe':
            st.production.approve(t['shot_id'])
        return {"ok": True}

    def take_delete(self, b):
        st = self.st
        t = st.db.one("SELECT * FROM takes WHERE id=?", (b["take_id"],))
        if t:
            if t["stage"] == "continue":
                shot = st.db.one("SELECT * FROM shots WHERE id=?", (t["shot_id"],))
                cont = json.loads((shot or {}).get("cont_takes") or "[]")
                if t["id"] in cont:
                    st.db.x("UPDATE shots SET cont_takes=?, final_take=NULL WHERE id=?", (json.dumps(cont[:cont.index(t["id"])]), t["shot_id"]))
            if t["stage"] in ("animate", "lipsync"):
                st.db.x("UPDATE shots SET cont_takes='[]', final_take=NULL WHERE id=? AND clip_take=?", (t["shot_id"], t["id"]))
            if t["stage"] in TAKE_FIELD:
                f = TAKE_FIELD[t["stage"]]
                st.db.x(f"UPDATE shots SET {f}=NULL WHERE id=? AND {f}=?", (t["shot_id"], t["id"]))
            st.db.x("DELETE FROM section_takes WHERE take_id=?",(t["id"],))
            st.db.x("DELETE FROM takes WHERE id=?", (t["id"],))
            # Retain media for recovery history and existing exports.
        return {"ok": True}

    def job_action(self, b):
        db, act, now = self.st.db, b["action"], time.time()
        if act == 'repeat':
            return {'ok':True,'job_ids':[self.st.snapshots.repeat(jid) for jid in b['ids']]}
        if act == 'priority':
            priority = max(-10, min(10, int(b.get('priority', 0))))
            for jid in b['ids']:
                db.x("UPDATE jobs SET priority=? WHERE id=? AND status='pending'", (priority, jid))
        elif act == "cancel":
            for jid in b["ids"]:
                db.x("UPDATE jobs SET status='cancelled', finished=? WHERE id=? AND status IN ('pending','running')", (now, jid))
        elif act == "retry":
            for jid in b["ids"]:
                db.x("UPDATE jobs SET prompt_id=NULL, instance_id=NULL WHERE id=? AND status='cancelled'", (jid,))
                db.x("UPDATE jobs SET status='pending', finished=NULL, error=NULL, progress='reconnect or queue' "
                     "WHERE id=? AND status IN ('error','cancelled')", (jid,))
        elif act == "retry_errors":
            db.x("UPDATE jobs SET status='pending', finished=NULL, error=NULL, progress='reconnect or queue' WHERE status='error'")
        elif act == "cancel_pending":
            db.x("UPDATE jobs SET status='cancelled', finished=? WHERE status='pending'", (now,))
        elif act == "clear":
            self.st.creative.sync_runs()
            from render_queue import remember_timings
            remember_timings(db)
            db.x("DELETE FROM jobs WHERE status IN ('done','cancelled')")
        self.st.creative.sync_runs()
        if act in ('retry', 'retry_errors'):
            db.x("UPDATE jobs SET started=NULL WHERE status='pending' AND prompt_id IS NULL")
        return {"ok": True}

    def music(self, b):
        val = "" if b.get("clear") else self.st.save_upload(b["file"])
        self.st.db.x("INSERT OR REPLACE INTO settings(key,value) VALUES('music',?)", (val,))
        return {"ok": True}

    def audio_cue_save(self, b):
        return self.st.soundtrack.save(b)

    def audio_cue_delete(self, b):
        return self.st.soundtrack.delete(b)

    def audio_discover(self, b):
        return self.st.soundtrack.discover()

    def audio_remix(self, b):
        return self.st.soundtrack.remix(b)

    def production_check(self, b):
        return dict(self.st.production.inspect(refresh=True), ok=True)

    def production_plan(self, b):
        return self.st.production.plan(b)

    def script_revision(self, b):
        with self.st.production.lock:
            return self.st.production.revision(b)

    def regenerate(self, b):
        selected = set(b.get('shot_ids', []))
        result = []
        for shot in self.state()['shots']:
            if shot['id'] not in selected:
                continue
            stages = shot['outdated']
            # Regenerate the earliest affected dependency; old takes remain available.
            stage = next((s for s in ('voice','keyframe','animate','continue','lipsync','upscale') if s in stages), None)
            if stage:
                request={'stage':stage,'shot_ids':[shot['id']],'takes':1,'replace':True}
                if stage=='continue':
                    stale=next((t for t in self.st.chain(shot)[1:] if self.st.production.provenance(t,shot)=='outdated'),None)
                    if stale:request['segment']=stale['segment']
                r = self.generate(request)
                result.append({'shot_id': shot['id'], 'stage': stage, **r})
        return {'ok': True, 'results': result}

    def workspace_action(self,b):
        return self.st.workspace.action(b)

    def shot_preview(self,b):
        return self.st.shot_previews.request(b)

    def finishing_action(self,b):
        with self.st.production.lock:
            return self.st.finishing.action(b)

    def alignment_action(self,b):
        return self.st.alignment.action(b)

    def storage_action(self,b):
        with self.st.production.lock:
            return self.st.storage.action(b)

    def section_action(self, b):
        with self.st.production.lock:
            return self.st.sections.action(b)

    def creative_action(self, b):
        before=self.st.db.one('SELECT COALESCE(MAX(id),0) n FROM jobs')['n']
        result=self.st.creative.action(b)
        for job in self.st.db.q("SELECT * FROM jobs WHERE id>? AND status='pending'",(before,)):self.st.snapshots.capture(job)
        return result

    def editor_action(self, b):
        actions = {'scene': self.st.editor.scene, 'history': self.st.editor.history,
                   'timeline': self.st.editor.timeline, 'continuity': self.st.editor.continuity,
                   'library': self.st.editor.library, 'backup': self.st.editor.backup,
                   'import': self.st.editor.restore_backup, 'subtitles': self.st.editor.subtitles,
                   'delivery': lambda body:self.st.exports.submit(dict(body,kind='stems')),
                   'qc': lambda _: {'ok': True, 'issues': self.st.editor.qc(self.state()['shots'])}}
        action = b.get('tool')
        if action not in actions:
            raise ValueError('Unknown editor action')
        with self.st.production.lock:
            return actions[action](b)

    def assemble(self, b):
        return self.st.exports.submit(dict(b,kind='episode'))

    def export_action(self,b):
        return self.st.exports.action(b)


EDIT_ROUTES = {'/api/settings','/api/shot/save','/api/shot/delete','/api/shot/move',
    '/api/take/delete','/api/approve','/api/asset/save','/api/asset/delete',
    '/api/voice_take/delete','/api/clip/delete','/api/line/set','/api/line/direction',
    '/api/audio/cue/save','/api/audio/cue/delete'}
ROUTES = {
    '/api/export':'export_action',
    '/api/finishing':'finishing_action',
    '/api/shot/preview':'shot_preview',
    '/api/creative': 'creative_action',
    '/api/section': 'section_action',
    '/api/workspace':'workspace_action',
    '/api/alignment':'alignment_action',
    '/api/storage':'storage_action',
    '/api/shot/voices': 'shot_voices',
    '/api/voice/recover': 'voice_recover',
    '/api/editor': 'editor_action',
    '/api/audio/cue/save': 'audio_cue_save', '/api/audio/cue/delete': 'audio_cue_delete',
    '/api/audio/discover': 'audio_discover', '/api/audio/remix': 'audio_remix',
    '/api/production/check': 'production_check', '/api/production/plan': 'production_plan',
    '/api/script/revision': 'script_revision', '/api/regenerate': 'regenerate',
    "/api/settings": "settings", "/api/instance/save": "instance_save", "/api/instance/delete": "instance_delete",
    "/api/instance/check": "instance_check", "/api/workflow": "workflow", "/api/workflow/meta": "workflow_meta",
    "/api/voices": "voices", "/api/reveal": "reveal", "/api/quit": "quit", "/api/background/rendering": "background_rendering", "/api/script/draft": "script_draft",
    "/api/asset/save": "asset_save", "/api/asset/delete": "asset_delete", "/api/asset/use_sheet": "asset_use_sheet",
    "/api/shot/save": "shot_save", "/api/shot/delete": "shot_delete", "/api/shot/move": "shot_move",
    "/api/csv/import": "csv_import", "/api/voice": "voice", "/api/clips/upload": "clips_upload", "/api/clip/update": "clip_update",
    "/api/clip/delete": "clip_delete", "/api/lines/match": "match", "/api/line/set": "line_set", "/api/voice_take/delete": "voice_take_delete", "/api/line/direction": "line_direction", "/api/keyframe/review": "keyframe_review", "/api/generate": "generate", "/api/approve": "approve",
    "/api/take/delete": "take_delete", "/api/jobs": "job_action", "/api/music": "music", "/api/assemble": "assemble",
}


def range_offset(value, size):
    digits = value.lstrip("0")
    if len(digits) > len(str(size)):
        return size
    return int(digits or "0")


# Host names allowed besides loopback: the --host the server was started on, plus
# ANIME_STUDIO_ALLOWED_HOSTS (comma-separated) for deliberate access from another machine.
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("ANIME_STUDIO_ALLOWED_HOSTS", "").split(",") if h.strip()}


def make_handler(st, api):
    class H(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _trusted(self, post=False):
            """Only this machine's own app pages may talk to the backend (blocks cross-site requests and DNS rebinding)."""
            port = self.server.server_port
            host = (self.headers.get("Host") or "").strip().lower()
            allowed = {f"{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]", *EXTRA_HOSTS)}
            if host not in allowed:
                return False
            if post:
                origin = self.headers.get("Origin")
                if origin not in (None, "http://" + host):
                    return False
                if self.headers.get_content_type() != "application/json":
                    return False
            return True

        def _send(self, code, body, ctype="application/json", extra=None):
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self._trusted():
                return self._send(403, {"ok": False, "error": "Requests must come from the Anime Studio app on this computer."})
            try:
                path = urllib.parse.urlparse(self.path).path
                if path in ("/", "/index.html"):
                    return self._send(200, INDEX_HTML, "text/html; charset=utf-8")
                if path == '/shot_voices.js':
                    return self._send(200, (Path(__file__).resolve().parent / 'shot_voices.js').read_text(encoding='utf-8'), 'text/javascript; charset=utf-8')
                if path in ('/voice_video.js','/studio_audio.js','/settings.js','/settings.css','/continuations.js','/workspace.js','/workspace.css','/finishing.js','/finishing.css','/shot_playback.js','/mix_processor.js','/audio_mix.json','/projects.js','/manga.js'):
                    return self._send(200, (Path(__file__).resolve().parent / path[1:]).read_text(encoding='utf-8'), 'application/json' if path.endswith('.json') else 'text/javascript; charset=utf-8' if path.endswith('.js') else 'text/css; charset=utf-8')
                if path == '/creative.css':
                    return self._send(200, (Path(__file__).resolve().parent / 'creative.css').read_text(encoding='utf-8'), 'text/css; charset=utf-8')
                if path == '/creative.js':
                    return self._send(200, (Path(__file__).resolve().parent / 'creative.js').read_text(encoding='utf-8'), 'text/javascript; charset=utf-8')
                if path == '/editor.js':
                    return self._send(200, (Path(__file__).resolve().parent / 'editor.js').read_text(encoding='utf-8'), 'text/javascript; charset=utf-8')
                if path == '/production.js':
                    return self._send(200, (Path(__file__).resolve().parent / 'production.js').read_text(encoding='utf-8'), 'text/javascript; charset=utf-8')
                if path == "/api/ping":
                    counts = {}
                    if st.projects and st.db.lock.acquire(blocking=False):
                        try:
                            counts = {row['status']: row['count'] for row in st.db.q(
                                "SELECT status,COUNT(*) AS count FROM jobs WHERE status IN ('pending','running') GROUP BY status")}
                        finally:
                            st.db.lock.release()
                    return self._send(200, {"ok": True, "app": "anime-studio", "version": VERSION,
                                            "projects": bool(st.projects), "job_counts": counts,
                                            "project": hashlib.sha256(str(st.data).encode()).hexdigest(),
                                            "background_rendering": (st.data/".keep-rendering").is_file()})
                if path == "/api/projects":
                    if not st.projects:
                        return self._send(200, {"ok": False, "error": "Open the desktop app to manage projects."})
                    return self._send(200, {"ok": True, "projects": st.projects.state()})
                if path in ("/api/state", "/api/queue/state"):
                    if st.projects:
                        st.projects.refresh_configuration()
                    queue_only = path != "/api/state"
                    etag = api.state_etag(queue_only)
                    headers = {"ETag": etag}
                    if self.headers.get("If-None-Match") == etag:
                        return self._send(304, b"", extra=headers)
                    data = api.queue_poll() if queue_only else api.state()
                    return self._send(200, data, extra=headers)
                if path == "/api/cast/export":
                    from cast_exchange import export_cast
                    from tempfile import TemporaryFile
                    args = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query, keep_blank_values=True)
                    with TemporaryFile() as bundle:
                        try:
                            ids = {int(v) for v in args["ids"][0].split(",")} if "ids" in args else None
                            export_cast(st, bundle, ids)
                        except ValueError as exc:
                            return self._send(400, {"error": str(exc)})
                        self.send_response(200)
                        self.send_header("Content-Type", "application/zip")
                        self.send_header("Content-Disposition", 'attachment; filename="anime-studio-cast.zip"')
                        self.send_header("Content-Length", str(bundle.tell()))
                        self.end_headers()
                        bundle.seek(0)
                        shutil.copyfileobj(bundle, self.wfile)
                    return
                if path == "/api/csv/export":
                    return self._send(200, api.csv_export(), "text/csv", {"Content-Disposition": 'attachment; filename="shotlist.csv"'})
                if path.startswith("/api/workflow/"):
                    if st.projects:
                        st.projects.refresh_configuration()
                    key = path.rsplit("/", 1)[-1]
                    row = st.wf_row(key)
                    return self._send(200, row["json"] if row else "{}", "application/json",
                                      {"Content-Disposition": f'attachment; filename="{key}_api.json"'})
                if path.startswith("/media/"):
                    return self._file(urllib.parse.unquote(path[len("/media/"):]))
                self._send(404, {"error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": str(e)})

        def do_POST(self):
            if not self._trusted(post=True):
                return self._send(403, {"ok": False, "error": "Requests must come from the Anime Studio app on this computer."})
            try:
                path = urllib.parse.urlparse(self.path).path
                shared_write = st.projects and path in ('/api/settings', '/api/instance/save', '/api/instance/delete',
                                                       '/api/workflow', '/api/workflow/meta')
                if shared_write:
                    origin = f"http://127.0.0.1:{self.server.server_port}"
                    if (self.headers.get("Host") != origin.removeprefix("http://")
                            or self.headers.get("Origin") not in (None, origin)
                            or self.headers.get_content_type() != "application/json"):
                        return self._send(403, {"ok": False, "error": "Shared settings require the local desktop app."})
                if path == "/api/projects":
                    origin = f"http://127.0.0.1:{self.server.server_port}"
                    if (not st.projects or self.headers.get("Host") != origin.removeprefix("http://")
                            or self.headers.get("Origin") not in (None, origin)
                            or self.headers.get_content_type() != "application/json"):
                        return self._send(403, {"ok": False, "error": "Project actions require the local desktop app."})
                    length = int(self.headers.get("Content-Length") or 0)
                    if not 0 < length <= 32768:
                        return self._send(400, {"ok": False, "error": "Invalid project request."})
                    return self._send(200, st.projects.action(json.loads(self.rfile.read(length))))
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                fn = ROUTES.get(path)
                if not fn:
                    return self._send(404, {"error": "not found"})
                with st.production.lock:
                    if st.projects:
                        st.projects.refresh_configuration()
                    if path in EDIT_ROUTES:
                        st.editor.checkpoint(path.removeprefix('/api/'))
                    result = getattr(api, fn)(body)
                if path.startswith('/api/workflow') or path.startswith('/api/instance/'):
                    st.production.readiness = {}
                self._send(200, result)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                traceback.print_exc()
                self._send(200, {"ok": False, "error": str(e)})

        def _file(self, rel):
            p = (st.data / rel).resolve()
            if st.data not in p.parents or not p.is_file():
                return self._send(404, {"error": "not found"})
            size = p.stat().st_size
            start, end, code = 0, size - 1, 200
            m = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", self.headers.get("Range") or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = range_offset(m.group(1), size)
                    end = min(range_offset(m.group(2), size), size - 1) if m.group(2) else size - 1
                else:
                    start = max(0, size - range_offset(m.group(2), size))
                if start >= size or end < start:
                    return self._send(416, b"", extra={"Content-Range": f"bytes */{size}"})
                code = 206
            self.send_response(code)
            self.send_header("Content-Type", mimetypes.guess_type(p.name)[0] or "application/octet-stream")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if code == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    chunk = f.read(min(1 << 20, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
    return H


INDEX_HTML = (Path(__file__).resolve().parent / "ui.html").read_text(encoding="utf-8") if (Path(__file__).resolve().parent / "ui.html").exists() else "<h1>ui.html missing</h1>"


def main():
    ap = argparse.ArgumentParser(description="Anime Studio - ComfyUI production app")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8700)
    ap.add_argument("--projects", action="store_true", help="Enable the local desktop project catalog")
    ap.add_argument("--data", default="~/Documents/AnimeStudio")
    ap.add_argument("--export-workflows", metavar="DIR", help="Write the starter API workflows to DIR and exit")
    args = ap.parse_args()
    if args.export_workflows:
        d = Path(args.export_workflows)
        d.mkdir(parents=True, exist_ok=True)
        for key, variants in STARTERS.items():
            for name, s in variants.items():
                (d / f"{key}__{name}_api.json").write_text(json.dumps(s["wf"], indent=2, ensure_ascii=False), encoding='utf-8')
        print("Wrote starter workflows to", d)
        return
    from platform_runtime import config_directory, project_controller
    if args.projects and args.host != '127.0.0.1':
        ap.error('Project management requires the IPv4 loopback interface.')
    if args.host not in ('127.0.0.1', '0.0.0.0', '::', ''):
        EXTRA_HOSTS.add(args.host.lower())
    project = Path(args.data).expanduser().resolve()
    project.mkdir(parents=True, exist_ok=True)
    with project_controller(project / '.controller.lock'):
        # Bind before touching project state so a failed launch cannot reset
        # another running controller's jobs.
        preferred = args.port
        if args.projects and not preferred:
            from project_workspace import saved_port
            preferred = saved_port(project) or 0
        try:
            srv = ThreadingHTTPServer((args.host, preferred), BaseHTTPRequestHandler)
        except OSError as error:
            if args.projects and args.port == 0 and preferred:
                srv = ThreadingHTTPServer((args.host, 0), BaseHTTPRequestHandler)
            else:
                print(f"Port {args.port} busy ({error}).", flush=True)
                sys.exit(3)
        with srv:
            st = Studio(project)
            if args.projects:
                from project_workspace import ProjectWorkspace
                from project_storage import atomic_text
                config = Path(os.environ.get('ANIME_STUDIO_CONFIG', str(config_directory())))
                if getattr(sys, 'frozen', False):
                    command = [sys.executable, '--backend']
                else:
                    command = [sys.executable, str(Path(__file__).resolve())]
                st.projects = ProjectWorkspace(st, config, command, DEFAULT_INSTANCES)
                atomic_text(project / '.controller-port', str(srv.server_port))

            import signal

            def stop_local(signum, frame):
                st.alignment.shutdown()
                st.exports.shutdown()
                st.shot_previews.shutdown()
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, stop_local)
            signal.signal(signal.SIGINT, stop_local)
            if hasattr(signal, 'SIGHUP'):
                signal.signal(signal.SIGHUP, signal.SIG_IGN)
            srv.RequestHandlerClass = make_handler(st, API(st))
            st.worker = Worker(st)
            st.worker.start()
            print(f"Anime Studio {VERSION} -> http://{args.host}:{srv.server_port}   data: {st.data}", flush=True)
            srv.serve_forever()


if __name__ == "__main__":
    main()
