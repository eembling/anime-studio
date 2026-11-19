# Anime Studio

A Mac app that runs a whole anime production on a ComfyUI server used as a render farm: script → shot list → characters → keyframes → animation → 24 fps → voices / lip sync → upscale → an assembled episode plus a DaVinci Resolve timeline.

The app talks to ComfyUI over its HTTP API. The core image/video workflows use nodes built into ComfyUI. Optional voices use the ComfyUI-Qwen-TTS custom node pack; the app checks the server before queuing work. Everything else runs on the Mac.

| Runs on | What |
|---|---|
| ComfyUI server | character turnaround sheets, keyframes (Qwen-Image-Edit 2509 + Lightning), animation (Wan 2.2 I2V 14B + Lightning, normal, first/last-frame and continuation), optional lip sync (Wan 2.2 S2V), optional upscaling (RealESRGAN anime) |
| Mac | script → shot list, shot board and choosing takes, temporary voices (`say`), 16 → 24 fps, assembly, music mix, Resolve export |

## Repository layout

| Path | What |
|---|---|
| `app/anime_studio.py` | The app server: job queue, ComfyUI client, workflow patching, assembly. Python 3.9+ standard library only. |
| `app/ui.html` | The interface (single page, no external dependencies). |
| `mac/` | macOS bundle parts: launcher, Swift/WebKit window, `Info.plist`, icon. |
| `build_app.sh` | Builds `Anime Studio.app` from the files above. |
| `server/download_models.sh` | Model downloads for the ComfyUI server (`wget -c` into `~/ComfyUI/models`). |
| `server/install_tts.sh` | Installs local text-to-speech (Qwen3-TTS) into ComfyUI for the Voices tab. |
| `workflows/` | The built-in ComfyUI workflows in API format, for reference or to open in ComfyUI. |
| `examples/shotlist_template.csv` | Shot-list spreadsheet format. |
| `tests/` | Fake ComfyUI server and an end-to-end test. |

## Setup

### ComfyUI server: models only
1. Start ComfyUI so other machines can reach it, e.g. `python main.py --listen 0.0.0.0 --port 8188`.
2. Copy `server/download_models.sh` to the server and run it. It downloads about 45 GB into `~/ComfyUI/models` and skips files that are already there.
3. Press **R** in the ComfyUI page, or restart ComfyUI.

