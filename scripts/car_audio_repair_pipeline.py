#!/usr/bin/env python3
"""Car / street-scene audio repair: Demucs + ambient processing + CFR-safe video mux."""

from __future__ import annotations

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch 1-hour footage into multi-hour slow motion.
# Always normalize to CFR 30/60fps before final muxing.

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import get_sv_cache  # noqa: E402
from utils.demucs_run import (  # noqa: E402
    clamp_demucs_segment,
    demucs_executable,
    demucs_fallback_segments,
    effective_demucs_segment_request,
    run_demucs_long_audio,
)

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def run_cmd(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-12000:]
    return r.returncode, tail


def ffprobe_json(path: Path, *, timeout_sec: float = 120.0) -> tuple[dict[str, Any] | None, str | None]:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        if r.returncode != 0:
            return None, (r.stderr or r.stdout or "ffprobe_failed")[:2000]
        return json.loads(r.stdout or "{}"), None
    except FileNotFoundError:
        return None, "ffprobe_not_found"
    except subprocess.TimeoutExpired:
        return None, "ffprobe_timeout"
    except json.JSONDecodeError as exc:
        return None, str(exc)


def parse_fps(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        a, b = rate.split("/", 1)
        try:
            na, nb = float(a), float(b)
            return na / nb if nb else None
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def detect_video_safety(
    path: Path,
    *,
    force_normalize: bool,
    timeout_sec: float = 120.0,
) -> dict[str, Any]:
    data, err = ffprobe_json(path, timeout_sec=timeout_sec)
    out: dict[str, Any] = {
        "error": err,
        "duration_sec": None,
        "codec": None,
        "width": None,
        "height": None,
        "r_frame_rate": None,
        "avg_frame_rate": None,
        "time_base": None,
        "format_name": None,
        "make": None,
        "model": None,
        "software": None,
        "encoder": None,
        "is_iphone_or_apple": False,
        "is_mov_container": False,
        "is_high_fps": False,
        "is_vfr_or_suspicious": False,
        "requires_cfr_normalization": bool(force_normalize),
    }
    if err or not data:
        out["requires_cfr_normalization"] = True
        return out
    fmt = data.get("format") or {}
    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
    out["duration_sec"] = None
    try:
        d = float(fmt.get("duration") or 0.0)
        out["duration_sec"] = d if d > 0 else None
    except (TypeError, ValueError):
        pass
    out["format_name"] = str(fmt.get("format_name") or "")
    blob = " ".join(str(v).lower() for v in tags.values()) + " " + path.suffix.lower()
    for key in ("com.apple.quicktime.make", "make", "com.apple.quicktime.model", "model"):
        if key in tags:
            out[key.split(".")[-1]] = tags.get(key)
    vs = None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            vs = s
            break
    if vs:
        out["codec"] = vs.get("codec_name")
        out["width"] = vs.get("width")
        out["height"] = vs.get("height")
        out["r_frame_rate"] = vs.get("r_frame_rate")
        out["avg_frame_rate"] = vs.get("avg_frame_rate")
        out["time_base"] = vs.get("time_base")
        st = vs.get("tags") if isinstance(vs.get("tags"), dict) else {}
        out["encoder"] = st.get("encoder") or tags.get("encoder")
        vblob = " ".join(str(v).lower() for v in st.values())
        blob = blob + " " + vblob

    needles = ("iphone", "apple", "quicktime", "ipad", "com.apple")
    out["is_iphone_or_apple"] = any(n in blob for n in needles)
    fn = out["format_name"].lower()
    out["is_mov_container"] = "mov" in fn or "mp4" in fn or path.suffix.lower() in {".mov", ".mp4"}

    r = parse_fps(out.get("r_frame_rate"))  # type: ignore[arg-type]
    avg = parse_fps(out.get("avg_frame_rate"))  # type: ignore[arg-type]
    peak = max((x for x in (r, avg) if x is not None), default=None)
    if peak is not None and peak > 60.5:
        out["is_high_fps"] = True
    if r is not None and avg is not None and abs(r - avg) > 0.05:
        out["is_vfr_or_suspicious"] = True

    risky = (
        force_normalize
        or out["is_iphone_or_apple"]
        or out["is_mov_container"]
        or out["is_high_fps"]
        or out["is_vfr_or_suspicious"]
    )
    out["requires_cfr_normalization"] = bool(risky)
    return out


def extract_audio(video: Path, wav_out: Path, *, dry_run: bool, timeout: float) -> bool:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return True
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(wav_out),
    ]
    rc, _ = run_cmd(cmd, timeout=timeout)
    return rc == 0 and wav_out.is_file() and wav_out.stat().st_size > 1024


