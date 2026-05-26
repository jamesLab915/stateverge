"""ffprobe helpers for DaVinci export / publish gate scripts (fail-open)."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def ffprobe_bin() -> str:
    return os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def ffprobe_json(
    path: Path, *, timeout_sec: float = 120.0
) -> tuple[dict[str, Any] | None, str | None]:
    """Return ``(data, error)``; on success ``error`` is ``None``."""
    exe = ffprobe_bin()
    cmd = [
        exe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or "ffprobe failed").strip()[:2000]
            logger.debug("ffprobe rc=%s %s", r.returncode, msg)
            return None, msg
        data = json.loads(r.stdout or "{}")
        return data, None
    except FileNotFoundError:
        logger.warning("ffprobe binary missing: %s", exe)
        return None, "ffprobe_not_found"
    except subprocess.TimeoutExpired:
        return None, "ffprobe_timeout"
    except json.JSONDecodeError as exc:
        return None, f"ffprobe_json_parse: {exc}"
    except OSError as exc:
        return None, str(exc)


def stream_summary(data: dict[str, Any]) -> tuple[bool, bool, float]:
    streams = data.get("streams") or []
    has_video = any(s.get("codec_type") == "video" for s in streams)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    dur_raw = (data.get("format") or {}).get("duration")
    try:
        duration = float(dur_raw) if dur_raw not in (None, "") else 0.0
    except (TypeError, ValueError):
        duration = 0.0
    return has_video, has_audio, duration
