# Anime Studio setup and operations guide

This guide covers the Mac app, separately maintained ComfyUI servers, native builds, project storage, recovery and development checks. Commands are instructions for the named machine; rebuilding the Mac app does not update the ComfyUI server.

For production steps, see the [Product guide](PRODUCT-GUIDE.md). For ownership of each subsystem, see [Architecture](ARCHITECTURE.md).

## 1. Mac requirements

The backend uses Python 3.9 or later. FFmpeg and its companion `ffprobe` are required for audio/video operations. The native shell builds with Xcode Command Line Tools. macOS `say` provides optional temporary voices. RIFE is optional for frame interpolation.

Typical Mac setup:

```bash
brew install ffmpeg
xcode-select --install
```

Install a usable Python 3.9+ if one is not already available. The launcher checks Homebrew locations and the system Python when Command Line Tools are installed. Verify tools from a terminal:

```bash
python3 --version
ffmpeg -version
ffprobe -version
xcode-select -p
```

The core app is not an npm/Electron project and does not require a JavaScript package install. Pillow is required by the mock pipeline test, not the normal Python server. Optional ComfyUI TTS nodes have their own server-side dependencies.

## 2. Build and open the Mac app

From the repository root:

```bash
bash build_app.sh
open -n "$PWD/dist/Anime Studio.app"
```

The default bundle is `dist/Anime Studio.app`. An alternate destination can be passed explicitly:

```bash
bash build_app.sh "$HOME/Applications"
```

With Command Line Tools available, the script compiles the Swift native window. Otherwise the launcher may fall back to a browser window and/or first-launch compilation; inspect the build output before claiming a native build succeeded.

If the app is already running from the destination bundle, build into a staging destination first. Verify the new build, quit the old native app gracefully, wait for its owned backend to exit, then replace the bundle. Never overwrite a running bundle. Launch the exact bundle through `open -n`, not by running its inner window executable; the launcher establishes Python, port and project-data configuration.

The separate `/rebuild-anime` development workflow also commits intended source changes, pushes a feature branch and opens/updates a PR. It natively relaunches the app and leaves the PR open. A rebuild, pushed commit, merged PR and published release are separate outcomes.

## 3. Storage and launch configuration

### Native defaults

App-level configuration and the launcher log live in:

```text
~/Library/Application Support/AnimeStudio/
```

The `data-folder` file contains the actual project path. Read it before investigating a database or comparing projects:

```bash
cat "$HOME/Library/Application Support/AnimeStudio/data-folder"
```

Typical managed projects are stored under `Project Data` or `Projects/<derived identifier>` inside the same Application Support directory. The remembered path, rather than the location of the `.app`, determines which project opens.

The project contains `studio.db`, its media, history and exports. Startup should load app-owned storage automatically. A protected external folder explicitly selected by the user can still require macOS access; do not equate that permission path with the normal managed-storage path.

### Legacy migration

When a remembered legacy project is outside app-owned storage, the storage helper copies it to a managed location, backs up SQLite correctly, validates the database and remembers the new path after success. The original remains. Migration conflicts stop with a message instead of silently overwriting either copy.

Do not delete a database to resolve an access error. Do not make an empty folder at a missing remembered path and assume it restores the original project. Locate the original data and preserve it before changing preferences.

### Explicit overrides and standalone development

`ANIME_STUDIO_DATA` and `ANIME_STUDIO_PORT` are launcher overrides for an intentionally different data folder/port. Preserve any confirmed overrides when relaunching. An override does not automatically rewrite the remembered project pointer.

For development, use an explicit isolated server command:

```bash
python3 app/anime_studio.py \
  --host 127.0.0.1 \
  --port 8791 \
  --data /tmp/anime-studio-development
```

Open the local URL only for that development instance. The standalone command without `--data` defaults to `~/Documents/AnimeStudio`, which may differ from the native app's project. A newly initialized project can contain the repository's default ComfyUI instance; disable/remove it before a development worker can submit test jobs. For UI-only tests, use an isolated fixture server without a running render worker.

