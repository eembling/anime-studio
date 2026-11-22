# Anime Studio

A Mac app that runs a whole anime production on a ComfyUI server used as a render farm: script → shot list → characters → keyframes → animation → 16 fps playback → voices / lip sync → upscale → an assembled episode plus a DaVinci Resolve timeline.

The app talks to ComfyUI over its HTTP API. The core image/video workflows use nodes built into ComfyUI. Optional voices use the ComfyUI-Qwen-TTS custom node pack; the app checks the server before queuing work. Everything else runs on the Mac.

| Runs on | What |
|---|---|
| ComfyUI server | character turnaround sheets, keyframes (Qwen-Image-Edit 2509 + Lightning), animation (Wan 2.2 I2V 14B + Lightning, normal, first/last-frame and continuation), optional lip sync (Wan 2.2 S2V), optional upscaling (RealESRGAN anime) |
| Mac | script → shot list, shot board and choosing takes, temporary voices (`say`), 16 fps playback and assembly, music mix, Resolve export |

## Documentation

Start with the [documentation index](docs/README.md). The [product guide](docs/PRODUCT-GUIDE.md) covers every workspace and production workflow; the [design guide](docs/DESIGN-GUIDE.md) defines the visual system, interaction rules and accessibility targets. Developers can use the [architecture guide](docs/ARCHITECTURE.md), and maintainers can use the [setup and operations guide](docs/OPERATIONS.md).

The guides describe the current 2.11 interface. Older version sections below are release history.

## Creative production tools

The [creative tools guide](docs/CREATIVE-TOOLS.md) covers the Reference library, per-shot Direction and Repair tools, dialogue Performance editor and Generation lab. It includes compatible ComfyUI workflow inputs, mask conventions, word-timing import and manual server setup notes.

## Repository layout

| Path | What |
|---|---|
| `app/anime_studio.py` | The app server: job queue, ComfyUI client, workflow patching, assembly. Python 3.9+ standard library only. |
| `app/ui.html` | The interface (single page, no external dependencies). |
| `mac/` | macOS bundle parts: launcher, Swift/WebKit window, `Info.plist`, icon. |
| `build_app.sh` | Builds `Anime Studio.app` from the files above. |
| `server/download_models.sh` | Pinned and checksum-verified model downloads for the ComfyUI server. |
| `server/install_tts.sh` | Installs local text-to-speech (Qwen3-TTS) into ComfyUI for the Voices tab. |
| `workflows/` | The built-in ComfyUI workflows in API format, for reference or to open in ComfyUI. |
| `examples/shotlist_template.csv` | Shot-list spreadsheet format. |
| `tests/` | Fake ComfyUI server and an end-to-end test. |

## Setup

### ComfyUI server: models only
1. Start ComfyUI so other machines can reach it, e.g. `python main.py --listen 0.0.0.0 --port 8188`.
2. Copy the Anime Studio `server` directory to the server and run `bash server/download_models.sh`. It downloads about 45 GB into `~/ComfyUI/models` and skips files that are already there.
3. Press **R** in the ComfyUI page, or restart ComfyUI.

**Voices (optional, local TTS).** The Voices tab needs one custom node pack on the server. Run `bash server/install_tts.sh` from the same Anime Studio checkout, then restart ComfyUI. The script:
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

You can also run it without the app bundle: `python3 app/anime_studio.py`, then open http://127.0.0.1:8700. The backend only answers requests addressed to this computer (`127.0.0.1` or `localhost`) from its own pages, so other websites can't reach it. To use it from another machine on purpose, start it with `--host <address>` or set `ANIME_STUDIO_ALLOWED_HOSTS` to a comma-separated list of host names.

