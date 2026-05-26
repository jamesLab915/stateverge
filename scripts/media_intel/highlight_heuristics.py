"""Highlight segment heuristics v1 — lightweight ffmpeg frame sampling, fail-open.

If ffmpeg cannot produce decoded gray frames, returns ``[]`` (no synthetic timestamps).

Heuristics (non-overlapping preference; multiple reasons may each emit one segment):
- ``stable_low_motion``: consecutive samples with low mean-luma delta.
- ``sunset_luma_ramp``: sustained upward trend in mean luma then plateau/peak.
- ``skyline_top_band``: early-window elevated top-third / full-frame luma ratio.
- ``low_motion_cinematic``: landscape + sustained low motion in mid clip.
- ``portrait_shorts_candidate``: portrait + short stable window.
- ``timelapse_slice_hint``: high fps or (short duration + low luma variance across samples).
"""

from __future__ import annotations

import math
import shutil
import subprocess
from pathlib import Path
from typing import Any


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def sample_gray_frames(path: Path, duration: float, *, timeout_sec: float = 120.0) -> list[dict[str, float]] | None:
    """Decode evenly spaced grayscale frames; return ``[{'t': sec, 'mean': 0-255, 'top_ratio': float}, ...]`` or ``None``."""
    try:
        if duration <= 0.1 or not path.is_file() or shutil.which("ffmpeg") is None:
            return None
    except OSError:
        return None
    n_target = min(28, max(8, int(duration / 3.0) + 1))
    fps_v = max(0.12, min(2.0, float(n_target) / float(duration)))
    fw, fh = 96, 54
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-threads",
        "1",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"fps={fps_v:.6f},scale={fw}:{fh}:flags=fast_bilinear,format=gray",
        "-frames:v",
        str(n_target),
        "-f",
        "rawvideo",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, check=False)
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    frame_sz = fw * fh
    raw = r.stdout
    n_frames = len(raw) // frame_sz
    if n_frames < 4:
        return None
    times: list[float] = []
    for i in range(n_frames):
        times.append(_clip(duration * (i / max(n_frames - 1, 1)), 0.0, max(0.0, duration - 0.05)))
    out: list[dict[str, float]] = []
    for i in range(n_frames):
        chunk = raw[i * frame_sz : (i + 1) * frame_sz]
        if len(chunk) != frame_sz:
            return None
        vals = list(chunk)
        mean = sum(vals) / len(vals)
        top_h = max(1, fh // 3)
        top_pix = vals[: fw * top_h]
        top_mean = sum(top_pix) / len(top_pix) if top_pix else mean
        ratio = (top_mean + 1e-3) / (mean + 1e-3)
        out.append({"t": times[i], "mean": float(mean), "top_ratio": float(ratio)})
    return out


def _segment(start: float, end: float, score: float, reason: str, duration: float) -> dict[str, Any] | None:
    start = _clip(start, 0.0, max(0.0, duration - 0.05))
    end = _clip(end, start + 0.15, duration)
    if end <= start + 0.1:
        return None
    return {"start_sec": round(start, 2), "end_sec": round(end, 2), "score": round(float(score), 3), "reason": reason}


def _merge_dedupe(segments: list[dict[str, Any]], *, max_out: int = 14) -> list[dict[str, Any]]:
    if not segments:
        return []
    segments = sorted(segments, key=lambda s: (-float(s.get("score") or 0), float(s.get("start_sec") or 0)))
    kept: list[dict[str, Any]] = []
    for seg in segments:
        a0, a1 = float(seg["start_sec"]), float(seg["end_sec"])
        overlap = False
        for ex in kept:
            b0, b1 = float(ex["start_sec"]), float(ex["end_sec"])
            if not (a1 <= b0 or a0 >= b1):
                overlap = True
                break
        if not overlap:
            kept.append(seg)
        if len(kept) >= max_out:
            break
    return sorted(kept, key=lambda s: float(s["start_sec"]))


def compute_highlight_segments(
    path: Path,
    *,
    duration: float,
    width: int,
    height: int,
    fps: float,
    orientation: str,
    likely_source_type: str,
) -> list[dict[str, Any]]:
    """Return highlight segments from sampled frames, or ``[]`` if sampling fails."""
    try:
        samples = sample_gray_frames(path, duration)
        if not samples or len(samples) < 4:
            return []
    except Exception:
        return []

    means = [float(s["mean"]) for s in samples]
    tops = [float(s["top_ratio"]) for s in samples]
    ts = [float(s["t"]) for s in samples]

    def mean_delta_window(i0: int, i1: int) -> float:
        sub = means[i0 : i1 + 1]
        if len(sub) < 2:
            return 0.0
        return sum(abs(sub[j + 1] - sub[j]) for j in range(len(sub) - 1)) / (len(sub) - 1)

    var_luma = float(sum((m - sum(means) / len(means)) ** 2 for m in means) / max(len(means), 1))
    var_norm = var_luma / max(1.0, sum(means) / len(means))

    segs: list[dict[str, Any]] = []

    # --- stable low motion: longest run of low delta ---
    low_thr = 6.5
    run_start = 0
    best_run = (0, 0)
    for i in range(1, len(means)):
        d = abs(means[i] - means[i - 1])
        if d > low_thr:
            if i - 1 - run_start >= 3 and (i - 1 - run_start) > (best_run[1] - best_run[0]):
                best_run = (run_start, i - 1)
            run_start = i
    if len(means) - 1 - run_start >= 3 and (len(means) - 1 - run_start) > (best_run[1] - best_run[0]):
        best_run = (run_start, len(means) - 1)
    i0, i1 = best_run
    if i1 - i0 >= 3:
        t0 = ts[i0]
        t1 = ts[i1]
        pad = min(2.0, duration * 0.08)
        sc = _clip(0.82 - min(mean_delta_window(i0, i1) / 20.0, 0.35), 0.35, 0.88)
        seg = _segment(t0 - pad, t1 + pad, sc, "stable_low_motion", duration)
        if seg:
            segs.append(seg)

    # --- sunset luma ramp: max positive slope region ---
    best_slope = 0.0
    best_i = 1
    for i in range(2, len(means) - 1):
        slope = (means[i] - means[i - 2]) / max(ts[i] - ts[i - 2], 0.25)
        if slope > best_slope:
            best_slope = slope
            best_i = i
    if best_slope > 8.0:  # luma units per second-ish
        t_peak = ts[best_i]
        win = min(14.0, duration * 0.22)
        seg = _segment(t_peak - win * 0.35, t_peak + win * 0.65, _clip(best_slope / 35.0, 0.4, 0.9), "sunset_luma_ramp", duration)
        if seg:
            segs.append(seg)

    # --- skyline opening: first ~18% duration with high top_ratio ---
    early_n = max(2, int(len(samples) * 0.22))
    early_avg_top = sum(tops[:early_n]) / early_n
    if early_avg_top > 1.18 and ts[0] <= duration * 0.25:
        t1 = min(duration * 0.2, ts[early_n - 1] + 2.0)
        seg = _segment(0.0, t1, _clip((early_avg_top - 1.0) * 1.2, 0.42, 0.88), "skyline_opening_top_band", duration)
        if seg:
            segs.append(seg)

    # --- low-motion cinematic (landscape, mid-body) ---
    if orientation == "landscape" and duration > 25:
        mid = len(samples) // 2
        half = max(3, len(samples) // 4)
        i0, i1 = max(0, mid - half), min(len(samples) - 1, mid + half)
        md = mean_delta_window(i0, i1)
        if md < 7.0:
            t0, t1 = ts[i0], ts[i1]
            seg = _segment(t0 - 1.0, t1 + 2.0, _clip(0.75 - md / 25.0, 0.38, 0.85), "low_motion_cinematic", duration)
            if seg:
                segs.append(seg)

    # --- portrait shorts candidate ---
    if orientation == "portrait" and duration >= 4:
        for i in range(0, len(means) - 3):
            if mean_delta_window(i, i + 3) < 8.0:
                t0, t1 = ts[i], ts[i + 3]
                seg = _segment(t0, min(t1 + 5.0, t0 + 12.0), 0.55, "portrait_shorts_candidate", duration)
                if seg:
                    segs.append(seg)
                break

    # --- timelapse hint (conservative: high fps or explicit timelapse + stable luma) ---
    hi_fps = fps >= 25.0
    short_clip = duration < 40
    if hi_fps or (likely_source_type == "timelapse" and short_clip and var_norm < 0.035):
        mid = duration / 2
        win = min(10.0, max(3.0, duration * 0.35))
        seg = _segment(mid - win / 2, mid + win / 2, 0.52, "timelapse_slice_hint", duration)
        if seg:
            segs.append(seg)

    # --- duration fallback: conservative window only when sampling succeeded (real timestamps from sample anchors) ---
    if not segs and duration >= 1.0:
        mid = len(samples) // 2
        t_mid = ts[mid]
        win = min(8.0, max(2.5, duration * 0.15))
        seg = _segment(t_mid - win / 2, t_mid + win / 2, 0.42, "sample_anchored_stable_window", duration)
        if seg:
            segs.append(seg)

    return _merge_dedupe(segs)
