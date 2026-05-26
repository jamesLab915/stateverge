#!/usr/bin/env python3
"""Orchestrate 3h ferry long master (May 15 prefer) + suno music-first mix. Encode only, no upload."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_NYC = Path(__file__).resolve().parent
_SCRIPTS = _NYC.parent
_REPO = _SCRIPTS.parent
for _p in (_SCRIPTS, _NYC, _REPO / "src"):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from nyc_ferry_prefer_date_v1 import ENV_PREFER_SOURCE_DATE, resolve_prefer_dates  # noqa: E402

LOG_PATH = Path.home() / "StateVerge_Control_Center" / "logs" / "ferry_3h_souno_run.json"
# suno inbox includes all subdirs (bounded recursive scan in schedule helpers).
SUNO_ROOT = Path("/Volumes/SV_CACHE/inbox/suno")
SOUNO_ROOT = Path("/Volumes/SV_CACHE/inbox/souno")
MIN_TOTAL_SEC = 10800.0


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _py() -> str:
    venv = _REPO / ".venv_audio" / "bin" / "python3"
    if venv.is_file():
        return str(venv)
    return sys.executable or "python3"


def _run(cmd: list[str], *, timeout: float | None = None) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            cmd,
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(r.returncode or 0), r.stdout or "", r.stderr or ""
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError as exc:
        return 125, "", repr(exc)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prefer-date", default="2026-05-15")
    ap.add_argument("--dry-run", action="store_true", help="Plan + master dry-run only; no ffmpeg encode/mix.")
    ap.add_argument("--force-schedule", action="store_true")
    ap.add_argument("--skip-mix", action="store_true")
    ap.add_argument("--encode-timeout-sec", type=float, default=float(os.environ.get("NYC_LONG_MASTER_ENCODE_TIMEOUT_SEC", str(10 * 3600))))
    args = ap.parse_args()

    prefer_iso = str(args.prefer_date).strip()
    os.environ[ENV_PREFER_SOURCE_DATE] = prefer_iso
    prefer_dates = resolve_prefer_dates(prefer_iso)

    py = _py()
    ts = _utc_stamp()
    log: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "prefer_source_dates": [d.isoformat() for d in prefer_dates],
        "suno_root": str(SUNO_ROOT),
        "souno_root": str(SOUNO_ROOT),
        "dry_run": bool(args.dry_run),
        "steps": {},
    }

    plan_cmd = [
        py,
        str(_NYC / "plan_nyc_long_master_allowed_raw_dry_run.py"),
        "--source-type",
        "ferry",
        "--min-total-sec",
        str(MIN_TOTAL_SEC),
        "--prefer-date",
        prefer_iso,
        "--may15-ferry-only",
    ]
    rc, out, err = _run(plan_cmd, timeout=900)
    log["steps"]["plan"] = {"returncode": rc, "stdout_tail": out[-6000:], "stderr_tail": err[-2000:]}
    ferry_found = 0
    ferry_ready = False
    for line in out.splitlines():
        if line.startswith("FERRY_MAY15_FILES_FOUND="):
            ferry_found = int(line.split("=", 1)[1].strip() or "0")
        if line.startswith("FERRY_MAY15_PRIORITY_READY="):
            ferry_ready = line.split("=", 1)[1].strip().lower() == "true"
    if rc != 0:
        log["ok"] = False
        log["block_reason"] = "plan_failed"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=false")
        print(f"BLOCK_REASON=plan_failed")
        print(f"FERRY_MAY15_FILES_FOUND={ferry_found}")
        print(f"FERRY_MAY15_PRIORITY_READY=false")
        return 2

    master_cmd = [
        py,
        str(_NYC / "create_next_long_master.py"),
        "--schedule-kind",
        "3h",
        "--source-type",
        "ferry",
        "--prefer-date",
        prefer_iso,
        "--min-total-sec",
        str(MIN_TOTAL_SEC),
    ]
    if args.force_schedule:
        master_cmd.append("--force-schedule")
    if args.dry_run:
        master_cmd.append("--dry-run")

    rc_m, out_m, err_m = _run(master_cmd, timeout=900 if args.dry_run else None)
    log["steps"]["master"] = {"returncode": rc_m, "stdout_tail": out_m[-8000:], "stderr_tail": err_m[-2000:]}
    for line in out_m.splitlines():
        if line.startswith("FERRY_MAY15_FILES_FOUND="):
            ferry_found = int(line.split("=", 1)[1].strip() or "0")
        if line.startswith("FERRY_MAY15_PRIORITY_READY="):
            ferry_ready = line.split("=", 1)[1].strip().lower() == "true"

    if rc_m != 0:
        log["ok"] = False
        log["block_reason"] = "master_failed"
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=false")
        print(f"BLOCK_REASON=master_failed")
        print(f"FERRY_MAY15_FILES_FOUND={ferry_found}")
        print(f"FERRY_MAY15_PRIORITY_READY={str(ferry_ready).lower()}")
        return 3

    output_video = ""
    for line in out_m.splitlines():
        if '"output"' in line or '"would_write"' in line:
            try:
                payload = json.loads(line.strip())
                output_video = str(payload.get("output") or payload.get("would_write") or "")
            except json.JSONDecodeError:
                pass

    if args.dry_run:
        log["ok"] = True
        log["block_reason"] = ""
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=true")
        print(f"OUTPUT_VIDEO={output_video}")
        print(f"FERRY_MAY15_FILES_FOUND={ferry_found}")
        print(f"FERRY_MAY15_PRIORITY_READY={str(ferry_ready).lower()}")
        return 0

    if not output_video:
        log["ok"] = False
        log["block_reason"] = "master_output_path_missing"
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=false")
        print(f"BLOCK_REASON=master_output_path_missing")
        return 4

    if args.skip_mix:
        log["ok"] = True
        log["output_video"] = output_video
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=true")
        print(f"OUTPUT_VIDEO={output_video}")
        return 0

    mixed_out = Path(output_video).parent / f"ferry_3h_may15_souno_{ts}.mp4"
    music_file = ""
    music_root = SUNO_ROOT if SUNO_ROOT.is_dir() else SOUNO_ROOT
    if music_root.is_dir():
        from nyc_long_schedule_v1 import _collect_music_candidates, _newest  # noqa: WPS433

        pick = _newest(_collect_music_candidates(music_root, recursive=True))
        if pick:
            music_file = str(pick)

    mix_cmd = [
        py,
        str(_NYC / "nyc_long_ambient_mix_v1.py"),
        "--input-video",
        output_video,
        "--output-video",
        str(mixed_out),
    ]
    if music_file:
        mix_cmd.extend(["--music-file", music_file])
    else:
        mix_cmd.extend(["--music-library-root", str(music_root)])

    rc_x, out_x, err_x = _run(mix_cmd, timeout=float(args.encode_timeout_sec))
    log["steps"]["mix"] = {"returncode": rc_x, "stdout_tail": out_x[-4000:], "stderr_tail": err_x[-2000:]}
    if rc_x != 0:
        log["ok"] = False
        log["block_reason"] = "mix_failed"
        LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"FERRY_3H_SOUNO_RUN_OK=false")
        print(f"BLOCK_REASON=mix_failed")
        print(f"OUTPUT_VIDEO={output_video}")
        return 5

    final = mixed_out if mixed_out.is_file() else Path(output_video)
    log["ok"] = True
    log["output_video"] = str(final)
    log["music_file"] = music_file
    log["finished_at"] = datetime.now(timezone.utc).isoformat()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"FERRY_3H_SOUNO_RUN_OK=true")
    print(f"OUTPUT_VIDEO={final}")
    print(f"MUSIC_FILE={music_file}")
    print(f"FERRY_MAY15_FILES_FOUND={ferry_found}")
    print(f"FERRY_MAY15_PRIORITY_READY={str(ferry_ready).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
