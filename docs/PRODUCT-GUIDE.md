# Anime Studio product guide

Anime Studio organizes a production around shots and selected takes. It combines a local Mac editor with one or more ComfyUI servers, so you can revise a performance or picture without rebuilding the entire episode.

Use this guide for production tasks. For installation and manual server updates, see the [Operations guide](OPERATIONS.md). For the rules behind the interface, see the [Design guide](DESIGN-GUIDE.md).

## 1. Product model

### Project, series and episode

A **project** is the database and media folder used by the app. It can hold a series library with multiple **episodes**. An episode has its own script, shot list, editorial settings, audio cues and export records. Character assets and reference voices are shared across episodes in that project, as are server and workflow configuration.

The current edit is stored in the live database. **Save current episode** also stores an episode snapshot in the library. Switching episodes saves the outgoing episode before opening the chosen snapshot. A library snapshot is not an independent backup of all media.

### Scene and shot

A **scene** groups shots by scene name. Production preparation, continuity and cleanup act on the selected scene.

A **shot** is an ordered editorial unit with a duration, cast, picture prompts, optional dialogue and selected media. A still shot can be assembled from its selected keyframe. A motion shot uses a selected animation and, when necessary, continuation clips. Source-in trimming changes which part of the selected media appears in the episode.

Shot numbers are display order. Internal shot IDs identify records. Reordering does not mean creating new shots.

### Take, selection and approval

A **take** is a generated candidate. A **selection** is the candidate used by the current edit. Generating more candidates is different from choosing one. The default manual shot workflow leaves picture choice to the user; guided production plans can select completed downstream results as they advance.

**Keyframe approval** records acceptance of a selected keyframe for a preparation plan. Selecting a keyframe in Shots also records its approval; Production displays the approval state and lets you approve the selected keyframe explicitly. Input changes can invalidate that approval.

An **outdated** selection was made with inputs that differ from the current shot. It is still an available take; it is not proof of a broken file. Older takes without recorded input signatures can have unknown provenance.

### Dialogue and reference voice

A **dialogue line** combines spoken words, a character and optional delivery direction. A **reference voice** is a recording that defines the character's voice identity. Its transcript describes the reference recording; it is not the text that must be spoken in every generated line.

For example, Tobi's reference might say “We will find our way home.” The shot can ask Tobi to say “Wait by the bridge.” Generation uses the reference voice and the shot's new words together.

### Workflow, server and job

A **workflow** is a ComfyUI API-format graph used for a stage. A **server** is a configured ComfyUI instance with enabled roles. A **job** is one request to produce a take. A **preparation plan** coordinates multiple jobs and approval steps for a scene.

Enabling a server role does not install its nodes or models. Run readiness checks after changing its environment.

## 2. Navigation

The sidebar stays in place while the main workspace scrolls. Pages retain their scroll position when you switch away and return.

- **Plan:** Script, Cast, Reference library and Voices establish the production material.
- **Produce:** Shots, Production, Render queue and Generation lab generate and review media.
- **Finish:** Studio edits the episode; Assemble builds picture and sound exports.
- **Workspace:** Settings configures servers, workflows, tools and project defaults.

The sidebar's counts describe the current edit and queue. A line needing audio can have a character assigned but no selected performance. Server dots report connectivity; they do not certify that every enabled stage can run.

## 3. Start with a short project

1. Open Settings, set the server address and enable only the roles that instance should handle.
2. Run **Production → Check readiness**. Resolve missing nodes and models before queuing a production batch.
3. Enter a short scene in Script and save it.
4. Choose **Create shots from script** and review the imported shot boundaries, cast, dialogue and durations.
5. Add character references and reference voices in Cast.
6. Open a shot's **Dialogue**, confirm the character for each line and generate a performance.
7. Generate keyframes, choose the best takes and preview an animatic.
8. Animate only after the dialogue and shot lengths are satisfactory.
9. Assemble a short episode and inspect picture, sound and timing together.

This first pass tests the complete workflow on your hardware before you commit to a long production.

## 4. Script

### Import a script

Blank-line-separated blocks define shot passages. Scene headings such as `# Lantern bridge`, `INT.` and `EXT.` establish scene membership. Labeled speech creates dialogue. Plain action paragraphs create motion shots.

