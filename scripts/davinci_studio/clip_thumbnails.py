#!/usr/bin/env python3
"""Preview thumbnails for DaVinci Folder Studio (cache only; never touches sources)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from folder_scan import scan_folder_dict
from paths import THUMBNAILS_ROOT, VIDEO_EXTS

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
THUMB_WIDTH = 320
THUMB_SEEK_SEC = 1.0


def _is_video_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in VIDEO_EXTS and not path.name.startswith("._")


def thumb_cache_path(source: Path) -> Path:
    """Deterministic cache file from source path + mtime + size."""
    source = source.expanduser().resolve()
    try:
        st = source.stat()
        token = f"{source}:{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        token = str(source)
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return THUMBNAILS_ROOT / key[:2] / f"{key}.jpg"


def ensure_thumbnail(source: Path, *, timeout_sec: float = 45.0) -> Path | None:
    """Generate a JPEG preview under THUMBNAILS_ROOT; return cache path or None."""
    source = source.expanduser().resolve()
    if not _is_video_file(source):
        return None
    out = thumb_cache_path(source)
    if out.is_file() and out.stat().st_size > 512:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        str(THUMB_SEEK_SEC),
        "-i",
        str(source),
        "-frames:v",
        "1",
        "-vf",
        f"scale={THUMB_WIDTH}:-2",
        "-q:v",
        "4",
        str(tmp),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 256:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return None
        tmp.replace(out)
        return out
    except (OSError, subprocess.TimeoutExpired):
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def list_folder_clips(folder: Path, *, probe_duration: bool = False) -> dict:
    """Scan folder and attach thumbnail cache keys (no generation until served)."""
    payload = scan_folder_dict(folder)
    clips_out: list[dict] = []
    for clip in payload.get("clips") or []:
        p = Path(str(clip.get("path") or ""))
        thumb = thumb_cache_path(p) if p.is_file() else None
        row = dict(clip)
        row["thumbnail_cache_key"] = thumb.name.removesuffix(".jpg") if thumb else ""
        row["has_thumbnail"] = bool(thumb and thumb.is_file())
        if probe_duration:
            try:
                from render_job import probe_duration_sec  # noqa: WPS433

                row["duration_sec"] = probe_duration_sec(p)
            except Exception:
                pass
        clips_out.append(row)
    return {
        "input_folder": payload.get("input_folder"),
        "clip_count": len(clips_out),
        "clips": clips_out,
        "thumbnails_root": str(THUMBNAILS_ROOT),
    }
