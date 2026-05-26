#!/usr/bin/env python3
"""Real Sound Cleanup Gate v1 — mild ambient-preserving audio + safe mux (no Demucs, no vocal split)."""
from __future__ import annotations

import argparse
import json
import os
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

LOUDNORM_HEAVY = "I=-14:LRA=11:TP=-1.5"
LOUDNORM_MILD = "I=-18:LRA=11:TP=-2.0"
LOUDNORM_TARGET = LOUDNORM_HEAVY
AUDIO_AF_CLEANUP = f"highpass=f=80,lowpass=f=14000,afftdn=nf=-22,loudnorm={LOUDNORM_HEAVY}"
AUDIO_AF_VERY_LIGHT = f"highpass=f=60,loudnorm={LOUDNORM_MILD}"
VIDEO_VF_LONG = "fps=30,format=yuv420p"

VALID_PRESETS = frozenset({"preserve", "very_light", "cleanup"})


def _normalize_preset(raw: str | None, *, channel: str) -> str:
    d = (raw or "").strip().lower()
    if d in VALID_PRESETS:
        return d
    return "very_light" if str(channel).strip().lower() == "long" else "cleanup"


def _mean_volume_db(stderr_text: str) -> float | None:
    for line in (stderr_text or "").splitlines():
        s = line.strip()
        if s.startswith("mean_volume:"):
            try:
                return float(s.split(":", 1)[1].strip().split()[0])
            except (ValueError, IndexError):
                return None
    return None


def _volumedetect_mean_db(path: Path, *, timeout_sec: float) -> float | None:
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-i",
        str(path),
        "-t",
        "120",
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=float(timeout_sec), check=False)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return _mean_volume_db(r.stderr or "")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _report_dir() -> Path:
    primary = get_sv_cache(verbose=False) / "audio_clean" / "reports"
    try:
        primary.mkdir(parents=True, exist_ok=True)
        probe = primary / ".write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return primary
    except OSError:
        pass
    fb = Path.home() / "StateVerge" / "data" / "audio_clean" / "reports"
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _probe_media(path: Path) -> dict[str, Any]:
    pr = apq._ffprobe_json(path)  # noqa: SLF001
    out: dict[str, Any] = {
        "duration_sec": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "video_codec": "",
        "audio_codec": "",
        "has_video": False,
        "has_audio": False,
    }
    if not pr:
        return out
    d, hv = apq._duration_and_has_video(pr)  # noqa: SLF001
    w, h = apq._primary_video_dims(pr)  # noqa: SLF001
    out["has_video"] = bool(hv)
    out["duration_sec"] = float(d or 0.0)
    out["width"], out["height"] = w, h
    for st in pr.get("streams") or []:
        if not isinstance(st, dict):
            continue
        if (st.get("codec_type") or "").lower() == "video" and not out["video_codec"]:
            out["video_codec"] = str(st.get("codec_name") or "")
        if (st.get("codec_type") or "").lower() == "audio":
            out["has_audio"] = True
            out["audio_codec"] = str(st.get("codec_name") or "")
    vst = None
    for st in pr.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "video":
            vst = st
            break
    if vst:
        afr = str(vst.get("avg_frame_rate") or "0/0")
        try:
            num, den = afr.split("/")
            fd = float(den) if float(den) != 0 else 1.0
            out["fps"] = round(float(num) / fd, 3)
        except (ValueError, ZeroDivisionError):
            out["fps"] = 0.0
    return out