```text
# Lantern bridge — dusk

Tobi waits beneath a lantern while the river moves below.

TOBI: Do you think the lanterns will guide us home?

PIPPA: (warmly) They always have. Keep walking.
```

The importer also recognizes supported Markdown speaker labels, including `**TOBI:** ...` and `**TOBI**: ...`. Recognized parenthetical delivery directions are separated from spoken words. Metadata and montage headings should not become speakers. Several labeled lines within one block can remain separate dialogue lines.

Always review the result. A visually formatted screenplay can contain ambiguous headings or action text that still needs editorial correction. Saving text and creating shots are separate actions. Repeating **Create shots from script** appends shots; use revision review for changes to an existing edit.

### Revise an existing script

Use **Review script revision** to inspect proposed changes and match passages to existing shots. Retain the existing shot mapping when you want to preserve its identity and media. Choose “Create new shot” for a genuinely new passage. Review removals explicitly; an omitted passage is not automatically permission to delete its existing shot.

Apply only the intended changes. If the project changes after preview, produce a fresh preview rather than applying stale decisions.

### Import a spreadsheet

Use [the CSV template](../examples/shotlist_template.csv). Fields include sequence, scene, kind, cast, keyframe and motion prompts, camera, dialogue, duration, retiming and continuity flags. Separate cast names with semicolons. Quote CSV fields containing commas or line breaks. Import/export is for the shot list; it is not a complete project backup.

## 5. Cast

### Establish picture identity

Create character, location or prop entries. Add a reference image and a concise description of persistent visual traits. Turnaround sheets help maintain clothing, proportions and appearance. Select a sheet when it should become the asset's active reference.

A character LoRA can be named in the asset settings when the corresponding file is available on the ComfyUI server. The app does not train a LoRA. References and LoRA choices affect keyframe generation and can make previous keyframe selections outdated.

### Establish voice identity

Only character assets provide dialogue voices. Locations and props should not be assigned as speaking characters.

Choose one of two approaches:

1. **Upload a reference recording.** Use a short, clean recording of that character alone. The Cast editor suggests 5–15 seconds. Enter exactly what is said in that recording. Listen and choose the intended reference.
2. **Design a voice.** Enter a voice description and a sample line, generate candidates, preview them and choose the reference. Voice design needs its own workflow and server role. It uses the VoiceDesign model rather than the voice-cloning model-size setting.

Use voice clips for recordings and references. Treat “Use as reference” as a casting decision: future jobs will use that identity. Jobs already queued capture their reference and transcript when queued, so changing the reference does not rewrite those requests.

## 6. Dialogue and character voices

### Open the correct line

From Shots, choose **Dialogue** to edit the shot's lines. From Voices, choose **Edit dialogue** on a line to open that same editor with the line selected.

The left pane lists lines in order, with character, text excerpt and readiness. The right pane edits one line. The footer shows save status and the actions **Save dialogue**, **Generate all lines** and **Generate this line**. The line list and actions remain available while the selected editor scrolls; a narrow window stacks the panes.

### Write and generate

1. Select the dialogue line, or use **Add line**.
2. Choose the speaking **Character**.
3. Confirm the displayed reference and preview it if needed.
4. Enter the exact **Spoken words**, without a speaker prefix.
5. Optionally enter **Delivery**, such as “quietly, with a pause before the last word.” Model adherence can vary.
6. Choose one to four takes per line.
7. Choose **Generate this line** for the selected line or **Generate all lines** for the whole shot.

Both generation actions save all dialogue edits before queuing their requested lines. **Save dialogue** saves without submitting generation. Nonempty words are required for every retained line; generation additionally requires usable character reference audio for the requested lines. A missing reference is repaired in Cast.

If dialogue was found in the picture prompt, review the offered words and character choices before saving. The suggestion does not automatically submit jobs or rewrite the picture prompt.

### Review performances

Preview each take, then choose **Use take**. The selected performance feeds the shot's dialogue track. The first newly generated take is selected automatically unless you choose another while the batch is rendering. This also allows a generated performance to replace a temporary voice.

Changing words, character or delivery clears the selected audio when saved. It does not permanently erase old media. Earlier performances must not be mistaken for a match to the new dialogue. Generate again for the new inputs.

Stable character and line identities prevent repeated words from being routed to another character or line occurrence. Choosing audio for one line preserves unsaved edits elsewhere in the open editor. Background results update without replacing text being typed.

