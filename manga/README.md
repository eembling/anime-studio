# Manga Studio

Manga Studio is a separate local desktop app. It opens its own macOS window and stores its cast, chapter plans, images, and queue under `~/Library/Application Support/MangaStudio`. It shares Anime Studio’s image-workflow code and design conventions.

## Produce a chapter

1. In **Script**, paste a manuscript or import a UTF-8 Markdown or text file. Choose the maximum panels per page (one to six) and **Plan pages**, review the panel prompts, then create the selected pages. **Planning prompt** copies a manuscript and formatting instructions for an external writing tool. Each plan retains its original manuscript. Planning again adds another version. **View source** opens an earlier manuscript.
2. Use scene headings, one action beat per line, and **Name: dialogue**. The planner pairs an action with the next speech or caption, starts a panel for each subsequent speaker, and breaks pages at scene boundaries, the panel limit, or an explicit **PAGE** line. Speaking characters become cast entries. Recognized names in action also link to panels.
3. In **Cast**, edit appearance details and optional character LoRAs, or add locations and props. In **Reference library**, upload labeled images into named collections. Choose an active reference for each entry. **Make turnaround sheet** creates another candidate using the current reference. Select the sheet to use it for future renders.
4. In **Pages**, review the original beat, edit the illustration direction and lettering, attach cast entries, and set balloon placement or a caption strip. **Start new page here** moves that panel and those following it to the next page. Choose a feature, grid, or strip layout and set chapter reading direction.
5. In **Settings → ComfyUI servers**, add one address per GPU and assign panel artwork or turnaround sheets. **Check connections** shows connectivity and the reported device. The queue runs one job per available enabled address. The first upgrade from the default local address copies Anime Studio’s saved image-capable servers. **Copy servers from Anime Studio** adds missing addresses later. The included Qwen Image Edit 2509 graph uses the same model files as Anime Studio’s keyframe workflow. Each panel uses its scene, direction, lettering context, cast descriptions, LoRAs, and all active references from the selected cast and locations. References use individual image inputs while slots are available. Additional images share reference sheets with row and column assignments in the prompt. The preceding panel gets its own input when the workflow has at least two slots.
6. In **Production**, generate missing artwork for a page or request another take for an individual panel. **Render queue** shows progress and failures. Select the preferred take for every panel. New takes leave the current selection in place. Use **Delete takes…** on a panel to remove old versions individually or select all unselected takes. From a completed panel render, **Manage panel takes** opens its panel in Production.
7. In **Studio**, proof the composed page. Lettering and layouts can be changed without rendering the artwork again. **Assemble** exports selected pages as 1200 × 1800 PNGs, or an entire finished chapter as a CBZ containing numbered PNGs in reading order.

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

In **Pages → Page direction**, set the dramatic intent, lighting, and continuity for every panel. **Edit panel** controls framing, director notes, cast and optional preceding-panel artwork. **Preview prompts** shows the positive and negative inputs, ordered reference pictures, LoRAs and dimensions. In **Settings**, choose the art style, negative prompt, panel resolution, sheet dimensions and timeout. **Workflows** accepts ComfyUI API image graphs, can copy Anime Studio’s saved keyframe graph, and can restore the starter. Queued jobs retain the settings, prompt and graph captured before dispatch.

## Transfer cast and locations from Anime Studio

In Anime Studio, open **Cast → Export to Manga Studio**, select the characters and locations, and choose **Export library**. Move the downloaded ZIP to the Mac running Manga Studio if needed.

In Manga Studio, open **Cast → Import from Anime Studio**, select the ZIP, and choose **Import library**. The result lists added, updated, and already-present entries and identifies unavailable source references. The library carries descriptions, LoRA filenames and strengths, active reference images, turnaround sheets, and labeled images from each reference collection version. LoRA model files still belong on the ComfyUI server.

Import before planning to use the cast in a new chapter, or import after planning to fill in existing character entries. Matching uses name and entry type and preserves panel assignments, existing descriptions, LoRA settings, and active references. Reimporting adds new reference images and collections. A same-name entry of a different type is reported and skipped.

## Run and build

From the repository root, run `./manga/build.sh` to build **dist/Manga Studio.app**. The local build requires the macOS command line tools, Python 3.9 or newer, and Pillow 10.1 or newer. The bundle includes the Pillow installation used at build time and uses that Python interpreter on this Mac. Set `MANGA_PYTHON` to choose another interpreter. ComfyUI and the image model run separately.

For development, run `python3 manga/studio.py` and open the local address it prints. Use `--port` and `--data` to choose a different port or project directory. The native app chooses an available local port automatically. Run `python3 manga/test_studio.py` to exercise planning, reference uploads, turnaround and panel jobs, art selection, proofing, chapter export, and saved plans against a local simulated ComfyUI server.
