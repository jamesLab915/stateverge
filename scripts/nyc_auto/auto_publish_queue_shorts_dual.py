#!/usr/bin/env python3
"""Run one Short upload on **both** channels per invocation (sequential encode).

Order: Real NYC Shorts (``token_shorts``) → StateVerge NYC long (``token.json``).
Global Shorts worker lock serializes encode; uploads use separate OAuth tokens.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
_REPO = _SCRIPTS.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_SHORTS_Q = _SCRIPTS / "nyc_auto" / "auto_publish_queue_shorts.py"
_NYC_LONG_Q = _SCRIPTS / "nyc_auto" / "auto_publish_queue_nyc_long_shorts.py"


def _run_queue(script: Path, extra: list[str], *, job_suffix: str) -> dict[str, Any]:
    py = sys.executable or "python3"
    job_id = uuid.uuid4().hex[:16] + job_suffix
    cmd = [py, str(script), "--job-id", job_id, *extra]
    print(json.dumps({"dual_channel_step": script.name, "cmd": cmd}, ensure_ascii=False), flush=True)
    try:
        r = subprocess.run(cmd, cwd=str(_REPO), capture_output=True, text=True, timeout=900, check=False)
    except subprocess.TimeoutExpired as exc:
        return {
            "script": str(script),
            "returncode": -1,
            "error": f"timeout:{exc!r}",
            "stdout_tail": "",
            "stderr_tail": "",
        }
    return {
        "script": str(script),
        "returncode": int(r.returncode),
        "stdout_tail": (r.stdout or "")[-4000:],
        "stderr_tail": (r.stderr or "")[-2000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--privacy-status", choices=("private", "unlisted"), default="unlisted")
    ap.add_argument("--duration-seconds", type=float, default=30.0)
    ap.add_argument("--asset-mode", default="video_only")
    ap.add_argument("--audio-mode", default="original")
    args = ap.parse_args()

    if not _SHORTS_Q.is_file() or not _NYC_LONG_Q.is_file():
        print("ERROR: queue script missing", file=sys.stderr)
        return 2

    common = [
        "--duration-seconds",
        str(float(args.duration_seconds)),
        "--asset-mode",
        str(args.asset_mode),
        "--audio-mode",
        str(args.audio_mode),
    ]
    if args.upload:
        common.extend(["--upload", "--privacy-status", str(args.privacy_status)])
    elif args.dry_run:
        common.append("--dry-run")

    results: list[dict[str, Any]] = []
    # Real NYC Shorts first, then StateVerge NYC long channel.
    results.append(_run_queue(_SHORTS_Q, common, job_suffix="s"))
    results.append(_run_queue(_NYC_LONG_Q, common, job_suffix="l"))

    ok_shorts = results[0].get("returncode") in (0, 10)
    ok_long = results[1].get("returncode") in (0, 10)
    body = {
        "status": "ok" if ok_shorts and ok_long else "partial" if ok_shorts or ok_long else "failed",
        "channels": {
            "real_nyc_shorts": results[0],
            "stateverge_nyc_long": results[1],
        },
    }
    print(json.dumps(body, indent=2, ensure_ascii=False))
    if ok_shorts and ok_long:
        return 0
    if ok_shorts or ok_long:
        return 11
    return 12


if __name__ == "__main__":
    raise SystemExit(main())