def find_stem_wavs(out_root: Path, track_guess: str) -> tuple[Path | None, Path | None]:
    vocals = no_vocals = None
    try:
        for p in out_root.rglob("vocals.wav"):
            vocals = p
            break
        for p in out_root.rglob("no_vocals.wav"):
            no_vocals = p
            break
    except OSError:
        pass
    if vocals and no_vocals:
        return vocals, no_vocals
    for sub in out_root.iterdir():
        if not sub.is_dir():
            continue
        cand_v = sub / track_guess / "vocals.wav"
        cand_nv = sub / track_guess / "no_vocals.wav"
        if cand_v.is_file():
            vocals = cand_v
        if cand_nv.is_file():
            no_vocals = cand_nv
        if vocals and no_vocals:
            break
    return vocals, no_vocals


def repair_ambient_audio(
    no_vocals_wav: Path,
    repaired_out: Path,
    *,
    ambient_volume: float,
    dry_run: bool,
    timeout: float,
) -> tuple[bool, str]:
    """Soft city ambience from Demucs no_vocals stem."""
    if dry_run:
        return True, "dry_run"
    repaired_out.parent.mkdir(parents=True, exist_ok=True)
    vol = float(ambient_volume)
    af = (
        "highpass=f=80,lowpass=f=14000,"
        "afftdn=nf=-25:nr=14,"
        "compand=attacks=0.005:decays=0.25:points=-80/-80|-35/-32|-20/-24|-10/-18|0/-12,"
        "alimiter=limit=0.70,"
        f"volume={vol},"
        "loudnorm=I=-20:TP=-2:LRA=11"
    )
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(no_vocals_wav),
        "-af",
        af,
        "-ac",
        "2",
        "-ar",
        "48000",
        str(repaired_out),
    ]
    rc, msg = run_cmd(cmd, timeout=timeout)
    ok = rc == 0 and repaired_out.is_file() and repaired_out.stat().st_size > 1024
    return ok, af if ok else msg[-4000:]


def repair_vocals_track(
    vocals_wav: Path,
    repaired_out: Path,
    *,
    dry_run: bool,
    timeout: float,
) -> tuple[bool, str]:
    """Light cleanup when mode keeps vocals only."""
    if dry_run:
        return True, "dry_run"
    af = (
        "highpass=f=100,lowpass=f=16000,afftdn=nf=-25:nr=8,"
        "compand=attacks=0.01:decays=0.2:points=-80/-80|-40/-35|-15/-18|0/-12,"
        "alimiter=limit=0.85,loudnorm=I=-16:TP=-1.5:LRA=11,volume=0.9"
    )
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(vocals_wav),
        "-af",
        af,
        "-ac",
        "2",
        "-ar",
        "48000",
        str(repaired_out),
    ]
    rc, msg = run_cmd(cmd, timeout=timeout)
    ok = rc == 0 and repaired_out.is_file() and repaired_out.stat().st_size > 1024
    return ok, af if ok else msg[-4000:]


