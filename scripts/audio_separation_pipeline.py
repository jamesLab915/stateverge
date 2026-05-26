#!/usr/bin/env python3
"""Demucs stem separation with CFR-safe video mux (no ``-c:v copy`` on risky sources by default)."""

from __future__ import annotations

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

from utils.davinci_ffprobe import ffprobe_json  # noqa: E402
from utils.storage_paths import get_sv_cache  # noqa: E402
from utils.vfr_video_safety import (  # noqa: E402
    is_iphone_or_apple_video,
    is_vfr_or_high_fps,
    probe_summary,
    requires_cfr_normalization,
)
from utils.demucs_run import (  # noqa: E402
    clamp_demucs_segment,
    demucs_executable,
    demucs_fallback_segments,
    effective_demucs_segment_request,
    run_demucs_long_audio,
)

FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run(cmd: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-8000:]
    return r.returncode, tail


def _duration_sec(path: Path) -> float:
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


def _ffprobe_stream_avg_fps(path: Path) -> str | None:
    data, err = ffprobe_json(path, timeout_sec=120.0)
    if err or not data:
        return None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            return str(s.get("avg_frame_rate") or "")
    return None


def _validate_output(
    inp: Path,
    outp: Path,
    *,
    orig_dur: float,
    expect_fps: int,
) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not outp.is_file() or outp.stat().st_size < 1024:
        return "failed", ["missing_or_tiny_output"]
    od = _duration_sec(outp)
    if orig_dur <= 0:
        warnings.append("could_not_compare_duration_input_unknown")
        return "ok", warnings
    delta = abs(od - orig_dur)
    tol = max(2.0, orig_dur * 0.01)
    if delta > tol:
        warnings.append(f"duration_drift_sec={delta:.3f}_tol={tol:.3f}")
    if od > orig_dur * 1.05:
        return "failed", warnings + ["output_duration_exceeds_input_by_more_than_5_percent"]
    avg = _ffprobe_stream_avg_fps(outp) or ""
    exp = f"{expect_fps}/1"
    if avg != exp:
        warnings.append(f"avg_frame_rate_expected_{exp}_got_{avg or 'unknown'}")
    data, _ = ffprobe_json(outp, timeout_sec=120.0)
    if not data:
        return "failed", warnings + ["ffprobe_output_failed"]
    types = [s.get("codec_type") for s in data.get("streams") or []]
    if "video" not in types:
        return "failed", warnings + ["no_video_stream"]
    if "audio" not in types:
        return "failed", warnings + ["no_audio_stream"]
    if warnings:
        return "ok_with_warnings", warnings
    return "ok", []


def _extract_wav(video: Path, wav_out: Path, *, dry_run: bool, timeout: float) -> bool:
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
    rc, _msg = _run(cmd, timeout=timeout)
    return rc == 0 and wav_out.is_file() and wav_out.stat().st_size > 1024


