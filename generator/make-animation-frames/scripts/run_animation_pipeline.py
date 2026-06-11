#!/usr/bin/env python3
"""Standalone make-animation-frames pipeline using the OpenAI Images API.

This runner does not depend on Codex built-in image tools. It requires:

- OPENAI_API_KEY
- openai
- Pillow

It orchestrates:
prepare prompts/jobs -> generate/edit images -> extract frames -> inspect -> previews -> spritesheet.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "gpt-image-2"


def script_dir() -> Path:
    return Path(__file__).resolve().parent


def run_step(args: list[str]) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run(args, check=True)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_client(dry_run: bool, *, api_key: str | None = None, base_url: str | None = None):
    if dry_run:
        return None
    resolved_api_key = api_key or os.getenv("OPENAI_API_KEY")
    resolved_base_url = base_url or os.getenv("OPENAI_BASE_URL")
    if not resolved_api_key:
        raise SystemExit("OPENAI_API_KEY is not set.")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise SystemExit("Missing dependency: openai. Install with `pip install openai`.") from exc
    kwargs = {"api_key": resolved_api_key}
    if resolved_base_url:
        kwargs["base_url"] = resolved_base_url
    return OpenAI(**kwargs)


def attr_or_key(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def parse_string_result(value: str) -> Any:
    text = value.strip()
    if not text:
        return value
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def find_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s\"')>\]]+", text)
    return match.group(0) if match else None


def find_data_uri_payload(text: str) -> str | None:
    match = re.search(r"data:image/[^;,]+;base64,([A-Za-z0-9+/=\s\r\n]+)", text)
    return match.group(1) if match else None


def looks_like_base64(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < 80:
        return False
    if len(compact) % 4 != 0:
        return False
    return re.fullmatch(r"[A-Za-z0-9+/]+={0,2}", compact) is not None


def response_snippet(value: Any) -> str:
    text = str(value)
    return text[:600] + ("..." if len(text) > 600 else "")


def extract_image_payload(result: Any) -> tuple[str, str]:
    if isinstance(result, str):
        parsed = parse_string_result(result)
        if parsed is not result:
            return extract_image_payload(parsed)
        url = find_url(result)
        if url:
            return "url", url
        data_uri_payload = find_data_uri_payload(result)
        if data_uri_payload:
            return "b64", data_uri_payload
        if looks_like_base64(result):
            return "b64", result
        raise RuntimeError("Image API returned text instead of an image payload:\n" + response_snippet(result))

    if isinstance(result, list):
        if not result:
            raise RuntimeError("Image API returned an empty list.")
        return extract_image_payload(result[0])

    data = attr_or_key(result, "data")
    if data is None and isinstance(result, dict):
        for key in ("b64_json", "b64", "base64", "url", "image", "content"):
            direct = result.get(key)
            if direct:
                return extract_image_payload(direct)
        if isinstance(result.get("message"), dict):
            return extract_image_payload(result["message"])
        if result.get("choices"):
            return extract_image_payload(result["choices"])
        for key in ("images", "output", "result"):
            data = result.get(key)
            if data is not None:
                break
    if isinstance(data, str):
        return extract_image_payload(data)
    if not data:
        raise RuntimeError(f"Image API returned no images. Raw response type: {type(result).__name__}")

    first = data[0] if isinstance(data, list) else data
    image_b64 = attr_or_key(first, "b64_json") or attr_or_key(first, "b64") or attr_or_key(first, "base64")
    if image_b64:
        return "b64", image_b64
    image_url = attr_or_key(first, "url")
    if image_url:
        return "url", image_url
    if isinstance(first, str):
        return extract_image_payload(first)
    raise RuntimeError(f"Image API response has no b64_json or url. Raw item type: {type(first).__name__}")


def decode_first_image(result: Any, output_path: Path, force: bool) -> None:
    if output_path.exists() and not force:
        raise SystemExit(f"Output exists: {output_path} (use --force)")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    kind, payload = extract_image_payload(result)
    if kind == "url":
        with urllib.request.urlopen(payload, timeout=120) as response:
            output_path.write_bytes(response.read())
    else:
        if "," in payload and payload.lstrip().startswith("data:"):
            payload = payload.split(",", 1)[1]
        try:
            output_path.write_bytes(base64.b64decode(payload, validate=True))
        except (binascii.Error, ValueError) as exc:
            raise RuntimeError("Image API returned an invalid base64 image payload:\n" + response_snippet(payload)) from exc
    print(f"Wrote {output_path}")


def generate_image(client: Any, *, prompt: str, output_path: Path, model: str, quality: str, size: str, force: bool) -> None:
    print(f"Generating {output_path.name}...", flush=True)
    payload = {
        "model": model,
        "prompt": prompt,
        "quality": quality,
        "output_format": "png",
    }
    if size:
        payload["size"] = size
    result = client.images.generate(**payload)
    decode_first_image(result, output_path, force)


def edit_image(client: Any, *, prompt: str, image_paths: list[Path], output_path: Path, model: str, quality: str, size: str, force: bool) -> None:
    print(f"Generating {output_path.name} from {len(image_paths)} image reference(s)...", flush=True)
    handles = [path.open("rb") for path in image_paths]
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "image": handles if len(handles) > 1 else handles[0],
            "quality": quality,
            "output_format": "png",
        }
        if size:
            payload["size"] = size
        result = client.images.edit(**payload)
    finally:
        for handle in handles:
            handle.close()
    decode_first_image(result, output_path, force)


def mark_job_complete(jobs_path: Path, job_id: str, source_path: Path) -> None:
    manifest = json.loads(jobs_path.read_text(encoding="utf-8"))
    for job in manifest["jobs"]:
        if job["id"] == job_id:
            job["status"] = "complete"
            job["source_path"] = str(source_path)
            job["completed_at"] = utc_now()
            break
    else:
        raise RuntimeError(f"Job not found: {job_id}")
    jobs_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def ready_jobs(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    complete = {job["id"] for job in manifest["jobs"] if job.get("status") == "complete"}
    pending = [job for job in manifest["jobs"] if job.get("status") != "complete"]
    return [job for job in pending if all(dep in complete for dep in job.get("depends_on", []))]


def reconcile_existing_outputs(run_dir: Path, jobs_path: Path) -> None:
    manifest = json.loads(jobs_path.read_text(encoding="utf-8"))
    changed = False
    for job in manifest["jobs"]:
        if job.get("status") == "complete":
            continue
        output_path = run_dir / job["output_path"]
        if output_path.exists() and output_path.is_file():
            job["status"] = "complete"
            job["source_path"] = str(output_path)
            job["completed_at"] = utc_now()
            changed = True
            print(f"Resuming: found existing output for {job['id']}, marked complete.", flush=True)
            if job["id"] == "base":
                canonical = run_dir / job.get("canonical_output_path", "references/canonical-base.png")
                canonical.parent.mkdir(parents=True, exist_ok=True)
                if not canonical.exists():
                    canonical.write_bytes(output_path.read_bytes())
    if changed:
        jobs_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def run_generation(
    run_dir: Path,
    *,
    model: str,
    quality: str,
    size: str,
    force: bool,
    dry_run: bool,
    api_key: str | None,
    base_url: str | None,
) -> None:
    jobs_path = run_dir / "imagegen-jobs.json"
    reconcile_existing_outputs(run_dir, jobs_path)
    client = ensure_client(dry_run, api_key=api_key, base_url=base_url)

    while True:
        manifest = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = ready_jobs(manifest)
        if not jobs:
            pending = [job for job in manifest["jobs"] if job.get("status") != "complete"]
            if pending:
                raise SystemExit(f"No ready jobs but pending jobs remain: {[job['id'] for job in pending]}")
            return

        for job in jobs:
            prompt = read_text(run_dir / job["prompt_file"])
            output_path = run_dir / job["output_path"]
            planned_image_paths = [run_dir / item["path"] for item in job.get("input_images", [])]
            job_size = str(size if size and size != "auto" else job.get("recommended_size") or "auto")

            if dry_run:
                print(json.dumps({
                    "job": job["id"],
                    "kind": job["kind"],
                    "prompt_file": job["prompt_file"],
                    "input_images": [str(path) for path in planned_image_paths],
                    "output_path": str(output_path),
                    "method": "edit" if planned_image_paths else "generate",
                    "size": job_size,
                }, indent=2))
                mark_job_complete(jobs_path, job["id"], output_path)
                continue

            image_paths = []
            for path in planned_image_paths:
                if not path.exists():
                    raise SystemExit(f"Required input image missing for job {job['id']}: {path}")
                image_paths.append(path)

            if image_paths:
                edit_image(client, prompt=prompt, image_paths=image_paths, output_path=output_path, model=model, quality=quality, size=job_size, force=force)
            else:
                generate_image(client, prompt=prompt, output_path=output_path, model=model, quality=quality, size=job_size, force=force)

            if job["id"] == "base":
                canonical = run_dir / job.get("canonical_output_path", "references/canonical-base.png")
                canonical.parent.mkdir(parents=True, exist_ok=True)
                canonical.write_bytes(output_path.read_bytes())

            mark_job_complete(jobs_path, job["id"], output_path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--subject", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--style", default="auto")
    parser.add_argument("--reference", action="append", default=[])
    parser.add_argument("--action", action="append", default=[])
    parser.add_argument("--cell-width", type=int, default=192)
    parser.add_argument("--cell-height", type=int, default=208)
    parser.add_argument("--background", default="auto")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-key", help="OpenAI API key. Defaults to OPENAI_API_KEY.")
    parser.add_argument("--base-url", help="Custom API base URL. Defaults to OPENAI_BASE_URL when set.")
    parser.add_argument("--quality", default="medium")
    parser.add_argument("--size", default="auto", help="Use auto unless you know the model supports the exact size.")
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--extract-method", choices=["auto", "stable-slots"], default="auto")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-generation", action="store_true", help="Use existing decoded images and run only post-processing.")
    parser.add_argument("--resume-existing", action="store_true", help="Reuse an existing run directory and continue incomplete jobs without preparing a new run.")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    scripts = script_dir()

    if args.resume_existing:
        if not (run_dir / "imagegen-jobs.json").exists():
            raise SystemExit(f"Cannot resume; missing imagegen-jobs.json in {run_dir}")
        print(f"Resuming existing run: {run_dir}", flush=True)
    else:
        if not args.subject.strip():
            raise SystemExit("--subject is required unless --resume-existing is used.")
        prepare_cmd = [
            sys.executable,
            str(scripts / "prepare_animation_run.py"),
            "--subject",
            args.subject,
            "--description",
            args.description,
            "--style",
            args.style,
            "--output-dir",
            str(run_dir),
            "--cell-width",
            str(args.cell_width),
            "--cell-height",
            str(args.cell_height),
            "--background",
            args.background,
        ]
        for ref in args.reference:
            prepare_cmd.extend(["--reference", ref])
        for action in args.action:
            prepare_cmd.extend(["--action", action])
        if args.force:
            prepare_cmd.append("--force")
        run_step(prepare_cmd)

    if not args.skip_generation:
        run_generation(
            run_dir,
            model=args.model,
            quality=args.quality,
            size=args.size,
            force=args.force,
            dry_run=args.dry_run,
            api_key=args.api_key,
            base_url=args.base_url,
        )

    if args.dry_run:
        print("Dry run complete; skipped post-processing because no image bytes were generated.")
        return 0

    run_step([sys.executable, str(scripts / "extract_action_frames.py"), "--run-dir", str(run_dir), "--method", args.extract_method])
    run_step([sys.executable, str(scripts / "inspect_frames.py"), "--frames-root", str(run_dir / "final/frames"), "--json-out", str(run_dir / "qa/review.json")])
    run_step([sys.executable, str(scripts / "render_previews.py"), "--run-dir", str(run_dir), "--duration", str(args.duration)])
    run_step([sys.executable, str(scripts / "compose_spritesheet.py"), "--run-dir", str(run_dir), "--webp-output", str(run_dir / "final/spritesheet.webp")])
    print(f"Done: {run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