## 4. Connect a ComfyUI server

Start the server using its normal environment and expose it to the Mac on the intended trusted network. A typical direct ComfyUI command is:

```bash
python main.py --listen 0.0.0.0 --port 8188
```

In Anime Studio Settings:

1. Enter the instance name and reachable base address, for example `http://render-host:8188`.
2. Enable the instance and select only its intended job roles.
3. Save it and check connectivity.
4. Open Production and run **Check readiness**.
5. Resolve the listed node/model problems, then check again.

Connectivity alone does not qualify a workflow. The app inspects `/object_info`, including node classes and advertised model filenames, before routing generation. A passing check does not prove adequate VRAM, all internally downloaded weights or output quality.

### Multiple GPUs

The product's scheduling model treats each ComfyUI instance as a separate worker. For separate GPUs, run separate instances using the deployment's supported GPU selection, ports and temporary directories. For example, on a compatible direct Python deployment:

```bash
python main.py --listen 0.0.0.0 --port 8189 \
  --cuda-device 1 --temp-directory ~/ComfyUI/temp_gpu1
```

Add each address separately in Settings and assign roles appropriate to that machine's measured capacity. Do not assume a model fits merely because a role is enabled. Test the intended workflow and resolution on that instance before a large production batch.

## 5. Models and manual server updates

Server updates are performed manually. Anime Studio does not update ComfyUI, install Python packages, change services or download models through its production UI.

Before updating a maintained server, finish or intentionally cancel its work, record the current installation state and use that deployment's established update procedure. Restart every instance that needs to load the changed nodes/models. Run readiness again from the app afterward.

### Image and animation models

[server/download_models.sh](../server/download_models.sh) lists the model files used by the shipped image, animation and upscale starters. It downloads into `~/ComfyUI/models`; inspect that assumption before running it on a differently located installation.

Copy the complete Anime Studio `server` directory to the server and run the helper there.

```bash
bash download_models.sh
```

The script's downloaded filenames must match the workflow inputs. Its presence in GitHub does not mean it has already run on your server. Verify downloaded files and restart/refresh ComfyUI so its model lists update.

To install the optional 5B starter weights, run `python3 recipe_assets.py fetch /path/to/ComfyUI ti2v`. To check a host without changing it, run `python3 recipe_assets.py verify /path/to/ComfyUI`, optionally followed by a group name. The inventory compares the ComfyUI checkout with the reference release, checks the pinned Qwen node revision and distinguishes missing files from checksum mismatches.

The manifest pins asset bytes and source revisions. A fresh GPU render still needs qualification with the installed Python packages and GPU driver. Record the active environment with `python -m pip freeze` before upgrading and restore from the deployment's environment snapshot if rollback is needed.

### Voice cloning and voice design

[server/install_tts.sh](../server/install_tts.sh) installs the pinned ComfyUI-Qwen-TTS node pack, installs its requirements in the detected ComfyUI Python environment, and downloads the tokenizer, voice-cloning Base models and VoiceDesign model.

On the ComfyUI server:

```bash
COMFY=/path/to/ComfyUI bash install_tts.sh
```

The helper prefers `venv/bin/python`, then `.venv/bin/python`; otherwise it uses `PYTHON` or `python3`. Confirm that this is the environment used by your running server. The helper saves a pre-install package list and can change dependencies, including the node pack's Transformers requirement. The saved package list is useful for diagnostics but is not a full environment/system snapshot.

After installation:

1. Restart the intended ComfyUI instances.
2. Inspect their startup logs for Qwen node import errors.
3. Enable `voice` for character speech and `voice_design` for creating reference identities.
4. Confirm the workflows in Settings and run readiness checks.
5. Test one short character line before a batch.

