#!/usr/bin/env python3
"""Inspect extracted animation frames for basic deterministic issues."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageChops


def alpha_bbox(img: Image.Image):
    return img.convert("RGBA").getchannel("A").getbbox()


def frames_identical(a: Image.Image, b: Image.Image) -> bool:
    diff = ImageChops.difference(a.convert("RGBA"), b.convert("RGBA"))
    return diff.getbbox() is None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames-root", required=True)
    parser.add_argument("--json-out", required=True)
    parser.add_argument("--allow-static", action="store_true")
    args = parser.parse_args()

    frames_root = Path(args.frames_root).resolve()
    manifest = json.loads((frames_root / "frames-manifest.json").read_text(encoding="utf-8"))
    cell_width = int(manifest["cell_width"])
    cell_height = int(manifest["cell_height"])

    errors = []
    warnings = []
    action_reports = []

    for action in manifest["actions"]:
        action_id = action["id"]
        paths = [Path(path) for path in action["frame_paths"]]
        report = {"id": action_id, "frames": len(paths), "issues": []}
        previous = None
        identical_pairs = 0

        for index, path in enumerate(paths):
            if not path.exists():
                issue = f"{action_id}[{index}] missing frame: {path}"
                errors.append(issue)
                report["issues"].append(issue)
                continue
            img = Image.open(path).convert("RGBA")
            if img.size != (cell_width, cell_height):
                issue = f"{action_id}[{index}] wrong size {img.size}; expected {(cell_width, cell_height)}"
                errors.append(issue)
                report["issues"].append(issue)
            if alpha_bbox(img) is None:
                issue = f"{action_id}[{index}] is empty/fully transparent"
                errors.append(issue)
                report["issues"].append(issue)
            if previous is not None and frames_identical(previous, img):
                identical_pairs += 1
            previous = img

        if len(paths) > 1 and identical_pairs == len(paths) - 1 and not args.allow_static:
            issue = f"{action_id} appears static; all adjacent frames are identical"
            warnings.append(issue)
            report["issues"].append(issue)

        action_reports.append(report)

    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "actions": action_reports,
    }
    out = Path(args.json_out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
