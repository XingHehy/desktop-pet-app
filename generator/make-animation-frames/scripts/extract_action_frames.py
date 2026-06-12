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
    chroma_channel = max(range(3), key=lambda index: chroma[index])
    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            rgb = (r, g, b)
            dominant_delta = rgb[chroma_channel] - max(rgb[index] for index in range(3) if index != chroma_channel)
            bright_chroma = rgb[chroma_channel] >= 96 and dominant_delta >= 36
            close_chroma = color_distance(rgb, chroma) <= tolerance
            if a and (close_chroma or bright_chroma):
                pixels[x, y] = (0, 0, 0, 0)
            elif a and chroma_channel == 1 and g > r and g > b:
                pixels[x, y] = (r, min(r, b), b, a)
    return rgba


def place_slot_in_cell(img: Image.Image, cell_width: int, cell_height: int) -> Image.Image:
    img = img.convert("RGBA")
    out = Image.new("RGBA", (cell_width, cell_height), (0, 0, 0, 0))
    x = (cell_width - img.width) // 2
    y = (cell_height - img.height) // 2
    out.alpha_composite(img, (x, y))
    return out


def alpha_bbox(img: Image.Image):
    return img.convert("RGBA").getchannel("A").getbbox()


def union_bbox(boxes: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int] | None:
    if not boxes:
        return None
    return (
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    )


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
    chroma = parse_hex(args.background or request.get("background", "#00ff00"))

    pending_frames = []
    inferred_width = 1
    inferred_height = 1

    for action in actions:
        action_id = action["id"]
        frame_count = int(action["frames"])
        strip_path = decoded_dir / f"{action_id}.png"
        if not strip_path.exists():
            raise FileNotFoundError(f"Missing decoded strip for {action_id}: {strip_path}")

        strip = Image.open(strip_path).convert("RGBA")
        crops = []
        boxes = []
        frame_paths = []
        for i in range(frame_count):
            left = round(i * strip.width / frame_count)
            right = round((i + 1) * strip.width / frame_count)
            crop = strip.crop((left, 0, right, strip.height))
            crop = remove_chroma(crop, chroma, args.tolerance)
            bbox = alpha_bbox(crop)
            if bbox is not None:
                boxes.append(bbox)
            crops.append(crop)

        action_box = union_bbox(boxes)
        if action_box is None:
            action_box = (0, 0, max(1, round(strip.width / max(1, frame_count))), strip.height)
        inferred_width = max(inferred_width, action_box[2] - action_box[0])
        inferred_height = max(inferred_height, action_box[3] - action_box[1])

        for i, crop in enumerate(crops):
            frame = crop.crop(action_box)
            frame_path = output_dir / action_id / f"{action_id}_{i:02d}.png"
            pending_frames.append({"path": frame_path, "frame": frame})
            frame_paths.append(str(frame_path))

        action["frame_paths"] = frame_paths

    cell_width = args.cell_width or inferred_width
    cell_height = args.cell_height or inferred_height

    manifest = {
        "cell_width": cell_width,
        "cell_height": cell_height,
        "method": args.method,
        "normalize_subject": False,
        "actions": [],
    }

    for action in actions:
        action_id = action["id"]
        frame_count = int(action["frames"])
        manifest["actions"].append(
            {
                "id": action_id,
                "display_name": action.get("display_name", action_id),
                "frames": frame_count,
                "frame_paths": action["frame_paths"],
            }
        )

    for item in pending_frames:
        item["path"].parent.mkdir(parents=True, exist_ok=True)
        item["frame"] = place_slot_in_cell(item["frame"], cell_width, cell_height)
        item["frame"].save(item["path"])

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "frames-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({"frames_manifest": str(manifest_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
