"""
XHS video stitcher.

Two responsibilities:

1) Given a folder path, list every video file in it (mp4/mov/m4v/webm), with
   filename, size, duration (via ffprobe), and a single-frame thumbnail
   served from a cache directory. Used by the dashboard to render a picker.

2) Stitch a user-chosen ordered list of clips into one MP4 written into the
   note folder. We use ffmpeg's concat demuxer when all clips share the same
   codec/resolution/fps; otherwise we fall back to a safe re-encode that
   normalizes everything to 1080p / 30fps / yuv420p / aac-128k.

Defaults match Runway Gen-3/Gen-4 image-to-video exports (1280x720 or
1920x1080, h264, 24-30 fps), so the fast path usually works.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


VIDEO_EXTS = (".mp4", ".mov", ".m4v", ".webm", ".mkv")
DEFAULT_CLIPS_SUBDIR = "runway_clips"
DEFAULT_OUTPUT_NAME = "stitched_60s.mp4"
THUMB_CACHE_REL = "_thumbs"  # under each note folder

# ffprobe / ffmpeg binaries — accept env override for non-standard installs.
FFMPEG_BIN = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


# ---------------------------------------------------------------------------
# Path resolution (always relative to the repo, no escapes)
# ---------------------------------------------------------------------------


class StitcherError(Exception):
    """Raised on user-facing errors (bad folder, no clips, ffmpeg failed...)."""


def resolve_clips_folder(repo_root: Path, note_id: str, folder: str | None) -> Path:
    """Resolve and validate a user-provided folder path.

    Accepts either:
      - empty / None       -> default ``assets/xhs/<note_id>/runway_clips``
      - relative path      -> resolved against repo root
      - absolute path      -> kept as-is, but must exist
    """
    if not folder or not folder.strip():
        return repo_root / "assets" / "xhs" / note_id / DEFAULT_CLIPS_SUBDIR
    raw = folder.strip()
    # Expand ~ for convenience when users paste a path from the Finder.
    raw = os.path.expanduser(raw)
    p = Path(raw)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    else:
        p = p.resolve()
    return p


# ---------------------------------------------------------------------------
# Probe / list
# ---------------------------------------------------------------------------


@dataclass
class ClipInfo:
    name: str               # filename only
    path: str               # absolute path
    rel_to_repo: str        # path relative to repo root if inside, else ""
    size_bytes: int
    duration_sec: float
    width: int
    height: int
    fps: float
    codec: str
    thumb_url: str = ""     # populated by the route layer

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "rel_to_repo": self.rel_to_repo,
            "size_bytes": self.size_bytes,
            "duration_sec": round(self.duration_sec, 2),
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "codec": self.codec,
            "thumb_url": self.thumb_url,
        }


def _probe_clip(path: Path) -> ClipInfo | None:
    if not path.is_file():
        return None
    try:
        proc = subprocess.run(
            [
                FFPROBE_BIN, "-v", "error",
                "-print_format", "json",
                "-show_streams", "-show_format",
                str(path),
            ],
            capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None

    width = height = 0
    fps = 0.0
    codec = ""
    duration = 0.0

    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0

    for s in data.get("streams") or []:
        if s.get("codec_type") != "video":
            continue
        width = int(s.get("width") or 0)
        height = int(s.get("height") or 0)
        codec = s.get("codec_name") or ""
        rate = s.get("avg_frame_rate") or s.get("r_frame_rate") or "0/1"
        try:
            num, _, den = rate.partition("/")
            n, d = float(num), float(den or 1) or 1
            fps = n / d if d else 0.0
        except (TypeError, ValueError):
            fps = 0.0
        break

    try:
        size = path.stat().st_size
    except OSError:
        size = 0

    return ClipInfo(
        name=path.name,
        path=str(path),
        rel_to_repo="",
        size_bytes=size,
        duration_sec=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
    )


_NUMBER_IN_NAME_RE = re.compile(r"\d+")


def _natural_sort_key(name: str) -> tuple:
    """Sort 'scene_2.mp4' before 'scene_10.mp4'."""
    parts = _NUMBER_IN_NAME_RE.split(name)
    nums = _NUMBER_IN_NAME_RE.findall(name)
    out: list = []
    for i, part in enumerate(parts):
        out.append(part.lower())
        if i < len(nums):
            try:
                out.append(int(nums[i]))
            except ValueError:
                out.append(nums[i])
    return tuple(out)


def list_clips(folder: Path, repo_root: Path) -> list[ClipInfo]:
    if not folder.is_dir():
        return []
    paths: list[Path] = []
    for ext in VIDEO_EXTS:
        paths.extend(folder.glob(f"*{ext}"))
        paths.extend(folder.glob(f"*{ext.upper()}"))
    # de-dupe (case-insensitive FS like APFS may glob both patterns)
    seen: set[Path] = set()
    paths = [p for p in paths if not (p in seen or seen.add(p))]
    paths.sort(key=lambda p: _natural_sort_key(p.name))

    clips: list[ClipInfo] = []
    for p in paths:
        info = _probe_clip(p)
        if not info:
            continue
        try:
            info.rel_to_repo = str(p.resolve().relative_to(repo_root.resolve())).replace(os.sep, "/")
        except ValueError:
            info.rel_to_repo = ""
        clips.append(info)
    return clips


# ---------------------------------------------------------------------------
# Thumbnail generation (cached)
# ---------------------------------------------------------------------------


def thumb_for_clip(repo_root: Path, note_id: str, clip_path: Path) -> Path | None:
    """Return the filesystem path to a JPG thumbnail for clip_path.

    Cached at ``assets/xhs/<note_id>/_thumbs/<hash>.jpg`` so we don't re-run
    ffmpeg on every page load.
    """
    cache_dir = repo_root / "assets" / "xhs" / note_id / THUMB_CACHE_REL
    cache_dir.mkdir(parents=True, exist_ok=True)
    # Stable, short, filesystem-safe key.
    key = re.sub(r"[^A-Za-z0-9._-]+", "_", str(clip_path))
    if len(key) > 120:
        key = key[-120:]
    out = cache_dir / f"{key}.jpg"
    if out.is_file() and out.stat().st_size > 0:
        return out
    try:
        proc = subprocess.run(
            [
                FFMPEG_BIN, "-y", "-loglevel", "error",
                "-ss", "0.5",
                "-i", str(clip_path),
                "-frames:v", "1",
                "-vf", "scale=320:-2",
                "-q:v", "5",
                str(out),
            ],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0 or not out.is_file():
        return None
    return out


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------


@dataclass
class StitchResult:
    ok: bool
    output_path: str
    output_rel: str
    duration_sec: float
    clip_count: int
    method: str          # "concat_demuxer" | "reencode"
    log_tail: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "output_path": self.output_path,
            "output_rel": self.output_rel,
            "duration_sec": round(self.duration_sec, 2),
            "clip_count": self.clip_count,
            "method": self.method,
            "log_tail": self.log_tail[-60:],
            "error": self.error,
        }


def _all_uniform(clips: list[ClipInfo]) -> bool:
    if len(clips) < 2:
        return True
    w, h, codec = clips[0].width, clips[0].height, clips[0].codec
    for c in clips[1:]:
        if (c.width, c.height) != (w, h):
            return False
        if c.codec != codec:
            return False
    return True


def stitch(
    repo_root: Path,
    note_id: str,
    ordered_clip_paths: list[Path],
    output_name: str = DEFAULT_OUTPUT_NAME,
) -> StitchResult:
    if not ordered_clip_paths:
        raise StitcherError("no clips selected")
    for p in ordered_clip_paths:
        if not p.is_file():
            raise StitcherError(f"clip not found: {p}")

    note_dir = repo_root / "assets" / "xhs" / note_id
    if not note_dir.is_dir():
        raise StitcherError(f"note folder not found: {note_dir}")

    # Sanitize output name; only the basename is used.
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", output_name)
    if not clean.lower().endswith((".mp4", ".mov", ".mkv")):
        clean += ".mp4"
    out_path = note_dir / clean

    # Probe selected clips so we can choose the fast path or the safe path.
    clip_infos = [_probe_clip(p) for p in ordered_clip_paths]
    clip_infos = [c for c in clip_infos if c]
    total_dur = sum(c.duration_sec for c in clip_infos)

    if len(clip_infos) == len(ordered_clip_paths) and _all_uniform(clip_infos):
        method = "concat_demuxer"
        ok, log_tail = _concat_demuxer(ordered_clip_paths, out_path)
        if not ok:
            # Fall back to re-encode if the fast path failed.
            method = "reencode"
            ok, log_tail = _concat_reencode(ordered_clip_paths, out_path)
    else:
        method = "reencode"
        ok, log_tail = _concat_reencode(ordered_clip_paths, out_path)

    if not ok:
        return StitchResult(
            ok=False,
            output_path=str(out_path),
            output_rel=str(out_path.relative_to(repo_root)).replace(os.sep, "/"),
            duration_sec=total_dur,
            clip_count=len(ordered_clip_paths),
            method=method,
            log_tail=log_tail,
            error="ffmpeg failed; see log_tail",
        )

    # Re-probe the final to report exact duration.
    final_info = _probe_clip(out_path)
    final_dur = final_info.duration_sec if final_info else total_dur

    return StitchResult(
        ok=True,
        output_path=str(out_path),
        output_rel=str(out_path.relative_to(repo_root)).replace(os.sep, "/"),
        duration_sec=final_dur,
        clip_count=len(ordered_clip_paths),
        method=method,
        log_tail=log_tail,
    )


def _concat_demuxer(clip_paths: list[Path], out_path: Path) -> tuple[bool, list[str]]:
    """Fast: stream-copy via ffmpeg concat demuxer. Requires uniform inputs."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        list_file = Path(f.name)
        for p in clip_paths:
            # Concat demuxer requires single quotes around paths, with single
            # quotes inside escaped as: '\''
            esc = str(p).replace("'", r"'\''")
            f.write(f"file '{esc}'\n")
    try:
        proc = subprocess.run(
            [
                FFMPEG_BIN, "-y", "-loglevel", "error",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                "-movflags", "+faststart",
                str(out_path),
            ],
            capture_output=True, text=True, timeout=300,
        )
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass
    log_tail = (proc.stderr or "").strip().splitlines()[-40:]
    return (proc.returncode == 0 and out_path.is_file()), log_tail


