#!/usr/bin/env python3
"""Prepare a generic animation sprite-frame run folder."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    Image = None
    ImageDraw = None


BUILT_IN_ACTIONS = {
    "idle": {
        "display_name": "待机",
        "frames": 6,
        "prompt": "calm low-distraction idle loop with subtle breathing, tiny blink, or slight head/body bob",
        "guidance": (
            "Keep this calm and low-distraction. Use only subtle breathing, a tiny blink, "
            "a slight head or body bob, a very small material sway, or another quiet identity-preserving motion. "
            "The loop must contain visible micro-variation; do not accept effectively identical frames. "
            "Do not show waving, walking, jumping, talking, working, emotional reactions, large gestures, item interactions, or new props."
        ),
    },
    "wave": {
        "display_name": "挥手",
        "frames": 6,
        "prompt": "friendly wave loop using a hand, paw, wing, or equivalent limb",
        "guidance": (
            "Show the wave through limb pose only. Do not draw wave marks, motion arcs, lines, sparkles, symbols, "
            "or floating effects around the gesture."
        ),
    },
    "jump": {
        "display_name": "跳跃",
        "frames": 6,
        "prompt": "vertical jump loop with takeoff, airborne pose, and return",
        "guidance": (
            "Show vertical motion through body position only. Do not draw shadows, dust, landing marks, impact bursts, "
            "bounce pads, floor cues, or detached motion effects."
        ),
    },
    "cheer": {
        "display_name": "开心",
        "frames": 6,
        "prompt": "happy celebratory bounce or upbeat pose loop",
        "guidance": (
            "Show happiness through posture, face, and body motion. Keep the gesture self-contained. "
            "Do not add confetti, stars, hearts, punctuation, text, floating symbols, or new props unless already part of the base identity."
        ),
    },
    "think": {
        "display_name": "思考",
        "frames": 6,
        "prompt": "thoughtful thinking loop with a small head tilt, blink, or pondering posture",
        "guidance": (
            "Show thoughtfulness through eyes, head tilt, posture, or hand/paw position. "
            "Do not add thought bubbles, question marks, papers, screens, UI, text, or floating icons."
        ),
    },
    "work": {
        "display_name": "工作",
        "frames": 6,
        "prompt": "focused active-work loop showing processing, making, typing, scanning, or purposeful effort",
        "guidance": (
            "Show active task work, processing, thinking, scanning, typing, or focused effort. "
            "Do not show literal foot-running, jogging, sprinting, treadmill motion, raised knees, long steps, pumping arms, "
            "directional travel, speed lines, dust clouds, floor shadows, motion trails, or detached motion effects."
        ),
    },
    "focus": {
        "display_name": "专注",
        "frames": 6,
        "prompt": "quiet focused attention loop with lean, blink, eyes, head tilt, or hand/paw position",
        "guidance": (
            "Show focus through lean, blink, eyes, head tilt, or hand/paw position. "
            "Do not add magnifying glasses, papers, code, UI, punctuation, symbols, or other new props unless already part of the base identity."
        ),
    },
    "move-right": {
        "display_name": "向右移动",
        "frames": 8,
        "prompt": "right-facing directional movement loop",
        "guidance": (
            "Show directional movement through body, limb, and prop movement only. The subject must face and travel right. "
            "The cadence must visibly alternate across the loop rather than repeating one nearly static stride. "
            "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects."
        ),
    },
    "move-left": {
        "display_name": "向左移动",
        "frames": 8,
        "prompt": "left-facing directional movement loop",
        "guidance": (
            "Show directional movement through body, limb, and prop movement only. The subject must face and travel left. "
            "The cadence must visibly alternate across the loop rather than repeating one nearly static stride. "
            "Do not draw speed lines, dust clouds, floor shadows, motion trails, or detached motion effects."
        ),
    },
    "lift": {
        "display_name": "拎起",
        "frames": 6,
        "prompt": "picked-up dangling pose loop for upward dragging, body slightly lifted as if gently held from above",
        "guidance": (
            "Show the subject being lifted through body pose only, as if gently picked up by the cursor. "
            "Keep the character self-contained. Do not draw hands, strings, hooks, cursor icons, shadows, floor cues, or detached effects."
        ),
    },
    "play": {
        "display_name": "玩耍",
        "frames": 6,
        "prompt": "playful self-contained loop for idle surprise, cute small play motion",
        "guidance": (
            "Show a playful upbeat motion using body pose, face, and small self-contained movement only. "
            "Do not add toys, balls, text, symbols, sparkles, hearts, detached effects, or new props unless already part of the identity."
        ),
    },
}

CHROMA_KEY_CANDIDATES = [
    {"name": "green", "hex": "#00ff00", "rgb": (0, 255, 0)},
    {"name": "magenta", "hex": "#ff00ff", "rgb": (255, 0, 255)},
    {"name": "cyan", "hex": "#00ffff", "rgb": (0, 255, 255)},
    {"name": "blue", "hex": "#0047ff", "rgb": (0, 71, 255)},
]


def slugify(value: str, fallback: str = "action") -> str:
    value = value.strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or fallback


def parse_action(spec: str) -> dict:
    """Parse id=frames:prompt, id:frames:prompt, id=prompt, or id."""
    raw = spec.strip()
    if not raw:
        raise ValueError("empty action spec")

    if "=" in raw:
        name, rest = raw.split("=", 1)
    else:
        parts = raw.split(":", 2)
        name = parts[0]
        rest = ":".join(parts[1:]) if len(parts) > 1 else ""

    name = name.strip()
    rest = rest.strip()
    action_id = slugify(name)
    preset = BUILT_IN_ACTIONS.get(action_id)
    frame_count = int(preset["frames"]) if preset else 6
    prompt = str(preset["prompt"]) if preset else ""

    if rest:
        parts = rest.split(":", 1)
        if parts[0].strip().isdigit():
            frame_count = int(parts[0].strip())
            prompt = parts[1].strip() if len(parts) > 1 else ""
        else:
            prompt = rest

    if frame_count < 1:
        raise ValueError(f"frame count must be positive for action {name!r}")

    return {
        "id": action_id,
        "display_name": str(preset["display_name"]) if preset else (name.strip() or action_id),
        "frames": frame_count,
        "prompt": prompt or f"{name.strip() or action_id} animation loop",
        "guidance": str(preset["guidance"]) if preset else "",
    }


def parse_hex_color(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"invalid color: #{value}")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a[index] - b[index]) ** 2 for index in range(3)))


def sampled_reference_pixels(paths: list[Path]) -> list[tuple[int, int, int]]:
    if Image is None:
        return []
    pixels: list[tuple[int, int, int]] = []
    for path in paths:
        try:
            img = Image.open(path).convert("RGBA")
        except Exception:
            continue
        img.thumbnail((96, 96), Image.Resampling.LANCZOS)
        data = list(img.getdata())
        opaque = [(r, g, b) for r, g, b, a in data if a > 32]
        pixels.extend(opaque[:: max(1, len(opaque) // 500)] or opaque)
    return pixels


def choose_chroma_key(reference_paths: list[Path], requested: str) -> dict:
    if requested.lower() != "auto":
        rgb = parse_hex_color(requested)
        return {"name": "custom", "hex": f"#{requested.strip().lstrip('#').lower()}", "rgb": rgb}

    pixels = sampled_reference_pixels(reference_paths)
    if not pixels:
        return CHROMA_KEY_CANDIDATES[0]

    scored = []
    for preference_index, candidate in enumerate(CHROMA_KEY_CANDIDATES):
        distances = sorted(color_distance(candidate["rgb"], pixel) for pixel in pixels)
        percentile_index = min(len(distances) - 1, max(0, int(len(distances) * 0.05)))
        scored.append((distances[percentile_index], -preference_index, candidate))
    return max(scored, key=lambda item: (item[0], item[1]))[2]


def rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


def make_layout_guide(path: Path, frames: int, cell_width: int, cell_height: int, bg_hex: str) -> None:
    if Image is None or ImageDraw is None:
        raise RuntimeError("Pillow ImageDraw is required to create layout guides.")
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (frames * cell_width, cell_height), "#f7f7f7")
    draw = ImageDraw.Draw(img)
    safe_x = max(18, round(cell_width * 0.14))
    safe_y = max(14, round(cell_height * 0.08))
    for i in range(frames):
        x0 = i * cell_width
        x1 = x0 + cell_width - 1
        draw.rectangle((x0, 0, x1, cell_height - 1), outline="#111111", width=2)
        draw.rectangle((x0 + safe_x, safe_y, x1 - safe_x, cell_height - 1 - safe_y), outline="#2f80ed", width=2)
        draw.line((x0 + cell_width // 2, safe_y, x0 + cell_width // 2, cell_height - 1 - safe_y), fill="#b8b8b8", width=1)
        draw.line((x0 + safe_x, cell_height // 2, x1 - safe_x, cell_height // 2), fill="#b8b8b8", width=1)
    img.save(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subject", required=True, help="Character, object, or subject to animate.")
    parser.add_argument("--description", default="", help="Stable identity description.")
    parser.add_argument("--style", default="auto", help="Visual style notes or preset.")
    parser.add_argument("--reference", action="append", default=[], help="Reference image path. Repeat as needed.")
    parser.add_argument("--action", action="append", default=[], help="Action spec: name=frames:prompt, name:frames:prompt, name=prompt, or name.")
    parser.add_argument("--output-dir", required=True, help="Run folder to create.")
    parser.add_argument("--cell-width", type=int, default=192)
    parser.add_argument("--cell-height", type=int, default=208)
    parser.add_argument("--background", default="auto", help="Flat chroma background hex color, or auto.")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.output_dir).resolve()
    if run_dir.exists() and not args.force and any(run_dir.iterdir()):
        raise SystemExit(f"Output directory exists and is not empty: {run_dir}")

    actions = [parse_action(spec) for spec in args.action]
    actions = [action for action in actions if action["id"] != "base"]
    if not actions:
        actions = [parse_action("idle")]

    for subdir in ["prompts/actions", "prompts/action-retries", "decoded", "qa", "final/frames", "final/previews", "references/layout-guides"]:
        (run_dir / subdir).mkdir(parents=True, exist_ok=True)

    raw_reference_paths = [Path(p).expanduser().resolve() for p in args.reference]
    copied_refs = []
    ref_dir = run_dir / "references"
    for index, source in enumerate(raw_reference_paths, start=1):
        if not source.exists():
            raise SystemExit(f"reference not found: {source}")
        suffix = source.suffix or ".png"
        copied = ref_dir / f"reference-{index:02d}{suffix}"
        shutil.copy2(source, copied)
        copied_refs.append({"path": str(copied), "role": "subject identity reference", "source_path": str(source)})

    copied_ref_paths = [Path(ref["path"]) for ref in copied_refs]
    chroma_key = choose_chroma_key(copied_ref_paths, args.background)
    background_hex = chroma_key["hex"]

    request = {
        "subject": args.subject,
        "description": args.description,
        "style": args.style,
        "references": copied_refs,
        "cell_width": args.cell_width,
        "cell_height": args.cell_height,
        "background": background_hex,
        "chroma_key": chroma_key,
        "actions": actions,
    }

    (run_dir / "animation_request.json").write_text(json.dumps(request, indent=2), encoding="utf-8")
    (run_dir / "actions.json").write_text(json.dumps({"actions": actions}, indent=2), encoding="utf-8")

    references = "\n".join(f"- {ref['path']} ({ref['role']})" for ref in copied_refs) or "- none"
    style_contract = (
        f"{args.style}. Pet-safe sprite production: compact full-body subject, readable in a "
        f"{args.cell_width}x{args.cell_height} cell, consistent face/features, proportions, material, "
        "palette, outline language, and props across every row. Prefer a desktop-pet mascot/standalone "
        "sprite interpretation over a poster, portrait, screenshot, or full-canvas photo."
    )
    write_text(
        run_dir / "prompts/base.md",
        f"""