**Optional:** for optional conversion of other source frame rates, unzip rife-ncnn-vulkan (github.com/nihui/rife-ncnn-vulkan/releases) into `~/Downloads` or `~/Applications`. The app finds it there.

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
5. **16 fps.** Studio playback and assembly default to the generated clips’ native 16 fps. Frame conversion is only needed when a source uses another frame rate.
6. **Voices (the Voices tab), then lip sync.** Video models make picture only, no sound. Make the voices **before** animating, so line lengths set shot lengths before you spend GPU time on video.
   - **Reference voice (Cast → Add or Edit).** Upload 5–15 seconds of clean speech for each character and type exactly what is said. Qwen3-TTS copies this voice.
   - **The Voices tab** lists every dialogue line in script order, grouped by scene.
     - **Generate all missing lines** (or selected lines) makes 1–4 takes per line in the speaker's voice on the ComfyUI server.
     - Play a take with ▶ and click its number to use it. The chosen take has a selected state and check mark.
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
   - **Assemble** makes a 1920×1080 / 16 fps episode with dialogue and music.
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

From your Anime Studio checkout on the server, run:

```bash
COMFY="$HOME/ComfyUI" bash server/install_lipsync.sh
```

The helper verifies existing files and downloads missing or mismatched pinned model files. It downloads:

- `models/diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors`
- `models/audio_encoders/wav2vec2_large_english_fp16.safetensors`
- `models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors` (shared with animation)
- `models/vae/wan_2.1_vae.safetensors` (shared with animation)

Restart **every** ComfyUI instance assigned lip-sync jobs using your usual launch commands or service manager. No extra custom-node pack is needed for this preset. The native `WanSoundImageToVideo`, `AudioEncoderLoader` and `AudioEncoderEncode` nodes must appear in `/object_info`; an older server missing those nodes needs updating.

### Use it in Anime Studio

1. Reopen the rebuilt app. In Settings → Workflows select the `wan22_s2v_14b_fp8` lip-sync starter if it is not already selected.
2. In Settings → ComfyUI servers enable `lipsync` on the intended server.
3. Run Production → Check readiness. Resolve missing model/node messages before queuing.
4. Choose a keyframe and generate or upload dialogue audio. Start with a short, front-facing character shot, then click **Animate with voice**. Review speaking direction and the close-up crop guide before generating.

The standard preset uses 20 steps at 16 fps, with at most 77 frames (4.81 seconds) per generation. Short shots render at least 73 frames, then trim when saving the take. Long motion shots use Continue shot; native S2V continuations capture up to 73 predecessor frames as motion context. An experimental 30-step option and same-seed comparison are available for the built-in starter. S2V creates a new talking video from the reference image; **Lip-sync existing clip** requires a separately tested video-conditioned workflow. A 14B model is demanding on a 12 GB GPU; render one short test before batching. See [voice animation quality and manual server setup](docs/lip-sync-quality.md).

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


## Studio: episode editing, history and delivery

**Studio → Episode timeline** previews selected takes, shot dialogue, timed music/effects and the music bed together. Scrub or step at the configured output frame rate, drag shot blocks to reorder, trim source-in/duration, or split a shot at the playhead. Timeline edits preserve the original takes and apply to picture and dialogue during assembly. Missing picture is shown explicitly. Preview is a timing aid; browser playback is not an encoded final-quality render. A dialogue waveform can be displayed for the current shot.

Audio cues can follow a **shot plus an offset**, rather than an absolute episode time. Anchored cues ripple when shots move or change length. A missing anchor disables the cue until it is reassigned. Absolute cues retain their existing behavior.

**Production → Scene actions → Delete scene clips** previews and removes animation, continuation, lip-sync and upscale takes from the selected scene. It keeps keyframes, dialogue audio and the script. **Reset scene settings** keeps scene membership, shot rows/order/kind, dialogue and the saved script; it clears generation prompts, cast links, camera/notes, duration overrides, retiming, source-in, continuity rules, selected/generated takes and shot-anchored audio cues. It returns shot duration to the project default. Both operations create a recovery checkpoint, retain underlying files for history, and reject a stale preview or a scene with queued/running renders. Existing exports remain intact. Finish/cancel jobs and preparation plans through their normal controls first.