def mix_music_optional(
    repaired_ambient: Path,
    music_path: Path | None,
    final_mix_out: Path,
    *,
    video_duration_sec: float,
    ambient_mix_gain: float,
    music_volume: float,
    want_music: bool,
    dry_run: bool,
    timeout: float,
) -> tuple[bool, str]:
    if dry_run:
        return True, "dry_run"
    final_mix_out.parent.mkdir(parents=True, exist_ok=True)
    dur = max(1.0, float(video_duration_sec))

    if want_music and music_path and music_path.is_file():
        # Loop music; weight ambience vs music (normalize=0).
        fc = (
            f"[0:a]aformat=sample_fmts=fltp:channel_layouts=stereo,asetpts=PTS-STARTPTS,"
            f"volume={float(ambient_mix_gain)}[a0];"
            f"[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,aloop=loop=-1:size=2e+09,"
            f"atrim=0:{dur},asetpts=PTS-STARTPTS,volume={float(music_volume)}[a1];"
            f"[a0][a1]amix=inputs=2:duration=first:normalize=0[m];"
            f"[m]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"
        )
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(repaired_ambient),
            "-i",
            str(music_path),
            "-filter_complex",
            fc,
            "-map",
            "[aout]",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(final_mix_out),
        ]
        rc, msg = run_cmd(cmd, timeout=timeout)
        ok = rc == 0 and final_mix_out.is_file() and final_mix_out.stat().st_size > 1024
        return ok, fc if ok else msg[-4000:]

    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(repaired_ambient),
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-ac",
        "2",
        "-ar",
        "48000",
        str(final_mix_out),
    ]
    rc, msg = run_cmd(cmd, timeout=timeout)
    ok = rc == 0 and final_mix_out.is_file() and final_mix_out.stat().st_size > 1024
    return ok, "loudnorm=I=-14:TP=-1.5:LRA=11" if ok else msg[-4000:]