def _write_report(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def run_cleanup_encode(
    inp: Path,
    outp: Path,
    *,
    channel: str,
    timeout_sec: float,
    report_path: Path,
    preset: str = "cleanup",
) -> dict[str, Any]:
    """Run ffmpeg; write JSON to ``report_path``. Returns report body (mutated)."""
    inp = inp.expanduser().resolve()
    outp = outp.expanduser().resolve()
    ch = str(channel or "long").strip().lower()
    ps = _normalize_preset(preset, channel=ch)
    loudnorm_label = "none" if ps == "preserve" else (LOUDNORM_MILD if ps == "very_light" else LOUDNORM_HEAVY)
    base: dict[str, Any] = {
        "input_path": str(inp),
        "output_path": str(outp),
        "status": "running",
        "ffmpeg_returncode": -1,
        "duration_input_sec": 0.0,
        "duration_output_sec": 0.0,
        "width": 0,
        "height": 0,
        "fps": 30.0 if ch == "long" else 0.0,
        "audio_codec": "aac",
        "video_codec": "h264",
        "loudnorm_target": loudnorm_label,
        "preset": ps,
        "filters_used": [],
        "warnings": [],
        "created_at": _utc_iso(),
        "demucs_used": False,
        "vocal_separation_used": False,
        "fallback_recommended": False,
        "channel": ch,
        "cleanup_implies_upload_ok": False,
    }

    pin = _probe_media(inp)
    base["duration_input_sec"] = float(pin.get("duration_sec") or 0.0)
    base["width"] = int(pin.get("width") or 0)
    base["height"] = int(pin.get("height") or 0)
    if not pin.get("has_video"):
        base["status"] = "failed"
        base["warnings"].append("input_no_video_stream")
        base["fallback_recommended"] = True
        _write_report(report_path, base)
        return base
    if not pin.get("has_audio"):
        base["status"] = "failed"
        base["warnings"].append("input_no_audio_stream")
        base["fallback_recommended"] = True
        _write_report(report_path, base)
        return base

    if ps == "preserve":
        base["filters_used"] = ["mux:stream_copy_faststart"]
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(inp),
            "-map",
            "0",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(outp),
        ]
    elif ch == "long":
        audio_af = AUDIO_AF_VERY_LIGHT if ps == "very_light" else AUDIO_AF_CLEANUP
        base["filters_used"] = [f"video:{VIDEO_VF_LONG}", f"audio:{audio_af}"]
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(inp),
            "-vf",
            VIDEO_VF_LONG,
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-af",
            audio_af,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(outp),
        ]
    else:
        audio_af = AUDIO_AF_VERY_LIGHT if ps == "very_light" else AUDIO_AF_CLEANUP
        base["filters_used"] = ["video:copy", f"audio:{audio_af}"]
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(inp),
            "-c:v",
            "copy",
            "-af",
            audio_af,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(outp),
        ]

    outp.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=float(timeout_sec),
            check=False,
        )
    except subprocess.TimeoutExpired:
        base["status"] = "failed"
        base["ffmpeg_returncode"] = -124
        base["warnings"].append("ffmpeg_timeout")
        base["fallback_recommended"] = True
        try:
            if outp.is_file():
                outp.unlink()
        except OSError:
            pass
        _write_report(report_path, base)
        return base
    except OSError as exc:
        base["status"] = "failed"
        base["ffmpeg_returncode"] = -1
        base["warnings"].append(f"ffmpeg_spawn_error:{exc!r}")
        base["fallback_recommended"] = True
        _write_report(report_path, base)
        return base

    base["ffmpeg_stderr_tail"] = (r.stderr or "")[-12000:]
    base["ffmpeg_returncode"] = int(r.returncode or 0)
    if r.returncode != 0 or not outp.is_file() or outp.stat().st_size < 4096:
        base["status"] = "failed"
        base["fallback_recommended"] = True
        try:
            if outp.is_file():
                outp.unlink()
        except OSError:
            pass
        _write_report(report_path, base)
        return base

    pout = _probe_media(outp)
    base["duration_output_sec"] = float(pout.get("duration_sec") or 0.0)
    ow, oh = int(pout.get("width") or 0), int(pout.get("height") or 0)
    if ow > 0:
        base["width"] = ow
    if oh > 0:
        base["height"] = oh
    base["video_codec"] = str(pout.get("video_codec") or ("h264" if ch == "long" else pin.get("video_codec")))
    base["audio_codec"] = str(pout.get("audio_codec") or "aac")
    if ch == "long":
        base["fps"] = 30.0
    else:
        base["fps"] = float(pout.get("fps") or pin.get("fps") or 0.0)
    di = base["duration_input_sec"]
    do = base["duration_output_sec"]
    if di > 0 and do > 0 and abs(do - di) > 2.0:
        base["warnings"].append(f"duration_drift_seconds:{abs(do - di):.3f}")
    if pin.get("has_audio") and not pout.get("has_audio"):
        base["status"] = "failed"
        base["warnings"].append("post_cleanup_missing_audio_stream")
        base["fallback_recommended"] = True
        try:
            if outp.is_file():
                outp.unlink()
        except OSError:
            pass
        _write_report(report_path, base)
        return base
    if ps in ("very_light", "cleanup"):
        vd_timeout = min(300.0, max(30.0, float(timeout_sec) * 0.01))
        mv = _volumedetect_mean_db(outp, timeout_sec=vd_timeout)
        if mv is not None and mv < -55.0:
            base["status"] = "failed"
            base["warnings"].append(f"post_cleanup_audio_anomaly_mean_volume_db:{mv:.2f}")
            base["fallback_recommended"] = True
            try:
                if outp.is_file():
                    outp.unlink()
            except OSError:
                pass
            _write_report(report_path, base)
            return base
    base["status"] = "ok"
    _write_report(report_path, base)
    return base


