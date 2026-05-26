#!/usr/bin/env python3
"""StateVerge drive ambience-only chain v1 — city bed / no dialogue path.

Thin orchestrator: ``separate_bad_background_v1`` stem pass, optional gentle
``stateverge_audio_repair_engine_v1``. Fail-open: always writes JSON report,
exits 0. Does not invoke auto_publish, upload, or DaVinci bridges.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SEPARATE_SCRIPT = _SCRIPT_DIR / "separate_bad_background_v1.py"
_REPAIR_SCRIPT = _SCRIPT_DIR / "stateverge_audio_repair_engine_v1.py"

_SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
_FALLBACK_SV_CACHE = Path.home() / "StateVerge" / "_storage_fallback" / "sv_cache"

_REPAIR_STRENGTH = 0.38
_SUBPROCESS_TIMEOUT = 14400.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_stamp_filename() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _resolve_sv_cache_base() -> Path:
    for cand in (_SV_CACHE_PRIMARY, _FALLBACK_SV_CACHE):
        try:
            probe = cand / "audio_drive_ambience" / ".write_probe"
            probe.parent.mkdir(parents=True, exist_ok=True)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)  # type: ignore[arg-type]
            return cand
        except OSError:
            continue
    fb = _FALLBACK_SV_CACHE
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _run_py_script(script: Path, argv: list[str]) -> tuple[int, str]:
    cmd = [sys.executable, str(script), *argv]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        return 127, "python_executable_not_found"
    except subprocess.TimeoutExpired:
        return 124, "subprocess_timeout"
    tail = ((r.stderr or "") + "\n" + (r.stdout or ""))[-12000:]
    return r.returncode, tail


def _symlink_or_copy(src: Path, dst: Path, warnings: list[str]) -> None:
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        warnings.append(f"output_dir_mkdir_failed:{e}")
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
        warnings.append(f"output_symlink_failed:{e};using_copy")
        try:
            shutil.copy2(src, dst)
        except OSError as e2:
            warnings.append(f"output_copy_failed:{e2}")


def _mp4_usable(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 1024
    except OSError:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input-video", required=True, help="Source video path.")
    ap.add_argument(
        "--output-video",
        default=None,
        help="Optional deliverable path; symlink/copy from final when set.",
    )
    ap.add_argument(
        "--skip-repair",
        action="store_true",
        help="Run background separation only (no repair engine pass).",
    )
    ap.add_argument(
        "--calm-air",
        action="store_true",
        help="Pass --lowpass-14k to the repair engine (softer high-frequency air).",
    )
    args = ap.parse_args()

    cache_base = _resolve_sv_cache_base()
    reports_dir = cache_base / "audio_drive_ambience" / "reports"
    report_path = reports_dir / "drive_ambience_only_report.json"
    work_dir = cache_base / "audio_drive_ambience" / "work"

    video_in = Path(args.input_video).expanduser().resolve()
    stem_name = f"{video_in.stem}_stem.mp4"
    intermediate_mp4 = work_dir / stem_name

    canonical_final = (
        cache_base / "audio_drive_ambience" / f"clean_drive_ambience_{_utc_stamp_filename()}.mp4"
    )
    user_out = Path(args.output_video).expanduser().resolve() if args.output_video else None
    final_deliverable = user_out if user_out is not None else canonical_final

    steps: list[dict[str, Any]] = []
    warnings: list[str] = []
    errors: list[str] = []

    payload: dict[str, Any] = {
        "input_video": str(video_in),
        "intermediate_video": str(intermediate_mp4),
        "output_video": str(final_deliverable),
        "steps": steps,
        "repair_strength": None if args.skip_repair else _REPAIR_STRENGTH,
        "warnings": warnings,
        "errors": errors,
        "success": False,
        "timestamp": _utc_now_iso(),
    }

    try:
        if not _SEPARATE_SCRIPT.is_file():
            errors.append("separate_script_missing")
            payload["warnings"].append(str(_SEPARATE_SCRIPT))
            _write_report(report_path, payload)
            return 0

        if not video_in.is_file():
            errors.append("input_missing_or_not_file")
            _write_report(report_path, payload)
            return 0

        work_dir.mkdir(parents=True, exist_ok=True)

        sep_argv = ["--input", str(video_in), "--output", str(intermediate_mp4)]
        rc_sep, tail_sep = _run_py_script(_SEPARATE_SCRIPT, sep_argv)
        steps.append(
            {
                "step": "separate_bad_background_v1",
                "exit_code": rc_sep,
                "stderr_tail": tail_sep[-4000:] if rc_sep != 0 else "",
            }
        )
        if rc_sep != 0:
            errors.append(f"separate_nonzero_exit:{rc_sep}")
        if not _mp4_usable(intermediate_mp4):
            errors.append("intermediate_missing_or_too_small_after_separate")
            if rc_sep == 0:
                warnings.append("separate_reported_zero_but_no_intermediate_file")
            _write_report(report_path, payload)
            return 0

        if args.skip_repair:
            _symlink_or_copy(intermediate_mp4, final_deliverable, warnings)
            payload["success"] = _mp4_usable(final_deliverable)
            if not payload["success"]:
                errors.append("final_deliverable_missing_after_skip_repair_copy")
            _write_report(report_path, payload)
            return 0

        if not _REPAIR_SCRIPT.is_file():
            errors.append("repair_script_missing")
            payload["warnings"].append(str(_REPAIR_SCRIPT))
            _write_report(report_path, payload)
            return 0

        repair_argv: list[str] = [
            "--input-video",
            str(intermediate_mp4),
            "--output-video",
            str(final_deliverable),
            "--repair-strength",
            str(_REPAIR_STRENGTH),
        ]
        if args.calm_air:
            repair_argv.append("--lowpass-14k")

        rc_rep, tail_rep = _run_py_script(_REPAIR_SCRIPT, repair_argv)
        steps.append(
            {
                "step": "stateverge_audio_repair_engine_v1",
                "exit_code": rc_rep,
                "stderr_tail": tail_rep[-4000:] if rc_rep != 0 else "",
            }
        )
        if rc_rep != 0:
            errors.append(f"repair_nonzero_exit:{rc_rep}")
        if not _mp4_usable(final_deliverable):
            errors.append("final_deliverable_missing_or_too_small_after_repair")
            if rc_rep == 0:
                warnings.append("repair_reported_zero_but_no_final_file")
        payload["success"] = _mp4_usable(final_deliverable)

        _write_report(report_path, payload)
        return 0

    except Exception as e:  # noqa: BLE001 — fail-open umbrella
        errors.append(f"orchestrator_exception:{type(e).__name__}:{e}")
        warnings.append(traceback.format_exc()[-8000:])
        payload["success"] = False
        _write_report(report_path, payload)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
