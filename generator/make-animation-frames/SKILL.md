---
name: make-animation-frames
description: Generate animated sprite action frames from a subject, prompt, references, character art, mascot concept, product cue, or existing image; create flexible action row strips with user-defined actions, split them into transparent frame PNGs, render preview GIFs, and merge the frames into a spritesheet/contact sheet. Use when the user wants animation frames, GIF previews, or a merged sprite image but does not need Codex pet packaging, pet.json, or the fixed hatch-pet state contract.
---

# Make Animation Frames

## Overview

Create a generic animated sprite-frame deliverable: base identity art, one generated horizontal strip per requested action, extracted transparent frame PNGs, per-action preview GIFs, and a merged spritesheet/contact sheet. This skill borrows the useful hatch-pet pattern of separating visual generation from deterministic frame processing, but it intentionally does not create Codex pet files.

Use `$imagegen` for visual generation. Use this skill's scripts only for deterministic setup, slicing, previews, and merging.

## Generation Delegation

Before generating base art or action strips, load and follow the installed image generation skill:

```text
${CODEX_HOME:-$HOME/.codex}/skills/.system/imagegen/SKILL.md
```

Do not call the Image API, image CLI, or a locally written image-generation script directly from this skill. Let `$imagegen` choose its own default built-in `image_gen` path and fallback rules. If `$imagegen` says the built-in `image_gen` tool is unavailable in the current environment, stop and report that limitation; offer the `$imagegen` CLI fallback only when the user explicitly confirms it and has local API credentials available.

For normal runs, invoke `$imagegen` with the generated prompt file as the visual spec and attach the listed input images with clear roles. The parent agent owns file copying, manifest state, frame extraction, GIF previews, and spritesheet composition.

Do not substitute deterministic placeholder frames, local transforms of a reference image, CSS/canvas drawings, or copied/cropped preview images when `$imagegen` is unavailable. The deterministic scripts are post-processing only. They may run only after real `$imagegen` outputs have been saved as `references/canonical-base.png` and `decoded/<action>.png`.

## Visible Progress Plan

Keep a visible checklist for normal runs:

1. Preparing the animation run.
2. Creating the base identity.
3. Generating action strips.
4. Processing frames and previews.
5. Reviewing the animation outputs.

Only mark a step complete when the corresponding files exist.

## Action Model

Actions are flexible. Infer a compact action list from the user's request, or honor explicit action names and frame counts. Prefer the built-in action names below for common animation requests, then add custom actions only when the user asks for something more specific.

Built-in actions:

- `idle`: calm low-distraction loop. Use subtle breathing, tiny blink, slight head/body bob, or very small material sway. The loop must contain visible micro-variation. Do not show waving, walking, jumping, talking, working, emotional reactions, large gestures, item interactions, or new props.
- `wave`: friendly wave. Show the wave through hand, paw, wing, or limb pose only. Do not draw wave marks, motion arcs, lines, sparkles, symbols, or floating effects.
- `jump`: vertical jump. Show vertical motion through body position only. Do not draw shadows, dust, landing marks, impact bursts, bounce pads, floor cues, or detached motion effects.
- `cheer`: happy celebratory bounce or upbeat pose. Show happiness through posture, face, and body motion. Do not add confetti, stars, hearts, punctuation, text, floating symbols, or new props unless already part of the base identity.
- `think`: thoughtful loop. Show thinking through eyes, head tilt, posture, or hand/paw position. Do not add thought bubbles, question marks, papers, screens, UI, text, or floating icons.
- `work`: focused activity loop. Show active task work, processing, scanning, typing, making, or purposeful effort. Do not show literal foot-running, jogging, sprinting, treadmill motion, raised knees, long steps, directional travel, speed lines, dust clouds, floor shadows, motion trails, or detached effects.
- `focus`: quiet attention loop. Show focus through lean, blink, eyes, head tilt, or hand/paw position. Do not add magnifying glasses, papers, code, UI, punctuation, symbols, or other new props unless already part of the base identity.
- `move-right`: right-facing directional movement. Show body, limb, and prop movement only. The cadence must visibly alternate rather than repeat one static stride. Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached effects.
- `move-left`: left-facing directional movement. Same as `move-right`, but the subject faces and travels left.

Good action specs:

```text
idle
wave
jump
cheer
work
move-right
move-left
attack=8:quick forward slash with recovery
blink=4:eyes close and reopen
```

The prepare script supplies default frame counts and guidance for built-in actions. Prefer 4-8 frames per custom action unless the user asks for a different cadence. Use `192x208` cells by default because it is proven readable for compact characters, but change `--cell-width` and `--cell-height` when the target format requires it.

## Workflow

Standalone Python option:

Use `scripts/run_animation_pipeline.py` when running outside Codex. It uses the OpenAI Images API directly and requires `OPENAI_API_KEY`.

```bash
pip install -r requirements.txt
set OPENAI_API_KEY=your_api_key_here
python scripts/run_animation_pipeline.py \
  --subject "<character or object>" \
  --description "<stable identity description>" \
  --reference /absolute/path/to/reference.png \
  --action idle \
  --action wave \
  --action jump \
  --run-dir /absolute/path/to/run \
  --force
```