**Voices (optional, local TTS).** The Voices tab needs one custom node pack on the server. Copy `server/install_tts.sh` to the server, run `bash install_tts.sh`, then restart ComfyUI. The script:
- saves your current Python package list, so you can roll back
- installs [ComfyUI-Qwen-TTS](https://github.com/flybirdxx/ComfyUI-Qwen-TTS) and its requirements; this upgrades `transformers` to 4.57 or newer in ComfyUI's Python
- downloads the Qwen3-TTS voice-cloning models (1.7B and 0.6B) into `models/qwen-tts`

Everything runs locally on your GPU; no cloud service or API key is used.

The server needs **32 GB of system RAM or more**. The Wan 14B fp8 models are 14.3 GB each, bigger than a 12 GB card, so ComfyUI keeps part of them in RAM.

### Mac
1. Install the tools:
   - `brew install ffmpeg` (required)
   - Python 3.9+: Homebrew, or the one that comes with the Xcode Command Line Tools
   - `xcode-select --install` (recommended; it's what builds the native window)
2. Build the app: `./build_app.sh`. This makes `dist/Anime Studio.app`. Or run `./build_app.sh ~/Applications`.
3. Open it. The first time, right-click it and choose **Open**, because the app isn't signed.
4. Settings and project data load automatically from `~/Library/Application Support/AnimeStudio`. On the first updated launch, the app copies your remembered legacy project into this app-owned location, validates the database, and remembers the new location. The original folder stays intact. Later launches use the saved copy without accessing Documents or asking you to select a folder. Set `ANIME_STUDIO_DATA` to explicitly use another folder.

You can also run it without the app bundle: `python3 app/anime_studio.py`, then open http://127.0.0.1:8700.

**Optional:** for the smoothest 16 → 24 fps conversion, unzip rife-ncnn-vulkan (github.com/nihui/rife-ncnn-vulkan/releases) into `~/Downloads` or `~/Applications`. The app finds it there.

## Using more than one GPU
ComfyUI uses **one GPU per running copy**. To use more cards, start more copies on the server. This installs nothing:
```bash
python main.py --listen 0.0.0.0 --port 8189 --cuda-device 1 --temp-directory ~/ComfyUI/temp_gpu1
python main.py --listen 0.0.0.0 --port 8190 --cuda-device 2 --temp-directory ~/ComfyUI/temp_gpu2
```
Add each copy under **Settings → ComfyUI servers** and tick which jobs it takes. Jobs are shared out automatically. Keep small cards (6 GB) for upscaling only.

## The workflow
1. **Script.** Paste the script and click **Create shots from script**. `# Scene` or `INT./EXT.` starts a scene, `NAME: line` becomes a dialogue shot (a still keyframe with a slow push-in), and any other paragraph becomes a motion shot. A CSV shot list works too.
2. **Cast.** Add one clean reference image per character or location, then use **Make turnaround sheet**. An optional character LoRA (trained elsewhere, placed in `models/loras` on the server) is added automatically to every keyframe that character appears in.
3. **Keyframes.** Generate 2–4 takes per shot and click one to choose it. Up to 3 references are sent as Picture 1–3, plus the previous shot's keyframe if *Match the previous shot* is ticked.
4. **Animate.** Wan 2.2 turns the chosen keyframe into a clip.
   - **New shots default to 4 seconds** (65 frames). You can change the default in Settings.
   - **One clip is at most about 5 s** (81 frames). That's a limit of the model.
   - **Continue** animates the next part of a longer shot from the last frame of the chosen clip, repeating until the shot's length is filled. The shot card shows how much is animated and warns about any time that would otherwise freeze on the last frame.
   - **End on the next shot's keyframe** uses first-last-frame mode on the final part, so the cut into the next shot is smooth.
   - **Draft** renders at 512×288 and at most 49 frames, for quick motion tests. **Render full quality** re-renders a draft take with the same seed.
5. **24 fps.** Set per shot and applied at assembly: hold frames (classic anime timing), blend (ffmpeg interpolation), or RIFE.
6. **Voices (the Voices tab), then lip sync.** Video models make picture only, no sound. Make the voices **before** animating, so line lengths set shot lengths before you spend GPU time on video.
   - **Reference voice (Cast → Add or Edit).** Upload 5–15 seconds of clean speech for each character and type exactly what is said. Qwen3-TTS copies this voice.
   - **The Voices tab** lists every dialogue line in script order, grouped by scene.
     - **Generate all missing lines** (or selected lines) makes 1–4 takes per line in the speaker's voice on the ComfyUI server.
     - Play a take with ▶ and click its number to use it. The chosen take is circled in red.
     - **Play scene** plays the chosen takes back to back, with the pause between lines.
     - An optional **delivery note** (for example "whispering" or "angry") is passed to the model as a style hint. How much it changes the delivery varies.
     - Filters: lines that need audio, all lines, or one character's lines.
   - Each line can also use a recorded voice clip (Cast → Voice clips, matched to lines by their words), a temporary macOS voice, or an uploaded file.
   - The chosen audio for each line is joined into the shot's dialogue track, with a pause between lines (adjustable in Settings). The shot gets longer if the dialogue needs more time. You can still use one audio file for a whole shot instead.
   - Settings → Project: voice model size (0.6B faster, 1.7B better) and voice language (Auto, English, Japanese, Chinese, Korean, …).
   - You can upload any other voice-copying TTS workflow under Settings → Workflows. The app fills a `LoadAudio` node titled `AUDIO_IN`, the text node titled `PROMPT`, and any `ref_text`, `language` or `instruct` inputs.
   - The shot preview plays the dialogue in sync with the video.
   - Lip sync is optional: use the built-in `wan22_s2v_14b_fp8` starter under Settings → Workflows. Update ComfyUI and download the S2V models first (see **Lip sync setup** below). It creates a talking clip from the chosen keyframe and dialogue audio.
7. **Upscale and assemble.**
   - Upscaling is optional (RealESRGAN anime → 1080p).
   - **Assemble** makes a 1920×1080 / 24 fps episode with dialogue and music.
   - It also writes each shot as its own file, plus `timeline.edl` for DaVinci Resolve (File → Import → Timeline).

## Rough numbers for an RTX 3060 12 GB
- Generate at 848×480 and upscale later.
- Use 4–5 s per clip; continue longer shots instead of asking for more frames.
- Expect a few minutes per 5 s clip. The Render queue shows real times, so start with a 60-second pilot of 15–20 shots before planning a full episode.
- Still and dialogue shots cost no video-generation time, so use them generously. That's also how anime is actually made.

## Using your own ComfyUI workflows
Build and test the workflow in ComfyUI, choose **Workflow → Export (API)**, and upload it under Settings → Workflows.

The app fills nodes by title: `PROMPT`, `NEGATIVE`, `REF1`–`REF3`, `START_IMAGE`, `END_IMAGE`, `VIDEO_IN`, `AUDIO_IN`, `SIZE` (width / height / length) and `SAVE`. If no title is set, it detects nodes by type. Every `seed` / `noise_seed` gets a new value for each take. For video workflows, set the model fps (Wan 14B = 16, Wan 5B = 24).

## Development
```bash
python3 tests/test_pipeline.py      # needs ffmpeg and Pillow
```
The test starts two fake ComfyUI servers and runs the whole pipeline: script, cast and sheets, keyframes on two servers, drafts, full-quality re-render, Continue chains, first-last-frame, voice clips and line matching, reference voices, per-line audio, TTS takes, upscale, music and assembly. It checks the exact output lengths.

## Troubleshooting
- **Red server dot:** check that ComfyUI was started with `--listen` and that `http://<server>:8188` opens from the Mac.
- **"value not in list":** a model file is missing on the server or has a different name. Compare with `server/download_models.sh`.
- **Unknown node:** update ComfyUI. The workflows use Qwen-Image-Edit 2509 and Wan 2.2 nodes, which were added in 2025.
- **Logs:** `~/Library/Application Support/AnimeStudio/launcher.log`; `app.log` and `build.log` live in the saved project folder named by `~/Library/Application Support/AnimeStudio/data-folder`.

## License
MIT. See `LICENSE`.

## Changes in 2.5 (review fixes)

- Deleting a shot or cast member now removes its takes and voice takes and cancels its queued jobs. Leftover rows are cleaned up on startup, so a new shot never picks up an old shot's takes.
- Jobs that finish after their shot was deleted are discarded.
- Parallel Continue jobs no longer read a half-written last frame.
- TTS voice and language settings are checked against the supported list (unknown values fall back to Auto).
- Assembly warns when a shot's dialogue is longer than the shot.
- Names with apostrophes no longer break buttons; Reveal only opens files inside the data folder.
- The UI no longer redraws the shot, voice and cast lists when nothing changed.

## Changes in 2.6

- Cast → Design a voice creates reference recordings from a written voice description using local Qwen3-TTS VoiceDesign. Enter a sample line, generate takes, listen, and choose a reference.
- Run `server/install_tts.sh` again to download the VoiceDesign model, then restart ComfyUI. Enable `voice_design` on the server in Settings. VoiceDesign uses the 1.7B model regardless of the voice-cloning model setting.
- Designed takes are saved under `AnimeStudio Data/voices/reference`; the first becomes the reference when the character has none.
- Missing custom-node errors now explain which node pack to install and where to inspect startup failures.

## Batch image utility

`comfy_batch.py` submits a folder of images through an API-format ComfyUI workflow. Install `requests`, then run `python3 comfy_batch.py --help` for server, input, output and node-selection options.

## Changes in 2.7 — production workflow

Open **Production** to work scene by scene:

1. **Preview scene / episode** builds a 960×540 animatic using selected keyframes, dialogue and shot durations. It never submits GPU jobs. Static/hold/locked camera notes hold the image; other notes use a slow push-in. Adjust duration and camera notes in Shots, then rebuild the preview. Missing images are black. Animatics are saved separately from episode exports.
2. **Outdated render tracking** records the inputs used for new takes. Changed prompts, references, voices, timing, selected upstream takes or workflows flag affected selections. Old takes remain available; older versions without recorded inputs show unknown history. **Regenerate affected stages** queues new takes for the earliest affected dependency per shot; choose them in Shots/Voices before regenerating downstream stages. Production and shot cards show warnings.
3. **Check readiness** inspects each enabled server’s `/object_info` for node types and advertised model choices. Missing nodes/models include repair guidance. Generation refuses to queue when no enabled server passes, and the dispatcher routes jobs only to compatible servers. Checks are cached for 60 seconds; run Check readiness after installing models. Internally managed custom-node weights and actual GPU memory requirements cannot be proven from this endpoint.
4. **Script → Review script revision** previews changes, maps passages to existing shots, and applies checked changes only. Map a passage to “Create new shot” to add it. Removed passages require explicit selection; otherwise those shots remain at the end. Unchanged and reordered passages retain shot IDs and takes. A preview expires if the shots change before applying.
5. **Prepare this scene** persists a scene plan: missing voices → two keyframe options → pause for explicit keyframe approval → animation and automatic continuations → optional lip sync/upscale. Choose takes in Shots and approve keyframes in Production. Failed or unsupported stages block with a reason; retry failed jobs in Render queue, then resume. Approval waits do not prevent another scene from progressing. Cancel stops the plan and its unfinished jobs.

Higher priorities run first among waiting jobs; running jobs are not interrupted. Change a pending job’s priority in Render queue. Completion estimates use previous successful job durations and enabled server roles; they are approximate, and future unqueued plan stages are excluded.

Build the Mac app with `bash build_app.sh`. The bundle includes `production.py` and `production.js`. Existing project databases migrate automatically. To validate development changes:

```bash
python3 tests/test_production.py
python3 tests/test_pipeline.py
```

## Lip sync setup (v2.8)

The app now supplies a native Wan2.2 S2V API workflow automatically for projects without a lip-sync workflow. Existing custom workflows are preserved. You can select **Settings → Workflows → Lip sync → Use wan22_s2v_14b_fp8**, or upload `workflows/lipsync__wan22_s2v_14b_fp8_api.json`. The Mac submits that workflow directly to ComfyUI; you do not need to save a workflow on the server.

### Manual server update

Stop queued/running renders before maintenance. On the server:

```bash
cd ~/ComfyUI
git pull --ff-only
# Use the SAME Python environment that runs ComfyUI. For a venv named venv:
./venv/bin/python -m pip install -r requirements.txt
# If yours is named .venv, use ./.venv/bin/python instead.
```

If Git reports local changes, preserve them and resolve the update normally. Do not reset the checkout. If ComfyUI runs with another environment, use that environment's Python for the requirements update.

Get Anime Studio's helper from the feature branch containing this change (until it merges):

```bash
curl -fL --retry 3 https://raw.githubusercontent.com/eembling/anime-studio/codex/native-lipsync/server/install_lipsync.sh -o /tmp/anime-install-lipsync.sh
COMFY="$HOME/ComfyUI" bash /tmp/anime-install-lipsync.sh
```

The helper only downloads/resumes model files. It does not update ComfyUI, Python packages, custom nodes or services. Set `COMFY` differently if your checkout is elsewhere. It downloads:

- `models/diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors`
- `models/audio_encoders/wav2vec2_large_english_fp16.safetensors`
- `models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` (shared with animation)
- `models/vae/wan_2.1_vae.safetensors` (shared with animation)

Restart **every** ComfyUI instance assigned lip-sync jobs using your usual launch commands or service manager. No extra custom-node pack is needed for this preset. The native `WanSoundImageToVideo`, `AudioEncoderLoader` and `AudioEncoderEncode` nodes must appear in `/object_info`; an older server missing those nodes needs updating.

### Use it in Anime Studio

1. Reopen the rebuilt app. In Settings → Workflows select the `wan22_s2v_14b_fp8` lip-sync starter if it is not already selected.
2. In Settings → ComfyUI servers enable `lipsync` on the intended server.
3. Run Production → Check readiness. Resolve missing model/node messages before queuing.
4. Choose a keyframe and generate or upload dialogue audio. Start with a short, front-facing character shot, then click **Lip sync**.

This is a 20-step, single-chunk preset at 16 fps and at most 77 frames (4.81 seconds). Short shots render at least 73 frames, then trim to the shot duration at assembly. Longer shots are rejected with instructions to split them or provide a custom extended S2V workflow. S2V creates a new talking video from the reference image; it replaces the selected clip rather than modifying an existing animation. A 14B model is demanding on a 12 GB GPU; render one short test before batching.

Source: [Comfy-Org's S2V template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_s2v.json) and [native S2V node implementation](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_wan.py). This preset uses the core single-chunk path with frame output for the app's existing stitching/audio assembly. GPU inference still requires acceptance testing on your server.

## Timed music and sound effects (v2.9)

The Assemble page supports a whole-episode music bed plus individually timed music and sound-effect cues. Add an audio file with **Add music / sound effect**, then set its episode start, source offset, play duration, volume, fades and optional looping. Times accept seconds, `MM:SS` or `HH:MM:SS` with milliseconds. **Use episode playhead** places the cue at the preview player's current time. Cues can overlap; enable/disable them or remove them without changing an existing export. Their timing is absolute and does not move with later shot edits.

Enabled cues are included in new episode builds when **Include enabled cues** is selected. Music and effects are mixed over the dialogue with a peak limiter; cues extending past the episode end are trimmed, and cues starting after the end are skipped. Animatic/scene previews do not apply episode-wide cues.

After rendering, choose a source under **Rendered episode**, click **Preview selected episode**, add/edit cues, then **Export new soundtrack**. This creates a separate MP4 and copies the encoded video stream without rendering shots or using ComfyUI. A companion `.audio.json` records the mix timing/settings. New episode builds retain `_dialogue_master.mp4` beside the output, so repeat exports start from dialogue-only audio and do not accumulate music/effects. Keeping that master uses additional local storage; do not delete it if you want clean soundtrack revisions.

For episodes rendered before this feature, click **Find rendered episodes**. Discovery only scans the current project's `exports` folders. Older files without a clean master retain whatever soundtrack was already embedded; rebuild the episode once to create a dialogue-only master if you need to replace that audio. Existing exports are never overwritten. Audio cue source files remain on disk when removing a cue, preserving in-flight mixes and export references.

No ComfyUI server changes are required. Validation: `python3 tests/test_soundtrack.py` uses FFmpeg to check audible cue placement, looping, dialogue retention, unchanged video packets, clean remixing and duration.

## Render queue (v2.10)

Render queue separates **Active renders**, **Queued renders**, and **Completed renders**, with failed/cancelled jobs in their own section. Active jobs show a progress bar, elapsed time, and estimated remaining time. Queued jobs show their priority/order and estimated start/finish when sufficient history is available. Completed jobs reach 100% only after finishing; the latest 100 are listed. Existing cancel, retry and priority controls remain available.

Progress percentages are estimates from elapsed time and the median of up to 20 recent successful runs of the same stage and draft/full-quality mode, preferring the same server. They are not measured ComfyUI sampler progress. Jobs without history show an indeterminate bar. Overdue jobs stay below 100% and report an uncertain finish time; work waiting behind an unknown/overdue render does not receive a fabricated ETA. Estimates account for enabled compatible servers and parallel work but cannot predict external ComfyUI workloads. Learned timings survive clearing completed jobs.

Focused validation: `python3 tests/test_render_queue.py` and `node --test tests/test_render_queue_ui.cjs`.

## macOS project-folder access

Normal startup uses Application Support so rebuilding the app does not require selecting a Documents folder again. A legacy project is copied once, including settings, media and SQLite WAL contents, and the saved location switches only after database validation. The original is retained. Migration failures are recorded in `~/Library/Application Support/AnimeStudio/launcher.log` and never silently replace a saved project with an empty one. An explicitly selected external project or `ANIME_STUDIO_DATA` override can still require macOS permission for its location; **Choose Project Folder…** remains available for these cases.

Do not delete `studio.db`, create a blank replacement project, or change broad disk permissions to repair an access denial. Database corruption errors remain separate from permission errors. Native startup exits are surfaced promptly with log access rather than waiting for the generic startup timeout.
