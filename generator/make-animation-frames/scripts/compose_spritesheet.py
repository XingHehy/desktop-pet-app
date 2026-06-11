#!/usr/bin/env python3
"""Compose extracted frames into a merged spritesheet and contact sheet."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def load_font(size: int = 14):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except OSError:
        return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--frames-dir")
    parser.add_argument("--output", help="Merged transparent PNG spritesheet.")
    parser.add_argument("--webp-output", help="Optional WebP spritesheet.")
    parser.add_argument("--contact-sheet", help="Optional labeled PNG contact sheet.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    frames_dir = Path(args.frames_dir).resolve() if args.frames_dir else run_dir / "final/frames"
    manifest = json.loads((frames_dir / "frames-manifest.json").read_text(encoding="utf-8"))
    actions = manifest["actions"]
    cell_width = int(manifest["cell_width"])
    cell_height = int(manifest["cell_height"])
    max_frames = max(int(action["frames"]) for action in actions)

    output = Path(args.output).resolve() if args.output else run_dir / "final/spritesheet.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet = Image.new("RGBA", (max_frames * cell_width, len(actions) * cell_height), (0, 0, 0, 0))

    for row, action in enumerate(actions):
        for col, path in enumerate(action["frame_paths"]):
            frame = Image.open(path).convert("RGBA")
            sheet.alpha_composite(frame, (col * cell_width, row * cell_height))

    sheet.save(output)

    result = {"spritesheet": str(output)}
    if args.webp_output:
        webp_output = Path(args.webp_output).resolve()
        webp_output.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(webp_output, lossless=True)
        result["spritesheet_webp"] = str(webp_output)

    contact_path = Path(args.contact_sheet).resolve() if args.contact_sheet else run_dir / "qa/contact-sheet.png"
    contact_path.parent.mkdir(parents=True, exist_ok=True)
    label_width = 160
    header_height = 28
    contact = Image.new("RGBA", (label_width + sheet.width, header_height + sheet.height), (246, 246, 246, 255))
    draw = ImageDraw.Draw(contact)
    font = load_font()
    for col in range(max_frames):
        draw.text((label_width + col * cell_width + 8, 6), str(col), fill=(40, 40, 40), font=font)
    for row, action in enumerate(actions):
        y = header_height + row * cell_height
        draw.text((8, y + 10), action.get("display_name", action["id"]), fill=(20, 20, 20), font=font)
        draw.rectangle((label_width, y, label_width + sheet.width - 1, y + cell_height - 1), outline=(210, 210, 210))
    checker = Image.new("RGBA", sheet.size, (255, 255, 255, 255))
    cdraw = ImageDraw.Draw(checker)
    tile = 16
    for y in range(0, checker.height, tile):
        for x in range(0, checker.width, tile):
            if (x // tile + y // tile) % 2:
                cdraw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=(226, 226, 226, 255))
    checker.alpha_composite(sheet)
    contact.alpha_composite(checker, (label_width, header_height))
    contact.save(contact_path)
    result["contact_sheet"] = str(contact_path)

    summary_path = run_dir / "qa/run-summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    result["run_summary"] = str(summary_path)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
