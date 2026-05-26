#!/usr/bin/env python3
"""Render Quality Review Layer (Quality Optimization Phase v1).

Inputs (project-dir mode):
  - rough_cut.mp4
  - audio_clean/final_audio_clean.mp4 (optional, preferred for audio metrics)
  - timeline_plan.json (optional)
  - selected_media_report.json (optional)

Outputs:
  - quality_review_report.json (default under SV_CACHE/review_reports/<project>/)

Non-negotiables:
  - Read-only analysis: never deletes/moves media, never uploads, never modifies launchd.
  - Fail-open: missing inputs produce warnings instead of crashing.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import get_sv_cache  # noqa: E402

log = logging.getLogger("review_render_quality")

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json_fail_open(p: Path) -> dict[str, Any] | None:
    try:
        if not p.is_file():
            return None
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return None


def _safe_mkdir(p: Path) -> None:
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass


def _resolve_path_str(p: Path) -> str:
    try:
        return str(p.expanduser().resolve())
    except OSError:
        return str(p.expanduser())


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=90, check=False)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _video_orientation_from_streams(probe: dict[str, Any] | None) -> str | None:
    if not probe:
        return None
    try:
        for s in probe.get("streams") or []:
            if s.get("codec_type") != "video":
                continue
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            if w <= 0 or h <= 0:
                continue
            if h > w:
                return "portrait"
            if w > h:
                return "landscape"
            return "square"
    except Exception:
        return None
    return None


def _parse_loudnorm_measure(stderr_text: str) -> tuple[float | None, float | None]:
    """Return (integrated_lufs, true_peak_db) if present."""
    if not stderr_text:
        return None, None
    mi = re.search(r'"input_i"\s*:\s*"([-\d.]+)"', stderr_text)
    mtp = re.search(r'"input_tp"\s*:\s*"([-\d.]+)"', stderr_text)
    lufs = tp = None
    if mi:
        try:
            lufs = float(mi.group(1))
        except ValueError:
            lufs = None
    if mtp:
        try:
            tp = float(mtp.group(1))
        except ValueError:
            tp = None
    return lufs, tp


def _measure_audio_quick(path: Path, *, max_seconds: float = 180.0) -> dict[str, Any]:
    """Fast, best-effort audio loudness snapshot (fail-open)."""
    out: dict[str, Any] = {
        "measured_lufs": None,
        "peak_db": None,
        "method": None,
        "error": None,
    }
    if not path.is_file():
        out["error"] = "missing_file"
        return out
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-t",
        str(float(max_seconds)),
        "-i",
        str(path),
        "-af",
        "loudnorm=I=-14:LRA=11:TP=-1.5:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=max(120.0, max_seconds + 60.0), check=False)
        lufs, tp = _parse_loudnorm_measure((r.stderr or "") + "\n" + (r.stdout or ""))
        out["measured_lufs"] = lufs
        out["peak_db"] = tp
        out["method"] = "ffmpeg_loudnorm_measure"
        if lufs is None and tp is None:
            out["error"] = f"no_metrics_rc={r.returncode}"
        return out
    except FileNotFoundError:
        out["error"] = "ffmpeg_not_found"
        return out
    except subprocess.TimeoutExpired:
        out["error"] = "ffmpeg_timeout"
        return out
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
        return out


def _load_media_index_map() -> dict[str, dict[str, Any]]:
    """Map resolved file_path -> item dict (fail-open)."""
    idx = Path("/Volumes/SV_TRANSFER/media_index/media_index.json")
    data = _read_json_fail_open(idx)
    if not data or not isinstance(data.get("items"), list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for it in data["items"]:
        if not isinstance(it, dict):
            continue
        fp = str(it.get("file_path") or "").strip()
        if not fp:
            continue
        try:
            out[str(Path(fp).expanduser().resolve())] = it
        except OSError:
            out[fp] = it
    return out


@dataclass(frozen=True)
class Clip:
    source_path: str
    duration_s: float
    source_type: str = ""
    orientation: str = ""
    location_name: str = ""
    nearby_landmark: str = ""
    camera_motion_hint: str = ""
    is_manual_timelapse: bool | None = None


def _as_float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return 0.0


def _coerce_clips_from_timeline_plan(timeline: dict[str, Any], *, idx_map: dict[str, dict[str, Any]]) -> list[Clip]:
    items = timeline.get("clips") or timeline.get("segments") or timeline.get("timeline") or []
    if not isinstance(items, list):
        return []
    out: list[Clip] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sp = str(it.get("source_path") or it.get("path") or it.get("file_path") or "").strip()
        if not sp:
            continue
        dur = _as_float(it.get("duration_seconds") or it.get("duration") or it.get("seconds") or 0.0)
        st = str(it.get("source_type") or it.get("likely_source_type") or "").strip()
        ori = str(it.get("orientation") or "").strip()
        motion = str(it.get("camera_motion_hint") or it.get("motion_hint") or "").strip()
        manual_tl = it.get("is_manual_timelapse")
        loc = str(it.get("location_name") or "").strip()
        near = str(it.get("nearby_landmark") or "").strip()
        mi = idx_map.get(_resolve_path_str(Path(sp)))
        if mi:
            st = st or str(mi.get("likely_source_type") or "").strip()
            ori = ori or str(mi.get("orientation") or "").strip()
            motion = motion or str(mi.get("camera_motion_hint") or "").strip()
            loc = loc or str(mi.get("location_name") or "").strip()
            near = near or str(mi.get("nearby_landmark") or "").strip()
            if manual_tl is None:
                manual_tl = mi.get("is_manual_timelapse")
        out.append(
            Clip(
                source_path=_resolve_path_str(Path(sp)),
                duration_s=max(0.0, float(dur)),
                source_type=st,
                orientation=ori,
                location_name=loc,
                nearby_landmark=near,
                camera_motion_hint=motion,
                is_manual_timelapse=bool(manual_tl) if manual_tl is not None else None,
            )
        )
    return out


def _coerce_clips_from_selected_media_report(sel: dict[str, Any], *, idx_map: dict[str, dict[str, Any]]) -> list[Clip]:
    items = sel.get("selected") or sel.get("items") or sel.get("sources") or []
    if not isinstance(items, list):
        return []
    out: list[Clip] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        sp = str(it.get("file_path") or it.get("path") or it.get("source_path") or "").strip()
        if not sp:
            continue
        dur = _as_float(it.get("duration") or it.get("seconds") or it.get("duration_seconds") or 0.0)
        mi = idx_map.get(_resolve_path_str(Path(sp)))
        st = str(it.get("likely_source_type") or it.get("source_type") or (mi or {}).get("likely_source_type") or "").strip()
        ori = str(it.get("orientation") or (mi or {}).get("orientation") or "").strip()
        motion = str(it.get("camera_motion_hint") or (mi or {}).get("camera_motion_hint") or "").strip()
        loc = str(it.get("location_name") or (mi or {}).get("location_name") or "").strip()
        near = str(it.get("nearby_landmark") or (mi or {}).get("nearby_landmark") or "").strip()
        manual_tl = it.get("is_manual_timelapse")
        if manual_tl is None and mi:
            manual_tl = mi.get("is_manual_timelapse")
        out.append(
            Clip(
                source_path=_resolve_path_str(Path(sp)),
                duration_s=max(0.0, float(dur)),
                source_type=st,
                orientation=ori,
                location_name=loc,
                nearby_landmark=near,
                camera_motion_hint=motion,
                is_manual_timelapse=bool(manual_tl) if manual_tl is not None else None,
            )
        )
    return out


def _coerce_clips_from_rough_manifest(man: dict[str, Any], *, idx_map: dict[str, dict[str, Any]]) -> list[Clip]:
    srcs = man.get("sources") or []
    if not isinstance(srcs, list):
        return []
    out: list[Clip] = []
    for s in srcs:
        if not isinstance(s, dict):
            continue
        sp = str(s.get("path") or "").strip()
        if not sp:
            continue
        dur = _as_float(s.get("seconds") or 0.0)
        mi = idx_map.get(_resolve_path_str(Path(sp)))
        out.append(
            Clip(
                source_path=_resolve_path_str(Path(sp)),
                duration_s=max(0.0, float(dur)),
                source_type=str((mi or {}).get("likely_source_type") or "").strip(),
                orientation=str((mi or {}).get("orientation") or "").strip(),
                location_name=str((mi or {}).get("location_name") or "").strip(),
                nearby_landmark=str((mi or {}).get("nearby_landmark") or "").strip(),
                camera_motion_hint=str((mi or {}).get("camera_motion_hint") or "").strip(),
                is_manual_timelapse=bool((mi or {}).get("is_manual_timelapse")) if mi else None,
            )
        )
    return out


def _clip_type_norm(x: str) -> str:
    return (x or "").strip().lower()


def _orientation_norm(x: str) -> str:
    o = (x or "").strip().lower()
    if o in ("landscape", "portrait", "square"):
        return o
    return ""


def _score_0_100(x: float) -> int:
    return int(max(0, min(100, round(float(x)))))


def _stats_durations(clips: Iterable[Clip]) -> dict[str, Any]:
    durs = [c.duration_s for c in clips if c.duration_s > 1e-6]
    if not durs:
        return {"count": 0, "min": None, "max": None, "mean": None, "variance": None}
    n = len(durs)
    mean = sum(durs) / n
    var = sum((x - mean) ** 2 for x in durs) / max(1, n)
    return {"count": n, "min": min(durs), "max": max(durs), "mean": mean, "variance": var}


def _analyze_timeline_quality(clips: list[Clip]) -> tuple[int, list[str], list[str], list[str], dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []
    metrics: dict[str, Any] = {}

    if not clips:
        warnings.append("timeline:missing_clips (timeline_plan/selected_media_report/manifest not found or empty)")
        return 60, issues, warnings, recs, metrics

    # Repetition: same source reused many times.
    counts: dict[str, int] = {}
    for c in clips:
        counts[c.source_path] = counts.get(c.source_path, 0) + 1
    repeats = sorted(((k, v) for k, v in counts.items() if v >= 2), key=lambda x: x[1], reverse=True)
    rep_total = sum(v - 1 for _k, v in repeats)
    metrics["repeat_sources"] = {"unique": len(counts), "repeated_unique": len(repeats), "repeat_extra_uses": rep_total}
    if rep_total >= max(3, int(0.08 * len(clips))):
        issues.append(f"timeline:many_repeats extra_uses={rep_total}")
        recs.append("Reduce repeated shots; widen B-roll pool or enforce per-source max uses.")
    elif rep_total > 0:
        warnings.append(f"timeline:repeats_present extra_uses={rep_total}")

    # Driving/walking switching chaos.
    types = [_clip_type_norm(c.source_type) for c in clips]
    chaos = 0
    last = ""
    for t in types:
        if t not in ("driving_fixed", "walking_handheld"):
            continue
        if last and t != last:
            chaos += 1
        last = t
    metrics["driving_walking_switches"] = chaos
    if chaos >= 8:
        issues.append(f"timeline:driving_walking_switch_chaos switches={chaos}")
        recs.append("Group sequences: cluster driving_fixed and walking_handheld into longer blocks to reduce whiplash.")
    elif chaos >= 4:
        warnings.append(f"timeline:driving_walking_switches switches={chaos}")

    # Timelapse too long.
    tl_long = 0
    for c in clips:
        if _clip_type_norm(c.source_type) == "timelapse" and c.duration_s > 12.0:
            tl_long += 1
    metrics["timelapse_long_count"] = tl_long
    if tl_long > 0:
        warnings.append(f"timeline:timelapse_too_long count={tl_long}")
        recs.append("Timelapse too long → shorten slice (target 5–8s segments).")

    # Portrait mixed into landscape.
    portrait = sum(1 for c in clips if _orientation_norm(c.orientation) == "portrait")
    metrics["portrait_count"] = portrait
    if portrait > 0:
        issues.append(f"timeline:portrait_mixed_into_landscape count={portrait}")
        recs.append("Strengthen landscape filter; avoid portrait in longform documentary renders.")

    score = 100
    score -= min(40, rep_total * 4)
    score -= min(25, chaos * 2)
    score -= min(20, tl_long * 5)
    score -= min(40, portrait * 10)
    return _score_0_100(score), issues, warnings, recs, metrics


def _analyze_motion_consistency(clips: list[Clip]) -> tuple[int, list[str], list[str], list[str], dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []
    metrics: dict[str, Any] = {}

    if not clips:
        warnings.append("motion:missing_clips")
        return 60, issues, warnings, recs, metrics

    st_counts: dict[str, int] = {"driving_fixed": 0, "walking_handheld": 0, "timelapse": 0, "unknown": 0}
    shaky_handheld = 0
    for c in clips:
        st = _clip_type_norm(c.source_type) or "unknown"
        if st not in st_counts:
            st = "unknown"
        st_counts[st] += 1
        if st == "walking_handheld":
            mh = _clip_type_norm(c.camera_motion_hint)
            if mh in ("shaky", "very_shaky", "unstable", "jittery"):
                shaky_handheld += 1
    metrics["source_type_counts"] = st_counts
    metrics["shaky_handheld_count"] = shaky_handheld

    total = max(1, len(clips))
    handheld_ratio = st_counts["walking_handheld"] / total
    driving_ratio = st_counts["driving_fixed"] / total
    metrics["ratios"] = {"walking_handheld": handheld_ratio, "driving_fixed": driving_ratio}

    if shaky_handheld >= max(3, int(0.25 * st_counts["walking_handheld"])):
        warnings.append(f"motion:handheld_shaky count={shaky_handheld}")
        recs.append("Handheld too shaky → reduce shaky handheld, or mix with more driving_fixed / stabilized shots.")

    if handheld_ratio > 0.75 and st_counts["walking_handheld"] >= 6:
        warnings.append(f"motion:too_much_handheld ratio={handheld_ratio:.2f}")
        recs.append("大量 handheld → 建议增加 driving_fixed 稳定镜头做呼吸。")
    if driving_ratio > 0.85 and st_counts["driving_fixed"] >= 6:
        warnings.append(f"motion:too_much_driving ratio={driving_ratio:.2f}")
        recs.append("大量 driving → 建议增加 walking POV（近景人群/街头质感）提升 documentary feel。")

    # Transition abruptness proxy: too many ultra-short clips.
    short = sum(1 for c in clips if 0 < c.duration_s < 1.5)
    metrics["ultra_short_count_lt_1p5s"] = short
    if short >= max(8, int(0.25 * total)):
        warnings.append(f"motion:transitions_abrupt ultra_short={short}")
        recs.append("Transitions feel abrupt → reduce <1.5s shots; prefer 2–6s for city doc rhythm.")

    score = 100
    score -= min(30, shaky_handheld * 3)
    score -= min(25, max(0, short - 6) * 2)
    score -= 10 if handheld_ratio > 0.75 else 0
    score -= 10 if driving_ratio > 0.85 else 0
    return _score_0_100(score), issues, warnings, recs, metrics


def _analyze_audio_quality(project_dir: Path) -> tuple[int, list[str], list[str], list[str], dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []
    metrics: dict[str, Any] = {}

    audio_report = project_dir / "audio_clean" / "audio_cleanup_report.json"
    rep = _read_json_fail_open(audio_report)
    lufs = None
    peak = None
    if rep:
        try:
            lufs = float(rep.get("measured_lufs")) if rep.get("measured_lufs") is not None else None
        except Exception:
            lufs = None
        try:
            peak = float(rep.get("peak_db")) if rep.get("peak_db") is not None else None
        except Exception:
            peak = None
        metrics["audio_cleanup_report"] = {
            "path": str(audio_report),
            "measured_lufs": lufs,
            "peak_db": peak,
            "loudnorm_applied": bool(rep.get("loudnorm_applied")),
            "noise_reduction_applied": bool(rep.get("noise_reduction_applied")),
        }

    if lufs is None or peak is None:
        audio_file = project_dir / "audio_clean" / "final_audio_clean.mp4"
        if not audio_file.is_file():
            audio_file = project_dir / "rough_cut.mp4"
        m = _measure_audio_quick(audio_file)
        metrics["audio_measure"] = {"path": str(audio_file), **m}
        lufs = lufs if lufs is not None else m.get("measured_lufs")
        peak = peak if peak is not None else m.get("peak_db")

    if lufs is None:
        warnings.append("audio:lufs_unknown")
        recs.append("Audio LUFS unknown → ensure loudnorm measurement is logged (audio_cleanup_report.json).")
        return 65, issues, warnings, recs, metrics

    # Scoring targets for NYC doc longform: -14 LUFS integrated, true peak <= -1.5 dBFS.
    target = -14.0
    delta = float(lufs) - target
    metrics["lufs"] = float(lufs)
    metrics["lufs_delta_vs_target"] = round(delta, 2)
    if peak is not None:
        metrics["peak_db"] = float(peak)

    if lufs > -12.5:
        issues.append(f"audio:too_loud lufs={lufs:.2f}")
        recs.append("Possible loudness fatigue → re-run loudnorm targeting -14 LUFS, TP -1.5.")
    elif lufs < -18.0:
        warnings.append(f"audio:too_quiet lufs={lufs:.2f}")
        recs.append("Audio too quiet → re-run loudnorm to -14 LUFS (avoid masking NYC ambience).")
    elif abs(delta) > 1.5:
        warnings.append(f"audio:lufs_unstable lufs={lufs:.2f}")
        recs.append("音频 LUFS 不稳定 → 建议重新 loudnorm（I=-14, TP=-1.5）。")

    if peak is None:
        warnings.append("audio:peak_unknown")
    else:
        if peak > -0.5:
            issues.append(f"audio:possible_clipping peak_db={peak:.2f}")
            recs.append("Peak too hot → keep true peak at or below -1.5 dBTP to avoid clipping after platform processing.")
        elif peak > -1.5:
            warnings.append(f"audio:peak_close_to_limit peak_db={peak:.2f}")

    # Silence detection (best-effort): short window only (avoid long ffmpeg runs)
    rc = project_dir / "rough_cut.mp4"
    if rc.is_file():
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-t",
            "180",
            "-i",
            str(rc),
            "-af",
            "silencedetect=noise=-40dB:d=3",
            "-f",
            "null",
            "-",
        ]
        try:
            r = subprocess.run(cmd, text=True, capture_output=True, timeout=120, check=False)
            txt = (r.stderr or "") + "\n" + (r.stdout or "")
            n_sil = len(re.findall(r"silence_start", txt))
            metrics["silence_events_ge_3s"] = n_sil
            if n_sil >= 2:
                warnings.append(f"audio:long_silence_events count={n_sil}")
                recs.append("Long silence detected → consider ambience bed or re-check muxed audio track.")
        except Exception:
            pass

    score = 100
    score -= min(50, int(abs(delta) * 12))
    if peak is not None:
        score -= 30 if peak > -0.5 else 10 if peak > -1.5 else 0
    return _score_0_100(score), issues, warnings, recs, metrics


def _analyze_visual_rhythm(clips: list[Clip]) -> tuple[int, list[str], list[str], list[str], dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []
    metrics: dict[str, Any] = {}

    if not clips:
        warnings.append("rhythm:missing_clips")
        return 60, issues, warnings, recs, metrics

    ds = _stats_durations(clips)
    metrics["duration_stats"] = ds
    short2 = sum(1 for c in clips if 0 < c.duration_s < 2.0)
    long20 = sum(1 for c in clips if c.duration_s > 20.0)
    metrics["count_lt_2s"] = short2
    metrics["count_gt_20s"] = long20
    n = max(1, ds["count"] or 0)
    if short2 >= max(10, int(0.30 * n)):
        warnings.append(f"rhythm:too_many_short_clips_lt_2s count={short2}")
        recs.append("大量 <2s 镜头 → 放慢节奏：优先 2–6s，关键 establishing 8–12s。")
    if long20 > 0:
        warnings.append(f"rhythm:very_long_clips_gt_20s count={long20}")
        recs.append("单镜头 >20s → 适度切分或加入 cutaways 保持 NYC doc 节奏。")

    score = 100
    score -= min(35, max(0, short2 - 6) * 2)
    score -= min(25, long20 * 6)
    return _score_0_100(score), issues, warnings, recs, metrics


def _analyze_location_consistency(clips: list[Clip], *, theme: str | None) -> tuple[int, list[str], list[str], list[str], dict[str, Any]]:
    issues: list[str] = []
    warnings: list[str] = []
    recs: list[str] = []
    metrics: dict[str, Any] = {}

    if not clips:
        warnings.append("location:missing_clips")
        return 60, issues, warnings, recs, metrics

    theme_s = (theme or "").strip().lower()
    wants_times = "times" in theme_s or "时代广场" in theme_s or "broadway" in theme_s
    counts: dict[str, int] = {}
    for c in clips:
        key = (c.location_name or "").strip() or "unknown"
        counts[key] = counts.get(key, 0) + 1
    metrics["location_counts"] = dict(sorted(counts.items(), key=lambda x: x[1], reverse=True))
    total = max(1, len(clips))

    ts = sum(1 for c in clips if (c.location_name or "").strip() == "Times Square" or (c.nearby_landmark or "").strip() == "Times Square")
    mid = sum(1 for c in clips if (c.location_name or "").strip() == "Midtown Manhattan")
    metrics["times_square_hits"] = ts
    metrics["midtown_hits"] = mid
    if wants_times:
        hit_ratio = (ts + mid) / total
        metrics["theme_hit_ratio_times_square_midtown"] = round(hit_ratio, 3)
        if hit_ratio < 0.55 and total >= 4:
            issues.append(f"location:theme_miss ratio={hit_ratio:.2f}")
            recs.append("Times Square theme miss → increase Times Square / Midtown hits; avoid unrelated boroughs for this episode.")
        elif hit_ratio < 0.75 and total >= 4:
            warnings.append(f"location:theme_weak ratio={hit_ratio:.2f}")

    score = 100
    if wants_times:
        hit_ratio = float(metrics.get("theme_hit_ratio_times_square_midtown") or 1.0)
        score -= int(max(0.0, (0.75 - hit_ratio) * 120))
    # penalize unknown locations heavily (GPS missing in index)
    unknown = counts.get("unknown", 0)
    metrics["unknown_location_count"] = unknown
    score -= min(30, unknown * 4)
    return _score_0_100(score), issues, warnings, recs, metrics


def _weighted_overall(scores: dict[str, int]) -> int:
    # Emphasize audio & timeline for doc feel.
    w = {"timeline": 0.25, "motion": 0.20, "audio": 0.25, "rhythm": 0.15, "location": 0.15}
    s = 0.0
    tot = 0.0
    for k, wk in w.items():
        if k in scores:
            s += wk * float(scores[k])
            tot += wk
    if tot <= 1e-9:
        return 0
    return _score_0_100(s / tot)


def _pick_project_dir(project_dir: Path) -> tuple[Path, list[str]]:
    """If project_dir lacks rough_cut.mp4, fall back to newest sibling matching prefix."""
    warns: list[str] = []
    proj = project_dir
    if (proj / "rough_cut.mp4").is_file():
        return proj, warns
    parent = proj.parent
    prefix = proj.name
    try:
        cands = [p for p in parent.glob(f"{prefix}*") if p.is_dir()]
    except OSError:
        cands = []
    def has_rough(p: Path) -> bool:
        return (p / "rough_cut.mp4").is_file()
    cands = [p for p in cands if has_rough(p)]
    if not cands:
        warns.append(f"project_dir_missing_rough_cut:{proj}")
        return proj, warns
    cands.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    pick = cands[0]
    warns.append(f"project_dir_fallback:{proj} -> {pick}")
    return pick, warns


def review_project(project_dir: Path, *, verbose: bool) -> dict[str, Any]:
    proj, proj_warns = _pick_project_dir(project_dir.expanduser())
    idx_map = _load_media_index_map()

    rough = proj / "rough_cut.mp4"
    audio = proj / "audio_clean" / "final_audio_clean.mp4"
    timeline_p = proj / "timeline_plan.json"
    selected_p = proj / "selected_media_report.json"
    rough_manifest_p = proj / "rough_cut_manifest.json"

    timeline = _read_json_fail_open(timeline_p) or {}
    selected = _read_json_fail_open(selected_p) or {}
    rough_manifest = _read_json_fail_open(rough_manifest_p) or {}
    theme = str(rough_manifest.get("theme") or timeline.get("theme") or selected.get("theme") or "").strip() or None

    clips: list[Clip] = []
    clips.extend(_coerce_clips_from_timeline_plan(timeline, idx_map=idx_map))
    if not clips:
        clips.extend(_coerce_clips_from_selected_media_report(selected, idx_map=idx_map))
    if not clips:
        clips.extend(_coerce_clips_from_rough_manifest(rough_manifest, idx_map=idx_map))

    # Project orientation check (rough_cut) for portrait contamination at output level.
    probe = _ffprobe_json(rough)
    out_ori = _video_orientation_from_streams(probe)

    t_score, t_issues, t_warns, t_recs, t_metrics = _analyze_timeline_quality(clips)
    m_score, m_issues, m_warns, m_recs, m_metrics = _analyze_motion_consistency(clips)
    a_score, a_issues, a_warns, a_recs, a_metrics = _analyze_audio_quality(proj)
    r_score, r_issues, r_warns, r_recs, r_metrics = _analyze_visual_rhythm(clips)
    l_score, l_issues, l_warns, l_recs, l_metrics = _analyze_location_consistency(clips, theme=theme)

    scores = {"timeline": t_score, "motion": m_score, "audio": a_score, "rhythm": r_score, "location": l_score}
    overall = _weighted_overall(scores)

    issues = [*proj_warns, *t_issues, *m_issues, *a_issues, *r_issues, *l_issues]
    warnings = [*t_warns, *m_warns, *a_warns, *r_warns, *l_warns]
    recommendations = []
    # de-dup stable order
    for x in [*t_recs, *m_recs, *a_recs, *r_recs, *l_recs]:
        if x not in recommendations:
            recommendations.append(x)

    # Output-level orientation mismatch warning.
    if out_ori == "portrait":
        warnings.append("output:rough_cut_portrait (expected landscape for longform doc)")
        recommendations.append("Strengthen landscape filter; prevent portrait renders for longform.")

    out: dict[str, Any] = {
        "ok": True,
        "generated_at": utc_now_iso(),
        "project_dir": str(proj),
        "inputs": {
            "rough_cut": str(rough) if rough.exists() else "",
            "final_audio_clean": str(audio) if audio.exists() else "",
            "timeline_plan": str(timeline_p) if timeline_p.exists() else "",
            "selected_media_report": str(selected_p) if selected_p.exists() else "",
            "rough_cut_manifest": str(rough_manifest_p) if rough_manifest_p.exists() else "",
            "theme": theme or "",
        },
        "overall_score": overall,
        "timeline_score": t_score,
        "audio_score": a_score,
        "location_score": l_score,
        "motion_score": m_score,
        "issues": issues,
        "warnings": warnings,
        "recommendations": recommendations,
        "metrics": {
            "clip_count": len(clips),
            "output_orientation": out_ori,
            "timeline": t_metrics,
            "motion": m_metrics,
            "audio": a_metrics,
            "rhythm": r_metrics,
            "location": l_metrics,
        },
    }

    # Optional diagnostics: long clip details (>20s). Do not change any existing fields.
    long_details: list[dict[str, Any]] = []
    for i, c in enumerate(clips):
        if c.duration_s > 20.0:
            long_details.append(
                {
                    "clip_path": c.source_path,
                    "duration": round(float(c.duration_s), 6),
                    "index": i,
                    "source_type": c.source_type or "",
                    "location_name": c.location_name or "",
                    "orientation": c.orientation or "",
                }
            )
    if long_details:
        out["long_clip_details"] = long_details

    # If nothing exists, mark as not-ok but still output structure.
    if not rough.is_file():
        out["ok"] = False
        out["warnings"].append("input:rough_cut_missing")
    return out


def default_report_dir(*, verbose: bool = False) -> Path:
    d = get_sv_cache(verbose=verbose) / "review_reports"
    _safe_mkdir(d)
    return d


def write_report(report_root: Path, project_name: str, payload: dict[str, Any]) -> Path:
    d = report_root / project_name
    _safe_mkdir(d)
    outp = d / "quality_review_report.json"
    try:
        tmp = outp.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(outp)
    except OSError as exc:
        payload["write_error"] = repr(exc)
        try:
            outp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass
    return outp


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    # Backward-compatible project-dir mode (existing behavior)
    ap.add_argument("--project-dir", type=Path, default=None, help="e.g. /Volumes/SV_CACHE/renders/times_square_v1")
    ap.add_argument("--outdir", type=Path, default=None, help="Override report root (default SV_CACHE/review_reports/).")
    # New optional single-input mode (Times Square Quality Fix Pass v1)
    ap.add_argument("--input-video", type=Path, default=None, help="Evaluate only this video (mp4/mov).")
    ap.add_argument("--project", type=str, default="", help="Project name for report folder (used with --input-video).")
    ap.add_argument("--output-dir", type=Path, default=None, help="Report root dir (used with --input-video).")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")

    # Single-input mode: only audio + basic stream checks, no timeline reconstruction.
    if args.input_video:
        inp = args.input_video.expanduser()
        project_name = (args.project or "").strip() or inp.stem
        outroot = (args.output_dir.expanduser() if args.output_dir else (args.outdir.expanduser() if args.outdir else default_report_dir(verbose=args.verbose)))
        payload: dict[str, Any] = {
            "ok": True,
            "generated_at": utc_now_iso(),
            "project_dir": str(inp.parent),
            "inputs": {
                "rough_cut": str(inp) if inp.exists() else "",
                "final_audio_clean": str(inp) if inp.exists() else "",
                "timeline_plan": "",
                "selected_media_report": "",
                "rough_cut_manifest": "",
                "theme": "",
            },
            "overall_score": 0,
            "timeline_score": 60,
            "audio_score": 65,
            "location_score": 60,
            "motion_score": 60,
            "issues": [],
            "warnings": [],
            "recommendations": [],
            "metrics": {
                "clip_count": 0,
                "output_orientation": _video_orientation_from_streams(_ffprobe_json(inp)),
                "timeline": {},
                "motion": {},
                "audio": {},
                "rhythm": {},
                "location": {},
            },
        }
        if not inp.is_file():
            payload["ok"] = False
            payload["warnings"].append("input:missing_input_video")
        else:
            m = _measure_audio_quick(inp)
            payload["metrics"]["audio"] = {"audio_measure": {"path": str(inp), **m}}
            lufs = m.get("measured_lufs")
            peak = m.get("peak_db")
            if lufs is None:
                payload["warnings"].append("audio:lufs_unknown")
            else:
                target = -14.0
                delta = float(lufs) - target
                payload["metrics"]["audio"]["lufs"] = float(lufs)
                payload["metrics"]["audio"]["lufs_delta_vs_target"] = round(delta, 2)
                if abs(delta) > 1.5:
                    payload["warnings"].append(f"audio:lufs_unstable lufs={float(lufs):.2f}")
                    payload["recommendations"].append("音频 LUFS 不稳定 → 建议重新 loudnorm（I=-14, TP=-1.5）。")
            if peak is not None and float(peak) > -1.5:
                payload["warnings"].append(f"audio:peak_close_to_limit peak_db={float(peak):.2f}")

            # Score audio similarly to project mode, others default to baseline.
            if lufs is not None:
                try:
                    delta = float(lufs) - (-14.0)
                    score = 100 - min(50, int(abs(delta) * 12))
                    if peak is not None:
                        score -= 30 if float(peak) > -0.5 else 10 if float(peak) > -1.5 else 0
                    payload["audio_score"] = _score_0_100(score)
                except Exception:
                    pass
            scores = {
                "timeline": int(payload["timeline_score"]),
                "motion": int(payload["motion_score"]),
                "audio": int(payload["audio_score"]),
                "rhythm": 60,
                "location": int(payload["location_score"]),
            }
            payload["overall_score"] = _weighted_overall(scores)

        outp = write_report(outroot, project_name, payload)
        log.info("wrote %s", outp)
        if args.verbose:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 2

    # Default (existing) project-dir mode
    if not args.project_dir:
        ap.error("need --project-dir or --input-video")
    proj = args.project_dir.expanduser()
    payload = review_project(proj, verbose=bool(args.verbose))
    project_name = proj.name
    outroot = args.outdir.expanduser() if args.outdir else default_report_dir(verbose=args.verbose)
    outp = write_report(outroot, project_name, payload)
    log.info("wrote %s", outp)
    if args.verbose:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())

