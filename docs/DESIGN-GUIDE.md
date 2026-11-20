# Anime Studio product design guide

Anime Studio should feel like a focused production desk: readable script pages, clear shot decisions, predictable tools and visible render progress. The interface must help people understand what will be saved, generated, selected or exported before they act.

This guide defines the design system for the 2.11 product and the acceptance requirements for future work. Token values and component names refer to the current implementation. Requirements below are review criteria; they are not a claim that the entire application has passed an accessibility certification.

See the [Product guide](PRODUCT-GUIDE.md) for user instructions and the [Architecture guide](ARCHITECTURE.md) for implementation contracts.

## 1. Product principles

### Keep editorial control visible

Users choose the words, speaking character, references, picture takes and final performances. Generation produces candidates; selection expresses the current edit. When an automatic selection occurs, such as the first new voice take or a guided plan's downstream choice, explain it in the workflow.

Distinguish preview, selection, approval and deletion in the interface. In the current implementation, selecting a keyframe also records its approval; surface that consequence rather than implying a mandatory second approval. A person should be able to inspect a candidate without accidentally changing the episode.

### Preserve the working context

Do not return the main workspace to the top when a user selects a take or receives a render result. Preserve scroll position per page, local draft text, caret position and the selected dialogue line. A refresh is new information, not a request to restart the task.

### Make expensive actions deliberate

Generating animation and redoing downstream stages can cost substantial render time. Show prerequisites, affected shots, number of takes and what has become outdated. Use explicit approval where the workflow already requires it. Do not make a simple inspection or navigation action submit GPU work.

### Expose the next useful decision

Place routine actions near the object they affect. Keep technical details in Settings and troubleshooting, except when a missing requirement prevents a meaningful production decision. Put scene cleanup and queue maintenance behind a named secondary action.

### Treat recovery as part of editing

Scene reset and deletion should describe their scope and recovery path. Preserve original projects during migration. State save outcomes accurately: saved words, queued work, selected audio and finished export are distinct results.

## 2. Information architecture

Keep the navigation groups and order:

- **Plan:** Script → Cast → Reference library → Voices.
- **Produce:** Shots → Production → Render queue → Generation lab.
- **Finish:** Studio → Assemble.
- **Workspace:** Settings.

Each page needs a plain title, one concise purpose statement and a clear starting action. Use the current page indicator and programmatic `aria-current="page"`. Japanese secondary labels are part of the existing visual identity; the English task label remains the primary navigation name.

Use counts as context, not as unexplained badges. “12 of 20 lines need audio” tells the user more than “12.” Readiness counts should not imply that a render has passed quality review.

A scene-level action must expose its scene context. A shot-level dialog must expose the shot number and scene. A line-level action must identify the character and line occurrence, especially when the text repeats.

## 3. Application shell and layout

### Main frame

The body is a fixed-height two-column grid. The default sidebar is 208 CSS px, reducing to 176 below 1000 px and 144 below 600 px. The main column uses `minmax(0,1fr)` so content can shrink without pushing navigation away.

Keep the sidebar independent of main scrolling. It may scroll internally when the window is too short. The main area owns vertical scrolling and retains a visible scrollbar gutter. Do not add a competing document/body scroll container.

Default main padding is 28 px horizontally and 80 px at the bottom, reduced on smaller windows. The low-contrast 24 px grid belongs to the workspace background. Cards and form surfaces remain solid white.

### Page and section hierarchy

Use the page title for the workspace purpose, section headings for tasks, and card headings for objects. Avoid presenting every panel as another top-level title. Keep repeated help text short; place rare recovery or maintenance instructions in disclosures.

Settings-style sections use a narrow explanatory column and a wider control panel. They stack at smaller widths. Technical tables may scroll horizontally; do not compress names and addresses until they are unreadable.

### Density

Use space to separate decisions, not to decorate the page. New layouts should use a small spacing family around 4, 8, 12, 16, 20, 24 and 28 px, matching adjacent components. Keep related label/control/help content together. Avoid many slightly different gaps in one form.

For a collection with repeated editors, prefer a list and a focused detail pane when showing every full form would require substantial scrolling. Dialogue is the reference example.

## 4. Visual tokens

The canonical values live in the `:root` block of [the interface](../app/ui.html). Reuse the variables rather than introducing near-duplicate colors.

### Neutral surfaces

