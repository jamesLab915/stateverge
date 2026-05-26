#!/usr/bin/env python3
"""Thumbnail ambient preset manifest v1 — manifest JSON only (no Pillow/ffmpeg required here).

Pipeline hook:
  1. ffprobe/video path → theme via content_routing_v1
  2. Map theme + time_of_day → preset in config/stateverge_thumbnail_presets_v1.json
  3. Write thumbnail_manifest.json beside package (frame extract deferred to ffmpeg job)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
_PRESETS_PATH = _REPO / "config" / "stateverge_thumbnail_presets_v1.json"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_thumbnail_presets() -> dict[str, Any]:
    try:
        if _PRESETS_PATH.is_file():
            return json.loads(_PRESETS_PATH.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {"presets": {}, "selection_rules": {}}


def select_preset_name(theme: str, time_of_day: str, cfg: dict[str, Any] | None = None) -> str:
    c = cfg if cfg is not None else load_thumbnail_presets()
    rules = c.get("selection_rules") if isinstance(c.get("selection_rules"), dict) else {}
    overrides = rules.get("time_of_day_overrides") if isinstance(rules.get("time_of_day_overrides"), dict) else {}
    tod = (time_of_day or "").strip().lower()
    if tod and tod in overrides:
        return str(overrides[tod])
    mapping = rules.get("theme_to_preset") if isinstance(rules.get("theme_to_preset"), dict) else {}
    return str(mapping.get(theme) or mapping.get("unknown") or "day")


def pick_overlay_text(preset_name: str, seed: str, cfg: dict[str, Any] | None = None) -> str:
    import hashlib

    c = cfg if cfg is not None else load_thumbnail_presets()
    presets = c.get("presets") if isinstance(c.get("presets"), dict) else {}
    preset = presets.get(preset_name) if isinstance(presets.get(preset_name), dict) else {}
    words = list(preset.get("text_overlay_words") or ["NYC Ambience"])
    idx = int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % len(words)
    return str(words[idx])[:40]


def build_thumbnail_manifest(
    *,
    video_path: Path,
    package_dir: Path,
    theme: str = "unknown",
    time_of_day: str = "",
    job_id: str = "",
) -> dict[str, Any]:
    cfg = load_thumbnail_presets()
    preset_name = select_preset_name(theme, time_of_day, cfg)
    preset = (cfg.get("presets") or {}).get(preset_name, {})
    overlay = pick_overlay_text(preset_name, job_id or video_path.name, cfg)
    pipeline = cfg.get("pipeline") if isinstance(cfg.get("pipeline"), dict) else {}
    seek_pct = 0.35 if preset_name == "ferry" else 0.25 if preset_name == "night" else 0.2
    out_jpg = package_dir / "thumbnail_candidate.jpg"
    ffmpeg_tpl = str(pipeline.get("frame_extract") or "")
    ffmpeg_cmd = (
        ffmpeg_tpl.format(seek_pct=seek_pct, video=str(video_path), out_jpg=str(out_jpg))
        if ffmpeg_tpl
        else ""
    )
    return {
        "version": str(cfg.get("version") or "stateverge_thumbnail_presets_v1"),
        "generated_at": _utc_now(),
        "video_path": str(video_path),
        "package_dir": str(package_dir),
        "theme": theme,
        "time_of_day": time_of_day,
        "preset_name": preset_name,
        "text_overlay": overlay,
        "text_word_count_target": preset.get("text_word_count") or [2, 4],
        "visual_rules": list(preset.get("visual_rules") or []),
        "frame_extract": {
            "seek_pct": seek_pct,
            "output_jpg": str(out_jpg),
            "ffmpeg_command_template": ffmpeg_cmd,
            "status": "pending_external_ffmpeg",
        },
        "manifest_only": True,
        "heavy_image_deps": False,
    }


def write_thumbnail_manifest(package_dir: Path, manifest: dict[str, Any]) -> Path:
    package_dir.mkdir(parents=True, exist_ok=True)
    out = package_dir / "thumbnail_manifest.json"
    out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def _cli() -> int:
    ap = argparse.ArgumentParser(description="Thumbnail ambient manifest v1 (no image render)")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--package-dir", type=Path, required=True)
    ap.add_argument("--theme", default="unknown")
    ap.add_argument("--time-of-day", default="")
    ap.add_argument("--job-id", default="")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if not args.video.is_file():
        print(f"ERROR: video not found: {args.video}", file=sys.stderr)
        return 2

    manifest = build_thumbnail_manifest(
        video_path=args.video.expanduser().resolve(),
        package_dir=args.package_dir.expanduser().resolve(),
        theme=str(args.theme),
        time_of_day=str(args.time_of_day),
        job_id=str(args.job_id),
    )
    if args.dry_run:
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0
    out = write_thumbnail_manifest(args.package_dir.expanduser().resolve(), manifest)
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
