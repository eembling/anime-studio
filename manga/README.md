# Manga Studio

Manga Studio is a separate local desktop app. It opens its own macOS window and stores its cast, chapter plans, images, and queue under `~/Library/Application Support/MangaStudio`. It shares Anime Studio’s image-workflow code and design conventions.

## Produce a chapter

1. In **Script**, paste a manuscript or import a UTF-8 Markdown or text file. Choose the maximum panels per page (one to nine) and **Plan pages**, review the panel prompts, then create the selected pages. **Planning prompt** copies a manuscript and formatting instructions for an external writing tool. Each plan retains its original manuscript. Planning again adds another version. **View source** opens an earlier manuscript.
2. Use scene headings, one action beat per line, and **Name: dialogue**. The planner pairs an action with the next speech or caption, starts a panel for each subsequent speaker, and breaks pages at scene boundaries, the panel limit, or an explicit **PAGE** line. Speaking characters become cast entries. Recognized names in action also link to panels. Every line becomes a **speech balloon tagged with its speaker**. A delivery note sets the balloon style: `Kenji (shouting):` makes a shout burst, `(whispering)` a dashed whisper balloon, `(thinking)` a thought cloud. `CAPTION:` and `SFX:` become caption boxes and sound-effect lettering. A panel with only dialogue gets a reaction beat that continues the previous action, and panels without a camera get suggested framing (wide to open a scene, then medium and close-up, never three the same in a row).
3. In **Cast**, edit appearance details and optional character LoRAs, or add locations and props. In **Reference library**, upload labeled images into named collections. Choose an active reference for each entry. **Make turnaround sheet** creates another candidate using the current reference. Select the sheet to use it for future renders.
4. In **Pages**, review the original beat, edit the illustration direction, attach cast entries and tag the speech balloons. **Edit panel → Speech balloons** lists the panel's balloons in reading order: choose each one's style (speech, shout, whisper, thought, caption or sound effect), who says it, and whether that speaker is drawn in the panel or speaks off-panel. A speaker drawn in the panel is added to its cast, so their reference is sent and the artwork prompt shows them speaking while the others listen. **First balloon** sets where reading starts. The page warns when three panels in a row use the same framing. **Start new page here** moves that panel and those following it to the next page. Choose a feature, grid, strip, rows-of-three, tall side panel or splash layout (up to nine panels) and set chapter reading direction.
5. In **Settings → ComfyUI servers**, add one address per GPU and assign panel artwork or turnaround sheets. **Check connections** shows connectivity and the reported device. The queue runs one job per available enabled address. The first upgrade from the default local address copies Anime Studio’s saved image-capable servers. **Copy servers from Anime Studio** adds missing addresses later. The included Qwen Image Edit 2509 graph uses the same model files as Anime Studio’s keyframe workflow. Each panel uses its scene, direction, lettering context, cast descriptions, LoRAs, and all active references from the selected cast and locations. References use individual image inputs while slots are available. Additional images share reference sheets with row and column assignments in the prompt. The preceding panel gets its own input when the workflow has at least two slots.
6. In **Production**, generate missing artwork for a page or request another take for an individual panel. **Render queue** shows progress and failures. **Stop render** stops a running render on the ComfyUI server; a timeout does the same, so the GPU is never left working on an abandoned job. Closing Manga Studio during a render no longer loses it: on the next start the app reconnects to ComfyUI and collects the artwork. A finished render whose download fails is retried automatically, then offers **Retry download** instead of a new render. Select the preferred take for every panel. New takes leave the current selection in place. Use **Delete takes…** on a panel to remove old versions individually or select all unselected takes. From a completed panel render, **Manage panel takes** opens its panel in Production.
7. In **Studio**, proof the composed page. Drag a balloon to move it, drag the red dot to aim its tail at the speaker, and drag the gutter between rows to resize them. The proof warns when a speaker's balloons switch sides between neighbouring panels. Lettering and layouts can be changed without rendering the artwork again. Every balloon on a page uses one type size, set in the bundled Comic Neue (sound effects in Bangers); Japanese, Chinese and Korean lettering uses a system font and can run vertically. **Assemble** exports pages as PNGs, the chapter as a CBZ (with `ComicInfo.xml`, so readers page in the right direction) or a print PDF.

## Direct a page

For a structured manuscript, use explicit panel headings. Review the resulting pages before committing them.

```text
EXT. LAST PLATFORM — NIGHT
Lighting: Cold moonlight, one lit window.
PAGE 1
PANEL 1
Keyframe: Mika stands beneath the broken station clock.
Cast: Mika
Camera: Wide establishing view from track level.
Direction: Leave open sky above her for a balloon.
Mika: Still time.
PANEL 2
Keyframe: A shadow crosses the clock face.
Camera: Extreme close-up.
SFX: TICK
```

**Settings → Project** sets the page size (web 1200 × 1800, B5, A5 or US comic), print resolution (300 or 600 dpi), optional 3 mm bleed, artwork tone (colour, greyscale or black and white with screentone dots), capital-letter lettering and vertical CJK lettering. Changes apply to proofs and exports without rendering again.

In **Pages → Page direction**, set the dramatic intent, lighting, and continuity for every panel. **Edit panel** controls framing, director notes, cast and optional preceding-panel artwork. **Preview prompts** shows the positive and negative inputs, ordered reference pictures, LoRAs and dimensions. In **Settings**, choose the art style, negative prompt, panel resolution, sheet dimensions and timeout. **Workflows** accepts ComfyUI API image graphs, can copy Anime Studio’s saved keyframe graph, and can restore the starter. Queued jobs retain the settings, prompt and graph captured before dispatch.

## Transfer cast and locations from Anime Studio

In Anime Studio, open **Cast → Export to Manga Studio**, select the characters and locations, and choose **Export library**. Move the downloaded ZIP to the Mac running Manga Studio if needed.

In Manga Studio, open **Cast → Import from Anime Studio**, select the ZIP, and choose **Import library**. The result lists added, updated, and already-present entries and identifies unavailable source references. The library carries descriptions, LoRA filenames and strengths, active reference images, turnaround sheets, and labeled images from each reference collection version. LoRA model files still belong on the ComfyUI server.

Import before planning to use the cast in a new chapter, or import after planning to fill in existing character entries. Matching uses name and entry type and preserves panel assignments, existing descriptions, LoRA settings, and active references. Reimporting adds new reference images and collections. A same-name entry of a different type is reported and skipped.

## Run and build

From the repository root, run `./manga/build.sh` to build **dist/Manga Studio.app**. The local build requires the macOS command line tools, Python 3.9 or newer, and Pillow 10.1 or newer. The bundle includes the Pillow installation used at build time and uses that Python interpreter on this Mac. Set `MANGA_PYTHON` to choose another interpreter. ComfyUI and the image model run separately.

The fonts in `manga/fonts` are Comic Neue and Bangers, both under the SIL Open Font License (licence files included).

For development, run `python3 manga/studio.py` and open the local address it prints. Use `--port` and `--data` to choose a different port or project directory. The native app chooses an available local port automatically. Run `python -m unittest discover -s manga -p 'test_*.py'` and `node --test manga/test_*.cjs` to exercise planning, reference uploads, turnaround and panel jobs, art selection, proofing, chapter export, and saved plans against a local simulated ComfyUI server.
