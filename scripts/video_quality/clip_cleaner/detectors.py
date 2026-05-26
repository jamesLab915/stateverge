"""Auto-detect bad shots via ffmpeg/ffprobe frame and audio sampling (v1 heuristics)."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Detection thresholds (v1)
SAMPLE_INTERVAL_SEC = 2.0
MAX_VIDEO_SAMPLES = 900
BLACK_YAVG_MAX = 16.0
DARK_YAVG_MAX = 28.0
OVEREXPOSE_YMAX_MIN = 245.0
OVEREXPOSE_YAVG_MIN = 200.0
SHAKE_YDIF_MIN = 42.0
BLUR_YDIF_MAX = 4.5
STATIC_YDIF_MAX = 2.8
STATIC_MIN_SEC = 8.0
SCENE_SPIKE_MIN = 0.42
AUDIO_CLIP_PEAK_MIN = 0.98
WIND_HIGH_RATIO_MIN = 2.2
MIN_BAD_SEGMENT_SEC = 0.75

_STAT_LINE = re.compile(
    r"lavfi\.signalstats\.(?P<key>YMIN|YMAX|YAVG|YDIF)=([0-9.+-eE]+)"
)
_SCENE_LINE = re.compile(r"scene_score=([0-9.]+)")
_ASTATS_PEAK = re.compile(r"Peak level dB:\s*([-\d.]+)")
_ASTATS_CLIP = re.compile(r"Number of samples clipped:\s*(\d+)")


@dataclass
class SampleWindow:
    t_sec: float
    ymin: float = 0.0
    ymax: float = 0.0
    yavg: float = 0.0
    ydif: float = 0.0
    scene: float = 0.0


def _parse_signalstats(stderr: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in (stderr or "").splitlines():
        m = _STAT_LINE.search(line)
        if m:
            try:
                out[m.group("key")] = float(m.group(2))
            except ValueError:
                pass
        sm = _SCENE_LINE.search(line)
        if sm:
            try:
                out["SCENE"] = float(sm.group(1))
            except ValueError:
                pass
    return out


def _parse_signalstats_stream(stderr: str, *, interval_sec: float) -> list[SampleWindow]:
    """Parse sequential signalstats lines from a single ffmpeg decode pass."""
    windows: list[SampleWindow] = []
    cur: dict[str, float] = {}
    idx = 0
    for line in (stderr or "").splitlines():
        m = _STAT_LINE.search(line)
        if m:
            try:
                cur[m.group("key")] = float(m.group(2))
            except ValueError:
                continue
            if m.group("key") == "YDIF" and "YAVG" in cur:
                windows.append(
                    SampleWindow(
                        t_sec=round(idx * interval_sec, 3),
                        ymin=float(cur.get("YMIN", 0.0)),
                        ymax=float(cur.get("YMAX", 0.0)),
                        yavg=float(cur.get("YAVG", 0.0)),
                        ydif=float(cur.get("YDIF", 0.0)),
                        scene=float(cur.get("SCENE", 0.0)),
                    )
                )
                cur = {}
                idx += 1
    return windows


def sample_video_windows(
    path: Path,
    *,
    ffmpeg: str,
    duration_sec: float,
    interval_sec: float = SAMPLE_INTERVAL_SEC,
    timeout_per_window: float = 25.0,
) -> list[SampleWindow]:
    """Sample frames across the timeline via one ffmpeg pass (signalstats + fps)."""
    _ = timeout_per_window
    windows: list[SampleWindow] = []
    if duration_sec <= 0:
        return windows

    interval = float(interval_sec)
    if duration_sec > 0:
        interval = max(interval, duration_sec / max(1, MAX_VIDEO_SAMPLES))

    fps_val = 1.0 / interval
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-vf",
        f"fps={fps_val:.6f},scale=320:-1,signalstats,metadata=print",
        "-an",
        "-f",
        "null",
        "-",
    ]
    timeout = max(180.0, min(7200.0, duration_sec * 1.5))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        windows = _parse_signalstats_stream(r.stderr or "", interval_sec=interval)
    except (subprocess.TimeoutExpired, OSError):
        windows = []

    if windows:
        return windows

    # Fallback: sparse seek sampling if single-pass produced no stats.
    t = 0.0
    while t < duration_sec:
        cmd = [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{t:.3f}",
            "-i",
            str(path),
            "-t",
            "0.35",
            "-vf",
            "scale=320:-1,signalstats,metadata=print,select='eq(n,0)'",
            "-an",
            "-f",
            "null",
            "-",
        ]
        stats: dict[str, float] = {}
        try:
            r = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=25.0,
                check=False,
            )
            stats = _parse_signalstats(r.stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            stats = {}
        windows.append(
            SampleWindow(
                t_sec=round(t, 3),
                ymin=float(stats.get("YMIN", 0.0)),
                ymax=float(stats.get("YMAX", 0.0)),
                yavg=float(stats.get("YAVG", 0.0)),
                ydif=float(stats.get("YDIF", 0.0)),
                scene=float(stats.get("SCENE", 0.0)),
            )
        )
        t += interval
    return windows


def _extend_or_append(
    cuts: list[dict[str, Any]],
    *,
    start: float,
    end: float,
    reason: str,
    score: float,
) -> None:
    if end - start < MIN_BAD_SEGMENT_SEC:
        return
    if cuts and cuts[-1]["reason"] == reason and abs(cuts[-1]["end_sec"] - start) < SAMPLE_INTERVAL_SEC * 1.5:
        cuts[-1]["end_sec"] = round(end, 3)
        cuts[-1]["score"] = round(max(float(cuts[-1].get("score") or 0.0), score), 4)
        return
    cuts.append(
        {
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "reason": reason,
            "source": "auto",
            "score": round(score, 4),
        }
    )


def detect_from_samples(
    samples: list[SampleWindow],
    *,
    interval_sec: float = SAMPLE_INTERVAL_SEC,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (issues, cut_ranges) from sampled windows."""
    issues: list[dict[str, Any]] = []
    cuts: list[dict[str, Any]] = []

    static_start: float | None = None
    prev_scene = 0.0

    for i, s in enumerate(samples):
        t0 = s.t_sec
        t1 = t0 + interval_sec
        reasons: list[str] = []
        score = 0.0

        if s.yavg <= BLACK_YAVG_MAX:
            reasons.append("black_or_too_dark")
            score = max(score, (BLACK_YAVG_MAX - s.yavg) / max(BLACK_YAVG_MAX, 1.0))
        elif s.yavg <= DARK_YAVG_MAX:
            reasons.append("too_dark")
            score = max(score, (DARK_YAVG_MAX - s.yavg) / max(DARK_YAVG_MAX, 1.0))

        if s.ymax >= OVEREXPOSE_YMAX_MIN and s.yavg >= OVEREXPOSE_YAVG_MIN:
            reasons.append("overexposed")
            score = max(score, (s.ymax - OVEREXPOSE_YMAX_MIN) / 10.0)

        if s.ydif >= SHAKE_YDIF_MIN:
            reasons.append("heavy_shake")
            score = max(score, s.ydif / 80.0)

        if s.ydif <= BLUR_YDIF_MAX and s.yavg > DARK_YAVG_MAX and s.ymax < OVEREXPOSE_YMAX_MIN:
            reasons.append("blur_or_low_detail")
            score = max(score, (BLUR_YDIF_MAX - s.ydif) / max(BLUR_YDIF_MAX, 1.0))

        scene_delta = abs(s.scene - prev_scene)
        if scene_delta >= SCENE_SPIKE_MIN and s.ydif >= SHAKE_YDIF_MIN * 0.55:
            reasons.append("sudden_camera_movement")
            score = max(score, scene_delta)

        prev_scene = s.scene

        if s.ydif <= STATIC_YDIF_MAX:
            if static_start is None:
                static_start = t0
        else:
            if static_start is not None and (t0 - static_start) >= STATIC_MIN_SEC:
                _extend_or_append(
                    cuts,
                    start=static_start,
                    end=t0,
                    reason="static_no_content",
                    score=0.7,
                )
                issues.append(
                    {
                        "timestamp_sec": static_start,
                        "duration_sec": round(t0 - static_start, 3),
                        "reasons": ["static_no_content"],
                        "score": 0.7,
                    }
                )
            static_start = None

        if reasons:
            primary = reasons[0]
            _extend_or_append(cuts, start=t0, end=t1, reason=primary, score=score)
            issues.append(
                {
                    "timestamp_sec": t0,
                    "duration_sec": round(interval_sec, 3),
                    "reasons": reasons,
                    "score": round(score, 4),
                    "metrics": {
                        "ymin": s.ymin,
                        "ymax": s.ymax,
                        "yavg": s.yavg,
                        "ydif": s.ydif,
                        "scene": s.scene,
                    },
                }
            )

    if static_start is not None and samples:
        end_t = samples[-1].t_sec + interval_sec
        if (end_t - static_start) >= STATIC_MIN_SEC:
            _extend_or_append(
                cuts,
                start=static_start,
                end=end_t,
                reason="static_no_content",
                score=0.7,
            )

    return issues, cuts