def _find_stem_wavs(out_root: Path, track_guess: str) -> tuple[Path | None, Path | None]:
    """Locate Demucs outputs under ``out_root/<model>/<track>/*.wav``.

    Never prefer ``debug/`` or ad-hoc test folders: ``rglob`` order can pick a short
    ``test_30s`` stem and mux wrong audio length onto the full video.
    """
    skip_roots = {"debug", "fixed", "__pycache__"}
    vocals: Path | None = None
    no_vocals: Path | None = None
    try:
        for sub in sorted(out_root.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir() or sub.name in skip_roots or sub.name.startswith("."):
                continue
            cand_v = sub / track_guess / "vocals.wav"
            cand_nv = sub / track_guess / "no_vocals.wav"
            if cand_v.is_file() and cand_nv.is_file():
                return cand_v, cand_nv
    except OSError:
        pass
    # Fallback: newest matching pair outside skip_roots (by mtime)
    best: tuple[float, Path, Path] | None = None
    try:
        for nv in out_root.rglob("no_vocals.wav"):
            if not nv.is_file():
                continue
            parts = {x.lower() for x in nv.parts}
            if "debug" in parts:
                continue
            v = nv.parent / "vocals.wav"
            if not v.is_file():
                continue
            try:
                mt = float(nv.stat().st_mtime)
            except OSError:
                continue
            if best is None or mt > best[0]:
                best = (mt, v, nv)
    except OSError:
        pass
    if best:
        return best[1], best[2]
    return vocals, no_vocals


def _mux_replace_audio(
    video_in: Path,
    audio_in: Path,
    video_out: Path,
    *,
    fps: int,
    allow_video_copy: bool,
    dry_run: bool,
    timeout: float,
) -> tuple[bool, bool, str]:
    """Returns ``(ok, video_copy_used, encoder_note)``."""
    video_out.parent.mkdir(parents=True, exist_ok=True)
    if dry_run:
        return True, False, "dry_run"

    use_copy = bool(
        allow_video_copy and not requires_cfr_normalization(video_in, force=False)
    )
    if use_copy:
        tmp = video_out.parent / f"{video_out.stem}._mux_{os.getpid()}{video_out.suffix}"
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(video_in),
            "-i",
            str(audio_in),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(tmp),
        ]
        rc, _msg = _run(cmd, timeout=timeout)
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            tmp.replace(video_out)
            return True, True, "h264_copy_mux"
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass

    tmp2 = video_out.parent / f"{video_out.stem}._cfr_{os.getpid()}{video_out.suffix}"
    vf = f"fps={fps},format=yuv420p"
    attempts: list[tuple[str, list[str]]] = [
        (
            "h264_videotoolbox",
            [
                "-c:v",
                "h264_videotoolbox",
                "-b:v",
                "35M",
                "-tag:v",
                "avc1",
            ],
        ),
        (
            "libx264",
            [
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-tag:v",
                "avc1",
            ],
        ),
    ]
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
            str(audio_in),
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
            str(tmp2),
        ]
        rc, _msg = _run(cmd, timeout=timeout)
        if rc == 0 and tmp2.is_file() and tmp2.stat().st_size > 1024:
            tmp2.replace(video_out)
            return False, False, label
        try:
            if tmp2.is_file():
                tmp2.unlink()
        except OSError:
            pass
    return False, False, "mux_failed"