def run_real_sound_cleanup(
    video: Path | str,
    channel: str,
    *,
    mode: str = "auto",
    dry_run: bool = False,
    keep_intermediates: bool = False,
    job_id: str = "",
    preset: str | None = None,
) -> tuple[Path, dict[str, Any] | None]:
    """Hook for ``auto_publish_queue`` / Shorts worker: produce ``*_clean_real_sound_*.mp4`` + sidecar JSON."""
    _ = mode, keep_intermediates
    vid = Path(video).expanduser().resolve()
    ch = str(channel or "long").strip().lower()
    env_key = "NYC_LONG_REAL_SOUND_PRESET" if ch == "long" else "SHORTS_REAL_SOUND_PRESET"
    env_default = "very_light" if ch == "long" else "cleanup"
    pr = _normalize_preset(preset or os.environ.get(env_key) or env_default, channel=ch)
    stem = vid.stem or "input"
    report_path = _report_dir() / f"{stem}_real_sound_cleanup_report.json"

    if "clean_real_sound" in vid.name.lower():
        rep = {
            "status": "skipped_already_clean",
            "publish_safe": True,
            "final_video": str(vid),
            "report_path": str(report_path),
            "fallback_to_original": False,
            "mode_used": "v1_skip",
            "preset": pr,
            "cfr_reencoded": False,
            "video_copy_used": True,
            "video_copy_reason": "already_clean_real_sound_filename",
            "demucs_used": False,
            "vocal_separation_used": False,
            "warnings": [],
            "cleanup_implies_upload_ok": False,
        }
        return vid, rep

    jid = (job_id or "").strip() or uuid.uuid4().hex[:12]
    out = vid.parent / f"{stem}_clean_real_sound_{jid}.mp4"
    n = 0
    while out.is_file():
        n += 1
        out = vid.parent / f"{stem}_clean_real_sound_{jid}_{n}.mp4"

    if dry_run:
        rep = {
            "status": "dry_run",
            "publish_safe": False,
            "final_video": str(vid),
            "would_output": str(out),
            "report_path": str(report_path),
            "fallback_to_original": False,
            "mode_used": f"preset_{pr}",
            "preset": pr,
            "cfr_reencoded": ch == "long" and pr != "preserve",
            "demucs_used": False,
            "vocal_separation_used": False,
            "warnings": [],
            "cleanup_implies_upload_ok": False,
        }
        return vid, rep

    timeout_sec = float(os.environ.get("REAL_SOUND_CLEANUP_TIMEOUT_SEC", str(24 * 3600)))
    body = run_cleanup_encode(
        vid, out, channel=ch, timeout_sec=timeout_sec, report_path=report_path, preset=pr
    )
    ok = body.get("status") == "ok"
    final = out if ok else vid
    rep = {
        "status": str(body.get("status") or ("ok" if ok else "error")),
        "publish_safe": bool(ok),
        "final_video": str(final),
        "report_path": str(report_path),
        "fallback_to_original": not ok,
        "mode_used": f"preset_{pr}",
        "preset": pr,
        "cfr_reencoded": ch == "long" and pr != "preserve",
        "video_copy_used": ch != "long" or pr == "preserve",
        "video_copy_reason": (
            "shorts_preserve_video_bitstream"
            if ch != "long"
            else ("long_stream_copy_mux" if pr == "preserve" else "long_cfr30_reencode")
        ),
        "demucs_used": False,
        "vocal_separation_used": False,
        "warnings": list(body.get("warnings") or []),
        "ffmpeg_returncode": body.get("ffmpeg_returncode"),
        "cleanup_implies_upload_ok": False,
    }
    return final, rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument(
        "--encode-timeout-sec",
        type=float,
        default=float(os.environ.get("REAL_SOUND_CLEANUP_TIMEOUT_SEC", str(24 * 3600))),
    )
    ap.add_argument(
        "--preset",
        choices=sorted(VALID_PRESETS),
        default="very_light",
        help="preserve=remux copy; very_light=60Hz highpass + mild loudnorm; cleanup=afftdn + stronger chain.",
    )
    args = ap.parse_args()

    inp = args.input.expanduser().resolve()
    outp = args.output.expanduser().resolve()
    stem = inp.stem or "input"
    report_path = _report_dir() / f"{stem}_real_sound_cleanup_report.json"

    if "clean_real_sound" not in outp.name.lower():
        body = {
            "input_path": str(inp),
            "output_path": str(outp),
            "status": "failed",
            "ffmpeg_returncode": -1,
            "duration_input_sec": 0.0,
            "duration_output_sec": 0.0,
            "width": 0,
            "height": 0,
            "fps": 0.0,
            "audio_codec": "",
            "video_codec": "",
            "loudnorm_target": LOUDNORM_HEAVY,
            "filters_used": [],
            "warnings": ["output_filename_must_contain_clean_real_sound"],
            "created_at": _utc_iso(),
            "demucs_used": False,
            "vocal_separation_used": False,
            "fallback_recommended": True,
        }
        try:
            _write_report(report_path, body)
        except OSError as exc:
            print(f"report_write_failed:{exc!r}", file=sys.stderr)
        print("REAL_SOUND_CLEANUP_STATUS=failed")
        print("REAL_SOUND_CLEANUP_OUTPUT=")
        print(f"REAL_SOUND_CLEANUP_REPORT={report_path}")
        print(json.dumps({"ok": False, "report": str(report_path)}, indent=2))
        return 1

    body = run_cleanup_encode(
        inp,
        outp,
        channel="long",
        timeout_sec=float(args.encode_timeout_sec),
        report_path=report_path,
        preset=str(args.preset),
    )
    ok = body.get("status") == "ok"
    print(f"REAL_SOUND_CLEANUP_STATUS={body.get('status', 'failed')}")
    print(f"REAL_SOUND_CLEANUP_OUTPUT={str(outp) if ok else ''}")
    print(f"REAL_SOUND_CLEANUP_REPORT={report_path}")
    print(json.dumps({"ok": ok, "report": str(report_path)}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