**Studio → History** provides undo/restore through labeled editorial checkpoints. Restoring creates another checkpoint so the change can be reversed. Daily startup checkpoints also save a standalone SQLite copy under `history/`. Deleted takes, reference clips and prior dialogue mixes retain their source media for recovery. History and archived episodes consume disk space; these controls remove items from the active edit rather than permanently purging their files. Running renders/exports and active preparation plans must finish or be cancelled before restoring. Restored preparation plans are blocked until explicitly reviewed/resumed.

**Studio → Series and episodes** saves, creates, duplicates and opens episodes with independent scripts, shot lists, settings, cues and export records. Cast/reference voices and server/workflow configuration are shared. Save or switch to persist the current episode into the series library; its live edit is always stored in the project database. Portable ZIP backups contain the database, media, exports and recovery files. Import validates the database and archive paths, namespaces imported media and preserves the current edit in history first. Imports merge archived episodes into the library and do not import server credentials/configuration. Large projects can take time and disk space to archive; the import size limit is 100 GB.

**Production → Scene continuity** records lighting, outfits, expressions, props and scene notes, with up to three cast/location reference overrides. These references and rules feed actual keyframe generation; edits flag older keyframes as outdated. **Compare takes** displays generated options side by side and lets you choose a take.

**Render recovery** keeps ComfyUI prompt/server IDs across app restarts and download failures. Retry reconnects to an existing remote render instead of submitting it again; explicitly cancelled jobs start a new render when retried. ComfyUI execution errors clear the failed remote prompt so Retry can regenerate. Optional WebSocket events show the executing node and current sampler-step progress, while overall completion estimates remain clearly labeled. A finished render is collected through history even when its current workflow/settings have changed. This cannot recover a submission whose HTTP response was lost before its prompt ID was saved, or history removed from ComfyUI. No server updates are applied automatically.

**Studio → Final delivery** checks missing picture/dialogue, incomplete animation, draft selections, outdated takes and dialogue cutoffs. Edit subtitle times/text and export SRT; generated timings for unvoiced lines are estimates and need review. After choosing an existing render in Assemble, export separate 24-bit WAV dialogue/music/effects stems and a loudness analysis report. A dialogue-only master is required. Quality checks describe the current edit; stems describe the chosen rendered master. Review picture/audio together before delivery. Existing EDL export for Resolve remains available.

Validation includes unit and UI behavior suites, FFmpeg checks for source trims and isolated stems, remote-output reconnection without resubmission, archive import safety, scoped scene cleanup and recovery, plus the full pipeline against local mock ComfyUI servers. Real GPU output quality and hardware playback require separate acceptance testing.


## Voice dialogue import and recovery

Script import and script revision recognize plain speaker labels (`TOBI: ...`) and Markdown labels (`**TOBI:** ...`, `**TOBI**: ...`, `__TOBI:__ ...`). Bold scene headings such as `**EXT. ROAD — DAWN**` start scenes. Script metadata and montage/sequence headings are not speakers. Italic parenthetical stage directions become delivery notes instead of spoken text. Several labeled lines in one paragraph remain separate speaker lines.

For an existing project imported before this fix, open **Voices → Dialogue recovery → Recover dialogue from shot prompts**. Review the checked rows and apply them to the original shots. This action adds dialogue/cast links and preserves shot IDs, scene membership, duration, picture prompts, existing dialogue and generated takes. It creates a history checkpoint and does not submit TTS jobs. New speakers need reference audio uploaded or designed in Cast before their lines can be generated. Whole-shot audio overrides are excluded from recovery.

Voice jobs and new takes are bound to the speaker and line occurrence, preventing identical words from routing to another character or repeated line. A changed speaker does not inherit the previous speaker’s selected audio. Duplicate clicks do not queue more work for lines already waiting/rendering. **More takes** remains available once the current batch finishes. Legacy takes without a saved line position are still matched by speaker and text; review ambiguous repeated lines manually.


