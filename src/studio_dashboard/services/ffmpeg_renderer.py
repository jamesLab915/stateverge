"""
FFmpeg-based Shorts renderer.

Inputs:
    topics/<slug>/audio/voice.wav
    topics/<slug>/mix/selected_assets.json

Output:
    topics/<slug>/output/final_short.mp4
        - 1080x1920 (portrait, 9:16)
        - 30 fps, yuv420p, libx264
        - AAC 48 kHz stereo, ~192k
        - +faststart for instant streaming

Pipeline:
    1. Probe voice.wav → voice_duration
    2. Build a segment plan from selected items, looping in order, per-clip
       duration = options.per_clip_sec (default 6.0s), until coverage ≥
       voice_duration. Avoids placing the same clip twice in a row when
       possible. Last segment is trimmed to the exact remaining time.
    3. Render each segment to .render_tmp/seg_NNN.mp4 with normalized
       resolution / fps / codec.
    4. ffmpeg concat-demuxer + -c copy → .render_tmp/silent.mp4
    5. Mux silent.mp4 + voice.wav → output/final_short.mp4 (-shortest)
    6. Validate with ffprobe; archive any prior final_short.mp4 first.

Failures at any step:
    - The ffmpeg/ffprobe stderr is appended to topics/<slug>/output/render_error.log
    - A RenderResult{ok=false, error=...} is returned; the partial files in
      .render_tmp/ are kept (so you can re-run with the cache warm) unless
      `cleanup=True`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..scanner import VIDEO_EXTS
from .paths import archive_existing, safe_subpath, safe_topic_dir


# Final delivery target — Shorts / Reels / TikTok aspect.
TARGET_W = 1080
TARGET_H = 1920
TARGET_FPS = 30
TARGET_AR = 48000
TARGET_AC = 2
DEFAULT_PER_CLIP_SEC = 6.0
MIN_CLIP_DUR = 1.0    # ignore clips shorter than this
MIN_SEGMENT_DUR = 1.0  # never emit a segment shorter than this
MAX_SEGMENTS = 400    # safety cap so a runaway loop can't fill the disk


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class RenderResult:
    ok: bool
    topic: str
    final_path: str = ""
    voice_duration_sec: float = 0.0
    final_duration_sec: float = 0.0
    final_width: int = 0
    final_height: int = 0
    final_fps: float = 0.0
    segment_count: int = 0
    archive_dir: str = ""
    error_log_path: str = ""
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Readiness:
    """Snapshot of whether a render can run RIGHT NOW (used by /render page)."""
    topic: str
    voice_path: str = ""
    voice_exists: bool = False
    voice_duration_sec: float = 0.0
    selection_path: str = ""
    selection_exists: bool = False
    selection_count: int = 0
    selection_duration_sec: float = 0.0
    final_path: str = ""
    final_exists: bool = False
    final_duration_sec: float = 0.0
    final_width: int = 0
    final_height: int = 0
    final_mtime: float = 0.0
    error_log_path: str = ""
    error_log_exists: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point: read-only readiness probe for the /render page
# ---------------------------------------------------------------------------


def get_readiness(slug: str, repo_root: Path) -> Readiness:
    out = Readiness(topic=slug)
    try:
        topic_dir = safe_topic_dir(slug, repo_root, create=False)
    except ValueError:
        return out

    voice = safe_subpath(topic_dir, "audio", "voice.wav")
    out.voice_path = str(voice.relative_to(repo_root))
    if voice.is_file():
        out.voice_exists = True
        out.voice_duration_sec = _ffprobe_duration(voice)

    sel = safe_subpath(topic_dir, "mix", "selected_assets.json")
    out.selection_path = str(sel.relative_to(repo_root))
    if sel.is_file():
        out.selection_exists = True
        try:
            data = json.loads(sel.read_text(encoding="utf-8"))
            items = data.get("items") or []
            out.selection_count = len(items)
            out.selection_duration_sec = float(
                sum(float(it.get("duration_sec") or 0.0) for it in items)
            )
        except (OSError, json.JSONDecodeError):
            pass

    final = safe_subpath(topic_dir, "output", "final_short.mp4")
    out.final_path = str(final.relative_to(repo_root))
    if final.is_file():
        out.final_exists = True
        info = _ffprobe_full(final)
        out.final_duration_sec = info["duration_sec"]
        out.final_width = info["width"]
        out.final_height = info["height"]
        try:
            out.final_mtime = final.stat().st_mtime
        except OSError:
            pass

    err_log = safe_subpath(topic_dir, "output", "render_error.log")
    out.error_log_path = str(err_log.relative_to(repo_root))
    out.error_log_exists = err_log.is_file()
    return out


# ---------------------------------------------------------------------------
# Public entry point: render
# ---------------------------------------------------------------------------


def render_short(
    *,
    slug: str,
    repo_root: Path,
    cleanup: bool = True,
) -> RenderResult:
    t0 = time.monotonic()
    err_log_path: Path | None = None

    try:
        topic_dir = safe_topic_dir(slug, repo_root, create=False)
    except ValueError as e:
        return RenderResult(ok=False, topic=slug, error=str(e))

    if not topic_dir.is_dir():
        return RenderResult(ok=False, topic=slug,
                            error=f"topic dir not found: topics/{slug}/")

    voice = safe_subpath(topic_dir, "audio", "voice.wav")
    sel_path = safe_subpath(topic_dir, "mix", "selected_assets.json")
    output_dir = safe_subpath(topic_dir, "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    err_log_path = safe_subpath(output_dir, "render_error.log")

    # ---- preconditions ----
    if not voice.is_file():
        return _fail(slug, err_log_path,
                     f"voice.wav not found at {voice.relative_to(repo_root)}",
                     t0)
    if not sel_path.is_file():
        return _fail(slug, err_log_path,
                     f"selected_assets.json not found at "
                     f"{sel_path.relative_to(repo_root)}",
                     t0)

    try:
        sel_data = json.loads(sel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return _fail(slug, err_log_path,
                     f"selected_assets.json invalid: {e}", t0)

    items = sel_data.get("items") or []
    valid_items = [it for it in items if _validate_item(it, repo_root)]
    if not valid_items:
        return _fail(slug, err_log_path,
                     "no valid items in selected_assets.json "
                     "(missing files / outside repo / wrong extension)", t0)

    voice_dur = _ffprobe_duration(voice)
    if voice_dur <= 0.5:
        return _fail(slug, err_log_path,
                     f"voice.wav has no usable duration "
                     f"({voice_dur:.2f}s)", t0)

    per_clip_sec = float(
        (sel_data.get("options") or {}).get("per_clip_sec")
        or DEFAULT_PER_CLIP_SEC
    )

    # ---- plan ----
    plan = _build_plan(voice_dur, valid_items, per_clip_sec=per_clip_sec)
    if not plan:
        return _fail(slug, err_log_path,
                     "render plan came out empty (clips too short?)", t0)

    # ---- per-segment encode ----
    tmp_dir = safe_subpath(topic_dir, "output", ".render_tmp")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    segment_paths: list[Path] = []
    for idx, seg in enumerate(plan, start=1):
        out_seg = tmp_dir / f"seg_{idx:03d}.mp4"
        rc, stderr = _encode_segment(
            input_path=Path(seg["input"]),
            start=seg["start"],
            duration=seg["duration"],
            out_path=out_seg,
        )
        if rc != 0:
            return _fail(
                slug, err_log_path,
                f"segment {idx:03d} encode failed (rc={rc}); "
                f"input={seg['input']}",
                t0, stderr_extra=stderr,
            )
        segment_paths.append(out_seg)

    # ---- concat ----
    silent_path = tmp_dir / "silent.mp4"
    rc, stderr = _concat_segments(segment_paths, silent_path)
    if rc != 0:
        return _fail(slug, err_log_path,
                     f"concat failed (rc={rc})", t0, stderr_extra=stderr)

    # ---- archive previous final (do this just before the mux that overwrites it) ----
    final_path = safe_subpath(output_dir, "final_short.mp4")
    archive_dir = archive_existing(
        [final_path],
        archive_root=output_dir / "archive",
    )

    # ---- mux ----
    rc, stderr = _mux_av(silent_path, voice, final_path)
    if rc != 0:
        return _fail(slug, err_log_path,
                     f"mux failed (rc={rc})", t0, stderr_extra=stderr,
                     archive_dir=archive_dir)

    # ---- final probe ----
    info = _ffprobe_full(final_path)

    # ---- cleanup tmp ----
    if cleanup:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # If we got here, the previous run's error log no longer applies; truncate
    # it so the operator isn't confused.
    try:
        if err_log_path.is_file():
            err_log_path.unlink()
    except OSError:
        pass

    return RenderResult(
        ok=True,
        topic=slug,
        final_path=str(final_path.relative_to(repo_root)),
        voice_duration_sec=round(voice_dur, 2),
        final_duration_sec=round(info["duration_sec"], 2),
        final_width=info["width"],
        final_height=info["height"],
        final_fps=info["fps"],
        segment_count=len(segment_paths),
        archive_dir=(
            str(archive_dir.relative_to(repo_root)) if archive_dir else ""
        ),
        error_log_path="",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


def _build_plan(
    voice_dur: float,
    items: list[dict],
    *,
    per_clip_sec: float = DEFAULT_PER_CLIP_SEC,
) -> list[dict]:
    """
    Loop through items in order. Each segment is:
        - up to per_clip_sec long
        - never longer than the source clip itself
        - never shorter than MIN_SEGMENT_DUR

    The last segment is trimmed so the total coverage is voice_dur (with up to
    +0.5s slack so the AAC tail isn't clipped mid-word).
    """
    usable = [it for it in items
              if float(it.get("duration_sec") or 0.0) >= MIN_CLIP_DUR]
    if not usable:
        return []

    n = len(usable)
    plan: list[dict] = []
    total = 0.0
    target = voice_dur + 0.5  # slack
    last_idx = -1
    i = 0

    while total < target and len(plan) < MAX_SEGMENTS:
        # Avoid same clip twice in a row when possible
        if i == last_idx and n > 1:
            i = (i + 1) % n
        item = usable[i]
        clip_dur = float(item["duration_sec"])
        seg_dur = min(per_clip_sec, clip_dur)
        # If almost done, trim the final segment to fit
        remaining = target - total
        if remaining < seg_dur:
            if remaining < MIN_SEGMENT_DUR:
                # Extend the previous (but never longer than that clip's source)
                if plan:
                    last = plan[-1]
                    last_clip_dur = float(last["clip_duration"])
                    bonus = min(MIN_SEGMENT_DUR - remaining,
                                last_clip_dur - last["duration"])
                    if bonus > 0.05:
                        last["duration"] = round(last["duration"] + bonus, 3)
                        total += bonus
                break
            seg_dur = remaining
        plan.append({
            "input": item["abs_path"],
            "start": 0.0,                 # always from clip start (simple, predictable)
            "duration": round(seg_dur, 3),
            "clip_duration": clip_dur,
            "label": item.get("rel_path") or item["abs_path"],
        })
        total += seg_dur
        last_idx = i
        i = (i + 1) % n

    return plan


# ---------------------------------------------------------------------------
# ffmpeg / ffprobe wrappers
# ---------------------------------------------------------------------------


def _validate_item(it: dict, repo_root: Path) -> bool:
    abs_path = (it.get("abs_path") or "").strip()
    if not abs_path:
        return False
    try:
        p = Path(abs_path).resolve()
    except OSError:
        return False
    try:
        if not p.is_relative_to(repo_root):
            return False
    except (TypeError, ValueError):
        return False
    if not p.is_file():
        return False
    if p.suffix.lower() not in VIDEO_EXTS:
        return False
    return True


def _encode_segment(
    *,
    input_path: Path,
    start: float,
    duration: float,
    out_path: Path,
) -> tuple[int, str]:
    """
    Render one normalized 1080x1920 / 30fps / yuv420p segment, no audio.
    `-ss` before `-i` so seek is fast even on long source files; the
    re-encode means we never inherit weird codecs / variable framerate.
    """
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},"
        f"fps={TARGET_FPS},"
        f"setpts=PTS-STARTPTS"
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(start, 0.0):.3f}",
        "-i", str(input_path),
        "-t", f"{max(duration, 0.1):.3f}",
        "-an",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return _run(cmd)


def _concat_segments(seg_paths: list[Path], out_path: Path) -> tuple[int, str]:
    if not seg_paths:
        return 1, "no segments to concat"
    list_file = out_path.parent / "_concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in seg_paths) + "\n",
        encoding="utf-8",
    )
    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_path),
        ]
        return _run(cmd)
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass


def _mux_av(
    silent_video: Path,
    voice_wav: Path,
    out_path: Path,
) -> tuple[int, str]:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(silent_video),
        "-i", str(voice_wav),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", str(TARGET_AR),
        "-ac", str(TARGET_AC),
        "-shortest",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return _run(cmd)


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0
    if r.returncode != 0:
        return 0.0
    try:
        j = json.loads(r.stdout or "{}")
        return float((j.get("format") or {}).get("duration") or 0.0)
    except (json.JSONDecodeError, TypeError, ValueError):
        return 0.0


def _ffprobe_full(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    if r.returncode != 0:
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    try:
        j = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    fmt = j.get("format") or {}
    streams = j.get("streams") or []
    s0 = streams[0] if streams else {}
    fps = 0.0
    rfr = s0.get("r_frame_rate")
    if isinstance(rfr, str) and "/" in rfr:
        try:
            num, den = rfr.split("/")
            num_f, den_f = float(num), float(den)
            if den_f > 0:
                fps = round(num_f / den_f, 3)
        except ValueError:
            pass
    try:
        dur = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "duration_sec": dur,
        "width": int(s0.get("width") or 0),
        "height": int(s0.get("height") or 0),
        "fps": fps,
    }


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as e:
        return 127, f"executable not found: {e}"
    return r.returncode, r.stderr or ""


# ---------------------------------------------------------------------------
# Failure → write error log + return RenderResult(ok=False)
# ---------------------------------------------------------------------------


def _fail(
    slug: str,
    err_log_path: Path | None,
    msg: str,
    t0: float,
    *,
    stderr_extra: str = "",
    archive_dir: Path | None = None,
) -> RenderResult:
    if err_log_path is not None:
        try:
            err_log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with err_log_path.open("a", encoding="utf-8") as f:
                f.write(f"\n=== {ts}  topic={slug} ===\n")
                f.write(msg.rstrip() + "\n")
                if stderr_extra.strip():
                    f.write("--- ffmpeg stderr ---\n")
                    f.write(stderr_extra.rstrip() + "\n")
        except OSError:
            pass
    return RenderResult(
        ok=False,
        topic=slug,
        error=msg,
        error_log_path=str(err_log_path) if err_log_path else "",
        archive_dir=str(archive_dir) if archive_dir else "",
        duration_ms=int((time.monotonic() - t0) * 1000),
    )
