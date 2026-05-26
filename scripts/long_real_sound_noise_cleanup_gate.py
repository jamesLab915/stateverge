#!/usr/bin/env python3
"""Long Real Sound Noise Cleanup Gate v1 — ambient-preserving denoise/de-click (not vocal separation). No upload."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402

try:
    from utils.storage_paths import get_sv_cache  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")


FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

MILD_KEYWORDS = (
    "ferry",
    "sunset",
    "waterfront",
    "rain",
    "ambient",
    "real_sound",
    "walk",
    "street",
)
STRONG_KEYWORDS = (
    "truck",
    "metal",
    "rattle",
    "turn_signal",
    "bad_audio",
    "noisy",
    "bang",
    "click",
)

MILD_AF = (
    "highpass=f=70,"
    "lowpass=f=15000,"
    "afftdn=nf=-25,"
    "acompressor=threshold=-18dB:ratio=2:attack=20:release=250,"
    "alimiter=limit=0.95,"
    "loudnorm=I=-14:LRA=11:TP=-1.5"
)

STRONG_AF_WITH_ANLMDN = (
    "highpass=f=100,"
    "lowpass=f=12000,"
    "afftdn=nf=-30,"
    "anlmdn=s=7:p=0.002:r=0.002:m=11,"
    "acompressor=threshold=-22dB:ratio=3:attack=10:release=200,"
    "alimiter=limit=0.90,"
    "loudnorm=I=-14:LRA=11:TP=-1.5"
)

STRONG_AF_FALLBACK = (
    "highpass=f=100,"
    "lowpass=f=12000,"
    "afftdn=nf=-30,"
    "acompressor=threshold=-22dB:ratio=3:attack=10:release=200,"
    "alimiter=limit=0.90,"
    "loudnorm=I=-14:LRA=11:TP=-1.5"
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _demucs_available() -> bool:
    return shutil.which("demucs") is not None


def _ffmpeg_filter_doc_available(name: str) -> bool:
    try:
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-h", f"filter={name}"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        err = (r.stderr or "") + (r.stdout or "")
        if r.returncode != 0:
            return False
        if re.search(rf"Filter\s+{re.escape(name)}", err, re.I):
            return True
        return name.lower() in err.lower() and "unknown" not in err.lower()
    except (OSError, subprocess.TimeoutExpired):
        return False


def _resolve_workdir(job_id: str) -> tuple[Path, bool]:
    primary_root = get_sv_cache(verbose=False) / "audio_processing" / "long_real_sound_noise_cleanup"
    primary = primary_root / job_id
    try:
        primary.mkdir(parents=True, exist_ok=True)
        probe = primary / ".write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return primary, False
    except OSError:
        pass
    fb = Path.home() / "StateVerge" / "data" / "audio_runtime" / "long_real_sound_noise_cleanup" / job_id
    fb.mkdir(parents=True, exist_ok=True)
    return fb, True


def _auto_mode_for_path(path: Path) -> str:
    s = str(path).lower()
    if any(k in s for k in STRONG_KEYWORDS):
        return "strong"
    if any(k in s for k in MILD_KEYWORDS):
        return "mild"
    return "mild"


def _probe_input_summary(path: Path) -> dict[str, Any]:
    probe = apq._ffprobe_json(path)  # noqa: SLF001
    out: dict[str, Any] = {
        "ffprobe_ok": probe is not None,
        "has_video": False,
        "has_audio": False,
        "duration_sec": 0.0,
        "width": 0,
        "height": 0,
        "video_codec": "",
        "audio_codec": "",
        "fps": 0.0,
        "aspect_ratio": 0.0,
    }
    if not probe:
        return out
    d, hv = apq._duration_and_has_video(probe)  # noqa: SLF001
    w, h = apq._primary_video_dims(probe)  # noqa: SLF001
    out["has_video"] = bool(hv)
    for st in probe.get("streams") or []:
        if not isinstance(st, dict):
            continue
        if (st.get("codec_type") or "").lower() == "audio":
            out["has_audio"] = True
            out["audio_codec"] = str(st.get("codec_name") or "")
            break
    vst = None
    for st in probe.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "video":
            vst = st
            out["video_codec"] = str(st.get("codec_name") or "")
            break
    out["duration_sec"] = float(d or 0.0)
    out["width"], out["height"] = w, h
    if w > 0 and h > 0:
        out["aspect_ratio"] = round(float(w) / float(h), 4)
    if vst:
        afr = str(vst.get("avg_frame_rate") or "0/0")
        try:
            num, den = afr.split("/")
            fd = float(den) if float(den) != 0 else 1.0
            out["fps"] = round(float(num) / fd, 3)
        except (ValueError, ZeroDivisionError):
            out["fps"] = 0.0
    return out


def _run_ffmpeg(args: list[str], *, timeout: float) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
        tail = (r.stderr or "")[-12000:]
        return int(r.returncode or 0), tail
    except (subprocess.TimeoutExpired, OSError) as exc:
        return -1, repr(exc)


def _validate_final(
    inp: Path,
    final: Path,
    *,
    orig_meta: dict[str, Any],
) -> tuple[bool, list[str], list[str]]:
    warns: list[str] = []
    errs: list[str] = []
    pr = apq._ffprobe_json(final)  # noqa: SLF001
    if not pr:
        errs.append("final_ffprobe_failed")
        return False, warns, errs
    dur, hv = apq._duration_and_has_video(pr)  # noqa: SLF001
    if not hv:
        errs.append("final_no_video_stream")
        return False, warns, errs
    has_a = False
    for st in pr.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "audio":
            has_a = True
            break
    if not has_a:
        errs.append("final_no_audio_stream")
        return False, warns, errs
    w, h = apq._primary_video_dims(pr)  # noqa: SLF001
    ow, oh = int(orig_meta.get("width") or 0), int(orig_meta.get("height") or 0)
    if ow > 0 and oh > 0 and (w != ow or h != oh):
        errs.append(f"final_resolution_mismatch:{w}x{h}_vs_{ow}x{oh}")
        return False, warns, errs
    o_dur = float(orig_meta.get("duration_sec") or 0.0)
    f_dur = float(dur or 0.0)
    if o_dur > 0 and abs(f_dur - o_dur) > 2.0:
        errs.append(f"final_duration_drift_gt_2s:{f_dur}_vs_{o_dur}")
        return False, warns, errs
    if w > 0 and h > 0:
        ar = float(w) / float(h)
        if not (1.55 <= ar <= 1.90) or w <= h:
            warns.append(f"final_aspect_gate_marginal:ar={ar:.4f}")
    return True, warns, errs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", required=True, type=Path, help="Input long-form MP4/MOV.")
    ap.add_argument("--mode", choices=("auto", "mild", "strong", "passthrough"), default="auto")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="Allow reuse / overwrite job workdir.")
    ap.add_argument("--keep-intermediates", action="store_true")
    ap.add_argument("--job-id", default="", help="Fixed job id (default random).")
    args = ap.parse_args()

    job_id = (args.job_id or "").strip() or uuid.uuid4().hex[:16]
    workdir, _fallback = _resolve_workdir(job_id)
    if args.force:
        for p in workdir.glob("*"):
            try:
                if p.is_file():
                    p.unlink()
            except OSError:
                pass

    inp = args.video.expanduser().resolve()
    report_path = workdir / "long_real_sound_noise_cleanup_report.json"

    mode_req = str(args.mode)
    mode_used = mode_req
    if mode_req == "auto":
        mode_used = _auto_mode_for_path(inp)

    rep: dict[str, Any] = {
        "job_id": job_id,
        "status": "dry_run" if args.dry_run else "running",
        "input_video": str(inp),
        "duration_sec": 0.0,
        "has_audio": False,
        "mode_requested": mode_req,
        "mode_used": mode_used,
        "audio_goal": "preserve_real_ambient_reduce_bad_noise_events",
        "vocal_separation_used": False,
        "demucs_used": False,
        "demucs_available": _demucs_available(),
        "extracted_audio": str(workdir / "extracted_audio.wav"),
        "clean_audio": "",
        "final_video": str(workdir / "final_long_clean_real_sound.mp4"),
        "filters_used": [],
        "publish_safe": False,
        "warnings": [],
        "errors": [],
        "workdir": str(workdir),
        "ffmpeg": FFMPEG,
        "ffprobe": FFPROBE,
    }

    if not inp.is_file():
        rep["status"] = "error"
        rep["errors"].append("input_missing")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 2

    meta = _probe_input_summary(inp)
    rep["input_probe"] = meta
    rep["duration_sec"] = float(meta.get("duration_sec") or 0.0)
    rep["has_audio"] = bool(meta.get("has_audio"))

    if args.dry_run:
        rep["status"] = "dry_run"
        rep["warnings"].append("dry_run_no_ffmpeg_encode")
        if mode_used == "passthrough":
            rep["filters_used"] = ["passthrough_requested"]
        elif not rep["has_audio"]:
            rep["warnings"].append("no_audio_input_passthrough_mux_only_planned")
        else:
            rep["filters_used"] = (
                ["mild_chain"] if mode_used == "mild" else ["strong_chain", f"anlmdn_available={_ffmpeg_filter_doc_available('anlmdn')}"]
            )
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        print(json.dumps({"ok": True, "dry_run": True, "report": str(report_path)}, indent=2))
        return 0

    if not meta.get("has_video"):
        rep["status"] = "error"
        rep["errors"].append("input_no_video_stream")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 3

    if not rep["has_audio"]:
        rep["status"] = "warning"
        rep["warnings"].append("no_audio")
        rep["mode_used"] = "passthrough"
        rep["publish_safe"] = False
        rep["final_video"] = ""
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        print(json.dumps({"ok": True, "warning": "no_audio", "report": str(report_path)}, indent=2))
        return 0

    if mode_used == "passthrough":
        rep["filters_used"] = ["passthrough_copy_streams"]
        rc, tail = _run_ffmpeg(
            [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(inp), "-c", "copy", str(workdir / "final_long_clean_real_sound.mp4")],
            timeout=float(os.environ.get("LONG_RSNC_MUX_TIMEOUT_SEC", "7200")),
        )
        rep["ffmpeg_tail"] = tail[-8000:]
        if rc != 0:
            rep["status"] = "error"
            rep["errors"].append("passthrough_ffmpeg_failed")
        else:
            rep["status"] = "ok"
            ok_v, wn, er = _validate_final(inp, workdir / "final_long_clean_real_sound.mp4", orig_meta=meta)
            rep["warnings"].extend(wn)
            rep["errors"].extend(er)
            rep["publish_safe"] = ok_v and not er
            if not ok_v:
                rep["status"] = "warning"
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 0 if rep["status"] != "error" else 5

    extracted = workdir / "extracted_audio.wav"
    rc, tail = _run_ffmpeg(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(inp),
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            str(extracted),
        ],
        timeout=float(os.environ.get("LONG_RSNC_EXTRACT_TIMEOUT_SEC", "7200")),
    )
    rep["extract_ffmpeg_tail"] = tail[-6000:]
    if rc != 0 or not extracted.is_file():
        rep["status"] = "error"
        rep["errors"].append("extract_audio_failed")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 6

    use_anlmdn = mode_used == "strong" and _ffmpeg_filter_doc_available("anlmdn")
    if mode_used == "mild":
        af = MILD_AF
        clean_name = "clean_real_sound_mild.wav"
        rep["filters_used"] = ["mild_chain", "highpass+lowpass+afftdn+compressor+limiter+loudnorm"]
    else:
        af = STRONG_AF_WITH_ANLMDN if use_anlmdn else STRONG_AF_FALLBACK
        clean_name = "clean_real_sound_strong.wav"
        rep["filters_used"] = [
            "strong_chain",
            "anlmdn" if use_anlmdn else "anlmdn_skipped_fallback",
        ]
        if mode_used == "strong" and not use_anlmdn:
            rep["warnings"].append("anlmdn_unavailable_using_fallback_chain")

    clean_wav = workdir / clean_name
    rc2, tail2 = _run_ffmpeg(
        [FFMPEG, "-hide_banner", "-nostdin", "-y", "-i", str(extracted), "-af", af, str(clean_wav)],
        timeout=float(os.environ.get("LONG_RSNC_CLEAN_TIMEOUT_SEC", str(4 * 3600))),
    )
    rep["clean_ffmpeg_tail"] = tail2[-12000:]
    if rc2 != 0 or not clean_wav.is_file():
        rep["status"] = "error"
        rep["errors"].append("clean_audio_failed")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 7

    rep["clean_audio"] = str(clean_wav)
    final_mp4 = workdir / "final_long_clean_real_sound.mp4"
    rc3, tail3 = _run_ffmpeg(
        [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(inp),
            "-i",
            str(clean_wav),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-shortest",
            str(final_mp4),
        ],
        timeout=float(os.environ.get("LONG_RSNC_MUX_TIMEOUT_SEC", "7200")),
    )
    rep["mux_ffmpeg_tail"] = tail3[-12000:]
    if rc3 != 0 or not final_mp4.is_file():
        rep["status"] = "error"
        rep["errors"].append("mux_final_failed")
        report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"REPORT_JSON={report_path}")
        return 8

    ok_v, wn, er = _validate_final(inp, final_mp4, orig_meta=meta)
    rep["warnings"].extend(wn)
    rep["errors"].extend(er)
    rep["publish_safe"] = ok_v and not er
    rep["status"] = "ok" if rep["publish_safe"] else "warning"

    if not args.keep_intermediates:
        for nm in ("extracted_audio.wav", clean_name):
            try:
                (workdir / nm).unlink(missing_ok=True)
            except OSError:
                rep["warnings"].append(f"unlink_failed:{nm}")

    report_path.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"REPORT_JSON={report_path}")
    print(json.dumps({"ok": rep["status"] == "ok", "report": str(report_path), "publish_safe": rep["publish_safe"]}, indent=2))
    return 0 if rep["status"] != "error" else 9


if __name__ == "__main__":
    raise SystemExit(main())