def mux_cfr_video(
    video_in: Path,
    audio_wav: Path,
    video_out: Path,
    *,
    fps: int,
    dry_run: bool,
    timeout: float,
) -> tuple[bool, str]:
    """Always CFR re-encode video (never ``-c:v copy``)."""
    if dry_run:
        return True, "dry_run"
    video_out.parent.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},format=yuv420p"
    tmp = video_out.parent / f"{video_out.stem}._cfr_{os.getpid()}{video_out.suffix}"
    attempts: list[tuple[str, list[str]]] = [
        ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-b:v", "35M", "-tag:v", "avc1"]),
        ("libx264", ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tag:v", "avc1"]),
    ]
    last = ""
    for label, enc in attempts:
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(video_in),
            "-i",
            str(audio_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            *enc,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        rc, msg = run_cmd(cmd, timeout=timeout)
        last = f"{label}:{msg[-2000:]}"
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            tmp.replace(video_out)
            return True, label
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
    return False, last


def duration_sec(path: Path) -> float:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120.0, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def stream_avg_fps(path: Path) -> str | None:
    data, err = ffprobe_json(path)
    if err or not data:
        return None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            return str(s.get("avg_frame_rate") or "")
    return None


def validate_output(
    inp: Path,
    outp: Path,
    *,
    orig_dur: float,
    expect_fps: int,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not outp.is_file() or outp.stat().st_size < 1024:
        return "failed", ["missing_or_tiny_output"]
    od = duration_sec(outp)
    if orig_dur <= 0:
        warnings.append("could_not_compare_duration_input_unknown")
        return "ok", warnings
    delta = abs(od - orig_dur)
    tol = max(2.0, orig_dur * 0.01)
    if delta > tol:
        warnings.append(f"duration_drift_sec={delta:.3f}_tol={tol:.3f}")
    if od > orig_dur * 1.05:
        return "failed", warnings + ["output_duration_exceeds_input_by_more_than_5_percent"]
    avg = stream_avg_fps(outp) or ""
    exp = f"{expect_fps}/1"
    if avg != exp:
        warnings.append(f"avg_frame_rate_expected_{exp}_got_{avg or 'unknown'}")
    data, err = ffprobe_json(outp)
    if err or not data:
        return "failed", warnings + ["ffprobe_output_failed"]
    types = [s.get("codec_type") for s in data.get("streams") or []]
    if "video" not in types:
        return "failed", warnings + ["no_video_stream"]
    if "audio" not in types:
        return "failed", warnings + ["no_audio_stream"]
    if warnings:
        return "ok_with_warnings", warnings
    return "ok", []


def write_report(out_dir: Path, stem: str, payload: dict[str, Any]) -> tuple[Path, Path]:
    jp = out_dir / "audio_repair_report.json"
    mp = out_dir / "audio_repair_report.md"
    jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    keys = [
        ("input_path", "input_path"),
        ("output_dir", "output_dir"),
        ("original_duration_sec", "original_duration_sec"),
        ("output_duration_sec", "output_duration_sec"),
        ("duration_delta_sec", "duration_delta_sec"),
        ("original_fps", "original_fps"),
        ("output_fps", "output_fps"),
        ("original_codec", "original_codec"),
        ("output_codec", "output_codec"),
        ("is_iphone_or_apple", "is_iphone_or_apple"),
        ("is_mov_container", "is_mov_container"),
        ("is_high_fps", "is_high_fps"),
        ("is_vfr_or_suspicious", "is_vfr_or_suspicious"),
        ("requires_cfr_normalization", "requires_cfr_normalization"),
        ("video_copy_used", "video_copy_used"),
        ("cfr_normalized", "cfr_normalized"),
        ("demucs_model", "demucs_model"),
        ("vocals_path", "vocals_path"),
        ("no_vocals_path", "no_vocals_path"),
        ("repaired_ambient_path", "repaired_ambient_path"),
        ("final_mix_path", "final_mix_path"),
        ("final_video_path", "final_video_path"),
        ("ambient_volume", "ambient_volume"),
        ("music_path", "music_path"),
        ("music_volume", "music_volume"),
        ("filters_used", "filters_used"),
        ("mode", "mode"),
        ("mux_encoder", "mux_encoder"),
        ("reason", "reason"),
        ("demucs_device", "demucs_device"),
        ("demucs_segment_requested", "demucs_segment_requested"),
        ("demucs_segment_used", "demucs_segment_used"),
        ("demucs_shifts", "demucs_shifts"),
        ("demucs_returncode", "demucs_returncode"),
        ("demucs_timeout_sec", "demucs_timeout_sec"),
        ("demucs_full_log_path", "demucs_full_log_path"),
        ("status", "status"),
    ]
    lines = [f"# Car audio repair ({stem})", ""]
    for label, k in keys:
        lines.append(f"- **{label}**: `{payload.get(k)}`")
    lines.extend(["", "## Warnings", ""])
    lines.extend([f"- {w}" for w in (payload.get("warnings") or [])])
    lines.append("")
    tail = payload.get("demucs_stderr_tail")
    if tail:
        lines.extend(["## Demucs log (tail)", "", "```", str(tail).strip(), "```", ""])
    att = payload.get("demucs_attempts")
    if att:
        lines.extend(
            [
                "## Demucs attempts",
                "",
                "```json",
                json.dumps(att, indent=2, ensure_ascii=False),
                "```",
                "",
            ]
        )
    mp.write_text("\n".join(lines), encoding="utf-8")
    return jp, mp


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument("--output-root", type=Path, default=None)
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60))
    ap.add_argument(
        "--mode",
        choices=(
            "no_vocals_ambient",
            "vocals_only",
            "ambient_only",
            "repaired_with_music",
        ),
        default="no_vocals_ambient",
    )
    ap.add_argument("--model", default="htdemucs")
    ap.add_argument(
        "--demucs-device",
        default="cpu",
        help="Demucs torch device (default cpu; avoid mps for long jobs).",
    )
    ap.add_argument(
        "--demucs-segment",
        type=int,
        default=None,
        help="Demucs segment (default: 7, or 15 for MDX models). Clamped to ≤7 for htdemucs.",
    )
    ap.add_argument(
        "--demucs-shifts",
        type=int,
        default=0,
        help="Demucs random shifts (default 0 for speed/stability).",
    )
    ap.add_argument(
        "--demucs-timeout",
        type=float,
        default=14400.0,
        help="Per-attempt Demucs subprocess timeout in seconds (default 14400).",
    )
    ap.add_argument("--ambient-volume", type=float, default=0.16)
    ap.add_argument("--music-volume", type=float, default=0.30)
    ap.add_argument("--music-path", type=Path, default=None)
    ap.add_argument(
        "--no-force-normalize",
        action="store_true",
        help="Disable treating sources as CFR-required when metadata is ambiguous (default: force on).",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--no-keep-intermediates",
        action="store_true",
        help="Delete intermediate WAVs after success (default: keep).",
    )
    args = ap.parse_args()
    force_normalize = not bool(args.no_force_normalize)
    keep_intermediates = not bool(args.no_keep_intermediates)

    inp = args.input.expanduser().resolve()
    if not inp.is_file():
        print(f"ERROR: missing input {inp}", file=sys.stderr)
        return 2

    cache = get_sv_cache(verbose=False)
    out_root = args.output_root.expanduser().resolve() if args.output_root else (cache / "audio_repaired")
    stem = inp.stem
    out_dir = out_root / stem

    safety = detect_video_safety(inp, force_normalize=force_normalize)
    orig_dur = float(safety.get("duration_sec") or duration_sec(inp) or 0.0)
    orig_fps_s = str(safety.get("avg_frame_rate") or safety.get("r_frame_rate") or "")

    seg_req = effective_demucs_segment_request(args.model, args.demucs_segment)
    seg_used, demucs_clamp_warns = clamp_demucs_segment(args.model, seg_req)
    demucs_fallbacks = demucs_fallback_segments(args.model)

    music_path = args.music_path.expanduser().resolve() if args.music_path else None
    want_music = bool(music_path and music_path.is_file())
    if args.mode == "repaired_with_music":
        if not music_path or not music_path.is_file():
            print("ERROR: repaired_with_music requires --music-path to an existing file.", file=sys.stderr)
            return 2
        want_music = True

    timeout_extract = max(600.0, orig_dur * 2.0 if orig_dur else 600.0)
    timeout_demucs = float(args.demucs_timeout)
    timeout_audio = max(3600.0, orig_dur * 2.0 if orig_dur else 3600.0)
    timeout_mux = max(7200.0, orig_dur * 5.0 if orig_dur else 7200.0)

    wav_original = out_dir / "original.wav"
    repaired_ambient_path = out_dir / "repaired_ambient.wav"
    repaired_vocals_path = out_dir / "repaired_vocals.wav"
    final_mix_path = out_dir / "final_repaired_mix.wav"
    fps_tag = args.fps
    if args.mode == "vocals_only":
        final_video = out_dir / f"{stem}_vocals_CFR{fps_tag}.mp4"
    else:
        final_video = out_dir / f"{stem}_repaired_CFR{fps_tag}.mp4"

    filters_used: list[str] = []

    if args.dry_run:
        plan = {
            "out_dir": str(out_dir),
            "mode": args.mode,
            "fps": args.fps,
            "safety": safety,
            "music_path": str(music_path) if music_path else None,
            "want_music": want_music,
            "final_video": str(final_video),
            "demucs_device": args.demucs_device,
            "demucs_segment_requested": seg_req,
            "demucs_segment_used_primary": seg_used,
            "demucs_fallback_segments": list(demucs_fallbacks),
            "demucs_shifts": args.demucs_shifts,
            "demucs_timeout_sec": args.demucs_timeout,
        }
        print(json.dumps(plan, indent=2, ensure_ascii=False))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)

    payload: dict[str, Any] = {
        "input_path": str(inp),
        "output_dir": str(out_dir),
        "original_duration_sec": orig_dur,
        "output_duration_sec": None,
        "duration_delta_sec": None,
        "original_fps": orig_fps_s,
        "output_fps": args.fps,
        "original_codec": safety.get("codec"),
        "output_codec": "h264",
        "is_iphone_or_apple": safety.get("is_iphone_or_apple"),
        "is_mov_container": safety.get("is_mov_container"),
        "is_high_fps": safety.get("is_high_fps"),
        "is_vfr_or_suspicious": safety.get("is_vfr_or_suspicious"),
        "requires_cfr_normalization": safety.get("requires_cfr_normalization"),
        "video_copy_used": False,
        "cfr_normalized": True,
        "demucs_model": args.model,
        "demucs_device": args.demucs_device,
        "demucs_segment_requested": seg_req,
        "demucs_segment_used": seg_used,
        "demucs_shifts": args.demucs_shifts,
        "demucs_timeout_sec": args.demucs_timeout,
        "demucs_full_log_path": None,
        "demucs_attempts": None,
        "demucs_stderr_tail": None,
        "demucs_returncode": None,
        "reason": None,
        "vocals_path": None,
        "no_vocals_path": None,
        "repaired_ambient_path": None,
        "final_mix_path": None,
        "final_video_path": str(final_video),
        "ambient_volume": args.ambient_volume,
        "music_path": str(music_path) if music_path else None,
        "music_volume": args.music_volume if want_music else None,
        "filters_used": "",
        "warnings": list(demucs_clamp_warns),
        "status": "running",
        "created_at": _utc(),
        "mode": args.mode,
        "force_normalize": force_normalize,
        "mux_encoder": None,
    }

    if not extract_audio(inp, wav_original, dry_run=False, timeout=timeout_extract):
        payload["status"] = "failed"
        payload["warnings"].append("extract_audio_failed")
        write_report(out_dir, stem, payload)
        return 3

    demucs_log_path = out_dir / "demucs_full.log"
    demucs_ok, demucs_meta = run_demucs_long_audio(
        demucs_exe=demucs_executable(),
        wav_in=wav_original,
        out_parent=out_dir,
        model=args.model,
        device=args.demucs_device,
        segment_requested=seg_req,
        segment_primary=seg_used,
        shifts=args.demucs_shifts,
        timeout_sec=timeout_demucs,
        dry_run=False,
        log_file=demucs_log_path,
        fallback_segments=demucs_fallbacks,
    )
    payload["demucs_model"] = demucs_meta.get("demucs_model") or args.model
    payload["demucs_full_log_path"] = demucs_meta.get("demucs_full_log_path")
    payload["demucs_attempts"] = demucs_meta.get("demucs_attempts")
    payload["demucs_stderr_tail"] = demucs_meta.get("demucs_stderr_tail")
    payload["demucs_device"] = demucs_meta.get("demucs_device")
    payload["demucs_segment_requested"] = demucs_meta.get("demucs_segment_requested")
    payload["demucs_segment_used"] = demucs_meta.get("demucs_segment_used")
    payload["demucs_shifts"] = demucs_meta.get("demucs_shifts")
    payload["demucs_returncode"] = demucs_meta.get("demucs_returncode")
    if not demucs_ok:
        payload["status"] = "failed"
        payload["reason"] = "demucs_failed"
        payload["warnings"].append("demucs_failed")
        write_report(out_dir, stem, payload)
        return 4

    vocals_p, no_vocals_p = find_stem_wavs(out_dir, wav_original.stem)
    if not vocals_p or not no_vocals_p:
        payload["status"] = "failed"
        payload["warnings"].append("demucs_wavs_not_found")
        write_report(out_dir, stem, payload)
        return 5

    payload["vocals_path"] = str(vocals_p)
    payload["no_vocals_path"] = str(no_vocals_p)

    # Processing stem selection
    if args.mode == "vocals_only":
        ok_r, fus = repair_vocals_track(vocals_p, repaired_vocals_path, dry_run=False, timeout=timeout_audio)
        filters_used.append(f"vocals_chain:{fus}")
        repaired_ambient_for_mix = repaired_vocals_path
        payload["repaired_ambient_path"] = str(repaired_vocals_path)
        if not ok_r:
            payload["status"] = "failed"
            payload["warnings"].append("repair_vocals_failed")
            write_report(out_dir, stem, payload)
            return 6
    else:
        ok_r, fus = repair_ambient_audio(
            no_vocals_p,
            repaired_ambient_path,
            ambient_volume=args.ambient_volume,
            dry_run=False,
            timeout=timeout_audio,
        )
        filters_used.append(f"ambient_chain:{fus}")
        repaired_ambient_for_mix = repaired_ambient_path
        payload["repaired_ambient_path"] = str(repaired_ambient_path)
        if not ok_r:
            payload["status"] = "failed"
            payload["warnings"].append("repair_ambient_failed")
            write_report(out_dir, stem, payload)
            return 6

    mix_want = want_music or args.mode == "repaired_with_music"
    if args.mode == "ambient_only":
        mix_want = bool(music_path and music_path.is_file())

    ok_m, fmix = mix_music_optional(
        repaired_ambient_for_mix,
        music_path if mix_want else None,
        final_mix_path,
        video_duration_sec=orig_dur,
        ambient_mix_gain=1.0,
        music_volume=args.music_volume,
        want_music=mix_want,
        dry_run=False,
        timeout=timeout_audio,
    )
    filters_used.append(f"mix:{fmix}")
    payload["final_mix_path"] = str(final_mix_path)
    payload["filters_used"] = " | ".join(filters_used)

    if not ok_m:
        payload["status"] = "failed"
        payload["warnings"].append("mix_or_loudnorm_failed")
        write_report(out_dir, stem, payload)
        return 7

    ok_x, enc_note = mux_cfr_video(
        inp,
        final_mix_path,
        final_video,
        fps=args.fps,
        dry_run=False,
        timeout=timeout_mux,
    )
    payload["mux_encoder"] = enc_note
    if not ok_x:
        payload["status"] = "failed"
        payload["warnings"].append("mux_cfr_failed")
        write_report(out_dir, stem, payload)
        return 8

    st, vw = validate_output(inp, final_video, orig_dur=orig_dur, expect_fps=args.fps)
    payload["warnings"].extend(vw)
    out_dur = duration_sec(final_video)
    payload["output_duration_sec"] = out_dur
    payload["duration_delta_sec"] = round(out_dur - orig_dur, 4) if orig_dur and out_dur else None

    if st == "failed":
        payload["status"] = "failed"
        bad = final_video.with_suffix(final_video.suffix + ".bad_duration")
        try:
            if final_video.is_file():
                final_video.rename(bad)
                payload["warnings"].append(f"renamed_bad_output:{bad.name}")
        except OSError:
            payload["warnings"].append("could_not_rename_bad_output")
    elif st == "ok_with_warnings":
        payload["status"] = "completed_with_warnings"
    else:
        payload["status"] = "completed"

    if not keep_intermediates:
        for p in (
            wav_original,
            repaired_ambient_path,
            repaired_vocals_path,
            final_mix_path,
        ):
            try:
                if p.is_file() and p != final_video:
                    p.unlink()
            except OSError:
                payload["warnings"].append(f"unlink_failed:{p.name}")

    write_report(out_dir, stem, payload)
    return 0 if payload["status"] in ("completed", "completed_with_warnings") else 9


if __name__ == "__main__":
    raise SystemExit(main())
