#!/usr/bin/env python3
"""Prefer finished_for_youtube uploads; optional gate run (fail-open)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from config import load_config
from gate_paths import FINISHED_FOR_YOUTUBE_ROOT, VIDEO_EXTS
from presets import resolve_preset


def _stem_key(path: Path) -> str:
    return path.stem.lower()


def planned_output_path(source: Path, preset: str) -> Path:
    """Deterministic finished output path (never overwrites source)."""
    stem = source.stem
    return FINISHED_FOR_YOUTUBE_ROOT / f"{stem}_yt_audio_{preset}.mp4"


def find_existing_finished(source: Path, preset: str | None = None) -> Path | None:
    """Return newest matching finished file for source stem, if any."""
    if not FINISHED_FOR_YOUTUBE_ROOT.is_dir():
        return None
    stem = source.stem
    candidates: list[tuple[float, Path]] = []
    for p in FINISHED_FOR_YOUTUBE_ROOT.iterdir():
        if not p.is_file() or p.suffix.lower() not in VIDEO_EXTS:
            continue
        name = p.name.lower()
        if not name.startswith(stem.lower()):
            continue
        if "_yt_audio_" not in name:
            continue
        if preset and f"_yt_audio_{preset.lower()}" not in name:
            continue
        try:
            candidates.append((p.stat().st_mtime, p))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def resolve_upload_video_path(
    source: Path,
    *,
    content_kind: str = "auto",
    preset: str | None = None,
    run_if_enabled: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """Pick upload path: finished > gate output > original. Never raises."""
    meta: dict[str, Any] = {
        "davinci_audio_finish_used": False,
        "davinci_audio_finish_source": str(source),
        "davinci_audio_finish_block_reason": None,
    }
    src = Path(source).expanduser()
    if not src.is_file():
        meta["davinci_audio_finish_block_reason"] = "source_missing"
        return src, meta

    cfg = load_config()
    resolved_preset = resolve_preset(
        src,
        preset or str(cfg.get("default_preset") or "auto"),
        content_kind=content_kind,
    )
    meta["davinci_preset"] = resolved_preset

    existing = find_existing_finished(src, preset=resolved_preset)
    if existing and existing.is_file():
        meta["davinci_audio_finish_used"] = True
        meta["davinci_audio_finished_path"] = str(existing)
        meta["davinci_audio_finish_reason"] = "existing_finished_for_youtube"
        return existing.resolve(), meta

    if not run_if_enabled or not bool(cfg.get("enable_davinci_audio_finish")):
        return src.resolve(), meta

    try:
        from gate_v1 import run_gate  # noqa: WPS433

        rep = run_gate(src, preset=resolved_preset, dry_run=False)
        meta["davinci_audio_finish_report"] = rep
        if rep.get("davinci_audio_finished") and rep.get("output_video"):
            out = Path(str(rep["output_video"]))
            if out.is_file() and out.resolve() != src.resolve():
                meta["davinci_audio_finish_used"] = True
                meta["davinci_audio_finished_path"] = str(out)
                return out.resolve(), meta
        if bool(cfg.get("require_davinci_audio_finish")):
            meta["davinci_audio_finish_block_reason"] = rep.get("block_reason") or "davinci_finish_required_failed"
            return src.resolve(), meta
    except Exception as exc:  # noqa: BLE001
        meta["davinci_audio_finish_exception"] = repr(exc)
        if bool(cfg.get("require_davinci_audio_finish")):
            meta["davinci_audio_finish_block_reason"] = "davinci_finish_exception"
        return src.resolve(), meta

    return src.resolve(), meta