You can also pass API settings directly:

```bash
python scripts/run_animation_pipeline.py \
  --api-key "<api key>" \
  --base-url "https://api.openai.com/v1" \
  --subject "<character or object>" \
  --action idle \
  --run-dir /absolute/path/to/run \
  --force
```

`--api-key` overrides `OPENAI_API_KEY`; `--base-url` overrides `OPENAI_BASE_URL`.

This standalone runner performs the full pipeline: prepare prompts/jobs, call the Image API, save `decoded/` outputs, extract `final/frames/`, render `final/previews/*.gif`, and compose `final/spritesheet.png` plus `final/spritesheet.webp`. Use `--dry-run` to print jobs without API calls. Use `--skip-generation` only when `decoded/` already contains real generated strips.

1. Prepare the run folder and prompts:

```bash
SKILL_DIR=/absolute/path/to/make-animation-frames
python "$SKILL_DIR/scripts/prepare_animation_run.py" \
  --subject "<character or object>" \
  --description "<stable identity description>" \
  --style "<style notes or auto>" \
  --reference /absolute/path/to/reference.png \
  --action idle \
  --action wave \
  --action jump \
  --action work \
  --action "attack=8:quick forward slash with recovery" \
  --output-dir /absolute/path/to/run \
  --force
```

The script writes:

```text
run/
  animation_request.json
  actions.json
  imagegen-jobs.json
  prompts/base.md
  prompts/actions/<action>.md
  prompts/action-retries/<action>.md
  references/reference-*.png
  references/canonical-base.png        after base generation
  references/layout-guides/<action>.png
  decoded/
  final/frames/
  final/previews/
  final/
```

Inspect `imagegen-jobs.json` for ready jobs. A job is ready when `status` is not `complete` and every id in `depends_on` is complete. The base job has no dependencies; every action strip depends on `base`.

2. Generate the base image with `$imagegen`.

Read `prompts/base.md` and attach every user-provided reference image. Save the selected output as `run/references/canonical-base.png`. The base must be one centered full-body subject on the requested flat chroma background, with no text, scenery, shadows, guide marks, detached effects, or extra subjects.

When a reference is a preview image, screenshot, photo, portrait, or busy composition, treat it as subject evidence only. Extract the primary person/character/object into a clean standalone sprite identity; do not reproduce the preview layout, UI, background, room, border, caption, watermark, camera crop, or other people. The base image is the approved character identity for animation, not a cleaned-up screenshot.

Also copy the selected base output to `run/decoded/base.png` and mark the `base` job complete in `imagegen-jobs.json` with `source_path` and `completed_at`.

3. Generate one row strip per action with `$imagegen`.

For each ready action job, read the `prompt_file` listed in `imagegen-jobs.json` and attach every listed `input_images` entry with its role:

- `run/references/canonical-base.png` as the identity lock
- every user reference image that still helps preserve identity, while ignoring its background/layout
- `run/references/layout-guides/<action>.png` as a layout-only guide

Save each selected row strip as:

```text
run/decoded/<action>.png
```

Each row strip must contain exactly the requested frame count arranged left-to-right in one horizontal row. Do not accept row strips that copy guide marks, add labels or frame numbers, use white/black/checker backgrounds, crop body parts, drift identity/style, or include detached effects unless the user explicitly requested them and they remain inside the sprite.

If `$imagegen` returns a transport-level `Bad Request` or the output is structurally unusable, retry that same action once with `prompts/action-retries/<action>.md` and the same input images. If the retry still fails, stop and report the failing action and prompt paths. Do not switch to local placeholder generation.

After selecting a row output, copy it into `decoded/<action>.png` and mark that job complete in `imagegen-jobs.json` with `source_path` and `completed_at`.

4. Split row strips into transparent frame PNGs.

Only start this step after every requested action has a real generated strip at `decoded/<action>.png`. Do not invent strips locally to keep the pipeline moving.

```bash
python "$SKILL_DIR/scripts/extract_action_frames.py" \
  --run-dir /absolute/path/to/run
```

The prepare script records the resolved chroma key in `animation_request.json`, so extraction normally does not need a manual background value. If overriding the key, pass `--background "#rrggbb"`. If chroma cleanup leaves halos, rerun with a lower or higher `--tolerance`.

If the preview GIFs show size popping caused by per-frame fit-to-cell extraction, and the original generated strip has stable scale and baseline, rerun extraction with:

```bash
python "$SKILL_DIR/scripts/extract_action_frames.py" \
  --run-dir /absolute/path/to/run \
  --method stable-slots
```

Use `stable-slots` as a QA-driven correction, not a replacement for bad source strips.

5. Inspect frames:

```bash
python "$SKILL_DIR/scripts/inspect_frames.py" \
  --frames-root /absolute/path/to/run/final/frames \
  --json-out /absolute/path/to/run/qa/review.json
```

Treat `errors` as blockers. Warnings require visual review.