Create one clean full-body reference sprite for desktop pet `{args.subject}`.

This is NOT an animation strip, spritesheet, contact sheet, pose lineup, turnaround sheet, expression sheet, costume sheet, or multi-character composition. Generate exactly one subject in exactly one neutral canonical pose. Do not include any second pose, alternate action, duplicate copy, side view, walking pose, waving pose, prop-use pose, or example frame anywhere in the image.

Pet identity: {args.description or args.subject or "Infer a clear, compact identity from the subject."}
Style: {style_contract}
Reference images:
{references}

Use the reference images only as identity and style cues. If a reference is a preview image, screenshot, photo, portrait, or busy composition, isolate the primary person/character/object and convert that subject into a clean standalone animated-sprite character. Do not reproduce the original preview layout, crop, UI, room, scenery, background, border, caption, watermark, camera framing, or other people.

Place a single centered full-body pose on a perfectly flat pure {background_hex} chroma-key background. Keep the full pet visible, compact, readable at {args.cell_width}x{args.cell_height}, and easy to animate. The subject should occupy roughly 60-75% of the image height, with generous empty chroma background on every side. Do not create a tall poster/photo portrait that fills the canvas.

Preserve approved identity cues from the reference: silhouette, face/feature impression, proportions, palette, outfit/materials, material style, outline language, and signature props. Simplify tiny details so they remain readable at desktop-pet size. Keep the subject self-contained; all visible pixels must belong to the pet or attached identity props.