### Save state and failures

**Unsaved changes** means the local draft differs from the saved dialogue. Switching between lines keeps the draft. Closing asks whether to discard unsaved work. Removing a populated line asks for confirmation; save to apply the removal.

A failed save retains the draft. If the app reports that dialogue changed elsewhere, reopen the shot's voice editor using the current state before retrying. Copy any words you want to retain before discarding the old draft.

“Dialogue saved” and “voice generation could not start” can both be true: the words were stored but server readiness prevented queuing. Fix readiness and generate again. Queued takes are not completed audio.

### Recorded dialogue and temporary audio

**Use matching recordings** explicitly searches the selected character's clips for matching transcript text. It does not generate new words and does not fall back to another character's recordings.

Temporary macOS voices help with timing; they are not the character voice model. You can also upload line audio. A whole-shot audio override replaces line-based playback for the shot; switching back to generated dialogue requires the explicit replacement checkbox.

Selected line audio is joined with the project line-gap setting, defaulting to 0.3 seconds. Missing lines retain a silent place rather than shifting later lines forward. Audio operations can lengthen the shot to fit its dialogue; check the resulting duration before rendering animation. Saving dialogue by itself does not promise to resize every existing shot.

## 7. Voices workspace

Voices is the scene-level view of dialogue. Filter to missing audio, all lines or a character. Select lines for a batch, set takes per line and generate. **Generate all missing lines** requests audio for lines without a selection.

Use compact take previews and selection controls, or **Play scene** to listen to the chosen lines in order. Use **Edit dialogue** for words, character and delivery instead of changing those decisions in multiple places.

For older imports, expand **Dialogue recovery** and review **Recover dialogue from shot prompts**. Recovery previews affected shots, preserves existing dialogue and media, and submits no TTS jobs. Whole-shot audio overrides are excluded. Review newly found speakers and add their reference voices before generating.

## 8. Shots workspace

### Build the shot

Use **Edit shot** for kind, duration, prompts, camera, cast, continuity flags and notes. Use the shot's dedicated **Dialogue** action for detailed voice work. The main shot editor can save its dialogue changes before opening character-voice controls.

The episode strip shows relative shot duration and jumps to a shot. Filters show missing keyframes, clips, ready shots or lines without audio. Bulk controls act on selected shots; additional voice and finishing actions are under **More actions**.

### Choose and delete keyframes

Generate candidates and click the preferred keyframe to select it. Enlarging a preview is different from choosing it. Each keyframe has a delete control labeled with its take and shot, so you can remove the intended candidate while reviewing the board.

Review the deletion confirmation. If a selected keyframe is removed, inspect the resulting shot selection before continuing generation. Use Studio history to restore a prior editorial checkpoint when necessary. Deleting an item from the active edit does not imply that its retained file was permanently purged from disk.

### Delete generated clips

In **Shots**, each animation, lip-sync, continuation and upscale take has its own **Delete** button. Previewing a video also shows **Delete clip** on the preview. Confirm the take and shot before deleting it. The action removes that exact take from the shot without deleting its other candidates or keyframe. If the take is selected, its selection and dependent continuation/upscale selections are cleared as needed; select a replacement before assembling. The view stays at the current scroll position. Use **Studio → History and recovery** to restore the prior checkpoint. Media files remain available for recovery and existing exports.

### Animate and continue

Motion generation starts from a selected keyframe. The default 14B animation starter uses 16 fps and a configured maximum of 81 frames per clip; other workflows can use different frame rates and limits. The default shot duration is four seconds.

A longer shot can need multiple parts. The shot card shows dialogue duration, editorial shot length, selected motion coverage and uncovered time together. **Extend shot to fit audio** explicitly lengthens a shorter shot without removing its existing takes or script.

**Continue shot** opens a dedicated section editor with its approximate time range, the selected predecessor’s actual final frame, a saved section prompt and optional camera override. Save that direction independently or choose **Generate continuation** to save and queue candidates. Use **Direction…** beside an existing part to edit or regenerate that section. The shared shot-wide motion prompt stays intact. You can explicitly copy it as a starting point; scene preparation can reuse it for missing sections only when its fallback checkbox is selected.

