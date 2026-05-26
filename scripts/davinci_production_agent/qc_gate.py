#!/usr/bin/env python3
"""Post-render ffprobe QC gate (ffmpeg validation only)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

_MIN_DURATION_SEC = 30.0
_SHORTS_MAX_DURATION_SEC = 65.0
_LONG_MIN_DURATION_SEC = 45 * 60.0


def _resolve_ffprobe() -> str:
    found = shutil.which("ffprobe")
    if found:
        return found
    brew = Path("/opt/homebrew/bin/ffprobe")
    if brew.is_file():
        return str(brew)
    raise FileNotFoundError("ffprobe not found")


def ffprobe_summary(video_path: Path, *, ffprobe: str | None = None) -> dict[str, Any] | None:
    exe = ffprobe or _resolve_ffprobe()
    cmd = [
        exe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def validate_render(
    video_path: Path,
    *,
    kind: str = "long",
    min_duration_sec: float | None = None,
) -> dict[str, Any]:
    """Run ffprobe QC. Never modifies source."""
    out: dict[str, Any] = {
        "path": str(video_path),
        "ok": False,
        "kind": kind,
        "checks": {},
        "errors": [],
    }
    if not video_path.is_file():
        out["errors"].append("file_missing")
        return out

    probe = ffprobe_summary(video_path)
    if not probe:
        out["errors"].append("ffprobe_failed")
        return out

    fmt = probe.get("format") or {}
    dur = float(fmt.get("duration") or 0)
    out["duration_sec"] = dur
    out["format_name"] = fmt.get("format_name")

    vstreams = [s for s in (probe.get("streams") or []) if s.get("codec_type") == "video"]
    astreams = [s for s in (probe.get("streams") or []) if s.get("codec_type") == "audio"]
    out["checks"]["has_video"] = bool(vstreams)
    out["checks"]["has_audio"] = bool(astreams)
    if vstreams:
        vs = vstreams[0]
        out["width"] = int(vs.get("width") or 0)
        out["height"] = int(vs.get("height") or 0)
        out["avg_frame_rate"] = vs.get("avg_frame_rate")
        out["r_frame_rate"] = vs.get("r_frame_rate")

    min_d = min_duration_sec if min_duration_sec is not None else _MIN_DURATION_SEC
    if kind == "shorts":
        if dur > _SHORTS_MAX_DURATION_SEC:
            out["errors"].append("shorts_too_long")
        if dur < 5.0:
            out["errors"].append("shorts_too_short")
    elif kind == "long":
        min_d = max(min_d, _LONG_MIN_DURATION_SEC)
        if dur < min_d:
            out["errors"].append("long_too_short")

    if not out["checks"].get("has_video"):
        out["errors"].append("no_video_stream")
    if not out["checks"].get("has_audio"):
        out["errors"].append("no_audio_stream")

    out["ok"] = not out["errors"]
    return out
