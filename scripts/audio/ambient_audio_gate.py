#!/usr/bin/env python3
"""StateVerge ambient audio gate — standalone NYC calm preset (fail-open).

WARNING: This script is NOT wired into the main ambient audio pipeline
(stateverge_drive_ambience_only_v1.py / davinci_audio_finish/gate_v1.py).
It is called only by run_one_ambient_audio_test.py as a standalone test harness.

Loudness note: preset nyc_calm_ambient_v2 targets -18 LUFS (intentionally
conservative for background/test use). The production pipeline uses -14 LUFS.
Do not use this script for final YouTube delivery.

Outputs a JSON report to <output>.gate_report.json (same directory as output).
Fail-open: writes report and exits 0 even on ffmpeg failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import warnings
from datetime import datetime, timezone
from pathlib import Path

warnings.warn(
    "ambient_audio_gate.py is a standalone test harness (not the production chain). "
    "For production, use scripts/davinci_audio_finish/gate_v1.py.",
    stacklevel=1,
)

PRESETS = {
    "nyc_calm_ambient_v2": (
        "highpass=f=32,"
        "acompressor=threshold=-20dB:ratio=1.3:attack=45:release=300,"
        "loudnorm=I=-18:LRA=11:TP=-2"
    ),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--preset", default="nyc_calm_ambient_v2")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print ffmpeg command and exit without running it.")
    args = ap.parse_args()

    inp = Path(args.input)
    out = Path(args.output)
    report_path = out.parent / (out.stem + ".gate_report.json")
    report: dict = {
        "script": "ambient_audio_gate.py",
        "input": str(inp),
        "output": str(out),
        "preset": args.preset,
        "dry_run": args.dry_run,
        "success": False,
        "returncode": None,
        "warnings": ["standalone_test_harness_not_production_chain",
                     f"lufs_target_18_not_14_production"],
        "timestamp": _utc_iso(),
    }

    if args.preset not in PRESETS:
        report["errors"] = [f"unknown_preset:{args.preset}"]
        report["warnings"].append(f"available_presets:{list(PRESETS.keys())}")
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            pass
        return 0

    af = PRESETS[args.preset]

    cmd = [
        "ffmpeg", "-y",
        "-i", str(inp),
        "-map", "0:v:0",
        "-map", "0:a:0?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-r", "30",
        "-pix_fmt", "yuv420p",
        "-af", af,
        "-c:a", "aac",
        "-b:a", "192k",
        "-movflags", "+faststart",
        str(out)
    ]

    if args.dry_run:
        print("[dry-run]", " ".join(cmd))
        report["success"] = True
        report["returncode"] = 0
        report["dry_run"] = True
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError:
            pass
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)

    print("+", " ".join(cmd))
    try:
        rc = subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        rc = 127
        report["warnings"].append("ffmpeg_not_found")
    except Exception as exc:
        rc = 1
        report["warnings"].append(f"subprocess_exception:{exc}")

    report["returncode"] = rc
    report["success"] = rc == 0 and out.is_file()

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