def _probe_audio_rms(
    path: Path,
    *,
    ffmpeg: str,
    start_sec: float,
    duration_sec: float,
    af: str,
    timeout_sec: float,
) -> tuple[float | None, int]:
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-ss",
        f"{start_sec:.3f}",
        "-i",
        str(path),
        "-t",
        f"{duration_sec:.3f}",
        "-af",
        f"{af},astats=metadata=1:reset=1",
        "-f",
        "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None, 0
    peak_db: float | None = None
    clipped = 0
    for line in (r.stderr or "").splitlines():
        pm = _ASTATS_PEAK.search(line)
        if pm:
            try:
                peak_db = float(pm.group(1))
            except ValueError:
                pass
        cm = _ASTATS_CLIP.search(line)
        if cm:
            try:
                clipped = int(cm.group(1))
            except ValueError:
                pass
    return peak_db, clipped


def detect_audio_issues(
    path: Path,
    *,
    ffmpeg: str,
    duration_sec: float,
    has_audio: bool,
    chunk_sec: float = 30.0,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if duration_sec > 3600:
        chunk_sec = 120.0
    elif duration_sec > 600:
        chunk_sec = 60.0
    issues: list[dict[str, Any]] = []
    cuts: list[dict[str, Any]] = []
    if not has_audio or duration_sec <= 0:
        return issues, cuts

    t = 0.0
    while t < duration_sec:
        win = min(chunk_sec, duration_sec - t)
        full_peak, full_clip = _probe_audio_rms(
            path, ffmpeg=ffmpeg, start_sec=t, duration_sec=win, af="aresample=async=1", timeout_sec=45.0
        )
        hf_peak, _ = _probe_audio_rms(
            path,
            ffmpeg=ffmpeg,
            start_sec=t,
            duration_sec=win,
            af="highpass=f=2500,aresample=async=1",
            timeout_sec=45.0,
        )

        if full_clip and full_clip > 0:
            peak_linear = 1.0
            _extend_or_append(
                cuts,
                start=t,
                end=t + win,
                reason="audio_clipping",
                score=0.95,
            )
            issues.append(
                {
                    "timestamp_sec": round(t, 3),
                    "duration_sec": round(win, 3),
                    "reasons": ["audio_clipping"],
                    "score": 0.95,
                    "metrics": {"clipped_samples": full_clip},
                }
            )
        elif full_peak is not None and full_peak >= -0.2:
            _extend_or_append(
                cuts,
                start=t,
                end=min(t + 4.0, t + win),
                reason="audio_clipping",
                score=0.85,
            )
            issues.append(
                {
                    "timestamp_sec": round(t, 3),
                    "duration_sec": 4.0,
                    "reasons": ["audio_clipping"],
                    "score": 0.85,
                    "metrics": {"peak_db": full_peak},
                }
            )

        if full_peak is not None and hf_peak is not None:
            # Wind / HF spike: HF band much hotter than full-band (dB difference).
            ratio = (hf_peak - full_peak) if hf_peak > full_peak else 0.0
            if ratio >= WIND_HIGH_RATIO_MIN and hf_peak > -18.0:
                _extend_or_append(
                    cuts,
                    start=t,
                    end=min(t + 6.0, t + win),
                    reason="wind_noise_spike",
                    score=min(1.0, ratio / 6.0),
                )
                issues.append(
                    {
                        "timestamp_sec": round(t, 3),
                        "duration_sec": 6.0,
                        "reasons": ["wind_noise_spike"],
                        "score": round(min(1.0, ratio / 6.0), 4),
                        "metrics": {"full_peak_db": full_peak, "hf_peak_db": hf_peak},
                    }
                )

        t += chunk_sec
    return issues, cuts


def run_auto_detect(
    path: Path,
    *,
    ffmpeg: str,
    duration_sec: float,
    has_audio: bool,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run all v1 detectors; return merged issues and raw cut candidates."""
    log = progress or (lambda _msg: None)
    log("sampling_video_windows")
    samples = sample_video_windows(path, ffmpeg=ffmpeg, duration_sec=duration_sec)
    if samples:
        eff = duration_sec / max(1, len(samples))
    else:
        eff = SAMPLE_INTERVAL_SEC
    log(f"sampled_windows:{len(samples)}:interval_sec:{eff:.2f}")
    v_issues, v_cuts = detect_from_samples(samples, interval_sec=eff)
    log("scanning_audio")
    a_issues, a_cuts = detect_audio_issues(
        path, ffmpeg=ffmpeg, duration_sec=duration_sec, has_audio=has_audio
    )
    return v_issues + a_issues, v_cuts + a_cuts
