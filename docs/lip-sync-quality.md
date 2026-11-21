# Voice animation and lip-sync quality

## Choose the right operation

**Animate with voice** creates a new performance from the selected keyframe and the shot’s selected character voice. It can change the whole picture. The built-in Wan 2.2 S2V FP8 workflow runs at **16 fps**, normally with 20 steps and CFG 6. It does not use a Lightning LoRA.

**Lip-sync existing clip** passes the selected clip and voice to an imported, tested video-conditioned workflow. It requires a `LoadVideo` or `VHS_LoadVideo` node titled `VIDEO_IN` and a connected `AUDIO_IN`; both must feed the saved video. This is an integration path for your tested workflow, not a supplied mouth-only editing model. A video-conditioned model can still alter appearance; compare its output before selecting it. The built-in image-based starter is rejected for this operation. Existing clips are retained as takes.

To keep the built-in voice animation starter available, import the editing workflow as a **lip-sync recipe** in Generation lab and assign it to the shot’s **lipsync** stage. The **animate** stage can continue to use the built-in S2V starter. Existing-clip input is the selected clip, not a flattened episode or an entire continuation chain.

Scene production’s optional lip-sync finishing phase has the same requirement. When using built-in S2V animation, leave that extra finishing option off: the animation already uses the selected voice.

## Framing and performance

Open **Animate with voice** on a shot containing selected voice audio and a selected keyframe. Review the reference image before generating.

- **Keep reference framing** preserves the reference image. Wide shots can leave too few face pixels for readable mouth articulation.
- **Dialogue close-up** crops the central 55% of image width and 55% of height, starting 22.5% from the left and 5% from the top. The editor outlines this exact region. This is a fixed crop, not face detection; choose a different keyframe if the subject is outside it. The original keyframe file is never modified.
- **Speaking direction** adds editable performance instructions to the captured prompt. It defaults to natural speech, clear articulation, restrained head movement and consistent facial features. Blank direction disables the addition.

Framing changes picture composition. Increasing resolution or upscaling does not guarantee better mouth movement; evaluate a tightly framed short example first.

## Sampling comparisons

The built-in starter offers **Standard (20 steps)** and **Experimental (30 steps)**, both CFG 6. A comparison queues two candidates with the same seed, reference, voice and direction. Inspect both takes; more steps cost time and are not a validated quality guarantee. Sampler step counts are recorded in the take’s information. Versioned recipes and imported workflows retain their sampling settings; the comparison selector is disabled for them in this editor.

These settings are captured when queuing. Editing the shot affects newly generated jobs; Retry/Repeat preserves captured inputs. Choosing another character voice or changing dialogue timing invalidates downstream animation provenance.

## Motion-aware continuation

For native S2V continuations, the client prepares a silent, bounded tail of up to **73 frames at 16 fps** from the selected predecessor. `LoadVideo` and `GetVideoComponents` feed those images into `WanSoundImageToVideo.ref_motion`. The original captured reference image anchors identity when available. The selected voice section is trimmed and aligned separately, using the same offsets as final audio muxing.

This uses Wan’s native motion-reference path between independent jobs. It does **not** persist raw GPU latents or run one multi-extension latent graph. It avoids requiring a completed GPU latent to remain resident or be serialized, but its visual quality still needs evaluation on the GPU server. It does not guarantee seamless joins.

The captured audio and generated video are combined without speeding up or slowing down the picture. Short native generations may be padded to the model minimum, then trimmed when saving the take. Long motion shots use **Continue shot** with separate direction for each section.

## Manual ComfyUI setup

No new model download is required for these improvements if the existing Wan S2V starter already runs. Required native nodes are:

- `WanSoundImageToVideo` with optional `ref_motion`.
- `LoadVideo` and `GetVideoComponents`.
- Existing audio encoder, sampler, model loader and VAE nodes from the starter.

The server inspected during development already exposed the motion-reference input. You can inspect all node schemas without submitting work:

```bash
curl -fsS http://127.0.0.1:8188/object_info/LoadVideo
curl -fsS http://127.0.0.1:8188/object_info/GetVideoComponents
curl -fsS http://127.0.0.1:8188/object_info/WanSoundImageToVideo
```

If these nodes or the `ref_motion` input are missing, update ComfyUI using your existing installation procedure, install its current requirements into its existing Python environment, and restart the instances assigned animation/continuation work. Then run Production → Check readiness. The app checks the effective continuation graph before queuing it; missing nodes block submission.

Keep FP8 initially on a 12 GB GPU. This change does not install BF16, switch prompt encoding to CPU, stop Ollama, or change server services. Avoid competing GPU workloads during quality comparisons.

## Acceptance on real GPU outputs

Use a short, clean, single-speaker line and a front-facing reference with a visible mouth. Compare source framing versus close-up, then compare 20 versus 30 steps with a fixed seed. Listen to consonants and inspect mouth closures, identity stability and the joins between continuations. Include a silent lead-in, a short final section and a line longer than one section.

Automated checks cover graph wiring, immutable captures, model cadence, bounded motion video, audio/video muxing, candidate controls and failure states. Mock/FFmpeg tests do not establish generated visual quality. No live GPU generation is triggered by the test suite.
