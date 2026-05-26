#!/usr/bin/env python3
"""StateVerge background separation repair chain v1 (no DaVinci API).

Orchestrates WAV extract, stem separation (repo pipeline or Demucs), light EQ,
and CFR H.264 + AAC remux. Fail-open: always writes a JSON report and exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PIPELINE_SCRIPT = _REPO_ROOT / "scripts" / "audio_separation_pipeline.py"

_SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
_FALLBACK_SV_CACHE = Path.home() / "StateVerge" / "_storage_fallback" / "sv_cache"

_STEM_PRIORITY = ("no_vocals", "accompaniment", "other", "ambience")

_FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _which_ffmpeg() -> str | None:
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    p = os.environ.get("FFMPEG_BIN")
    return p if p and Path(p).is_file() else None


def _resolve_sv_cache_base() -> Path:
    """Prefer ``/Volumes/SV_CACHE`` when usable; else StateVerge fallback tree."""
    for cand in (_SV_CACHE_PRIMARY, _FALLBACK_SV_CACHE):
        try:
            root = cand
            probe = root / "audio_separated" / ".write_probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)  # type: ignore[arg-type]
            return root
        except OSError:
            continue
    fb = _FALLBACK_SV_CACHE
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _output_mp4_basename(output_arg: str, input_path: Path) -> str:
    p = Path(output_arg).expanduser()
    name = p.name
    if name.lower().endswith(".mp4"):
        return name
    return f"{input_path.stem}_bg_clean.mp4"


def _run_cmd(
    cmd: list[str],
    *,
    timeout: float,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except FileNotFoundError:
        return 127, "executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-8000:]
    return r.returncode, tail


def _ffmpeg_summary_from_cmd(cmd: list[str]) -> str:
    if not cmd:
        return "ffmpeg: (empty)"
    if Path(cmd[0]).name == "ffmpeg" or "ffmpeg" in cmd[0]:
        parts: list[str] = ["ffmpeg"]
        i = 0
        while i < len(cmd):
            if cmd[i] == "-i" and i + 1 < len(cmd):
                parts.append("-i")
                parts.append("<path>")
                i += 2
                continue
            if cmd[i] in ("-af", "-filter:a") and i + 1 < len(cmd):
                parts.append(cmd[i])
                parts.append("<afilter_chain>")
                i += 2
                continue
            if cmd[i] == "-vf" and i + 1 < len(cmd):
                parts.append("-vf")
                parts.append("<vf_chain>")
                i += 2
                continue
            if cmd[i].startswith("-"):
                parts.append(cmd[i])
            i += 1
        return " ".join(parts)
    if "demucs" in cmd[0] or (len(cmd) > 1 and cmd[1] == "-m" and "demucs" in cmd):
        return "demucs: " + " ".join(x for x in cmd[1:8] if not str(x).startswith("/"))
    if "audio_separation_pipeline.py" in " ".join(cmd):
        return "python: audio_separation_pipeline.py <args>"
    return cmd[0] + ": <args>"


def _find_stem_wav(search_root: Path) -> tuple[str | None, Path | None]:
    """Pick stem by filename/dir token priority (case-insensitive)."""
    skip_dir_parts = {"debug", "__pycache__", ".git"}
    wavs: list[Path] = []
    try:
        for p in search_root.rglob("*.wav"):
            if not p.is_file():
                continue
            if any(part.lower() in skip_dir_parts for part in p.parts):
                continue
            wavs.append(p)
    except OSError:
        return None, None

    def stem_matches(path: Path, token: str) -> bool:
        t = token.lower()
        if path.stem.lower() == t:
            return True
        return any(part.lower() == t for part in path.parts)

    for token in _STEM_PRIORITY:
        hits = [p for p in wavs if stem_matches(p, token)]
        if hits:
            hits.sort(key=lambda x: (len(x.parts), str(x)))
            return token, hits[0]
    return None, None


def _read_pipeline_no_vocals(pipeline_out_dir: Path) -> Path | None:
    rep = pipeline_out_dir / "audio_separation_report.json"
    if not rep.is_file():
        return None
    try:
        data = json.loads(rep.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    p = data.get("no_vocals_path")
    if not p:
        return None
    path = Path(str(p))
    return path if path.is_file() else None


def _try_separation_pipeline(
    video_in: Path,
    pipeline_root: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[str | None, Path | None, bool]:
    if not _PIPELINE_SCRIPT.is_file():
        warnings.append("audio_separation_pipeline_missing")
        return None, None, False

    out_root = pipeline_root
    out_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(_PIPELINE_SCRIPT),
        "--input",
        str(video_in),
        "--output-root",
        str(out_root),
        "--mode",
        "no_vocals",
        "--fps",
        "30",
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0:
        errors.append(f"pipeline_exit_{rc}")
        if tail.strip():
            warnings.append("pipeline_stderr_tail:" + tail.strip()[-800:])
        return None, None, True

    stem_dir_name = video_in.stem
    pipeline_track_dir = out_root / stem_dir_name
    nv = _read_pipeline_no_vocals(pipeline_track_dir)
    if nv and nv.is_file():
        return "no_vocals", nv, True

    tok, path = _find_stem_wav(pipeline_track_dir)
    if path:
        return tok, path, True

    errors.append("pipeline_ok_but_no_stem_wav")
    return None, None, True


def _try_demucs(
    wav_in: Path,
    demucs_parent: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    warnings: list[str],
    errors: list[str],
) -> tuple[str | None, Path | None]:
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "--two-stems",
        "vocals",
        "--device",
        "cpu",
        "--segment",
        "7",
        "--shifts",
        "0",
        "-n",
        "htdemucs",
        "-o",
        str(demucs_parent),
        str(wav_in),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0:
        errors.append(f"demucs_exit_{rc}")
        if "No module named" in tail or "No module named" in (tail or ""):
            errors.append("demucs_module_not_installed")
        return None, None

    tok, path = _find_stem_wav(demucs_parent)
    if path:
        return tok, path
    errors.append("demucs_ok_but_no_stem_wav")
    return None, None


def _extract_wav(
    video_in: Path,
    wav_out: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_in),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(wav_out),
    ]
    summary.append(_ffmpeg_summary_from_cmd(cmd))
    rc, _tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0 or not wav_out.is_file() or wav_out.stat().st_size < 1024:
        errors.append("extract_wav_failed")
        return False
    return True


def _eq_and_loudnorm_wav(
    stem_wav: Path,
    out_wav: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    # Highpass 90 Hz; dips at 3.5 kHz / 6 kHz; loudnorm integrates I/TP targets.
    af = (
        "highpass=f=90,"
        "equalizer=f=3500:width_type=o:width=2:g=-3,"
        "equalizer=f=6000:width_type=o:width=2:g=-3,"
        "loudnorm=I=-14:TP=-1.5:LRA=11"
    )
    cmd = [
        _FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(stem_wav),
        "-af",
        af,
        str(out_wav),
    ]
    summary.append("ffmpeg: -af highpass+equalizer(x2)+loudnorm(I=-14,TP=-1.5)")
    rc, _tail = _run_cmd(cmd, timeout=timeout_sec)
    if rc != 0 or not out_wav.is_file() or out_wav.stat().st_size < 1024:
        errors.append("eq_process_failed")
        return False
    return True


def _mux_cfr_h264_aac(
    video_in: Path,
    audio_wav: Path,
    mp4_out: Path,
    *,
    timeout_sec: float,
    summary: list[str],
    errors: list[str],
) -> bool:
    mp4_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = mp4_out.parent / f"{mp4_out.stem}._tmp_{os.getpid()}_{uuid.uuid4().hex}{mp4_out.suffix}"
    vf = "fps=30,format=yuv420p"
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
            _FFMPEG,
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
            "-r",
            "30",
            "-vsync",
            "cfr",
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
        summary.append(f"ffmpeg: mux CFR30 {label}+aac (no -c:v copy)")
        rc, _tail = _run_cmd(cmd, timeout=timeout_sec)
        if rc == 0 and tmp.is_file() and tmp.stat().st_size > 1024:
            try:
                tmp.replace(mp4_out)
            except OSError:
                shutil.copy2(tmp, mp4_out)
                try:
                    tmp.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
            return True
        try:
            if tmp.is_file():
                tmp.unlink()
        except OSError:
            pass
    errors.append("mux_cfr_h264_failed")
    return False


def _symlink_or_copy(src: Path, dst: Path, warnings: list[str]) -> None:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        warnings.append(f"user_output_dir_mkdir_failed:{e}")
        return
    try:
        if dst.is_symlink() or dst.is_file():
            dst.unlink()
    except OSError:
        pass
    try:
        rel = os.path.relpath(src, start=dst.parent)
        os.symlink(rel, dst)
    except OSError as e:
        warnings.append(f"user_output_symlink_failed:{e};using_copy")
        try:
            shutil.copy2(src, dst)
        except OSError as e2:
            warnings.append(f"user_output_copy_failed:{e2}")


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="Path to source video.")
    ap.add_argument(
        "--output",
        required=True,
        help="Desired clean MP4 path (basename used for canonical deliverable; file linked/copied when possible).",
    )
    args = ap.parse_args()

    cache_base = _resolve_sv_cache_base()
    reports_dir = cache_base / "audio_separated" / "reports"
    bg_clean_dir = cache_base / "audio_separated" / "background_clean"
    report_path = reports_dir / "background_separation_report.json"

    video_in = Path(args.input).expanduser().resolve()
    user_out = Path(args.output).expanduser().resolve()
    basename = _output_mp4_basename(args.output, video_in)
    canonical_out = bg_clean_dir / basename

    payload: dict[str, Any] = {
        "input": str(video_in),
        "output": str(canonical_out),
        "stem_used": None,
        "separation_backend": "none",
        "ffmpeg_commands_summary": [],
        "warnings": [],
        "errors": [],
        "timestamp": _utc_now_iso(),
        "success": False,
    }

    work_root: Path | None = None
    try:
        if not _which_ffmpeg():
            payload["errors"].append("ffmpeg_not_found")
            payload["warnings"].append("set_FFMPEG_BIN_or_install_ffmpeg")
            return 0

        if not video_in.is_file():
            payload["errors"].append("input_missing_or_not_file")
            return 0

        work_root = cache_base / "audio_separated" / "tmp" / f"bg_repair_{video_in.stem}_{uuid.uuid4().hex}"
        work_root.mkdir(parents=True, exist_ok=True)
        wav_path = work_root / "original.wav"
        eq_wav = work_root / "stem_eq.wav"
        pipeline_bundle = work_root / "pipeline_bundle"
        demucs_parent = work_root / "demucs_out"

        timeout_extract = min(14400.0, 3600.0)
        timeout_demucs = 14400.0
        timeout_mux = 14400.0
        timeout_eq = min(7200.0, 3600.0)

        if not _extract_wav(
            video_in,
            wav_path,
            timeout_sec=timeout_extract,
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            return 0

        stem_token: str | None = None
        stem_path: Path | None = None
        pipeline_attempted = False

        if _PIPELINE_SCRIPT.is_file():
            st, sp, attempted = _try_separation_pipeline(
                video_in,
                pipeline_bundle,
                timeout_sec=timeout_demucs,
                summary=payload["ffmpeg_commands_summary"],
                warnings=payload["warnings"],
                errors=payload["errors"],
            )
            pipeline_attempted = attempted
            if sp:
                stem_token, stem_path = st, sp
                payload["separation_backend"] = "pipeline"

        if not stem_path:
            if pipeline_attempted:
                payload["warnings"].append("pipeline_failed_trying_demucs")
            st, sp = _try_demucs(
                wav_path,
                demucs_parent,
                timeout_sec=timeout_demucs,
                summary=payload["ffmpeg_commands_summary"],
                warnings=payload["warnings"],
                errors=payload["errors"],
            )
            if sp:
                stem_token, stem_path = st, sp
                payload["separation_backend"] = "demucs"
                payload["errors"] = [e for e in payload["errors"] if not str(e).startswith("pipeline_exit_")]
            else:
                payload["separation_backend"] = "none"
                payload["errors"].append("separation_failed_no_backend_stem")
                return 0

        payload["stem_used"] = stem_token

        if not _eq_and_loudnorm_wav(
            stem_path,
            eq_wav,
            timeout_sec=timeout_eq,
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            return 0

        if not _mux_cfr_h264_aac(
            video_in,
            eq_wav,
            canonical_out,
            timeout_sec=timeout_mux,
            summary=payload["ffmpeg_commands_summary"],
            errors=payload["errors"],
        ):
            return 0

        if user_out.resolve() != canonical_out.resolve():
            _symlink_or_copy(canonical_out, user_out, payload["warnings"])
            payload["warnings"].append(f"user_output_target:{user_out}")

        payload["success"] = True

    except Exception as e:
        payload["errors"].append(f"unexpected:{e}")
        payload["warnings"].append("traceback_tail:" + traceback.format_exc()[-4000:])
    finally:
        _write_report(report_path, payload)
        if work_root is not None:
            shutil.rmtree(work_root, ignore_errors=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