6. Render preview GIFs:

```bash
python "$SKILL_DIR/scripts/render_previews.py" \
  --run-dir /absolute/path/to/run \
  --duration 120
```

Preview GIF rendering defaults to `--despill both` to reduce green/purple chroma fringes before palette conversion. The script also hardens semi-transparent edges into GIF-safe transparency to avoid black matte backgrounds. If needed, tune with `--despill green|purple|both|none` and `--alpha-threshold <0-255>`.

7. Merge frames into a spritesheet and contact sheet:

```bash
python "$SKILL_DIR/scripts/compose_spritesheet.py" \
  --run-dir /absolute/path/to/run \
  --webp-output /absolute/path/to/run/final/spritesheet.webp
```

Expected final deliverables:

```text
run/
  final/frames/frames-manifest.json
  final/frames/<action>/<action>_00.png
  final/previews/<action>.gif
  qa/contact-sheet.png
  qa/review.json
  qa/run-summary.json
  final/spritesheet.png
  final/spritesheet.webp
```

Stop here. Do not create `pet.json`, do not copy files into `${CODEX_HOME:-$HOME/.codex}/pets`, and do not require a fixed atlas size or fixed action order.

Keep the intermediate process. Do not delete `prompts/`, `references/`, `decoded/`, `final/frames/`, `imagegen-jobs.json`, `actions.json`, or `animation_request.json` unless the user explicitly asks for cleanup.

## Lightweight Workers

Use lightweight workers for image-heavy work when the environment supports them:

- Base worker: generate only the base job using `$imagegen`, then return `selected_source=<path>` and `qa_note=<short note>`.
- Action worker: generate exactly one action strip using `$imagegen`, retry once with the retry prompt when appropriate, then return `selected_source=<path>` and `qa_note=<short note>`.
- Final visual QA worker: inspect `qa/contact-sheet.png`, `final/previews/*.gif`, `qa/review.json`, and final spritesheet outputs. Return `visual_qa=pass|fail`, `repair_actions=<ids or none>`, and `repair_notes=<short notes>`.

The parent agent owns copying selected outputs, marking `imagegen-jobs.json`, running deterministic scripts, and reporting final paths.

## Visual QA

Inspect `qa/contact-sheet.png` and every GIF in `final/previews/` before accepting the result. Block or repair rows with:

- wrong frame count or non-horizontal layout
- identity, palette, material, proportions, face, or prop drift
- guide marks, frame numbers, text, labels, scenery, white backgrounds, checkerboards, or shadows
- copied preview/screenshot/photo layout, background, UI, border, watermark, or extra people
- clipped poses, slot overlap, blank frames, or repeated static frames when motion was requested
- unintended size popping or baseline jumping introduced by generation
- action semantics that do not match the user request

Deterministic validation is necessary but not sufficient. A run is not accepted until visual QA confirms the same subject identity, style, palette, silhouette, face/feature impression, proportions, outfit/materials, and props across all actions.

## Repair Workflow

Repair the smallest failing scope. If one action fails, regenerate only that action strip with the canonical base image, original references, contact sheet, and the failure note as context. Replace `decoded/<action>.png`, update the matching job in `imagegen-jobs.json`, and rerun extraction, inspection, previews, and composition.

For extraction-induced motion popping, try `--method stable-slots` before regenerating imagery. Regenerate the action only when the original strip itself is clipped, unstable, semantically wrong, or has identity drift.

Do not repair by drawing, warping, mirroring, or locally transforming frames unless the user explicitly asks for a non-generative edit workflow.

## Acceptance Criteria

- `imagegen-jobs.json` records base and every action strip as complete.
- `references/canonical-base.png` exists and represents one clean standalone subject.
- `decoded/<action>.png` exists for every requested action and came from `$imagegen`.
- `final/frames/frames-manifest.json` exists and every listed frame is the expected cell size.
- `qa/review.json` has no errors.
- `qa/contact-sheet.png` and `final/previews/*.gif` exist and have been visually inspected.
- Preview GIFs do not show black backgrounds, green/purple edge fringes, or obvious matte artifacts.
- `final/spritesheet.png` exists; `final/spritesheet.webp` should exist when requested.
- Intermediate process files are retained.

## Rules

- Use `$imagegen` as the visual generation layer and let it choose built-in versus fallback behavior.
- Stop before frame extraction if `$imagegen` cannot produce the base image and action strips.
- Use `imagegen-jobs.json` as the source of truth for prompt files, inputs, output paths, dependencies, and completion state.
- Keep generated row prompts concise and action-specific.
- Keep the canonical base image attached for every action row.
- Use references as identity/style cues, not as layouts to copy.
- Treat layout guides as invisible construction references; never accept copied guide pixels.
- Generate only the actions needed for the user request.
- Use deterministic scripts only for splitting already-generated strips, transparent cleanup, GIF previews, contact sheets, and merged spritesheets.
- Preserve unused spritesheet cells as transparent when action rows have different frame counts.
- Preserve intermediate files by default.
- Keep final outputs in the run folder unless the user asks for another destination.