## Generate a character's dialogue directly from a shot

In **Shots**, choose **Dialogue**. In Voices, **Edit dialogue** opens the same editor on that exact line. A line navigator shows the ordered dialogue; the focused editor contains **Character**, reference preview, **Spoken words**, optional **Delivery**, and compact voice takes. Use **Save dialogue**, **Generate this line**, or **Generate all lines**. Both generation actions save all entered dialogue before queuing their requested lines. Only character assets can provide a voice; locations are excluded. The reference recording establishes voice identity, while generation speaks the shot's exact words.

The footer shows save state. Line navigation preserves the draft, closing guards unsaved changes, and selecting a take for one line keeps edits to other lines. See the [detailed dialogue workflow](docs/PRODUCT-GUIDE.md#6-dialogue-and-character-voices).

The first new take is applied to the shot automatically, including replacement of a temporary voice, unless another take was selected while it rendered. Preview alternatives and choose **Use take** to change the shot's dialogue audio. Results update while the panel is open without replacing text being edited. In the main shot editor, **Save shot & choose character voices** saves unsaved dialogue before opening these controls. Add missing reference voices in Cast first. Replacing a whole-shot audio file requires checking the explicit switch to line voices.

Character IDs and persistent per-shot line IDs bind generated takes to dialogue, so renaming a character or reordering repeated lines does not route a voice by guessing from text. Jobs capture the reference audio, reference transcript and delivery note when queued. Changed words, punctuation, character or delivery clear the selected audio but keep previous takes/files. A changed line cannot receive a stale job's audio. Multi-character shots without speaker labels require an explicit choice; no location or arbitrary first cast member is used as a voice. Legacy takes without stable IDs still use their existing speaker/text binding.

**Use matching recordings** remains an explicit option for recorded dialogue. It only searches the selected character's recordings and never falls back to another character's clips. It is separate from generating a new performance. Single-character legacy shots and speaker labels such as `TOBI: ...` can prefill the character choice, which is visible and editable in the shot voice panel.


## Version 2.11 — reliability and editing

Settings is organized by task. Studio adds fitted, synchronized playback, visible picture/dialogue/music/effects tracks, waveform scrubbing, trim handles and cut-boundary previews. Continuations have independent saved prompts, boundary previews, audio coverage and captured generation inputs (issue #9). All newly queued jobs capture workflows, parameters and media; completed jobs can repeat their recorded inputs. Local speech timing, recovery drafts, reviewed storage cleanup and actionable render errors support longer editing sessions. Active renders list their submitted models beneath the progress bar. The bottom-left version badge opens source/build information.

See the [product workflows](docs/PRODUCT-GUIDE.md#17-reliable-rendering-recovery-and-workspace-tools-211) and [operations guide](docs/OPERATIONS.md#local-speech-alignment-211). ComfyUI remains manually maintained; these Mac features do not update the server.


## GitHub desktop releases

The Desktop release workflow builds macOS Apple silicon and a per-user Windows x64 installer from the same commit, preserving the current app version. Both platform builds and packaged smoke tests must pass before the publication job creates the version tag and publishes assets. Setup changes run on `codex/github-release-*` branches; a manual dispatch is also available once the workflow is on the default branch. Existing version tags/releases are never replaced.

See [release installation requirements](packaging/RELEASE.md). Windows bundles Python and FFmpeg/FFprobe and requires Windows 11, WebView2 and .NET 4.8. The native macOS arm64 application requires separately installed Python 3.9+ and FFmpeg. No signing secrets are configured: downloads are unsigned, and the macOS app is not Developer ID signed or notarized. Build outputs include SHA-256 checksums. No ComfyUI server changes or generation-model downloads are performed by packaging.

Windows stores projects under `%LOCALAPPDATA%\AnimeStudio`; uninstall preserves them. macOS keeps its existing Application Support storage. Windows supports manual dialogue timing; automatic speech-alignment setup and temporary macOS system voices remain macOS-only.
