# Anime Studio

A Mac app that runs a whole anime production on a ComfyUI server used as a render farm: script → shot list → characters → keyframes → animation → 24 fps → voices / lip sync → upscale → an assembled episode plus a DaVinci Resolve timeline.

The app talks to ComfyUI over its HTTP API. Every workflow uses **nodes built into ComfyUI**, so nothing gets installed on the server; you only download model files there. Everything else runs on the Mac.

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
| `workflows/` | The built-in ComfyUI workflows in API format, for reference or to open in ComfyUI. |
| `examples/shotlist_template.csv` | Shot-list spreadsheet format. |
| `tests/` | Fake ComfyUI server and an end-to-end test. |

## Setup

### ComfyUI server: models only
1. Start ComfyUI so other machines can reach it, e.g. `python main.py --listen 0.0.0.0 --port 8188`.
2. Copy `server/download_models.sh` to the server and run it. It downloads about 45 GB into `~/ComfyUI/models` and skips files that are already there.
3. Press **R** in the ComfyUI page, or restart ComfyUI.

The server needs **32 GB of system RAM or more**. The Wan 14B fp8 models are 14.3 GB each, bigger than a 12 GB card, so ComfyUI keeps part of them in RAM.

### Mac
1. Install the tools:
   - `brew install ffmpeg` (required)
   - Python 3.9+: Homebrew, or the one that comes with the Xcode Command Line Tools
   - `xcode-select --install` (recommended; it's what builds the native window)
2. Build the app: `./build_app.sh`. This makes `dist/Anime Studio.app`. Or run `./build_app.sh ~/Applications`.
3. Open it. The first time, right-click it and choose **Open**, because the app isn't signed.
4. Project data lives in `AnimeStudio Data`, next to the app.

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
6. **Voices and lip sync.** Video models make picture only, no sound. Dialogue audio is set **per line** and joined into each shot's dialogue track, with a short pause between lines (adjustable in Settings). The shot also gets longer if its dialogue needs more time.
   - **Reference voice (Cast → Add or Edit).** Upload 5–15 seconds of clean speech for a character and type exactly what is said. This is the voice the TTS step copies. Voice-copying TTS models need the transcript to match the recording.
   - **Voice clips per character (Cast).** Upload any number of recorded lines at once. The file name becomes the clip's transcript, for example `aiko_03_you_came_after_all.wav` becomes "you came after all"; you can edit it. Clips are matched automatically to dialogue lines with the same words, when you upload them or with **Match clips**.
   - **Per line in the shot editor.** For each line, pick one of the speaker's clips, a temporary voice, an uploaded file, or no audio. A line without audio keeps its place as silence, so the timing stays right.
   - **Temp voices** use the macOS voices assigned to each character, for lines that have no audio yet.
   - **Voice (TTS), optional.** Upload a text-to-speech workflow with a `LoadAudio` node titled `AUDIO_IN`, a text node titled `PROMPT`, an optional text node titled `REF_TEXT` and a `SaveAudio` node. The app sends each line together with the character's reference voice and its transcript, so the result is in that character's voice. ComfyUI has no free voice-copying TTS built in: its Fish Audio and ElevenLabs partner nodes work (paid), as does a custom TTS node if you install one.
   - You can still use one audio file for a whole shot instead of per-line audio.
   - The shot preview plays the dialogue in sync with the video.
   - Lip sync is optional: export ComfyUI's Wan2.2 S2V template in API format and upload it under Settings → Workflows. It uses the shot's dialogue track.
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
The test starts two fake ComfyUI servers and runs the whole pipeline: script, cast and sheets, keyframes on two servers, drafts, full-quality re-render, Continue chains, first-last-frame, voice clips and line matching, per-line audio, TTS, upscale, music and assembly. It checks the exact output lengths.

## Troubleshooting
- **Red server dot:** check that ComfyUI was started with `--listen` and that `http://<server>:8188` opens from the Mac.
- **"value not in list":** a model file is missing on the server or has a different name. Compare with `server/download_models.sh`.
- **Unknown node:** update ComfyUI. The workflows use Qwen-Image-Edit 2509 and Wan 2.2 nodes, which were added in 2025.
- **Logs:** `AnimeStudio Data/app.log`, `launcher.log` and `build.log`.

## License
MIT. See `LICENSE`.
