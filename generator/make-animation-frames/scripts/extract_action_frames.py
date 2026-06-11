#!/usr/bin/env python3
"""Split generated action strips into transparent frame PNGs."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from PIL import Image


def parse_hex(value: str) -> tuple[int, int, int]:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        raise ValueError(f"Expected 6-digit hex color, got {value!r}")
    return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))


def color_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))


def remove_chroma(img: Image.Image, chroma: tuple[int, int, int], tolerance: float) -> Image.Image:
    rgba = img.convert("RGBA")
    pixels = rgba.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if a and color_distance((r, g, b), chroma) <= tolerance:
                pixels[x, y] = (0, 0, 0, 0)
    return rgba


def fit_to_cell(img: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    img = img.convert("RGBA")
    img.thumbnail((cell_width, cell_height), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    x = (cell_width - img.width) // 2
    y = (cell_height - img.height) // 2
    out.alpha_composite(img, (x, y))
    return out


def place_slot_in_cell(img: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    """Preserve row-slot scale; resize only the whole slot if it exceeds the target cell."""
    img = img.convert("RGBA")
    if img.width > cell_width or img.height > cell_height:
        scale = min(cell_width / img.width, cell_height / img.height)
        new_size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
        img = img.resize(new_size, Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    x = (cell_width - img.width) // 2
    y = (cell_height - img.height) // 2
    out.alpha_composite(img, (x, y))
    return out


def alpha_bbox(img: Image.Image):
    return img.convert("RGBA").getchannel("A").getbbox()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    low = math.floor(index)
    high = math.ceil(index)
    if low == high:
        return float(ordered[low])
    weight = index - low
    return float(ordered[low] * (1 - weight) + ordered[high] * weight)


def compute_subject_target(frames: list[Image.Image], cell_width: int, cell_height: int) -> tuple[int, int]:
    widths = []
    heights = []
    for frame in frames:
        bbox = alpha_bbox(frame)
        if bbox is None:
            continue
        widths.append(bbox[2] - bbox[0])
        heights.append(bbox[3] - bbox[1])
    if not heights:
        return int(cell_width * 0.72), int(cell_height * 0.72)

    target_height = percentile(heights, 0.75)
    target_height = min(cell_height * 0.82, max(cell_height * 0.58, target_height))

    aspects = [w / h for w, h in zip(widths, heights) if h > 0]
    median_aspect = percentile(aspects, 0.5) if aspects else 0.75
    target_width = min(cell_width * 0.82, target_height * median_aspect)

    return max(1, round(target_width)), max(1, round(target_height))


def normalize_subject_size(img: Image.Image, target_width: int, target_height: int) -> Image.Image:
    rgba = img.convert("RGBA")
    bbox = alpha_bbox(rgba)
    if bbox is None:
        return rgba

    subject = rgba.crop(bbox)
    subject_width = bbox[2] - bbox[0]
    subject_height = bbox[3] - bbox[1]
    if subject_width <= 0 or subject_height <= 0:
        return rgba

    scale = target_height / subject_height
    if subject_width * scale > target_width:
        scale = target_width / subject_width
    new_size = (
        max(1, round(subject_width * scale)),
        max(1, round(subject_height * scale)),
    )
    subject = subject.resize(new_size, Image.Resampling.LANCZOS)

    old_cx = (bbox[0] + bbox[2]) / 2
    old_cy = (bbox[1] + bbox[3]) / 2
    x = round(old_cx - subject.width / 2)
    y = round(old_cy - subject.height / 2)
    x = max(0, min(rgba.width - subject.width, x))
    y = max(0, min(rgba.height - subject.height, y))

    out = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    out.alpha_composite(subject, (x, y))
    return out


def load_request(run_dir: Path) -> dict:
    request_path = run_dir / "animation_request.json"
    if request_path.exists():
        return json.loads(request_path.read_text(encoding="utf-8"))
    actions_path = run_dir / "actions.json"
    return json.loads(actions_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--decoded-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--background")
    parser.add_argument("--tolerance", type=float, default=70.0)
    parser.add_argument("--cell-width", type=int)
    parser.add_argument("--cell-height", type=int)
    parser.add_argument("--method", choices=["auto", "stable-slots"], default="auto")
    parser.add_argument("--no-normalize-subject", action="store_true", help="Disable subject-size normalization across frames.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    request = load_request(run_dir)
    actions = request["actions"]
    decoded_dir = Path(args.decoded_dir).resolve() if args.decoded_dir else run_dir / "decoded"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else run_dir / "final/frames"
    cell_width = args.cell_width or int(request.get("cell_width", 192))
    cell_height = args.cell_height or int(request.get("cell_height", 208))
    chroma = parse_hex(args.background or request.get("background", "#00ff00"))

    manifest = {
        "cell_width": cell_width,
        "cell_height": cell_height,
        "method": args.method,
        "normalize_subject": not args.no_normalize_subject,
        "actions": [],
    }

    pending_frames = []

    for action in actions:
        action_id = action["id"]
        frame_count = int(action["frames"])
        strip_path = decoded_dir / f"{action_id}.png"
        if not strip_path.exists():
            raise FileNotFoundError(f"Missing decoded strip for {action_id}: {strip_path}")

        strip = Image.open(strip_path).convert("RGBA")
        frame_dir = output_dir / action_id
        frame_dir.mkdir(parents=True, exist_ok=True)
        frame_paths = []

        for i in range(frame_count):
            left = round(i * strip.width / frame_count)
            right = round((i + 1) * strip.width / frame_count)
            crop = strip.crop((left, 0, right, strip.height))
            crop = remove_chroma(crop, chroma, args.tolerance)
            frame = place_slot_in_cell(crop, cell_width, cell_height) if args.method == "stable-slots" else fit_to_cell(crop, cell_width, cell_height)
            frame_path = frame_dir / f"{action_id}_{i:02d}.png"
            pending_frames.append({"path": frame_path, "frame": frame})
            frame_paths.append(str(frame_path))

        manifest["actions"].append(
            {
                "id": action_id,
                "display_name": action.get("display_name", action_id),
                "frames": frame_count,
                "frame_paths": frame_paths,
            }
        )

    if pending_frames and not args.no_normalize_subject:
        target_width, target_height = compute_subject_target([item["frame"] for item in pending_frames], cell_width, cell_height)
        manifest["subject_target"] = {"width": target_width, "height": target_height}
        for item in pending_frames:
            item["frame"] = normalize_subject_size(item["frame"], target_width, target_height)

    for item in pending_frames:
        item["frame"].save(item["path"])

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "frames-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"frames_manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
