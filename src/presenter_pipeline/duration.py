"""
ffprobe / ffmpeg wrappers: duration, silence detection, normalization parameters.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

LOG = logging.getLogger("presenter.duration")


def run_cmd(
    args: list[str], *, capture_stderr: bool = True
) -> tuple[int, str, str]:
    p = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
    )
    return p.returncode, p.stdout, p.stderr if capture_stderr else ""


def ffprobe_duration(path: Path) -> float:
    """
    Return stream duration in seconds (3 decimal places) using ffprobe.
    Supports wav, mp3, m4a, mp4, etc.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"ffprobe: file not found: {path}")
    args = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    code, out, err = run_cmd(args)
    if code != 0:
        raise RuntimeError(f"ffprobe failed ({code}): {err.strip()}")
    data = json.loads(out)
    d = _extract_duration(data)
    if d is None:
        raise RuntimeError(f"ffprobe: could not parse duration for {path}")
    return round(d, 3)


def get_media_duration(path: Path) -> float:
    """
    Same as :func:`ffprobe_duration` — any supported media (``wav``/``mp3``/``m4a``/``mp4`` …), 3 decimal places.
    """
    return ffprobe_duration(path)


def _extract_duration(data: dict) -> Optional[float]:
    fmt = data.get("format", {})
    if "duration" in fmt:
        try:
            return float(fmt["duration"])
        except (TypeError, ValueError):
            pass
    for s in data.get("streams", []):
        if s.get("codec_type") in ("video", "audio") and "duration" in s:
            try:
                return float(s["duration"])
            except (TypeError, ValueError):
                pass
    return None


def round_duration_sec(sec: float) -> float:
    return round(float(sec), 3)


def detect_silence_endpoints(
    path: Path,
    *,
    noise_db: int = -40,
    min_silence: float = 0.25,
) -> list[float]:
    """
    Return candidate split points (seconds) in the interior of the file
    (mid-silence), using silencedetect.
    """
    path = Path(path)
    if not path.is_file():
        return []
    # silencedetect logs to stderr
    args = [
        "ffmpeg",
        "-y",
        "-i",
        str(path),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_silence}",
        "-f",
        "null",
        "-",
    ]
    code, _out, err = run_cmd(args)
    if code != 0 and "silence_start" not in err and "Silence" not in err:
        from .logutil import log_presenter
        log_presenter(
            LOG, "--", "--", "ffmpeg_silence", err[:500], level=logging.WARNING
        )
    points: list[float] = []
    # silence_start: X, silence_end: Y
    for m in re.finditer(r"silence_start:\s*([\d.]+)", err):
        try:
            points.append(float(m.group(1)))
        except ValueError:
            pass
    for m in re.finditer(r"silence_end:\s*([\d.]+)", err):
        try:
            points.append(float(m.group(1)))
        except ValueError:
            pass
    points = sorted({round(p, 3) for p in points})
    return points