No text, labels, logos, UI, scenery, guide marks, shadows, floor shadows, glows, halos, motion trails, detached effects, extra props, extra subjects, duplicate subjects, multiple poses, action examples, cropped body parts, checkerboard transparency, white background, black background, or screenshot/photo rectangle. Keep {background_hex} and close colors out of the subject, props, highlights, and effects.
""",
    )

    jobs = [
        {
            "id": "base",
            "kind": "base-reference",
            "status": "pending",
            "depends_on": [],
            "prompt_file": "prompts/base.md",
            "input_images": [{"path": rel(Path(ref["path"]), run_dir), "role": ref["role"]} for ref in copied_refs],
            "output_path": "decoded/base.png",
            "canonical_output_path": "references/canonical-base.png",
            "allow_prompt_only_generation": not copied_refs,
            "recommended_size": "1024x1024",
        }
    ]

    for action in actions:
        guide = run_dir / "references/layout-guides" / f"{action['id']}.png"
        make_layout_guide(guide, action["frames"], args.cell_width, args.cell_height, background_hex)
        if not guide.exists():
            raise SystemExit(f"failed to create layout guide: {guide}")
        write_text(
            run_dir / "prompts/actions" / f"{action['id']}.md",
            f"""
Create one horizontal animation strip for desktop pet `{args.subject}`, action `{action['id']}`.