Section capacity follows the effective workflow or assigned recipe’s fps and frame limit. The app snapshots direction, camera/style, boundary file/hash, source take chain, seed and workflow when queued. Retry keeps that snapshot. Changing an upstream selection retains later takes and flags incompatible continuation inputs for review. Regenerate affected stages restarts the earliest affected continuation, preserving successful earlier sections. Unknown historical directions are not invented for older clips.

Boundary frames are deduplicated during assembly, dialogue plays continuously across parts, and excess padding is trimmed to editorial duration. Without sufficient motion coverage, assembly holds the final frame and logs a warning. Inspect the joins on your real ComfyUI output.

**End on the next shot's keyframe** uses the first/last-frame workflow for the final part. Ensure the next shot has the intended keyframe and that the server supports that workflow.

Draft generation uses the configured smaller dimensions and frame cap; defaults are 512×288 and 49 frames. Rerendering a draft at full quality keeps its seed, but a matching seed does not guarantee identical results after other inputs change.

### Finish the picture

Generate and select dialogue first. For motion shots with selected audio, **Animate with voice** automatically uses an audio-capable animation workflow or a ready Lip sync/S2V default. It sends the corresponding dialogue section to ComfyUI and embeds the selected voice in the generated take. The separate Lip sync action remains available, including for still shots.

Upscaling is optional and needs the intended source clip. Choose finishing takes deliberately. Retiming is applied during assembly: hold frames, blend or RIFE when available. Workflow fps and episode output fps are separate settings.

## 9. Production workspace

Choose a scene before running scene actions. Set lighting, outfit, expression, prop and scene reference rules in **Scene continuity**. Up to three reference overrides feed keyframe generation. **Compare takes** helps review alternatives.

**Preview scene** and **Preview episode** build local animatics using selected keyframes, audio and durations. They submit no GPU jobs. Missing keyframes appear black. Use these previews to settle pacing before animation.

**Prepare this scene** coordinates missing/outdated voices, keyframes, explicit keyframe approval, animation, continuation and optional lip sync/upscaling. The plan persists across app restarts. When approval is requested, choose the intended keyframes in Shots and check their approval state in Production. Use **Approve this keyframe** there when needed.

A blocked plan explains its reason. Repair readiness or retry the failed jobs in Render queue, then resume. Cancelling a plan stops its unfinished work. Changing keyframe inputs can send a plan back to review.

**Regenerate affected stages** begins at the earliest affected dependency. Choosing a new upstream take can make later stages outdated again. Review selections before spending time on downstream renders.

### Delete clips or reset a scene

Open **Scene actions** and inspect the preview before applying either action:

- **Delete scene clips** removes generated animation, continuation, lip-sync and upscale takes from the active scene. It keeps shots, script, keyframes and dialogue audio.
- **Reset scene settings** keeps shot rows, order, kind, scene membership, dialogue text and the saved script. It clears generation prompts, cast links, camera/notes, retiming, source-in, continuity, generated/selected picture and voice media, and shot-anchored cues. Durations return to the project default.

Both create recovery checkpoints and retain underlying media for history. Existing exports remain. Finish or cancel the scene's queued/running work and the current export before cleanup; a changed preview must be reviewed again.

## 10. Render queue

The queue separates active, queued and completed renders, with failed/cancelled work shown when it needs attention.

- **Queued:** not started; position, priority and any known wait reason are visible.
- **Active:** dispatched or being collected. Current sampler/node progress can be shown alongside learned duration estimates.
- **Completed:** output has been collected and recorded. A job should reach 100% here, not merely because its estimated time expired.
- **Failed/cancelled:** inspect the reason, repair it and retry when appropriate.

Below an active render’s progress bar, **Models** lists the model filenames selected by its submitted workflow, including named LoRAs and supporting encoders. Voice jobs identify the Qwen3-TTS model size. The list describes the render’s workflow, not every model currently resident in GPU memory. Captured recipes are labelled when used as a fallback; earlier jobs without a captured graph show that details are unavailable. Later workflow edits do not rewrite a submitted render’s model list.

A sampler's step count describes that sampler node, not the entire workflow. Estimated completion uses comparable successful runs. New stages, unavailable servers and overdue work may have no reliable estimate. Higher priority affects waiting jobs; it does not preempt a running render.

**Manage queue** contains cancel-waiting and clear-finished actions. Clearing finished records does not mean deleting selected media; learned timing history is retained separately.

