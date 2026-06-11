#!/usr/bin/env python3
"""Render per-action animated GIF previews from extracted frames."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def remove_green_matte(img: Image.Image) -> Image.Image:
    """Reduce green spill on semi-transparent antialiased edges."""
    img = img.convert("RGBA")
    arr = np.array(img, dtype=np.uint8)
    r = arr[:, :, 0].astype(np.int16)
    g = arr[:, :, 1].astype(np.int16)
    b = arr[:, :, 2].astype(np.int16)
    a = arr[:, :, 3]
    mask = (a > 0) & (a < 255) & (g > r) & (g > b)
    g[mask] = np.minimum(r[mask], b[mask])
    arr[:, :, 1] = np.clip(g, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def remove_purple_fringe(img: Image.Image) -> Image.Image:
    """Reduce purple/magenta spill on semi-transparent antialiased edges."""
    img = img.convert("RGBA")
    arr = np.array(img, dtype=np.int16)
    r, g, b, a = arr[..., 0], arr[..., 1], arr[..., 2], arr[..., 3]
    mask = (a > 0) & (a < 255) & (r > g) & (b > g)
    g[mask] = np.maximum(r[mask], b[mask])
    arr[..., 1] = np.clip(g, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def harden_alpha(img: Image.Image, threshold: int) -> Image.Image:
    """GIF supports binary transparency; remove semi-transparent black matte risk."""
    rgba = img.convert("RGBA")
    arr = np.array(rgba, dtype=np.uint8)
    alpha = arr[..., 3]
    arr[..., 3] = np.where(alpha >= threshold, 255, 0).astype(np.uint8)
    arr[alpha < threshold, 0:3] = 0
    return Image.fromarray(arr, "RGBA")


def rgba_to_gif_frame(img: Image.Image, alpha_threshold: int) -> Image.Image:
    """Convert RGBA to paletted GIF frame with a reserved transparent index."""
    rgba = harden_alpha(img, alpha_threshold)
    alpha = np.array(rgba.getchannel("A"))
    transparent_mask = alpha == 0

    rgb = rgba.convert("RGB")
    paletted = rgb.convert("P", palette=Image.Palette.ADAPTIVE, colors=255)
    palette = paletted.getpalette() or []
    palette = (palette + [0] * 768)[:768]
    palette[255 * 3 : 255 * 3 + 3] = [0, 0, 0]

    arr = np.array(paletted, dtype=np.uint8)
    arr[transparent_mask] = 255
    out = Image.fromarray(arr, "P")
    out.putpalette(palette)
    out.info["transparency"] = 255
    return out


def process_frame(img: Image.Image, despill: str, alpha_threshold: int) -> Image.Image:
    img = img.convert("RGBA")
    if despill in {"green", "both"}:
        img = remove_green_matte(img)
    if despill in {"purple", "both"}:
        img = remove_purple_fringe(img)
    return rgba_to_gif_frame(img, alpha_threshold)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--frames-dir")
    parser.add_argument("--output-dir")
    parser.add_argument("--duration", type=int, default=120, help="Frame duration in ms.")
    parser.add_argument("--despill", choices=["none", "green", "purple", "both"], default="both")
    parser.add_argument("--alpha-threshold", type=int, default=8, help="Alpha below this becomes transparent; the rest becomes opaque for GIF.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    frames_dir = Path(args.frames_dir).resolve() if args.frames_dir else run_dir / "final/frames"
    output_dir = Path(args.output_dir).resolve() if args.output_dir else run_dir / "final/previews"
    manifest = json.loads((frames_dir / "frames-manifest.json").read_text(encoding="utf-8"))
    output_dir.mkdir(parents=True, exist_ok=True)

    previews = {}
    for action in manifest["actions"]:
        frames = [
            process_frame(Image.open(path), args.despill, args.alpha_threshold)
            for path in action["frame_paths"]
        ]
        if not frames:
            continue
        preview_path = output_dir / f"{action['id']}.gif"
        frames[0].save(
            preview_path,
            save_all=True,
            append_images=frames[1:],
            duration=args.duration,
            loop=0,
            disposal=2,
            transparency=255,
        )
        previews[action["id"]] = str(preview_path)

    print(json.dumps({"previews": previews}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
