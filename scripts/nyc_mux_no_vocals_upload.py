#!/usr/bin/env python3
"""NYC: mux Demucs ``no_vocals.wav`` with **mandatory CFR H.264** (no default ``-c:v copy``).

- Default ``mux_mode``: ``cfr30_h264_videotoolbox_aac`` (``cfr60_...`` when ``--fps 60``).
- iPhone / MOV / VFR inputs must not use ``-c:v copy`` for deliverables; if ``--allow-video-copy`` is set
  but the input still requires CFR per ``requires_cfr_normalization``, raises ``UNSAFE_VIDEO_COPY_FOR_IPHONE_VFR``.
- Prefers ``<stems_dir>/fixed/<stem>_no_vocals_CFR{fps}_FIXED.mp4``, then ``--use-existing-cfr-output``, else encodes.
- Upload only with explicit ``--upload``. Single-source long dedupe bypass requires ``--allow-single-source-long``
  and full validation in ``nyc_cut_upload_job._run_dedupe_check`` (see ``dedupe_exception``).

Original camera files are never deleted.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
_SRC = ROOT / "src"
for p in (_SRC, ROOT / "scripts"):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from utils.storage_paths import get_sv_cache, get_transfer_ready_to_upload  # noqa: E402

try:
    from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
except Exception:  # noqa: BLE001
    ffprobe_json = None  # type: ignore[assignment]
    stream_summary = None  # type: ignore[assignment]

try:
    from utils.vfr_video_safety import requires_cfr_normalization  # noqa: E402
except Exception:  # noqa: BLE001

    def requires_cfr_normalization(_path: Path, **_kw: Any) -> bool:  # type: ignore[no-redef]
        return True


def _load_audio_sep_find_stems():
    spec = importlib.util.spec_from_file_location(
        "audio_separation_pipeline",
        ROOT / "scripts" / "audio_separation_pipeline.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod._find_stem_wavs  # type: ignore[attr-defined]


def _load_nyc_upload_stack():
    spec = importlib.util.spec_from_file_location(
        "nyc_cut_upload_job",
        ROOT / "scripts" / "jobs" / "nyc_cut_upload_job.py",
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod._run_publish_gate, mod._run_dedupe_check, mod._verify_nyc_channel, mod._upload_video


def _ffprobe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except (ValueError, subprocess.TimeoutExpired, FileNotFoundError):
        return 0.0


def _nyc_long_clips_dir() -> Path:
    return (get_transfer_ready_to_upload(verbose=False) / "nyc_long_clips").resolve()


def _primary_video_stream(data: dict[str, Any]) -> dict[str, Any] | None:
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            return s
    return None


def _validate_cfr_output_file(
    path: Path,
    *,
    source_duration_sec: float,
    expected_fps: int,
) -> tuple[bool, str, float]:
    """Check duration vs source, streams, CFR avg_frame_rate == fps/1 and r_frame_rate match."""
    if ffprobe_json is None or stream_summary is None:
        return False, "ffprobe_helpers_unavailable", 0.0
    data, err = ffprobe_json(path, timeout_sec=120.0)
    if err or not data:
        return False, f"ffprobe_failed:{err}", 0.0
    has_v, has_a, odur = stream_summary(data)
    if not has_v:
        return False, "missing_video_stream", odur
    if not has_a:
        return False, "missing_audio_stream", odur
    sd = float(source_duration_sec or 0.0)
    if sd > 0 and odur > 0:
        delta = abs(odur - sd)
        if delta > max(2.0, sd * 0.01):
            return False, "duration_mismatch_vs_source", odur
    vs = _primary_video_stream(data)
    if not vs:
        return False, "no_video_stream", odur
    exp = f"{int(expected_fps)}/1"
    afr = str(vs.get("avg_frame_rate") or "")
    rfr = str(vs.get("r_frame_rate") or "")
    if afr != exp:
        return False, f"avg_frame_rate_want_{exp}_got_{afr}", odur
    if rfr != afr:
        return False, f"vfr_or_mismatch_r_{rfr}_avg_{afr}", odur
    return True, "ok", odur


def _mux_no_vocals_video_copy(
    video_in: Path,
    no_vocals_wav: Path,
    video_out: Path,
    *,
    timeout: float,
) -> tuple[bool, str]:
    video_out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
    tmp = video_out.with_name(f"{video_out.stem}.tmp.{os.getpid()}{video_out.suffix}")
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_in),
        "-i",
        str(no_vocals_wav),
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
        "-f",
        "mp4",
        str(tmp),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return False, "ffmpeg_timeout"
    if r.returncode != 0 or not tmp.is_file() or tmp.stat().st_size < 1024:
        tail = (r.stderr or "")[-2000:]
        return False, f"ffmpeg_rc={r.returncode} {tail}"
    try:
        tmp.replace(video_out)
    except OSError:
        shutil.copy2(tmp, video_out)
        tmp.unlink(missing_ok=True)
    return True, "ok"


def _mux_no_vocals_cfr_encode(
    video_in: Path,
    no_vocals_wav: Path,
    video_out: Path,
    *,
    fps: int,
    timeout: float,
) -> tuple[bool, str, str]:
    """CFR encode + AAC; try ``h264_videotoolbox`` then ``libx264``. Returns (ok, note, encoder_used)."""
    video_out.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
    tmp = video_out.with_name(f"{video_out.stem}.tmp.{os.getpid()}{video_out.suffix}")
    vf = f"fps={int(fps)},format=yuv420p"

    def _cmd_vt() -> list[str]:
        return [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(video_in),
            "-i",
            str(no_vocals_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            "35M",
            "-tag:v",
            "avc1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(tmp),
        ]

    def _cmd_x264() -> list[str]:
        return [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-fflags",
            "+genpts",
            "-i",
            str(video_in),
            "-i",
            str(no_vocals_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "avc1",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-shortest",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            str(tmp),
        ]

    for label, cmd in (("h264_videotoolbox", _cmd_vt()), ("libx264", _cmd_x264())):
        tmp.unlink(missing_ok=True)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return False, "ffmpeg_timeout", ""
        if r.returncode == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(video_out)
            except OSError:
                shutil.copy2(tmp, video_out)
                tmp.unlink(missing_ok=True)
            return True, "ok", label
        if label == "h264_videotoolbox":
            continue
        tail = (r.stderr or "")[-2000:]
        return False, f"ffmpeg_rc={r.returncode} {tail}", label
    tail = "h264_videotoolbox_failed_no_libx264_success"
    return False, tail, "h264_videotoolbox"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", type=Path, required=True, help="Source MOV/MP4 (e.g. yewan.mov).")
    ap.add_argument(
        "--stems-dir",
        type=Path,
        default=None,
        help="Demucs work dir (default: SV_CACHE/audio_separated/<input_stem>).",
    )
    ap.add_argument("--fps", type=int, default=30, choices=(30, 60), help="Target CFR (default 30).")
    ap.add_argument(
        "--use-existing-cfr-output",
        type=Path,
        default=None,
        help="Use this CFR MP4 if it passes validation; copied into nyc_long_clips.",
    )
    ap.add_argument(
        "--allow-video-copy",
        action="store_true",
        help="Allow -c:v copy mux only when input does NOT require CFR normalization.",
    )
    ap.add_argument(
        "--allow-single-source-long",
        action="store_true",
        help="Allow dedupe exception repaired_single_source_long (validated at upload).",
    )
    ap.add_argument("--title", default="", help="YouTube title (default: stem + timestamp).")
    ap.add_argument("--privacy-status", default="unlisted", choices=("private", "unlisted", "public"))
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument(
        "--upload",
        action="store_true",
        help="Explicit: run publish gate + dedupe + NYC channel + YouTube upload.",
    )
    ap.add_argument("--dry-run", action="store_true", help="Plan only; no ffmpeg write, no upload.")
    args = ap.parse_args()

    inp = args.input.expanduser().resolve()
    if not inp.is_file():
        print(f"ERROR: input not found: {inp}", file=sys.stderr)
        return 2

    if args.allow_video_copy and requires_cfr_normalization(inp):
        print("ERROR: UNSAFE_VIDEO_COPY_FOR_IPHONE_VFR", file=sys.stderr)
        raise RuntimeError("UNSAFE_VIDEO_COPY_FOR_IPHONE_VFR")

    cache = get_sv_cache(verbose=False)
    stems_dir = (
        args.stems_dir.expanduser().resolve()
        if args.stems_dir
        else (cache / "audio_separated" / inp.stem)
    )
    find_stems = _load_audio_sep_find_stems()
    run_publish_gate, run_dedupe_check, verify_nyc_channel, upload_video = _load_nyc_upload_stack()

    vocals_p, no_vocals_p = find_stems(stems_dir, "original")
    if not no_vocals_p or not no_vocals_p.is_file():
        print(f"ERROR: no_vocals.wav not under {stems_dir} (run Demucs / audio_separation first).", file=sys.stderr)
        return 3

    ready = _nyc_long_clips_dir()
    ready.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_mp4 = ready / f"{inp.stem}_no_vocals_cfr{args.fps}_{ts}.mp4"

    vd = _ffprobe_duration(inp)
    ad = _ffprobe_duration(no_vocals_p)
    mux_mode = f"cfr{args.fps}_h264_videotoolbox_aac"
    requires_cfr = bool(requires_cfr_normalization(inp))

    preferred_fixed = stems_dir / "fixed" / f"{inp.stem}_no_vocals_CFR{args.fps}_FIXED.mp4"
    existing_arg = args.use_existing_cfr_output.expanduser().resolve() if args.use_existing_cfr_output else None

    plan: dict[str, Any] = {
        "input": str(inp),
        "video_duration_sec": vd,
        "no_vocals_wav_duration_sec": ad,
        "no_vocals_wav": str(no_vocals_p),
        "vocals_wav": str(vocals_p) if vocals_p else "",
        "output": str(out_mp4),
        "mux_mode": mux_mode,
        "video_copy_used": False,
        "cfr_normalized": True,
        "requires_cfr_normalization": requires_cfr,
        "preferred_fixed_cfr": str(preferred_fixed),
        "use_existing_cfr_output": str(existing_arg) if existing_arg else "",
        "upload": bool(args.upload),
        "allow_video_copy": bool(args.allow_video_copy),
        "allow_single_source_long": bool(args.allow_single_source_long),
        "fps": int(args.fps),
    }
    print(json.dumps(plan, indent=2, ensure_ascii=False))
    if args.dry_run:
        return 0

    timeout_mux = max(7200.0, vd * 2.0 + 600.0 if vd > 0 else 7200.0)
    video_copy_used = False
    cfr_normalized = True
    encoder_used = ""
    source_from = ""

    def _try_adopt_cfr_file(src_file: Path, label: str) -> tuple[bool, str]:
        nonlocal encoder_used, source_from
        ok_v, why, _od = _validate_cfr_output_file(src_file, source_duration_sec=vd, expected_fps=args.fps)
        if not ok_v:
            return False, why
        try:
            shutil.copy2(src_file, out_mp4)
        except OSError as exc:
            return False, f"copy_failed:{exc}"
        ok2, why2, _od2 = _validate_cfr_output_file(out_mp4, source_duration_sec=vd, expected_fps=args.fps)
        if not ok2:
            out_mp4.unlink(missing_ok=True)
            return False, f"post_copy_validation:{why2}"
        encoder_used = "existing_cfr_file"
        source_from = label
        return True, "ok"

    adopted = False
    if existing_arg and existing_arg.is_file():
        ok_ad, note_ad = _try_adopt_cfr_file(existing_arg, "use_existing_cfr_output")
        if ok_ad:
            adopted = True
        else:
            print(f"WARN: --use-existing-cfr-output rejected ({note_ad}); trying fixed/encode.", file=sys.stderr)

    if not adopted and preferred_fixed.is_file():
        ok_pf, note_pf = _try_adopt_cfr_file(preferred_fixed, "preferred_fixed")
        if ok_pf:
            adopted = True
        else:
            print(f"WARN: preferred fixed CFR rejected ({note_pf}); encoding.", file=sys.stderr)

    if not adopted:
        if args.allow_video_copy and not requires_cfr:
            ok_c, note_c = _mux_no_vocals_video_copy(inp, no_vocals_p, out_mp4, timeout=timeout_mux)
            if not ok_c:
                print(f"ERROR: video-copy mux failed: {note_c}", file=sys.stderr)
                return 4
            video_copy_used = True
            cfr_normalized = False
            encoder_used = "c:v_copy"
            source_from = "allow_video_copy_mux"
        else:
            ok_e, note_e, enc = _mux_no_vocals_cfr_encode(inp, no_vocals_p, out_mp4, fps=args.fps, timeout=timeout_mux)
            if not ok_e:
                print(f"ERROR: CFR encode failed: {note_e}", file=sys.stderr)
                return 4
            encoder_used = enc
            source_from = "cfr_encode"
            ok_p, why_p, _dur = _validate_cfr_output_file(out_mp4, source_duration_sec=vd, expected_fps=args.fps)
            if not ok_p:
                out_mp4.unlink(missing_ok=True)
                print(f"ERROR: CFR output validation failed: {why_p}", file=sys.stderr)
                return 4

    out_dur = _ffprobe_duration(out_mp4)
    vs_data, _e = ffprobe_json(out_mp4, timeout_sec=120.0) if ffprobe_json else (None, "no_ffprobe")
    vs0 = _primary_video_stream(vs_data or {}) or {}
    afr_out = str(vs0.get("avg_frame_rate") or "")

    eff_long = out_dur >= 1800.0
    video_type = "repaired_single_source_long" if eff_long else "repaired_single_source"

    result: dict[str, Any] = {
        **plan,
        "output_written": str(out_mp4),
        "output_duration_sec": out_dur,
        "video_copy_used": video_copy_used,
        "cfr_normalized": cfr_normalized,
        "encoder_used": encoder_used,
        "source_from": source_from,
        "avg_frame_rate": afr_out,
        "video_type": video_type,
        "mux_mode": ("c:v_copy_c:a_aac_shortest" if video_copy_used else plan["mux_mode"]),
    }
    print(json.dumps({"result": result}, indent=2, ensure_ascii=False))

    if vd > 0 and out_dur > 0:
        delta = abs(out_dur - vd)
        if delta > max(1.5, vd * 0.005):
            print(
                f"WARN: duration delta input={vd:.3f}s output={out_dur:.3f}s delta={delta:.3f}s",
                file=sys.stderr,
            )

    if not args.upload:
        print(f"OK: wrote {out_mp4} (upload skipped; pass --upload for NYC YouTube).")
        return 0

    title = (args.title or "").strip() or f"{inp.stem} no vocals CFR{args.fps} {ts}"[:100]
    description = (
        f"StateVerge NYC no-vocals CFR mux. Source: {inp.name}. Stem: {no_vocals_p.name}. "
        f"Encoder: {encoder_used}. copy={video_copy_used}."
    )
    tags = ["NYC", "New York", "StateVerge", "no vocals"]

    pg: dict[str, Any] = run_publish_gate(out_mp4)
    print(json.dumps({"publish_gate": pg}, indent=2, ensure_ascii=False))
    if not pg.get("passed"):
        print("ERROR: publish_gate failed; not uploading.", file=sys.stderr)
        return 5

    ded = run_dedupe_check(
        out_mp4,
        title=title,
        eff_dur=float(out_dur or vd),
        selected_paths=[str(inp)],
        single_take_long=False,
        allow_single_source_long=True,
        repaired_single_source_ctx={
            "source_path": str(inp),
            "no_vocals_wav": str(no_vocals_p),
            "source_duration_sec": float(vd),
            "expected_fps": int(args.fps),
            "video_copy_used": bool(video_copy_used),
            "cfr_normalized": bool(cfr_normalized),
        },
    )
    print(json.dumps({"dedupe": ded}, indent=2, ensure_ascii=False))
    if ded.get("skipped"):
        pass
    elif ded.get("allowed") is False:
        print(f"ERROR: dedupe blocked: {ded.get('reason')}", file=sys.stderr)
        return 6

    ok_ch, chinfo = verify_nyc_channel()
    print(json.dumps({"channel": chinfo}, indent=2, ensure_ascii=False))
    if not ok_ch:
        print(f"ERROR: NYC channel not confirmed: {chinfo}", file=sys.stderr)
        return 7

    up = upload_video(
        out_mp4,
        title=title,
        description=description,
        tags=tags,
        privacy=str(args.privacy_status),
        allow_public=bool(args.allow_public),
    )
    print(json.dumps({"upload": up}, indent=2, ensure_ascii=False))
    if not up.get("ok"):
        return 8
    print(f"OK: uploaded https://www.youtube.com/watch?v={up.get('video_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