The cloning model setting supports 0.6B and 1.7B. VoiceDesign uses its separate 1.7B model. A recording's exact transcript and the selected character reference are required for a useful voice-cloning workflow.

### Optional lip sync

The app includes the `wan22_s2v_14b_fp8` starter. You do not have to upload an API workflow when that built-in starter is compatible with your server.

1. Manually update ComfyUI through your deployment's normal process so the native S2V nodes are available.
2. Copy the complete Anime Studio `server` directory to the server.
3. Run it against the correct ComfyUI directory:

```bash
COMFY=/path/to/ComfyUI bash install_lipsync.sh
```

4. The helper downloads the S2V diffusion model, wav2vec2 audio encoder, UMT5 encoder and Wan VAE. It does not update code, packages, custom nodes or services.
5. Restart every instance assigned lip-sync work.
6. In Settings → Workflows, select the built-in S2V starter for Lip sync; enable `lipsync` on the intended server.
7. Run Production readiness checks.
8. Generate/select dialogue and a keyframe, then test one short shot.

If you prefer a custom S2V graph, test it in ComfyUI first and export API format as described next. A local application rebuild cannot repair missing server nodes or model files.

## 6. Use a custom API workflow

Build and run the graph in ComfyUI before importing it. Choose **Workflow → Export (API)** and upload the result under the appropriate Settings workflow slot. A normal editable UI graph is not interchangeable with API format.

The app recognizes these title slots:

- `PROMPT`, `NEGATIVE` for text.
- `REF1`, `REF2`, `REF3` for reference images.
- `START_IMAGE`, `END_IMAGE` for video boundary frames.
- `VIDEO_IN`, `AUDIO_IN` for input media.
- `SIZE` for applicable dimensions/frame length.
- `SAVE` for output naming.

Node-type detection provides some fallback behavior when titles are absent. Explicit, correctly tested titles make custom graphs easier to maintain. Seed/noise-seed inputs are patched per take. Check output nodes and media type, not just whether a graph returns success.

For video, set the workflow's model fps and maximum frames in Settings. The shipped 14B and 5B variants use different fps assumptions. Output episode fps is configured separately.

For voice cloning, the app supplies reference audio/transcript, spoken text and applicable language/style/model inputs. Match the actual node pack's input schema. Run readiness after uploading or editing a workflow.

Export built-in API examples without launching the app:

```bash
python3 app/anime_studio.py --export-workflows /tmp/anime-api-workflows
```

## 7. Backups and recovery

### Routine backup

Use Studio → History and recovery → **Back up portable project** when work is idle. Download the resulting archive and retain it outside the working project's disk/location. The archive includes database, media, exports and recovery files, so size can be substantial.

A checkpoint or archive inside the project is useful for revision recovery but is not protection against losing the entire project disk. History retains generated media and consumes space; the product does not provide a general automatic purge policy.

### Restore or import

Finish/cancel pending and running jobs, complete exports and cancel active preparation plans before restoring or changing episodes. Review the intended checkpoint and restore it. The current edit is checkpointed before replacement.

Use **Import project archive** for a portable archive. Imports validate paths and the database, reject expanded archives above 100 GB, namespace media to avoid collisions and retain the current edit in history. Server configuration is not adopted from the archive. Recheck imported episodes and media after opening them.

### Storage incidents

Close the verified app gracefully before copying a whole project directory for investigation. Preserve all database companion files when making a stopped-project copy. Prefer the SQLite backup API for a live database; an ordinary copy of only `studio.db` can omit committed WAL contents.

Do not run database-repair or purge operations merely because macOS reports authorization denied. First verify the active path, access and intact original data. Resolve a migration conflict by comparing the preserved copies, not by deleting whichever folder is inconvenient.

## 8. Troubleshooting

### The app does not start

Read:

```bash
tail -80 "$HOME/Library/Application Support/AnimeStudio/launcher.log"
```

