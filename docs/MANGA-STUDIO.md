# Manga Studio: prompting, speech bubbles and roadmap

This is the working plan for turning Anime Studio into a manga production tool. Live review doc: https://claude.ai/artifact/Pb79UixKEC4RV4zerexP96

## Status

Speech-bubble tagging and a structured keyframe prompt are implemented (see [What changed](#what-changed)). The biggest remaining gap is that the app still works with **shots on a timeline**, not **pages made of panels**. There is no page layout, no lettering after generation and no page export.

## How prompts reached ComfyUI before this change

Every keyframe prompt was built in `Worker.compose` (`app/anime_studio.py`) and sent as one positive prompt to the `TextEncodeQwenImageEditPlus` node titled PROMPT, with up to 3 reference images (REF1–REF3).

| Problem | Effect on images |
| --- | --- |
| One long run of fragments joined with ". " | The model couldn't tell scene from camera from continuity notes |
| Reference labels didn't say character or location | "Picture 2 is Rooftop" could be read as a person's name |
| Dialogue and speakers never reached the image prompt | The person speaking wasn't drawn speaking, and listeners weren't drawn reacting |
| Composition boxes from **Direction** were only used by an optional layout-conditioning recipe | Placing characters left or right had no effect with the default workflow |
| Nothing told the model what to do about text | Qwen tends to invent gibberish signs and balloons |
| `keyframe_negative` goes to a negative encoder, but the Lightning starter runs at CFG 1.0 | The negative prompt has no effect (not fixed yet) |
| Animation prompts didn't say who talks | Wan moves the wrong mouth or several mouths |

## What changed

The keyframe prompt is now built in `app/manga.py`. It has labelled sections, one per line:

1. **Reference map:** "Picture 1 is the character AIKO (…)", "Picture 2 is the location Rooftop (…)"
2. **Setting** and **Characters in frame**, from the shot's tagged locations and characters
3. **Scene:** the keyframe text
4. **Placement:** written from the Direction composition boxes ("AIKO on the left, close to the camera, large in frame")
5. **Acting:** the speaker is drawn mid-word with their expression, and the others are drawn listening. Off-panel speakers are stated as heard but not shown
6. **Camera**, direction notes, scene lighting and continuity
7. **Lettering plan**, which depends on the lettering setting below
8. **Style**

**Speech bubbles.** Open **Edit shot → Speech bubbles**. Each bubble has a style (speech, shout, whisper, thought, narration box or sound effect), a speaker, the words, an expression, a position and an **Off-panel** tick box.

- **Copy from dialogue** turns `AIKO (angry): line` into a bubble; the words in brackets become the expression.
- A speaker who isn't tagged in the shot is added to it, so their reference image is sent.
- Up to 12 bubbles per panel. The server checks every bubble: a speech bubble needs a speaker, and the speaker must be a character.
- A shot with no bubbles uses its dialogue lines for the acting section.

**Settings → Project** has two new settings:

- **Speech bubbles in keyframes**
    - *Off* (the default): acting only, so existing anime projects behave as before.
    - *Leave space*: no text is drawn and the balloon areas stay empty, ready for lettering afterwards.
    - *Draw balloons*: the model draws each balloon with its tail and the exact quoted words.
- **Panel reading order:** right to left (manga) or left to right. *Auto* balloons follow reading order, or sit above their speaker's composition box.

**Animation.** The motion prompt now says who talks ("AIKO talks, lips moving naturally"). Off-panel and untagged speakers are left out.

**Outdated keyframes.** Bubbles and lettering settings are added to the keyframe signature only when they're used, so existing takes don't suddenly show as outdated.

## Roadmap

| # | Missing | Why it matters | Suggested approach |
| --- | --- | --- | --- |
| 1 | **Pages and panels** (4-koma, 3-tier, splash, diagonal templates; each panel linked to a shot) | Manga is read as a page | New `pages` table with panel slots pointing to shots; a canvas page editor that crops each chosen keyframe into its slot |
| 2 | **Lettering after generation** | Final pages need exact, editable text and placement | Render bubbles as SVG over the panel; drag a bubble and its tail; save the position back as x/y; a balloon shape per style |
| 3 | **Page export** (PNG, PDF, CBZ; B5 tankobon and 6.6×9.4 in; 600 dpi line art) | Finished output | Export from the page editor canvas; zip pages for CBZ; add bleed and trim lines |
| 4 | **Per-panel aspect ratio and size** | A fixed 1280×720 keyframe doesn't fit tall or wide panels | Take keyframe width/height from the panel slot, rounded to 16 px |
| 5 | **Black-and-white manga style preset** (screentone, inking, speed lines) | The current style is colour cel-shaded anime | Style presets in Settings; a screentone/greyscale finishing step; consider a manga line-art LoRA for Qwen-Image-Edit |
| 6 | **Usable negative prompt** | It does nothing at CFG 1 | Keep "no text" in the positive lettering plan (done for *Leave space*), or offer an 8-step recipe at CFG 2.5–4 |
| 7 | **Character consistency beyond 3 references** | Two characters plus a location fill all three Qwen reference slots | Use turnaround sheets; composite several characters into one reference image; keep character LoRAs |
| 8 | **Bubbles in CSV and script import** | Big scripts are entered by hand | Add a `bubbles` CSV column; create bubbles from dialogue when importing a script in manga mode |
| 9 | **Panel-to-panel continuity** | Characters jump sides between panels | Carry composition boxes over from the previous panel; warn when a speaker swaps sides |
| 10 | **Manga mode switch** | Video steps clutter a manga project | A project type that hides animate, lip sync and music and shows Pages |

Open question: is Manga Studio a mode inside Anime Studio, or a separate app built from the same code? The roadmap assumes a mode, so both kinds of project can share cast and references.

## Server notes (2× RTX 3060 12 GB + RTX 3050 6 GB)

- For a manga project, give both 3060s to keyframes, each in its own ComfyUI copy (ports 8188 and 8189). Keep the 3050 for upscaling.
- *Draw balloons* depends on Qwen's text rendering. Keep lines short (under about 12 words a balloon) and treat the result as a draft until roadmap item 2 exists.
- This change installs nothing on the server; it only changes the text sent to the existing PROMPT node.