def _write_reports(
    out_dir: Path,
    stem: str,
    payload: dict[str, Any],
) -> tuple[Path, Path]:
    jp = out_dir / "audio_separation_report.json"
    mp = out_dir / "audio_separation_report.md"
    jp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [
        f"# Audio separation report ({stem})",
        "",
        f"- **status**: {payload.get('status')}",
        f"- **input**: `{payload.get('input_path')}`",
        f"- **output_dir**: `{payload.get('output_dir')}`",
        f"- **original_duration_sec**: {payload.get('original_duration_sec')}",
        f"- **output_duration_sec**: {payload.get('output_duration_sec')}",
        f"- **duration_delta_sec**: {payload.get('duration_delta_sec')}",
        f"- **original_fps**: {payload.get('original_fps')}",
        f"- **output_fps**: {payload.get('output_fps')}",
        f"- **original_codec**: {payload.get('original_codec')}",
        f"- **output_codec**: {payload.get('output_codec')}",
        f"- **is_iphone_or_apple**: {payload.get('is_iphone_or_apple')}",
        f"- **is_vfr_or_high_fps**: {payload.get('is_vfr_or_high_fps')}",
        f"- **cfr_normalized**: {payload.get('cfr_normalized')}",
        f"- **video_copy_used**: {payload.get('video_copy_used')}",
        f"- **demucs_model**: {payload.get('demucs_model')}",
        f"- **reason**: `{payload.get('reason')}`",
        f"- **demucs_device**: {payload.get('demucs_device')}",
        f"- **demucs_segment_requested**: {payload.get('demucs_segment_requested')}",
        f"- **demucs_segment_used**: {payload.get('demucs_segment_used')}",
        f"- **demucs_shifts**: {payload.get('demucs_shifts')}",
        f"- **demucs_returncode**: {payload.get('demucs_returncode')}",
        f"- **demucs_full_log_path**: `{payload.get('demucs_full_log_path')}`",
        "",
        "## Paths",
        "",
        f"- vocals: `{payload.get('vocals_path')}`",
        f"- no_vocals: `{payload.get('no_vocals_path')}`",
        f"- no_vocals_video: `{payload.get('no_vocals_video_path')}`",
        f"- vocals_only_video: `{payload.get('vocals_only_video_path')}`",
        "",
        "## Warnings",
        "",
        *[f"- {w}" for w in (payload.get("warnings") or [])],
        "",
    ]
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True)
    ap.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="Default: SV_CACHE/audio_separated",
    )
    ap.add_argument("--mode", choices=("no_vocals", "vocals_only", "both"), default="no_vocals")
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60))
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
    ap.add_argument("--force-normalize", action="store_true", help="Treat input as requiring CFR handling.")
    ap.add_argument(
        "--allow-video-copy",
        action="store_true",
        help="Allow -c:v copy only when probe says source is safe (non‑iPhone/CFR); default is CFR re-encode.",
    )
    ap.add_argument(
        "--no-keep-original-wav",
        action="store_true",
        help="Delete extracted original.wav after Demucs (default keeps WAV).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned paths and probe summary only.",
    )
    ap.add_argument(
        "--reuse-demucs-stems",
        action="store_true",
        help="Skip WAV extract + Demucs; mux from existing stems under output_dir (full-length no_vocals).",
    )
    args = ap.parse_args()

    inp = args.input.expanduser().resolve()
    if not inp.is_file():
        print(f"ERROR: input missing: {inp}", file=sys.stderr)
        return 2

    cache = get_sv_cache(verbose=False)
    out_root = (
        args.output_root.expanduser().resolve()
        if args.output_root
        else (cache / "audio_separated")
    )
    stem = inp.stem
    out_dir = out_root / stem
    wav_path = out_dir / "original.wav"

    summary = probe_summary(inp)
    orig_dur = float(summary.get("duration_sec") or _duration_sec(inp) or 0.0)
    orig_fps_s = str(summary.get("avg_frame_rate") or summary.get("r_frame_rate") or "")
    force_n = bool(args.force_normalize)
    iphone = is_iphone_or_apple_video(inp)
    vfr_hi = is_vfr_or_high_fps(inp)
    need_norm = force_n or requires_cfr_normalization(inp, force=False)

    warnings: list[str] = []
    seg_req = effective_demucs_segment_request(args.model, args.demucs_segment)
    seg_used, clamp_warns = clamp_demucs_segment(args.model, seg_req)
    warnings.extend(clamp_warns)
    demucs_fallbacks = demucs_fallback_segments(args.model)

    if args.dry_run:
        plan = {
            "out_dir": str(out_dir),
            "wav": str(wav_path),
            "demucs_model": args.model,
            "demucs_device": args.demucs_device,
            "demucs_segment_requested": seg_req,
            "demucs_segment_used_primary": seg_used,
            "demucs_fallback_segments": list(demucs_fallbacks),
            "demucs_shifts": args.demucs_shifts,
            "demucs_timeout_sec": args.demucs_timeout,
            "mode": args.mode,
            "fps": args.fps,
            "force_normalize": force_n,
            "requires_cfr_normalization": need_norm,
            "allow_video_copy": args.allow_video_copy,
            "reuse_demucs_stems": bool(args.reuse_demucs_stems),
            "probe": summary,
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
        "original_codec": summary.get("codec"),
        "output_codec": None,
        "is_iphone_or_apple": iphone,
        "is_vfr_or_high_fps": vfr_hi,
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
        "no_vocals_video_path": None,
        "vocals_only_video_path": None,
        "warnings": warnings,
        "status": "running",
        "created_at": _utc_now_iso(),
    }

    timeout_demucs = float(args.demucs_timeout)
    timeout_mux = max(7200.0, orig_dur * 4.0 if orig_dur > 0 else 7200.0)

    vocals_p: Path | None = None
    no_vocals_p: Path | None = None

    if args.reuse_demucs_stems:
        vocals_p, no_vocals_p = _find_stem_wavs(out_dir, wav_path.stem)
        if not vocals_p or not no_vocals_p:
            payload["status"] = "failed"
            payload["reason"] = "reuse_demucs_stems_missing"
            payload["warnings"].append("reuse_demucs_stems_but_no_stem_wavs_found")
            _write_reports(out_dir, stem, payload)
            return 9
        payload["warnings"].append("skipped_extract_and_demucs_reuse_demucs_stems")
        payload["demucs_returncode"] = "skipped_reuse"
        payload["demucs_full_log_path"] = str(out_dir / "demucs_full.log")
        payload["demucs_attempts"] = []
    else:
        if not _extract_wav(inp, wav_path, dry_run=False, timeout=min(timeout_demucs, 14400.0)):
            payload["status"] = "failed"
            payload["warnings"].append("extract_wav_failed")
            _write_reports(out_dir, stem, payload)
            return 3

        demucs_parent = out_dir
        demucs_log_path = out_dir / "demucs_full.log"
        demucs_ok, demucs_meta = run_demucs_long_audio(
            demucs_exe=demucs_executable(),
            wav_in=wav_path,
            out_parent=demucs_parent,
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
            _write_reports(out_dir, stem, payload)
            return 4

        vocals_p, no_vocals_p = _find_stem_wavs(out_dir, wav_path.stem)
        if not vocals_p or not no_vocals_p:
            payload["status"] = "failed"
            payload["warnings"].append("demucs_output_wavs_not_found")
            _write_reports(out_dir, stem, payload)
            return 5

    if args.no_keep_original_wav and wav_path.is_file() and not args.reuse_demucs_stems:
        try:
            wav_path.unlink()
        except OSError:
            payload["warnings"].append("could_not_delete_original_wav")

    payload["vocals_path"] = str(vocals_p)
    payload["no_vocals_path"] = str(no_vocals_p)

    fps_tag = args.fps
    nv_video = out_dir / f"{stem}_no_vocals_CFR{fps_tag}.mp4"
    vo_video = out_dir / f"{stem}_vocals_only_CFR{fps_tag}.mp4"

    video_copy_used_any = False
    encoder_notes: list[str] = []

    if args.mode in ("no_vocals", "both"):
        ok, vcopy, note = _mux_replace_audio(
            inp,
            no_vocals_p,
            nv_video,
            fps=args.fps,
            allow_video_copy=args.allow_video_copy,
            dry_run=False,
            timeout=timeout_mux,
        )
        encoder_notes.append(f"no_vocals:{note}")
        video_copy_used_any = video_copy_used_any or vcopy
        if not ok:
            payload["status"] = "failed"
            payload["warnings"].append("mux_no_vocals_failed")
            payload["video_copy_used"] = video_copy_used_any
            _write_reports(out_dir, stem, payload)
            return 6
        payload["no_vocals_video_path"] = str(nv_video)

    if args.mode in ("vocals_only", "both"):
        ok, vcopy, note = _mux_replace_audio(
            inp,
            vocals_p,
            vo_video,
            fps=args.fps,
            allow_video_copy=args.allow_video_copy,
            dry_run=False,
            timeout=timeout_mux,
        )
        encoder_notes.append(f"vocals_only:{note}")
        video_copy_used_any = video_copy_used_any or vcopy
        if not ok:
            payload["status"] = "failed"
            payload["warnings"].append("mux_vocals_only_failed")
            payload["video_copy_used"] = video_copy_used_any
            _write_reports(out_dir, stem, payload)
            return 7
        payload["vocals_only_video_path"] = str(vo_video)

    payload["video_copy_used"] = video_copy_used_any
    payload["output_codec"] = "h264"
    if video_copy_used_any:
        payload["output_codec"] = "stream_copy_mux"
    payload["cfr_normalized"] = not video_copy_used_any
    payload["force_normalize_flag"] = force_n

    check_path = nv_video if args.mode in ("no_vocals", "both") else vo_video
    st, vw = _validate_output(inp, check_path, orig_dur=orig_dur, expect_fps=args.fps)
    payload["warnings"].extend(encoder_notes)
    payload["warnings"].extend(vw)
    out_dur = _duration_sec(check_path)
    payload["output_duration_sec"] = out_dur
    payload["duration_delta_sec"] = (
        round(out_dur - orig_dur, 4) if orig_dur > 0 and out_dur > 0 else None
    )

    if st == "failed":
        payload["status"] = "failed"
        bad = check_path.with_suffix(check_path.suffix + ".bad_duration")
        try:
            if check_path.is_file():
                check_path.rename(bad)
                payload["warnings"].append(f"renamed_bad_output_to:{bad.name}")
        except OSError:
            payload["warnings"].append("could_not_rename_bad_output")
    elif st == "ok_with_warnings":
        payload["status"] = "completed_with_warnings"
    else:
        payload["status"] = "completed"

    _write_reports(out_dir, stem, payload)
    return 0 if payload["status"] in ("completed", "completed_with_warnings") else 8


if __name__ == "__main__":
    raise SystemExit(main())