Then read `app.log` in the folder named by `data-folder`. A fallback compilation can also write `build.log` there. Confirm Python availability, the exact bundle path and which process owns the local port. Quit the verified Anime Studio app and its owned backend gracefully; do not terminate unrelated Python services.

`/api/ping` identifies the app and version:

```bash
curl --noproxy '*' http://127.0.0.1:8700/api/ping
```

A matching version alone does not prove a new build is running. Verify a fresh launch log and the native executable/backend paths when rebuilding without a version bump.

### The project looks empty or the wrong episode is open

Check the remembered data folder and any launch overrides. Compare the native launcher configuration with a standalone server's `--data` argument. Inspect the episode library. Preserve the existing folders before changing a pointer; a new empty database is not recovery.

### SQLite reports authorization denied

Check whether the path is a legacy protected folder or an explicit external override. Managed Application Support storage should load without a recurring Documents-folder prompt. If intentionally using an external protected project, grant access through the app's folder-selection path. Keep the original database; this error alone does not mean corruption.

### Server dot is red or jobs stay queued

Verify the exact address from the Mac, the server's listening settings, enabled roles and connectivity. Run readiness, then inspect wait reasons and priorities in Render queue. A role with no compatible ready server will not dispatch. Clear or unknown timing estimates do not themselves indicate a stuck job.

### Missing node or unsupported model name

Read the readiness details and the ComfyUI startup log. Compare workflow filenames with installed models. Update/install on the server manually, restart the affected instances and check readiness again. Node presence does not prove an internally managed model has downloaded successfully.

### Voice is wrong, silent or attached to the wrong line

Open that line's Dialogue editor. Check its exact words, character, reference recording and transcript, then the selected take. Locations are not speaking characters. Use generation for new words; use matching recordings only when the selected character has a matching recorded line. Inspect whole-shot override mode and missing audio files before submitting another batch.

A changed line clears its prior selection. A stale editor error requires reviewing the latest saved shot. Save failures retain the local draft, so copy valuable changes before closing/reopening it.

### Render finished remotely but output is missing locally

Retry the recorded job so it can reconnect to its saved remote prompt/server and download existing output. Inspect ComfyUI history and local logs. Avoid deleting history or submitting another identical job before checking recovery. A submission lost before its prompt ID was recorded cannot be recovered by ID.

### Playback freezes or dialogue is cut off

Compare shot duration, source-in, animation coverage and audio length. Continue an incomplete animation chain or shorten the editorial shot. Generate/select voices before settling animation duration. Review quality-check issues and the assembly log; a valid encoded file can still have black/missing picture or held frames.

### A music cue is missing or doubled

Check enabled state, absolute start/anchor, source offset, duration, loop, volume and the build/remix inclusion checkbox. Repair a missing anchor. Check whether the chosen older export already contains music and lacks a clean master. Use the correct rendered master rather than stacking a new mix over embedded sound unintentionally.

## 9. Development validation