- `--paper: #F2F4F7` — workspace and quiet secondary surfaces.
- `--sheet: #FFFFFF` — cards, forms and dialogs.
- `--ink: #1E2229` — primary text and strong neutral actions.
- `--graphite: #677080` — secondary text.
- `--line: #C9D7E6` — primary borders and dividers.
- `--line2: #DCE6F0` — softer internal separators.
- `--screen: #262A31` — picture and video monitors.

### Semantic colors

- `--blue: #2F6FA8`, `--blue-soft: #E4EEF8` — primary actions, focus and selection.
- `--red: #C8372D`, `--red-soft: #FBEAE8` — destructive actions and errors; the existing nav indicator also uses red.
- `--green: #2E8B5E`, `--green-soft: #E3F3EA` — successful and ready states.
- `--amber: #C27712`, `--amber-soft: #FBF0DE` — warnings, missing prerequisites and pending attention.

A token's presence does not approve every foreground/background combination. Measure contrast for actual text size, weight and surface. Amber and softened/transparent colors especially need review before being used for small essential text.

State must also have text or another distinguishable cue: “Selected,” “Queued,” “Needs voice,” a check mark or a labeled progress state. A colored dot alone is insufficient.

### Typography

- **Interface:** `--f-ui` uses Hiragino Sans/Hiragino Kaku Gothic ProN, system fonts and CJK fallbacks. Body baseline is 14 px with 1.5 line height.
- **Display:** `--f-display` uses Avenir Next Condensed, Avenir Next and condensed fallbacks for titles, section labels and shot numbers.
- **Script:** `--f-script` uses Courier Prime, Courier New and monospace fallbacks for screenplay-like text and code.

Current reference sizes include a 30 px page title, 22 px dialog heading, 28 px Dialogue heading, 18–19 px focused section headings, 15 px spoken-word input and 12–13 px labels/help. Essential production content should not depend on tiny decorative labels. Allow text wrapping and system fallback fonts without losing actions.

Use tabular numbers for duration and progress where available. Distinguish a shot number from a timecode and a job ID.

### Shape and depth

Keep corners restrained: approximately 5–7 px for controls/cards and 8 px for dialogs. Use thin borders to establish surfaces. Reserve strong shadows for modal dialogs and floating action menus. Soft selected backgrounds and explicit borders should communicate selection more clearly than hand-drawn decoration.

## 5. Components

### Buttons and action hierarchy

Use `.btn` for a standard action and `.btn.pri` for the main next step. Standard buttons have a 36 px minimum height; compact `.btn.sm` controls use 30 px. Compact does not mean inaccessible: provide adequate hit area and spacing.

Use `.btn.ghost` for low-emphasis navigation or auxiliary work. Destructive actions use `.danger` and explicit wording, not the same visual priority as routine generation. Keep the action's object in its name: “Delete selected shots…” or “Reset scene settings…”.

A label should say what happens: Save dialogue, Generate this line, Use take, Build episode. Use an ellipsis when the action opens a further choice or confirmation. Keep one dominant action per task region; a page can have several regions, but its toolbar should not look like an undifferentiated command strip.