On restart or retry after an output-download problem, a job with a saved remote prompt ID can reconnect to existing ComfyUI work. Recovery cannot recover a prompt ID that was never saved, or history that ComfyUI no longer retains. Explicit cancellation followed by retry can start a new render.

## 11. Studio episode editing

Studio previews the episode's selected picture and dialogue with timed audio cues. Seek the full-width playhead, step frames or play the episode. Drag timeline shots to reorder them.

**Trim current shot** changes source-in and editorial duration. **Split at playhead** creates two shot sections using the same underlying media; it is not a new generation. Each half must be at least half a second. Inspect dialogue, continuation coverage and anchored cues after structural edits.

Use **Show current dialogue waveform** for the current shot's audio. It is an inspection aid; the app is not a full waveform-based audio workstation.

Manage episodes under **Series and episodes**. Use **History and recovery** for checkpoints, undo, restore and portable project archives. Complete/cancel pending work and active preparation before switching episodes or restoring a checkpoint.

## 12. Music and sound effects

### Add a cue at a specific time

In Assemble, choose **Add music / sound effect**. You can also add a cue from Studio.

1. Upload an audio file and give the cue a useful name.
2. Choose Music or Sound effect.
3. Set its episode start, or anchor it to a shot plus an offset.
4. Set the source offset, play duration, volume and fades.
5. Enable looping when the source should repeat to fill its duration.
6. Save and preview the relevant part of the episode.

Time inputs accept seconds, `MM:SS` or `HH:MM:SS`, including fractions. Source offset selects where playback begins within the uploaded file. Episode start selects where the cue enters the production. For example: start `00:12.5`, source offset `00:02`, duration `00:04` places four seconds of sound at episode time 12.5, beginning two seconds into the source.

Volume 0 mutes the cue; 1 retains its level; values up to 4 amplify it. Amplification can clip. Combined fades cannot exceed cue duration. Without looping, playback duration is limited to the available source audio. Cues may overlap.

Absolute cues stay at their episode time after shot edits. Shot-anchored cues follow their shot as order and earlier durations change. If the anchor disappears, the cue is disabled until repaired. Check cues that start beyond the episode end.

### Add a whole-episode music bed

Upload a music bed in Assemble and enable its mix checkbox. This is separate from timed cues. Check both the music-bed and timed-cue inclusion settings before building or remixing.

### Change sound after picture is rendered

Select a rendered episode in Assemble, preview it and choose **Export new soundtrack**. The app copies the existing picture stream and mixes the enabled cues/music without generating shots or submitting GPU jobs.

New episode builds retain a dialogue-only master, so repeated soundtrack exports can start from clean dialogue. Older discovered exports may already contain embedded music/effects and lack a clean master. Remixing those cannot remove the embedded soundtrack. Select the correct export; the current shot list may have changed since that picture was built.

## 13. Build and deliver

Run **Studio → Check episode quality** and address missing picture, missing dialogue, insufficient motion coverage, selected drafts, outdated takes and cut-off dialogue.

In Assemble, confirm sound inclusion and build the episode. Defaults are 1920×1080 at 16 fps; project output settings can differ. Review the build log. Missing picture can become black and insufficient motion can hold frames, so a successful encode is not a production-quality approval.

Assembly writes the episode, per-shot clips and a `timeline.edl` for further editing in DaVinci Resolve. Preserve the referenced clips when transferring the EDL.

Use **Edit subtitles** in Studio to review text and timing and export SRT. Generated timings for unvoiced lines are estimates. Subtitle generation follows the current edit; it does not automatically prove alignment with an older rendered export or burn captions into the picture.

For stems, choose a rendered episode in Assemble, then use **Export stems and loudness report** in Studio. A dialogue-only master is required. The export includes dialogue, music and effects as 24-bit WAV files, plus a loudness analysis report. Music/effects use the current cue settings; the chosen picture/master defines the rendered episode. Review that combination intentionally. Loudness analysis is not an automatic mastering approval.

## 14. History, backups and recovery

Editorial operations create checkpoints. Restoring a checkpoint first saves the current state, allowing a later reversal. Automatic daily checkpoints are created at startup when due and include a SQLite backup.

History and retained media consume disk space. They are stored with the project and do not protect against loss of that entire disk. Use **Back up portable project**, download the archive and keep a copy outside the working project location.