From the repository root:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
node --test tests/test_*.cjs
bash -n build_app.sh mac/launcher.sh
python3 tests/test_pipeline.py
```

The pipeline requires FFmpeg/ffprobe and Pillow. It starts mock ComfyUI servers on 9288/9289 and an isolated app on 8790, writing disposable fixtures under `tests/_run`. Check those ports and the fixture directory before starting it. It exercises script import, media generation, voice identity, lip sync, continuation, assembly and soundtrack export without a real ComfyUI GPU server.

For a documentation-only edit, check links, headings, examples, source references and terminology; a full rendering run is unnecessary. For rendering, queue, workflow, audio or assembly code, run the mock pipeline as well as relevant unit tests.

For UI changes, use isolated sample data and test wide/narrow layouts, keyboard focus, scroll preservation, asynchronous result updates and error retention. HTML changes require restarting the preview backend; reload the page after source changes. Native WebKit and hardware/GPU output remain separate acceptance checks.

After a native build, verify both plist version fields against `VERSION`, executable permissions and copied resources. New Python/JavaScript modules must be added to the explicit copy list. Do not commit bundles, project databases, generated media, logs, test fixture outputs, distribution archives or personal skill files.


## Local speech alignment (2.11)

The optional engine uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) for CPU word timestamps, then matches recognized characters to the saved script and exposes mismatches/confidence. Runtime audio decoding is normalized through the app’s existing FFmpeg dependency. The setup pins faster-whisper 1.2.1 and a compatible PyAV range rather than relying on an untested decoder update.

Use Settings → Tools & storage → Set up local alignment. The environment, downloaded base model, readiness marker and setup log live under `~/Library/Application Support/AnimeStudio/Alignment/`. Setup needs network access for packages/model files; recognition uses cached local files and never uploads project audio. A source-checkout installation can also run `bash mac/install_alignment.sh`; the script writes the readiness marker only after the model loads. If setup fails, inspect `setup.log` and use Repair alignment setup. Speech jobs interrupted by restart are marked for rerun. App shutdown terminates owned local alignment/setup processes without cancelling ComfyUI renders.

Validate with a short clean recorded line, inspect the recognized words and listen before saving suggestions. Model recognition quality varies with language, names, background music and delivery. Low-confidence or mismatched words require correction/import rather than silent acceptance.

The Settings storage manager is a project-scoped inventory. Moving candidates into its trash does not free disk space. Permanent deletion is a separate reviewed action and blocks referenced media/history/exports. Restore trash before restoring a checkpoint that newly references moved media.


### ComfyUI sound effects and ambience

In ComfyUI, install the checkpoint and encoder listed in the [Stable Audio Open template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/audio_stable_audio_example.json). Test the template with one short effect. In Anime Studio, select the Stable Audio Open starter under Settings → Workflows → Sound effects and ambience. Assign that task to a server and check readiness.

In Studio, move the playhead into a shot and choose Generate sound effect. Enter a prompt and choose Effects or Ambience. On completion, audition the candidate in the Sound library and use Add to episode to place it. The cue follows the original shot at the captured offset. The library keeps each candidate separate, including its seed and prompt.

For a custom workflow, tag its text node PROMPT and its optional negative encoder NEGATIVE. Use a seconds_total field or tag a node with a seconds or duration field DURATION. The final saver must export one audio file. A Load Image node titled PICTURE_IN receives the selected shot keyframe if your recipe uses one.

### ComfyUI music cues

Download the checkpoint listed in the [ACE-Step 1.5 Turbo ComfyUI template](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/audio_ace_step_1_5_checkpoint.json). Test a short instrumental cue on the server. In Settings, select the ACE-Step starter under Music cues, enable the Music cues task on that server, and check readiness.

In Studio, move the playhead to the start point and select Generate music cue. Enter the arrangement and instrumentation in the prompt. Tempo is available when the selected workflow exposes a BPM input. Audition candidates in the Sound library. Use Add to episode for a fresh cue or Replace music cue to preserve the timing and mixer placement of an existing cue while changing its source. The chosen cue feeds the same music bus used by the preview, final mix and stems.

### Working across projects

Use **Projects** in the sidebar to create a named project or open a folder containing an existing `studio.db`. New projects live in the application's managed storage. Opening a project adds it to the list and makes it the project that opens the next time you launch the desktop app.

Projects keep separate scripts, cast, media, ComfyUI connections, and render queues. When you switch, the previous controller remains active and imports its results into its own project. The Projects screen shows queued and active jobs for local controllers. Projects on this workstation take turns submitting work to a shared ComfyUI endpoint. To share that dispatch lock, use the same server address in each project.

Closing the desktop window uses **Keep rendering when Anime Studio closes** for the project on screen. Controllers left running by a switch continue independently. To stop one, open that project, turn off that setting, and close the app. Process and queue recovery after a system restart still occurs when each project is opened.