Use the attached canonical base for identity. Use the attached layout guide only for slot count, spacing, centering, and padding; do not draw the guide.

Output exactly {action['frames']} full-body frames in one left-to-right row on flat pure {background_hex}. Treat the row as {action['frames']} invisible equal-width slots: one centered complete pose per slot, evenly spaced, with no overlap, clipping, empty slots, labels, or borders.

Identity: same pet in every frame: {args.description or "match the canonical base image exactly"}. Preserve silhouette, face/features, proportions, markings, palette, material, style, outfit, and props.
Style: {style_contract}
Animation continuity: keep apparent pet scale and baseline stable within the row unless the action intentionally changes vertical position. Move the pose within the slot instead of redrawing the pet larger or smaller frame to frame.

Action: {action['prompt']}

Action requirements:
- {action.get('guidance') or 'Use clear readable pose changes that match the action while preserving the subject identity.'}
- Keep each pose inside the layout guide safe area, around 55-70% of slot height and no more than 65% of slot width.
- Leave clear empty chroma-key gap between neighboring poses.
- Prefer pose, expression, and silhouette changes over decorative effects.

Clean extraction: crisp opaque edges, safe padding, no scenery, text, guide marks, checkerboard, shadows, glows, motion blur, speed lines, dust, detached effects, stray pixels, white/black backgrounds, or chroma-key colors inside the pet.
""",
        )
        write_text(
            run_dir / "prompts/action-retries" / f"{action['id']}.md",
            f"""
Create desktop pet row `{action['id']}` for `{args.subject}`: exactly {action['frames']} full-body frames in one horizontal strip on flat pure {background_hex}.

Use the attached canonical base as the identity lock and the layout guide only for spacing. Same standalone subject in every frame: {args.description or "match the canonical base image exactly"}. Preserve face/feature impression, proportions, palette, outfit/materials, props, silhouette, and style.

Action: {action['prompt']}
Action guidance: {action.get('guidance') or 'Use clear readable pose changes that match the action while preserving the subject identity.'}

One centered complete compact pose per invisible slot, stable apparent scale and baseline unless the action intentionally moves vertically. Keep each pose inside the layout guide safe area, around 55-70% of slot height and no more than 65% of slot width, with clear empty chroma-key gap between neighboring poses. Match the same visual subject size used by the other action rows; do not shrink or enlarge the character between actions. Keep all visible pixels attached to the pet silhouette or its approved props. No copied preview layout, UI, borders, extra people, text, labels, boxes, guide marks, scenery, shadows, floor shadows, glows, halos, motion blur, speed lines, dust, wave marks, detached effects, stray pixels, white/black backgrounds, or {background_hex} colors inside the subject.
""",
        )
        jobs.append(
            {
                "id": action["id"],
                "kind": "action-strip",
                "status": "pending",
                "depends_on": ["base"],
                "prompt_file": f"prompts/actions/{action['id']}.md",
                "retry_prompt_file": f"prompts/action-retries/{action['id']}.md",
                "input_images": [
                    {"path": rel(guide, run_dir), "role": "layout-only frame guide"},
                    {"path": "references/canonical-base.png", "role": "canonical identity reference"},
                ],
                "output_path": f"decoded/{action['id']}.png",
                "frames": action["frames"],
                "recommended_size": "1536x1024",
            }
        )

    job_manifest = {
        "version": 1,
        "subject": args.subject,
        "background": background_hex,
        "cell_width": args.cell_width,
        "cell_height": args.cell_height,
        "jobs": jobs,
    }
    (run_dir / "imagegen-jobs.json").write_text(json.dumps(job_manifest, indent=2), encoding="utf-8")

    summary = {
        "run_dir": str(run_dir),
        "base_prompt": str(run_dir / "prompts/base.md"),
        "imagegen_jobs": str(run_dir / "imagegen-jobs.json"),
        "background": background_hex,
        "action_prompts": {a["id"]: str(run_dir / "prompts/actions" / f"{a['id']}.md") for a in actions},
        "decoded_outputs": {a["id"]: str(run_dir / "decoded" / f"{a['id']}.png") for a in actions},
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