Archive import validates the database and archive paths, retains the current edit in history and namespaces imported media. It also brings archived episodes into the library without adopting their server configuration. Import has a 100 GB expanded-size limit. Use a checkpoint and an external backup before a major revision.

For startup, storage and server failures, follow the symptom-based instructions in the [Operations guide](OPERATIONS.md#8-troubleshooting).

## 15. Creative production tools

Use the [creative tools guide](CREATIVE-TOOLS.md) for versioned reference collections, continuity reviews, shot composition/pose/camera direction, masked image and interval video retakes, word timing and phrase performance editing, plus pinned generation recipes and comparison batches. These extend the workflows above without requiring a new project.


## 16. Settings

Settings uses focused sections instead of a single long form:

- **Project:** title, visual style and shared motion direction; negative prompts are under Advanced.
- **Generation:** source dimensions, draft limits, new-shot duration and render timeout.
- **Dialogue & voices:** model, language, temporary voice and inter-line pause.
- **Episode output:** assembled width, height and fps. Model generation fps is configured separately per workflow.
- **ComfyUI servers:** readable connection cards; Add/Edit server opens a focused name/address/task editor. Enable/disable controls whether it takes new jobs.
- **Workflows:** select one stage, then choose a starter or import/export API format. Video timing and server/API details are disclosures.
- **Tools & storage:** local tools and active project folder. Stopping the local server is under Advanced maintenance.

Project-setting drafts survive section navigation and background refresh. **Save changes** saves only changed values; **Discard changes** clears the draft after confirmation. Server and workflow actions save their own configuration, independently of project-setting drafts. A failed save retains your entries and displays an error. Configured workflows are not a claim of server readiness; use Production’s readiness check.


## 17. Reliable rendering, recovery and workspace tools (2.11)

### Captured render inputs

New render requests capture their API graph, model controls, seed, effective prompt/settings and independent copies of input media before entering the worker queue. Changing Settings, character references or source files later does not change a queued job. Results made with earlier inputs remain candidates and are not automatically selected over a changed shot. Completed jobs offer **Repeat captured render**, which queues a separate candidate with the recorded inputs and seed. Retry after a connection loss retains the remote prompt where available. Older jobs cannot recover configuration that was never recorded; clear-finished removes the retained queue record, so export/back up the project before discarding provenance you need.

### Local speech timing

Open a selected dialogue line’s **Performance** editor and choose **Align spoken words locally**. Setup is under **Settings → Tools & storage → Local speech alignment**. It installs a separate local Python environment and downloads a multilingual base speech model; recognition then runs on this Mac without uploading audio.

The result shows the recognized transcript, word ranges and confidence. **Use speech timing suggestions** is offered only when the script matches confidently with ordered ranges. Listen and choose **Save timing & pronunciation** to apply them. Unmatched/low-confidence speech remains available for manual review; the app does not present estimated or uncertain timings as verified alignment. Imported/manual timing remains supported. Recognition plus script matching is speech-based timing assistance, not phoneme-level forced alignment.

### Recovery drafts

Settings changes, shot dialogue and continuation direction are saved as recovery drafts on this device as you edit. Keys are scoped to the project and episode. After reopening, choose **Restore draft**, review it against current saved values and explicitly save to apply it to the project. Restoring never starts generation. **Discard saved draft** removes the recovery copy. Saving or explicitly discarding current edits clears the corresponding copy. Browser data deletion or a failed local-storage write can remove this protection; a draft is not an external backup.

### Studio tracks

Studio displays Picture, Dialogue, Music and Effects tracks with a time ruler, zoom and moving playhead. Click a track/ruler to scrub. **Show dialogue waveforms** loads cached waveform previews; trims show the relevant source window. Drag a picture clip’s start/end handle to trim, or use **Trim current shot** for keyboard/numeric editing. Start trimming changes source-in and duration; shot boundaries ripple. Drag cue blocks to move them; anchored cues retain their shot anchor. Double-click a cue to edit its mix settings. Multiple overlapping cues remain separately visible.

**Preview cut boundary** compares the outgoing and incoming source frames at their trim positions. It respects selected continuation chains. Review the assembled output for final camera effects and retiming.

### Storage review

Choose **Settings → Tools & storage → Review disk usage & cleanup**. Usage separates generated picture, dialogue, references/render inputs, cache, history, exports, backups, trash and other project files. Select candidates from a reviewed list and choose **Move reviewed files to trash**. This is reversible and does not free space yet. Restore a batch with **Restore**. **Review permanent deletion…** requires a fresh review and explicit confirmation before freeing space.

Selected media, retained render inputs, archived episodes, history and exports are protected. Cleanup waits for rendering, alignment and export work to finish; changed reviews and new references reject deletion. Keep an external backup before major maintenance.

### Errors and version information

Failed renders distinguish memory, model/file, node-pack, connection, timeout and input problems. Repair guidance links to the relevant shot or Settings section, with the full technical error in a disclosure. **Retry same inputs** retains the original configuration; use a new generation after changing resolution, models or prompts. **Reconnect render** uses the saved remote prompt when possible.

The bottom-left badge shows **Anime Studio v2.11**. Click it for the source commit, branch and build time. A local build with uncommitted changes is labelled accordingly; the badge is not a claim that a release was published.

## 18. Shot sound, mixing and transitions

Use **Export with sound** on a shot, or **Export current shot with sound** in Studio, to render its selected picture, dialogue and cues within that shot's timeline range. Absolute and shot-anchored cues are cropped to the range, including sounds that began earlier. The result appears in Assemble with a download link.

Choose **Render scene preview** in Studio, or **Render scene with sound** in Production. This uses the same picture conversion, transitions, dialogue timing and mixer as episode assembly. An unchanged scene reuses its cached MP4. Scene shots must be consecutive. Source selections, media stamps, timing, cues and mixer changes invalidate the cache.

**Audio mixer**, available in Studio and Assemble, controls Dialogue, Music, Ambience and Effects independently. Gain, mute, solo and stereo pan preview immediately while Studio plays. Live meters show each bus's peak level. Save the mixer to apply it to renders. Automatic ducking lowers music while dialogue is audible. Use rendered previews for final mix review.

Open a shot's **Transitions & audio timing** dialog to choose a cut, dissolve or fade through black into the next shot. Transitions preserve shot lengths and absolute cue positions. A dissolve reveals the incoming picture over the outgoing final frame at the cut. Negative dialogue offsets start speech before the picture (J-cut). A dialogue tail allows the recording to continue into the next shot (L-cut). Overlap adjacent dialogue and set fades to crossfade it. The source recording must contain enough audio for a tail.

**Sound library** stores reusable source recordings across episodes in the project. Import a sound, name it, choose a category and add comma-separated tags. Search by any of those fields, audition the sound and choose **Add to episode**. Select its track and timing in the existing audio cue dialog. Removing a library entry retains files referenced by existing cues, history and exports.

## 19. Reliable playback and export jobs

Shot clip playback now waits for the dialogue clock to start and synchronizes the video against the selected take's position in the shot. Continuation previews skip the duplicated boundary frame when finding their dialogue offset. Loading and playback failures appear on the preview. Replacing a take or leaving its player stops its audio.

Studio, subtitles, anchored cues and export use output-frame boundaries for shot durations. For example, a 0.1-second edit at 16 fps occupies two frames. The saved editorial duration remains available for further editing.

Exports run from a captured database and copies of their required media, while editing and render queue controls remain responsive. Assemble lists each export's status and completed media operations. **Cancel export** stops its local media subprocesses. **Resume captured export** retries its captured inputs after cancellation, an error or an app restart. A source changed before it could be captured produces an explicit error; start a new export to use changed inputs.

Live mixer tracks feed summed buses. Bus meters measure the combined output, and Studio and exports use the same block RMS ducking parameters. **Export stems and loudness report** runs in the background and its downloads appear with the export job. New stems use the rendered episode's saved mix configuration, even if current controls have changed. Older exports without a saved recipe use the current mix and identify that fallback in the result.

Scoped preview cache keys include the selected picture, audible dialogue and cues, media stamps and render settings. Changing unrelated script text does not invalidate the preview. Dissolve and fade boundaries render each affected shot once and then concatenate the results, avoiding repeated encoding of the accumulated episode.

### Shot playback with sound

In Shots, choose a clip take and press **Play with sound**. The app prepares a local MP4 containing that take's picture, selected shot dialogue, and overlapping timed music, ambience and effects. Preparation runs independently from episode exports and never submits a ComfyUI render. The same file supplies picture and sound to the native video controls, including seeking, volume, fullscreen and looping. **Save preview** downloads that combined file.

The first play prepares and caches a preview up to 1280 pixels wide. Later plays reuse it; dialogue, timing, source files, cues or mixer changes produce a new cache entry. Source clip timing and frame rate are retained. Continuation previews use the corresponding section of shot dialogue, accounting for duplicate boundary frames. Previewing an alternate take does not approve it. Clip previews show the complete take; shot trims and the assembled continuation chain are applied by **Export with sound**.

Assigned dialogue replaces embedded clip audio to avoid hearing voices twice. If no dialogue is assigned, existing embedded clip audio is retained. Timed cues and saved mixer gain, mute, solo, pan and ducking are applied; the separate episode-wide music bed is reserved for episode/scene playback and exports. Missing files produce a visible Retry action. The player identifies unassigned dialogue or a muted mix instead of silently running a second audio player.

### Voice-aware clip generation

The selected shot dialogue is copied into the render snapshot when you queue Animate, Continue or Lip sync. Editing or regenerating a voice afterward leaves the queued render unchanged. Repeat/retry uses the captured voice; generating a new take uses the new selection. Generation includes the voice in the take's own MP4, so opening the generated file outside Anime Studio also plays it. Upscaled takes retain the selected voice as well.

Compatible workflows receive a bounded WAV aligned to that clip's source time, including dialogue offsets and fades. Continuations receive the later dialogue section with the duplicate boundary frame accounted for. The voice and picture keep their original time scale; audio is never sped up to change frame rate. S2V's required generation padding is trimmed to the requested frame count in the saved take.

An explicitly assigned generation recipe and end-on-next-shot constraints are retained. Audio-aware assigned recipes receive the voice directly. If the selected recipe cannot use audio, or the default S2V workflow is unavailable on enabled servers, generation keeps the picture workflow and embeds the voice; the queue confirmation explains that audio-driven animation was unavailable. It never claims that attaching a soundtrack alone creates lip sync.

For manual ComfyUI server setup, configure or verify **Settings → Workflows → Lip sync** and run **Production → Check readiness**. On the rendering server, run a compatible S2V template with its required nodes/models, then export its API workflow if it differs from the configured default. Label its connected image/audio loaders `START_IMAGE` and `AUDIO_IN`, and enter its actual generation fps and frame limit in the app. Enable the server for Clip/Continue jobs so automatically routed renders can use it. No server installation or remote GPU render is performed by an app rebuild.

### GPU memory failures during voice animation

`CLIPTextEncode: VRAM grow failed` means prompt encoding could not allocate GPU memory, before video sampling starts. S2V prompt encoding stays on GPU: new jobs use `CLIPLoader.device=default` and remove explicit CPU overrides from captured S2V graphs. Other workflows and existing snapshots are preserved. The app does not silently switch voice animation to CPU.

On the rendering server, run `nvidia-smi` to inspect **free memory on the GPU ComfyUI is using** and the processes holding allocations. A 12 GB card with only 1 GB free cannot satisfy a new 2 GB allocation. Identify the owners before stopping any process. If the competing process is Ollama, `ollama ps` identifies its loaded models; stop only models you intend to unload. If the allocation belongs to idle ComfyUI, restart ComfyUI after checking no renders are active. Do not use CPU/low-VRAM flags as an automatic replacement for GPU prompt encoding.

After freeing memory, Retry can use the same GPU snapshot. After changing a workflow, generate a **new** job; Retry intentionally retains its captured inputs. Reducing resolution/frame count can reduce video-sampling memory, but does not directly reduce the text encoder's model-weight allocation. The 14B video model still needs sufficient peak GPU memory even after prompt encoding succeeds. The shot-length/Continue notice is about segment coverage, not an explanation of this encoder failure.

Reference: [ComfyUI CLIPLoader documentation](https://github.com/Comfy-Org/embedded-docs/blob/main/comfyui_embedded_docs/docs/CLIPLoader/en.md).

### Native 16 fps Studio playback

Studio and new episode exports default to 16 fps to match generated shots. The playhead, previous/next-frame actions, trim increments, frame timecode, animatics and camera previews use the project frame rate. Playback stays at normal speed: one source second remains one real second, with audio at its original rate.

Existing projects and older saved episodes using the legacy 24 fps default are upgraded to 16 fps once. Source files, completed exports and captured render/export jobs retain their original media and timing. A later explicit output-setting change is preserved across restarts.
