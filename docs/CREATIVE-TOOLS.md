# Creative production tools

The creative tools add versioned references, shot direction, repair candidates, performance editing and generation recipes to the existing Anime Studio workflow. All data stays in the project. Generation runs through the existing Render queue and configured ComfyUI instances. These tools do not install nodes, download models or update your server.

## 1. Reference library

Open **Reference library** in the Plan group, or **Reference collections** on a Cast card.

1. Choose **Save reference collection** and select a character, location or prop.
2. Name the collection, such as “Travelling outfit.”
3. Choose existing reference images and/or upload additional PNGs. Add continuity notes.
4. Save. The collection captures those images and the cast description/LoRA settings as a new immutable version.
5. Choose **Use in scene**, the scene and the reference view to feed generation.

Saving the same collection name again creates the next version. Existing scene assignments remain pinned. A scene can use up to three image overrides, consistent with the existing reference workflow. Removing a binding returns that asset to the ordinary scene/cast reference behavior.

The continuity review pairs selected shot keyframes with the pinned scene references. **Mark reviewed** records the current keyframe/input signature and optional notes. Changed picture or generation inputs make that review outdated. This is a human continuity decision, not an automatic identity score.

The library is shared across episodes; each episode's scene assignments are stored with that episode. Changing Cast later does not rewrite an existing collection's captured description or LoRA configuration.

## 2. Shot Direction

Choose **Direction** on a shot card.

- **Composition:** drag subject boxes; add/remove subjects and adjust numeric coordinates.
- **Pose:** drag the body skeleton's joints. Joint coordinates also have keyboard-editable fields.
- **Camera:** choose start/end center and zoom. Clicking the canvas in Camera mode places the end-frame center.
- **Direction notes:** describe framing, eyeline or other generation instructions.

**Save & preview camera** saves the direction and renders a local preview from the selected keyframe. It does not submit GPU work. Camera paths apply to animatics and still-shot assembly; they do not automatically force a generative video model to follow that path. Trimming/splitting remains editorial work; splitting also divides an existing camera path between the two shot halves.

### Pose, depth and layout conditioning

To send conditioning into generation:

1. Enable the pose skeleton and/or composition map, or upload a depth PNG.
2. Enable **Send conditioning to generation** and save.
3. Assign a compatible keyframe recipe in Generation lab.
4. Check that recipe's servers, then generate keyframes normally.

A compatible API graph must have connected `LoadImage` nodes titled `POSE`, `DEPTH` and/or `LAYOUT` for each enabled input. Connect those nodes to the model-specific conditioning branch and ultimately to a save output. Merely adding an unused input node is not sufficient. The app rejects missing/disconnected requested slots instead of silently ignoring them.

The body skeleton is a pose-conditioning image, not a character rig. Correct control models and preprocessors depend on the chosen image-generation model. Do not assume an SD1.5 ControlNet can be attached to the Qwen or Wan starters. Build and test the compatible graph on your manually maintained server, then import API format.

Camera-only changes do not invalidate the original keyframe. Notes or conditioning changes that affect image generation do.

## 3. Repair keyframes and video intervals

Choose **Repair** on a shot card and select the source take. Repair requests create candidates and retain the original file and selection.

### Masked keyframe repair

Paint the area to replace, or upload a mask PNG. Add repair instructions and choose a repair recipe. **Queue repair candidate** sends the request through the render queue. Preview the result, then choose **Use result** when satisfied.

The supplied **Masked keyframe repair** recipe uses the shipped Qwen image-edit workflow and the core `ImageCompositeMasked` node. A generated edit is composited into the mask while the original image is retained outside it. Review the edited region and its edges; compositing does not guarantee that the generated replacement is artistically correct.

The brush overlay marks the editable area. Exported masks follow `LoadImage`'s inverse-alpha MASK convention: transparent mask pixels are editable, opaque pixels are protected. For a custom repair graph, use connected `SOURCE` and `MASK` `LoadImage` nodes; the mask normally comes from the MASK output of the mask loader. The built-in graph also uses the source as its first image-edit reference.

Canvas painting supports source images up to 4096 pixels per side. Uploaded PNGs are validated for bounded dimensions/size. For a large source, supply an appropriately prepared mask and test the recipe's dimensions.

### Video interval retake

Choose a video take, enter start/end times in that source clip and describe the motion to replace. The supplied **Boundary-guided video retake** recipe uses the source boundary frames with Wan first/last-frame generation.

The interval is limited by the recipe's fps/frame cap. A completed result is assembled into a new full-length candidate, retaining the portions before and after the interval. The candidate is re-encoded, so retained footage is not promised to be byte-identical. Dialogue stays separate in the shot's audio track.

Inspect temporal continuity at both joins. Short-window retaking is not a guarantee of flicker-free video inpainting, and the supplied interval recipe does not provide a moving spatial mask. Custom video recipes can also receive an interval through an explicit `VIDEO_IN` input.

Changed source/shot inputs reject a stale repair request or stale direct application. Retained candidates remain available for deliberate review in Shots.

## 4. Dialogue Performance

Generate/select line audio first. Open **Dialogue → Performance** on that saved line. Save dialogue edits before switching to the performance editor.

The editor shows the selected audio, waveform, word start/end times, line lead-in and pause-after controls. Word timing is relative to the source line audio; lead-in places that line later in the shot's assembled dialogue.

