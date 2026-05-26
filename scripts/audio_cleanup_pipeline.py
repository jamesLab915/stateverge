#!/usr/bin/env python3
"""Unified audio cleanup for StateVerge video outputs (fail-open, never touches sources).

Reads ambient / environment audio from mp4/mov, applies optional denoise + loudnorm + gain,
then remuxes. For **iPhone / VFR / high-fps** sources the video track is **re-encoded to CFR**
instead of stream-copied so replaced audio stays time-aligned.

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch 1-hour footage into multi-hour slow motion.
# Always normalize to CFR 30/60fps before final muxing.

Default out: ``SV_CACHE/audio_clean/<stem>_cleaned_audio_mix.mp4``
Project mode: ``SV_CACHE/renders/<project>/audio_clean/final_audio_clean.mp4``

Optional linkage to ``media_index.json`` (``likely_source_type``, ``usable_for``, ``orientation``)
for per-source presets.

Does **not** delete media, modify originals, or upload anywhere.
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
import time
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_sv_cache,
    get_sv_cache_renders,
    get_sv_transfer,
)
from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
from utils.vfr_video_safety import requires_cfr_normalization  # noqa: E402

log = logging.getLogger("audio_cleanup_pipeline")

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"

DEFAULT_CONFIG: dict[str, Any] = {
    "preserve_original_audio": True,
    "original_audio_gain": 0.65,
    "apply_noise_reduction": True,
    "apply_loudnorm": True,
    "target_lufs": -14.0,
    "highpass_hz": 80,
    "lowpass_hz": 14000,
    "peak_limit_db": -1.5,
}

MEDIA_INDEX_REL = Path("media_index") / "media_index.json"


def _ffmpeg_bin() -> str:
    return FFMPEG


def default_audio_clean_dir(*, verbose: bool = False) -> Path:
    d = get_sv_cache(verbose=verbose) / "audio_clean"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("mkdir audio_clean failed %s: %s", d, exc)
    return d


def media_index_path(*, verbose: bool = False) -> Path:
    return get_sv_transfer(verbose=verbose) / MEDIA_INDEX_REL


def load_index_row_for_file(video_path: Path) -> dict[str, Any] | None:
    p = media_index_path(verbose=False)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None
    try:
        key = str(video_path.expanduser().resolve())
    except OSError:
        key = str(video_path)
    for it in items:
        if not isinstance(it, dict):
            continue
        fp = str(it.get("file_path") or "")
        if fp == key:
            return it
        try:
            if fp and str(Path(fp).expanduser().resolve()) == key:
                return it
        except OSError:
            continue
    return None


def count_audio_streams(path: Path) -> int:
    data, _ = ffprobe_json(path)
    if not data:
        return 0
    n = 0
    for s in data.get("streams") or []:
        if s.get("codec_type") == "audio":
            n += 1
    return n


def build_preset(
    base: dict[str, Any],
    *,
    source_type: str,
    usable_for: str,
    orientation: str,
    audio_stream_count: int,
) -> dict[str, Any]:
    cfg = dict(base)
    st = (source_type or "").strip().lower()
    ori = (orientation or "").strip().lower()

    # Defaults already NYC street-friendly
    if st == "driving_fixed":
        cfg["highpass_hz"] = max(int(cfg["highpass_hz"]), 120)
        cfg["lowpass_hz"] = min(int(cfg["lowpass_hz"]), 12000)
        cfg["afftdn_nr"] = 18
    elif st == "walking_handheld":
        cfg["highpass_hz"] = 80
        cfg["lowpass_hz"] = min(int(cfg["lowpass_hz"]), 16000)
        cfg["afftdn_nr"] = 10
    elif st == "timelapse":
        cfg["original_audio_gain"] = 0.40
        cfg["afftdn_nr"] = 8
    else:
        cfg["afftdn_nr"] = 12
    cfg.setdefault("afftdn_nr", 12)

    if ori == "portrait":
        cfg["lowpass_hz"] = min(int(cfg["lowpass_hz"]), 15000)

    if "longform" in (usable_for or "") and st != "timelapse":
        cfg["peak_limit_db"] = min(float(cfg["peak_limit_db"]), -1.5)

    cfg["timelapse_dual_audio"] = st == "timelapse" and audio_stream_count >= 2
    return cfg


def _filter_chain_simple(cfg: dict[str, Any], *, with_nr: bool, with_loudnorm: bool) -> str:
    hp = int(cfg["highpass_hz"])
    lp = int(cfg["lowpass_hz"])
    gain = float(cfg["original_audio_gain"])
    tp = float(cfg["peak_limit_db"])
    t_lufs = float(cfg["target_lufs"])
    parts: list[str] = [f"highpass=f={hp}", f"lowpass=f={lp}"]
    if with_nr:
        nr = int(cfg.get("afftdn_nr", 12))
        parts.append(f"afftdn=nf=-25:nr={nr}")
    parts.append(f"volume={gain}")
    if with_loudnorm:
        # Keep end-result anchored to target LUFS even if the user
        # wants a pre-gain for ambience preservation.
        parts.append(f"loudnorm=I={t_lufs}:LRA=11:TP={tp}")
    return ",".join(parts)


def _filter_complex_timelapse_mix(cfg: dict[str, Any], *, with_nr: bool, with_loudnorm: bool) -> str:
    """Graph: mix stream0 (ambient, low) + stream1 (music, full), then shared chain."""
    hp = int(cfg["highpass_hz"])
    lp = int(cfg["lowpass_hz"])
    amb_gain = 0.25
    mus_gain = 1.0
    nr = int(cfg.get("afftdn_nr", 8))
    tp = float(cfg["peak_limit_db"])
    t_lufs = float(cfg["target_lufs"])
    tail_parts: list[str] = [f"highpass=f={hp}", f"lowpass=f={lp}"]
    if with_nr:
        tail_parts.append(f"afftdn=nf=-25:nr={nr}")
    tail_parts.append(f"volume={float(cfg['original_audio_gain'])}")
    if with_loudnorm:
        tail_parts.append(f"loudnorm=I={t_lufs}:LRA=11:TP={tp}")
    tail = ",".join(tail_parts)
    return (
        f"[0:a:0]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000[aa];"
        f"[0:a:1]aformat=sample_fmts=fltp:channel_layouts=stereo,aresample=48000[bb];"
        f"[aa]volume={amb_gain}[aamb];[bb]volume={mus_gain}[amus];"
        f"[aamb][amus]amix=inputs=2:duration=longest:normalize=0[mx];"
        f"[mx]{tail}[aout]"
    )


def run_ffmpeg(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "ffmpeg_not_found"
    except subprocess.TimeoutExpired:
        return 124, "ffmpeg_timeout"
    err = (r.stderr or "") + "\n" + (r.stdout or "")
    return r.returncode, err


def try_transcode_video_audio_both_cfr(
    inp: Path,
    outp: Path,
    *,
    fps: int,
    timeout: float,
) -> tuple[bool, str]:
    """Re-encode video to CFR + AAC audio (or video-only) when stream-copy is unsafe."""
    data, _ = ffprobe_json(inp)
    has_audio = False
    if data:
        has_audio = any(s.get("codec_type") == "audio" for s in data.get("streams") or [])
    tmp = outp.parent / f"{outp.stem}._ffmpeg_tmp_{os.getpid()}{outp.suffix}"
    vf = f"fps={fps},format=yuv420p"
    attempts: list[list[str]] = [
        ["-c:v", "h264_videotoolbox", "-b:v", "35M", "-tag:v", "avc1"],
        ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tag:v", "avc1"],
    ]
    last_msg = ""
    for enc in attempts:
        cmd: list[str] = [
            _ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(inp),
            "-vf",
            vf,
            "-map",
            "0:v:0?",
        ]
        if has_audio:
            cmd += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "48000"]
        else:
            cmd += ["-an"]
        cmd += [*enc, "-movflags", "+faststart", str(tmp)]
        rc, msg = run_ffmpeg(cmd, timeout=timeout)
        last_msg = msg
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(outp)
            except OSError:
                return False, msg
            return True, msg
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
    return False, last_msg


def try_transcode_audio_cfr_video(
    inp: Path,
    outp: Path,
    _cfg: dict[str, Any],
    *,
    use_filter_complex: bool,
    filter_complex: str | None,
    af: str | None,
    timeout: float,
    fps: int,
) -> tuple[bool, str, str]:
    outp.parent.mkdir(parents=True, exist_ok=True)
    tmp = outp.parent / f"{outp.stem}._ffmpeg_tmp_{os.getpid()}{outp.suffix}"
    vf_chain = f"fps={fps},format=yuv420p"
    enc_attempts: list[tuple[str, list[str]]] = [
        ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "35M", "-tag:v", "avc1"]),
        ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tag:v", "avc1"]),
    ]
    last_msg = ""
    for enc_label, enc_args in enc_attempts:
        cmd: list[str] = [
            _ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(inp),
        ]
        if use_filter_complex and filter_complex:
            fc = f"[0:v]{vf_chain}[v];" + filter_complex
            cmd += ["-filter_complex", fc, "-map", "[v]", "-map", "[aout]"]
        else:
            cmd += ["-vf", vf_chain, "-map", "0:v:0?", "-map", "0:a:0?"]
            if af:
                cmd += ["-af", af]
        cmd += [
            *enc_args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        rc, msg = run_ffmpeg(cmd, timeout=timeout)
        last_msg = msg
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(outp)
            except OSError as exc:
                return False, "", f"replace_failed:{exc}"
            tag = f"cfr_{fps}:{enc_label}:" + (filter_complex or af or "")
            return True, tag, msg[-4000:]
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
    return False, (filter_complex or af or ""), last_msg[-6000:]


def try_transcode_audio_copy_video(
    inp: Path,
    outp: Path,
    cfg: dict[str, Any],
    *,
    use_filter_complex: bool,
    filter_complex: str | None,
    af: str | None,
    timeout: float,
    allow_video_copy: bool = False,
    cfr_fps: int = 30,
) -> tuple[bool, str, str]:
    if requires_cfr_normalization(inp) and not allow_video_copy:
        return try_transcode_audio_cfr_video(
            inp,
            outp,
            cfg,
            use_filter_complex=use_filter_complex,
            filter_complex=filter_complex,
            af=af,
            timeout=timeout,
            fps=cfr_fps,
        )
    outp.parent.mkdir(parents=True, exist_ok=True)
    # Must end in .mp4/.mov so ffmpeg infers the muxer (``.mp4.tmp`` breaks format detection).
    tmp = outp.parent / f"{outp.stem}._ffmpeg_tmp_{os.getpid()}{outp.suffix}"
    cmd: list[str] = [
        _ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(inp),
    ]
    if use_filter_complex and filter_complex:
        cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-map",
            "0:v:0?",
        ]
    else:
        cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
        if af:
            cmd += ["-af", af]
    cmd += [
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    rc, msg = run_ffmpeg(cmd, timeout=timeout)
    if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
        try:
            tmp.replace(outp)
        except OSError as exc:
            return False, "", f"replace_failed:{exc}"
        return True, (filter_complex or af or ""), msg[-4000:]
    try:
        if tmp.is_file():
            tmp.unlink()
    except OSError:
        pass
    return False, (filter_complex or af or ""), msg[-6000:]


def select_processing_plan(cfg: dict[str, Any], *, dual_audio: bool) -> list[tuple[str, bool, str | None, str | None]]:
    """Ordered (label, use_fc, fc, af_simple)."""
    plans: list[tuple[str, bool, str | None, str | None]] = []
    if dual_audio and cfg.get("apply_noise_reduction") and cfg.get("apply_loudnorm"):
        plans.append(
            (
                "dual_full",
                True,
                _filter_complex_timelapse_mix(cfg, with_nr=True, with_loudnorm=True),
                None,
            )
        )
        plans.append(
            (
                "dual_no_nr",
                True,
                _filter_complex_timelapse_mix(cfg, with_nr=False, with_loudnorm=True),
                None,
            )
        )
    if cfg.get("apply_noise_reduction") and cfg.get("apply_loudnorm"):
        plans.append(("full", False, None, _filter_chain_simple(cfg, with_nr=True, with_loudnorm=True)))
    if cfg.get("apply_loudnorm"):
        plans.append(("no_nr", False, None, _filter_chain_simple(cfg, with_nr=False, with_loudnorm=True)))
    if cfg.get("apply_noise_reduction"):
        plans.append(("no_loudnorm", False, None, _filter_chain_simple(cfg, with_nr=True, with_loudnorm=False)))
    hp = int(cfg["highpass_hz"])
    gain = float(cfg["original_audio_gain"])
    plans.append(("minimal_hp_vol", False, None, f"highpass=f={hp},volume={gain}"))
    plans.append(("copy_audio_fallback", False, None, None))
    return plans


def try_copy_streams(
    inp: Path,
    outp: Path,
    *,
    timeout: float,
    allow_video_copy: bool = False,
    cfr_fps: int = 30,
) -> tuple[bool, str]:
    if requires_cfr_normalization(inp) and not allow_video_copy:
        return try_transcode_video_audio_both_cfr(inp, outp, fps=cfr_fps, timeout=timeout)
    tmp = outp.parent / f"{outp.stem}._ffmpeg_tmp_{os.getpid()}{outp.suffix}"
    cmd = [
        _ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(inp),
        "-map",
        "0",
        "-c",
        "copy",
        str(tmp),
    ]
    rc, msg = run_ffmpeg(cmd, timeout=timeout)
    if rc == 0 and tmp.is_file():
        try:
            tmp.replace(outp)
        except OSError:
            return False, msg
        return True, msg
    try:
        if tmp.is_file():
            tmp.unlink()
    except OSError:
        pass
    return False, msg


def analyze_audio_metrics(path: Path, *, max_seconds: float = 120.0) -> dict[str, Any]:
    """Non-fatal loudness / peak snapshot (short window for speed)."""
    out: dict[str, Any] = {
        "measured_integrated_lufs": None,
        "peak_db": None,
        "analyze_error": None,
    }
    if not path.is_file():
        out["analyze_error"] = "missing_file"
        return out
    dur_cap = os.environ.get("AUDIO_CLEANUP_ANALYZE_MAX_SEC")
    try:
        tsec = float(dur_cap) if dur_cap else max_seconds
    except ValueError:
        tsec = max_seconds
    # loudnorm JSON (measurement pass) — stderr contains {"input_i": "...", ...}
    cmd0 = [
        _ffmpeg_bin(),
        "-hide_banner",
        "-nostdin",
        "-t",
        str(tsec),
        "-i",
        str(path),
        "-af",
        "loudnorm=I=-14:LRA=11:TP=-1.5:print_format=json",
        "-f",
        "null",
        "-",
    ]
    rc0, msg0 = run_ffmpeg(cmd0, timeout=max(120.0, tsec + 60.0))
    if rc0 not in (0, 234):
        out["analyze_error"] = f"loudnorm_measure_rc={rc0}"
    mi = re.search(r'"input_i"\s*:\s*"([-\d.]+)"', msg0)
    if mi:
        try:
            out["measured_integrated_lufs"] = float(mi.group(1))
        except ValueError:
            pass
    mtp = re.search(r'"input_tp"\s*:\s*"([-\d.]+)"', msg0)
    if mtp:
        try:
            out["peak_db"] = float(mtp.group(1))
        except ValueError:
            pass
    if out["peak_db"] is None:
        cmd2 = [
            _ffmpeg_bin(),
            "-hide_banner",
            "-nostdin",
            "-t",
            str(tsec),
            "-i",
            str(path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ]
        rc2, msg2 = run_ffmpeg(cmd2, timeout=120.0)
        if rc2 == 0:
            m3 = re.search(r"max_volume:\s*([-\d.]+)\s*dB", msg2)
            if m3:
                try:
                    out["peak_db"] = float(m3.group(1))
                except ValueError:
                    pass
    return out


def run_cleanup(
    inp: Path,
    outp: Path,
    *,
    dry_run: bool,
    verbose: bool,
    timeout_sec: float = 7200.0,
    allow_video_copy: bool = False,
    cfr_fps: int = 30,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    row = load_index_row_for_file(inp)
    source_type = str((row or {}).get("likely_source_type") or "")
    usable_for = str((row or {}).get("usable_for") or "")
    orientation = str((row or {}).get("orientation") or "")
    n_audio = count_audio_streams(inp)
    cfg = build_preset(
        dict(DEFAULT_CONFIG),
        source_type=source_type,
        usable_for=usable_for,
        orientation=orientation,
        audio_stream_count=n_audio,
    )
    dual = bool(cfg.get("timelapse_dual_audio"))

    report: dict[str, Any] = {
        "input_file": str(inp),
        "output_file": str(outp),
        "source_type": source_type or "unknown",
        "noise_reduction_applied": False,
        "loudnorm_applied": False,
        "original_gain": cfg["original_audio_gain"],
        "measured_lufs": None,
        "peak_db": None,
        "filters_used": "",
        "processing_time_seconds": 0.0,
        "dry_run": dry_run,
        "media_index_match": bool(row),
        "audio_streams_in": n_audio,
        "preset_notes": [],
        "allow_video_copy": allow_video_copy,
        "cfr_fps": cfr_fps,
        "requires_cfr_normalization": requires_cfr_normalization(inp),
        "video_copy_used": None,
        "cfr_normalized": None,
    }

    if dry_run:
        report["preset_notes"].append(
            f"would_encode dual_audio={dual} highpass={cfg['highpass_hz']} "
            f"lowpass={cfg['lowpass_hz']} gain={cfg['original_audio_gain']}"
        )
        report["processing_time_seconds"] = round(time.perf_counter() - t0, 3)
        return report

    data_in, _ = ffprobe_json(inp)
    if not data_in:
        report["error"] = "ffprobe_input_failed"
        report["processing_time_seconds"] = round(time.perf_counter() - t0, 3)
        return report
    hv, ha, _dur = stream_summary(data_in)
    if not ha:
        report["preset_notes"].append("no_audio_stream_mux_video_copy_or_cfr")
        ok, msg = try_copy_streams(
            inp,
            outp,
            timeout=min(timeout_sec, 600.0),
            allow_video_copy=allow_video_copy,
            cfr_fps=cfr_fps,
        )
        report["filters_used"] = (
            "cfr_video_reencode_no_audio"
            if requires_cfr_normalization(inp) and not allow_video_copy
            else "stream_copy_no_audio_processing"
        )
        report["noise_reduction_applied"] = False
        report["loudnorm_applied"] = False
        if not ok:
            report["error"] = f"copy_failed:{msg[-800:]!r}"
        report["video_copy_used"] = not (
            requires_cfr_normalization(inp) and not allow_video_copy
        )
        report["cfr_normalized"] = bool(requires_cfr_normalization(inp) and not allow_video_copy)
        report["processing_time_seconds"] = round(time.perf_counter() - t0, 3)
        return report

    if not hv:
        report["preset_notes"].append("no_video_stream_audio_only_encode_warning")

    last_err = ""
    for label, use_fc, fc, af in select_processing_plan(cfg, dual_audio=dual):
        if label == "copy_audio_fallback" or af is None:
            ok, msg = try_copy_streams(
                inp,
                outp,
                timeout=min(timeout_sec, 600.0),
                allow_video_copy=allow_video_copy,
                cfr_fps=cfr_fps,
            )
            if ok:
                report["filters_used"] = (
                    "cfr_full_mux_fallback"
                    if requires_cfr_normalization(inp) and not allow_video_copy
                    else "stream_copy_fallback"
                )
                break
            last_err = msg
            continue
        ok, used_f, msg = try_transcode_audio_copy_video(
            inp,
            outp,
            cfg,
            use_filter_complex=use_fc,
            filter_complex=fc,
            af=af if not use_fc else None,
            timeout=timeout_sec,
            allow_video_copy=allow_video_copy,
            cfr_fps=cfr_fps,
        )
        if ok:
            report["filters_used"] = used_f
            report["noise_reduction_applied"] = "afftdn" in used_f
            report["loudnorm_applied"] = "loudnorm" in used_f
            if verbose:
                log.info("ok plan=%s filters=%s", label, used_f[:200])
            break
        last_err = msg
        if verbose:
            log.warning("plan_fail label=%s err_tail=%s", label, msg[-400:])
    else:
        report["error"] = f"all_plans_failed:{last_err[-1200:]!r}"

    fu = str(report.get("filters_used") or "")
    report["cfr_normalized"] = fu.startswith("cfr_") or "cfr_" in fu
    report["video_copy_used"] = not report["cfr_normalized"]

    if outp.is_file():
        metrics = analyze_audio_metrics(outp)
        report["measured_lufs"] = metrics.get("measured_integrated_lufs")
        report["peak_db"] = metrics.get("peak_db")
        if metrics.get("analyze_error"):
            report["preset_notes"].append(f"analyze:{metrics['analyze_error']}")

    report["processing_time_seconds"] = round(time.perf_counter() - t0, 3)
    return report


def write_report(outp: Path, report: dict[str, Any]) -> Path:
    rp = outp.parent / "audio_cleanup_report.json"
    try:
        rp.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        report["report_write_error"] = repr(exc)
    return rp


def postprocess_rough_cut_project(
    project_render_dir: str | Path,
    *,
    dry_run: bool = False,
    verbose: bool = False,
    allow_video_copy: bool = False,
    cfr_fps: int = 30,
) -> dict[str, Any]:
    """Hook for rough-cut workflows: ``renders/<project>/rough_cut.mp4`` → ``audio_clean/final_audio_clean.mp4``."""
    proj = Path(project_render_dir).expanduser()
    inp = proj / "rough_cut.mp4"
    outp = proj / "audio_clean" / "final_audio_clean.mp4"
    try:
        (proj / "audio_clean").mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    rep = run_cleanup(
        inp,
        outp,
        dry_run=dry_run,
        verbose=verbose,
        allow_video_copy=allow_video_copy,
        cfr_fps=cfr_fps,
    )
    write_report(outp, rep)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, help="Input mp4/mov.")
    ap.add_argument(
        "--project-render-dir",
        type=Path,
        help="e.g. .../SV_CACHE/renders/times_square_v1 — reads rough_cut.mp4, "
        "writes audio_clean/final_audio_clean.mp4",
    )
    ap.add_argument(
        "--output-dir",
        type=Path,
        help="Override default SV_CACHE/audio_clean/ (used with --input).",
    )
    ap.add_argument(
        "--output-name",
        default="",
        help="Optional filename when using --input (default <stem>_cleaned_audio_mix.mp4).",
    )
    # Optional overrides (keep defaults + backward compat; fail-open on bad values)
    ap.add_argument("--target-lufs", type=float, default=None, help="Override target LUFS (default -14).")
    ap.add_argument("--volume", type=float, default=None, help="Override original audio gain (default 0.65).")
    ap.add_argument("--highpass-hz", type=int, default=None, help="Override highpass Hz (default 80).")
    ap.add_argument("--lowpass-hz", type=int, default=None, help="Override lowpass Hz (default 14000).")
    ap.add_argument("--no-afftdn", action="store_true", help="Disable afftdn noise reduction (default on).")
    ap.add_argument("--no-loudnorm", action="store_true", help="Disable loudnorm (default on).")
    ap.add_argument(
        "--preserve-ambient-audio",
        action="store_true",
        help="Compatibility flag (pipeline always preserves ambient by processing original track).",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--allow-video-copy",
        action="store_true",
        help="Unsafe escape hatch: allow -c:v copy / stream copy on iPhone/VFR sources.",
    )
    ap.add_argument(
        "--cfr-fps",
        type=int,
        default=30,
        choices=(30, 60),
        help="CFR fps when re-encoding video for risky sources (default 30).",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    inp: Path | None = args.input
    outp: Path | None = None

    if args.project_render_dir:
        proj = args.project_render_dir.expanduser()
        inp = proj / "rough_cut.mp4"
        outp = proj / "audio_clean" / "final_audio_clean.mp4"
        try:
            (proj / "audio_clean").mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("mkdir project audio_clean: %s", exc)

    if inp is None:
        # Auto-test times_square_v1 if present
        ts = get_sv_cache_renders(verbose=args.verbose) / "times_square_v1" / "rough_cut.mp4"
        if ts.is_file():
            inp = ts
            outp = ts.parent / "audio_clean" / "final_audio_clean.mp4"
            try:
                outp.parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
            log.info("auto-selected times_square_v1 rough_cut: %s", inp)

    if inp is None:
        if args.dry_run:
            log.info("dry-run: no input resolved; showing defaults")
            print(json.dumps({"default_config": DEFAULT_CONFIG}, indent=2))
            print("default_audio_clean_dir:", default_audio_clean_dir(verbose=args.verbose))
            return 0
        ap.error("need --input, --project-render-dir, or existing times_square_v1/rough_cut.mp4")

    assert inp is not None
    inp = inp.expanduser()
    if outp is None:
        stem = inp.stem
        name = args.output_name.strip() or f"{stem}_cleaned_audio_mix.mp4"
        root = args.output_dir.expanduser() if args.output_dir else default_audio_clean_dir(verbose=args.verbose)
        outp = root / name

    if not inp.is_file():
        log.error("input missing: %s", inp)
        return 2 if not args.dry_run else 0

    # Apply optional overrides without breaking old behavior (fail-open).
    # NOTE: We intentionally do not change any presets unless the user explicitly overrides.
    try:
        if args.target_lufs is not None:
            DEFAULT_CONFIG["target_lufs"] = float(args.target_lufs)
    except Exception:
        log.warning("bad --target-lufs (ignored): %r", args.target_lufs)
    try:
        if args.volume is not None:
            DEFAULT_CONFIG["original_audio_gain"] = float(args.volume)
    except Exception:
        log.warning("bad --volume (ignored): %r", args.volume)
    try:
        if args.highpass_hz is not None:
            DEFAULT_CONFIG["highpass_hz"] = int(args.highpass_hz)
    except Exception:
        log.warning("bad --highpass-hz (ignored): %r", args.highpass_hz)
    try:
        if args.lowpass_hz is not None:
            DEFAULT_CONFIG["lowpass_hz"] = int(args.lowpass_hz)
    except Exception:
        log.warning("bad --lowpass-hz (ignored): %r", args.lowpass_hz)
    if bool(args.no_afftdn):
        DEFAULT_CONFIG["apply_noise_reduction"] = False
    if bool(args.no_loudnorm):
        DEFAULT_CONFIG["apply_loudnorm"] = False

    rep = run_cleanup(
        inp,
        outp,
        dry_run=bool(args.dry_run),
        verbose=args.verbose,
        allow_video_copy=bool(args.allow_video_copy),
        cfr_fps=int(args.cfr_fps),
    )
    rp = write_report(outp, rep)
    log.info("report %s", rp)
    if args.dry_run:
        log.info("[dry-run] out would be %s", outp)
    elif rep.get("error"):
        log.error("%s", rep["error"])
        return 3
    else:
        log.info("wrote %s", outp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