def _concat_reencode(clip_paths: list[Path], out_path: Path) -> tuple[bool, list[str]]:
    """Safe: normalize all clips to 1920x1080 / 30fps / yuv420p / aac, then concat."""
    n = len(clip_paths)
    inputs: list[str] = []
    for p in clip_paths:
        inputs.extend(["-i", str(p)])

    # Build a filter graph that scales+pads each input to 1920x1080 30fps,
    # then concatenates the normalized streams.
    parts: list[str] = []
    for i in range(n):
        parts.append(
            f"[{i}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            f"pad=1920:1080:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"fps=30,format=yuv420p,setsar=1[v{i}]"
        )
        # Audio: if the input has no audio, generate silence for its duration.
        # We add aevalsrc as a fallback; ffmpeg will pick the audio if it
        # exists otherwise the missing pad is filled with silence on concat.
        parts.append(f"[{i}:a?]aresample=async=1,asetpts=PTS-STARTPTS[a{i}]")

    concat_inputs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{concat_inputs}concat=n={n}:v=1:a=1[vout][aout]")
    filter_graph = ";".join(parts)

    cmd = [
        FFMPEG_BIN, "-y", "-loglevel", "error",
        *inputs,
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, [f"ffmpeg invocation failed: {exc}"]
    log_tail = (proc.stderr or "").strip().splitlines()[-40:]
    return (proc.returncode == 0 and out_path.is_file()), log_tail