For broader platform guidance, consult [Apple's button guidance](https://developer.apple.com/design/human-interface-guidelines/buttons). Anime Studio's concrete sizes and color roles remain product-specific.

### Toolbars and action menus

Group selection controls, batch parameters and generation actions. Wrap the group at small widths instead of forcing the page wider. Show the common actions; put additional finishing and maintenance actions in **More actions**, **Scene actions** or **Manage queue**.

Action disclosures must have meaningful summaries. Close an action menu after a choice, on Escape and when clicking outside. Do not hide the only way to understand a current error in a closed menu.

### Fields

Use a visible label associated with every field. Placeholders provide an example; they do not replace labels. Put units in the label or adjacent text: seconds, takes per line, source offset. Keep technical keys out of everyday production labels.

Use a textarea for spoken words and script. Use a character selector for voice identity; do not ask users to infer voice identity from an unrelated cast list. Optional delivery must look optional.

Validation should state the repair, preserve input and focus the relevant field. Distinguish a field error from a server-readiness problem. During a request, disable the affected controls, prevent duplicate submissions and show the operation's status. Restore each control's valid disabled state afterward.

### Cards and media candidates

A shot card contains stable shot identity, picture preview, narrative/production context, dialogue access and stage controls. A generated-take item identifies the candidate and its selection state.

Preview controls need names that identify the object. Selection and deletion must be real buttons, not clickable text spans. Use a persistent selection label/check mark as well as color. Keep small delete controls physically separate from preview/selection targets.

For voice takes, use one compact preview control, take name, optional duration/provenance and a Use take/Selected action. Avoid embedding a full browser audio player for every candidate. One shared preview should not continue invisibly after leaving its editing context.

### Dialogs

Use the shared modal lifecycle rather than implementing another independent overlay. Standard dialogs have a named header, scrollable body and reachable footer. Dialogue uses a wider detail layout. A long dialog should not scroll its primary actions out of reach.

Move keyboard focus inside on open, contain forward/backward Tab navigation, support Escape subject to the local dirty/busy guard, and restore focus to the opener without moving the main workspace. Provide a visible close/cancel action. Keep underlying content unavailable to interaction while the modal is active. These are the relevant behaviors in the [WAI-ARIA modal dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/).

Choose initial focus for the task. Spoken words are a useful starting point in Dialogue. For an irreversible or difficult-to-recover decision, favor a safe review/cancel choice over an immediately destructive default.

### Status, toasts and warnings

A toast confirms an event; it should not be the sole record of a blocking problem. Keep save/generation outcomes near the edited object. Use live status regions for asynchronous feedback without rebuilding the input field.

Connection interruption should explain automatic reconnection and the status of the draft. Do not replace a user's text with an empty form because a poll failed. Distinguish “dialogue saved, generation blocked” from “nothing saved.”

### Progress

Keep four concepts separate: queue position, elapsed time, measured current-node progress and estimated overall finish. A sampler can finish while other workflow nodes remain. Estimated elapsed/typical duration must not be presented as a measured percentage.

Unknown progress uses an indeterminate state and a reason where available. An overdue estimate becomes “taking longer than usual,” not a fabricated new guarantee. Completed output is the only full-completion state. Readiness and quality approval are separate from completion.

## 6. Dialogue interaction specification

### Structure

The dialog header contains shot/scene context and the title **Dialogue**. The line pane has a count, Add line and ordered line summaries. Each summary includes character, readiness and an excerpt. The focused pane contains character, reference preview, words, delivery and takes.

The footer contains save status and three distinct actions. Generate this line operates on the selected line; Generate all lines operates on the shot. Both save all current dialogue entries before submitting generation. Save dialogue does not submit jobs.

### Reference layout

This wireframe describes hierarchy, not exact pixel dimensions. On desktop, the line navigator stays available while the detail pane scrolls; on narrow windows, navigation stacks above the detail editor.

```text
┌──────────────────────────────────────────────────────────────────┐
│ SHOT 001 · Lantern bridge                                        │
│ Dialogue                                                   Close │
├──────────────────────┬───────────────────────────────────────────┤
│ Lines 3    Add line  │ Line 1                       Remove line  │
│                      │ Character: Tobi                           │
│ 01 · Tobi            │ Reference: calm voice           Preview   │
│ Ready to generate    │                                           │
│ Do you think…        │ Spoken words                              │
│                      │ [ Do you think the lanterns…            ] │
│ 02 · Pippa           │ Delivery (optional)                       │
│ Needs voice          │ [ Quietly, uncertain                    ] │
│ They always have…    │                                           │
│                      │ Voice takes             Generate: 1 take  │
│ 03 · Tobi            │ Preview  Take 1                  Use take │
│ Audio ready          │ Preview  Take 2                ✓ Selected │
├──────────────────────┴───────────────────────────────────────────┤
│ Unsaved changes     Save dialogue  Generate all  Generate this    │
└──────────────────────────────────────────────────────────────────┘
```

The production UI uses the full labels **Generate all lines** and **Generate this line**. Reference-preview and performance-preview controls need distinct accessible names. Both generation actions save all edited lines, even when only one line is generated.

### Local states

- **Empty:** explain what a line needs and offer Add first line.
- **Unassigned:** show Choose character and explain that a character reference is needed for generation.
- **Needs words:** focus the empty spoken-word field on submission.
- **Needs voice:** identify the missing reference and the Cast repair path.
- **Ready to generate:** words and a usable character reference are available.
- **Queued/generating:** keep the line visible and update pending results in place.
- **Audio ready:** a compatible performance is selected for the saved inputs.
- **Unsaved changes:** retain the draft across line switches; guard close/discard.
- **Saving:** prevent duplicate submission and avoid losing work to close/navigation.
- **Failure:** show the reason, retain the draft and allow a meaningful retry.

These states can overlap at different levels. A shot can have saved dialogue, one generating line and another line missing a reference. Avoid a single global “Ready” badge that conceals those differences.

### Identity and invalidation

Bind character choices by character ID and line occurrences by stable line ID. Names are display labels, and identical words can occur multiple times. Delivery and words are part of the performance input.

When words, character or delivery change, do not keep presenting old audio as a match to the new draft. Preserve older media for review/recovery. A stale result must not overwrite a newer performance choice. Selecting a take for one line must never replace other unsaved lines with server copies.

### Responsive behavior

At regular desktop widths, the navigator and focused editor have independent scrolling within the dialog. Header and footer stay available. At the narrow breakpoint, stack navigation above the editor and keep both reachable through scrolling.

Test a long script, many takes, long character names, no reference, suggestions from a prompt, a whole-shot override, an empty shot and repeated dialogue. The layout must remain usable when the generated media arrives during editing.

## 7. Editing, deletion and recovery

Use object-scoped confirmation for deletion. Show the number of affected shots/takes and the selected scene before scene cleanup. Preserve the distinction between deleting clips and resetting generation settings.

Reset copy must state what remains: shots, scene membership and script/dialogue text. It must also explain meaningful removals such as selected media, generation settings and anchored cues. Link the recovery path through Studio history.

Do not use “permanently deleted” for an active-record removal that retains source files. Do not imply that a history checkpoint is an off-device backup. Export records describe existing renders; changing the edit does not silently rewrite those files.

## 8. Accessibility and input acceptance

Use WCAG 2.2 AA as the review target for the web interface, alongside native macOS behavior where applicable. Verify the actual WebKit app, not only a browser preview.

- Check at least 4.5:1 for normal text and 3:1 for large text, using the applicable exceptions and definitions in [WCAG contrast guidance](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html).
- Review pointer targets against the 24×24 CSS px minimum or its defined spacing/equivalent-control exceptions. Prefer the larger product button sizes over designing to exceptions. See [WCAG target size guidance](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html).
- Provide visible keyboard focus and ensure sticky headers/footers do not conceal the focused control.
- Give icon-only buttons object-specific accessible names. Add selection/current-state semantics where appropriate.
- Keep labels associated with controls and errors available in text. Do not depend on hover-only instructions.
- Test keyboard-only operation, VoiceOver, zoom, high text density and long Japanese/English content.
- Respect reduced motion and avoid autoplay of generated media. Provide a way to stop audio.
- Review drag-based editing for an equivalent keyboard path. Do not claim this requirement is satisfied merely because the timeline has buttons; keyboard reordering needs its own acceptance check.

Current UI helpers implement part of this behavior, including modal focus and semantic voice-take actions. Existing small preview controls, palette combinations, drag interactions and native assistive-technology behavior still require specific checks before declaring full conformance.

## 9. Product writing

Use familiar production terms consistently: scene, shot, keyframe, clip, continuation, voice take, reference voice, selected take and rendered episode.

Write short action labels and explain consequences beside the action. Avoid conflating these messages:

- “Dialogue saved.” — words and character choices are persisted.
- “2 voice takes queued.” — generation requests exist, but audio is not ready.
- “Selected take is used by this shot.” — the editorial choice changed.
- “No ready server for voice generation.” — configuration blocks the job.
- “Export complete.” — an output file exists, but still needs review.

Prefer “Choose a character with a reference voice” to an internal missing-ID error. Preserve useful technical details in logs and an appropriate details view. Do not insert library names or implementation explanations into routine creative choices.

## 10. Review a new feature

Before implementation, identify its user task, object scope, entry point, required inputs, saved state and effect on existing media. Decide whether the action edits, previews, generates, selects or exports. Design empty, blocked, busy, successful and failed states before polishing the populated screen.

Before accepting the result:

1. Confirm the task works from its expected workspace and related entry points.
2. Verify save behavior and what happens if the user closes or changes context.
3. Simulate server failure and stale data; retain work and explain recovery.
4. Receive a background result while typing; preserve text, caret and selection.
5. Select a take far down the shot board; retain main scroll position.
6. Test keyboard navigation and return focus from dialogs.
7. Resize through the layout breakpoints; keep all actions reachable.
8. Check deletion/reset scope and recovery against the actual backend behavior.
9. Use an isolated project for generation tests. Do not submit a real GPU batch just to inspect layout.
10. Update the user guide, design rules and relevant behavioral tests together.

The baseline is the current source, not a separate mockup. A new visual pattern should either reuse existing tokens/components or document a deliberate extension to this guide.