### Word alignment

Automatic suggestions use audio energy and word-length allocation. They are explicitly **estimated**, not forced-alignment or speech-recognition results. Correct them manually or import a word-alignment JSON file, listen and save.

Supported import shapes include a word list, `{ "words": [...] }`, or a transcript with `segments[].words`. Each word needs `text` (or `word`), `start` and `end`, in seconds:

```json
{
  "words": [
    { "text": "Wait", "start": 0.15, "end": 0.48 },
    { "text": "here.", "start": 0.55, "end": 1.02 }
  ]
}
```

Word count/order must match the saved dialogue. Import tolerates casing/punctuation differences when matching, while preserving the original script words. Ranges must fit the audio, be positive and not overlap. Performance editing is bounded to ten minutes of source audio per line.

### Pauses and pronunciation

Set lead-in silence and the pause after the line. The final line does not add a trailing inter-line gap. These changes rebuild the shot's dialogue mix and are also considered when generating subtitle timing.

The character pronunciation dictionary accepts one pair per line:

```text
Tobi = Toh bee
Pippa = Pip ah
```

Synthesis receives the spoken spelling while the script, line identity and visible dialogue remain unchanged. Dictionaries are shared with the character across episodes. Timing and pronunciation are validated together on save; a failed derived-audio rebuild restores the prior timing/dictionary values.

### Phrase retakes

1. Review and save the word timings.
2. Select the words to replace. The editor uses the entire interval between the first and last selected word.
3. Choose **Queue phrase retake**. It uses the line's character reference and voice workflow/recipe.
4. Preview the returned phrase; the selected full-line performance remains unchanged.
5. Choose **Apply phrase** explicitly.

With **Fit replacement to the original interval** enabled, the replacement is time-adjusted to keep that interval's length. With it disabled, later audio ripples by the new phrase duration. Small fades soften the cut edges. Application creates and selects a new full-line voice take; history and the previous take preserve the original.

Changed words, character, delivery, selected base audio or relevant voice-generation inputs prevent a stale phrase from overwriting a newer performance. A line with pending voice work must finish/cancel that work before queuing another phrase.

Detailed viseme/rig editing is not part of the current S2V backend. Use the timing/retake tools for speech and the existing lip-sync or repair workflows for the talking picture.

## 5. Generation lab

Open **Generation lab** in the Produce group. Recipes are project-wide immutable versions. Assignments are per shot and stage, so different shots can use different workflows without replacing the global workflow.

### Create and assign recipes

1. Choose **Create recipe**. Copy the current stage or import a tested API workflow/previous recipe package.
2. Name it, choose its stage and enter video fps/frame limits where applicable.
3. Select the scalar controls artists may adjust, such as guidance, sampler steps or voice temperature.
4. Add manual node/model installation notes and save the version.
5. Choose **Shot recipe settings**, select a stage/version and set its exposed values.

Shot media, spoken words, seed, dimensions, reference transcript and delivery remain app-managed. They cannot be exposed as conflicting recipe controls. Parameter choices/ranges are checked against the enabled servers' advertised node inputs. **Check servers** reports readiness and installation notes; it does not install or update anything, or certify VRAM fit.

Normal generation uses the pinned recipe when assigned. New queued recipe jobs capture the graph, controls, prompts and input paths, so subsequent recipe/global-workflow edits do not rewrite them. Changing a shot's assignment can make its existing takes outdated. An unavailable recipe from an old episode is reported instead of silently replacing it.

### Controlled comparisons

Choose **Compare variants** on a generation recipe. Select a shot (and a dialogue line for voice recipes), choose a starting seed, and queue one to eight candidates. Either vary seeds with the same settings or enter one value per line for an exposed parameter. Keep the same seed when isolating a parameter change.

Candidates appear side by side with their recipe version, settings, seed, status and elapsed job time. Nothing in a comparison is selected automatically. **Use result** adopts its recipe values for that shot and selects the candidate. Invalid variants leave no partially queued comparison batch.

### Benchmark records and exports

Recent completed runs provide median/minimum/maximum job wall times per recipe and recorded server identity/hardware. Wall time includes output collection and can include reconnect delays; it is not a pure GPU kernel benchmark. Failed runs are excluded. Queue cleanup retains the recorded run statistics, while media remains accessible from the ordinary shot/voice take lists.

Export an **API workflow** to inspect/run it in ComfyUI, or a **recipe package** to preserve the graph, exposed controls, version information and installation notes for reimport. Installed package/model versions are not automatically attested; record those requirements manually when reproducibility matters. Equal seeds do not guarantee identical output across changed software/hardware.

## Storage, recovery and validation

Collections, scene assignments, direction, recipes and performance settings participate in project history/portable archives and episode scope as appropriate. Per-shot data is cleared when a shot is deleted or generation settings are reset; shared collections and recipes remain available. Old checkpoints clear newer episode-specific controls rather than attaching them to reused IDs.

The backend and UI suites cover immutable versions, actual conditioning paths, stale-data protection, recipe dispatch and controlled comparison. FFmpeg tests check camera previews, interval duration/outside-frame retention and full-line phrase assembly. The expanded mock ComfyUI pipeline exercises queued recipe generation, masked repair collection and phrase application. Real GPU output quality and model-specific conditioning still need acceptance on your server.
