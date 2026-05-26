#!/usr/bin/env python3
"""Standalone test harness: run ambient_audio_gate.py on one long-form NYC clip.

Picks the most-recently-modified long video from SV_TRANSFER/ready_to_upload,
runs the ambient gate (nyc_calm_ambient_v2 preset, -18 LUFS — test only),
and writes a JSON report to logs/ambient_audio_one_test_report.json.

WARNING: This calls ambient_audio_gate.py which is NOT the production pipeline.
         For production, use scripts/davinci_audio_finish/gate_v1.py.

Fail-open: always writes the JSON report and exits 0 (test harness, not upload).
"""
import datetime
import json
import subprocess
import sys
from pathlib import Path

ROOTS = [
    Path("/Volumes/SV_TRANSFER/ready_to_upload/nyc_long_clips"),
    Path("/Volumes/SV_TRANSFER/ready_to_upload"),
]

OUT_DIR = Path("/Volumes/SV_CACHE/audio_ambient_tests")
GATE = Path(__file__).resolve().parent / "ambient_audio_gate.py"
OUT_GLOB = "*_ambient_v2.mp4"

_REPORT_PATH = Path(__file__).resolve().parents[2] / "logs" / "ambient_audio_one_test_report.json"


def _utc() -> str:
    return datetime.datetime.utcnow().isoformat() + "Z"


def _write_report(report: dict) -> None:
    try:
        _REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        _REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"WARNING: could not write report: {exc}", file=sys.stderr)


def is_ok(p: Path) -> bool:
    s = str(p).lower()
    try:
        sz = p.stat().st_size
    except OSError:
        return False
    return (
        p.suffix.lower() in [".mp4", ".mov", ".m4v"]
        and "short" not in s
        and "_ambient" not in s
        and "_ambient_v2" not in s
        and "_test_audio" not in s
        and sz > 50_000_000
    )


def main() -> int:
    report: dict = {
        "status": "failed",
        "input": None,
        "output": None,
        "returncode": None,
        "upload_performed": False,
        "shorts_touched": False,
        "warnings": ["standalone_test_harness_not_production_chain"],
        "finished_at": _utc(),
    }

    candidates: list[Path] = []
    for r in ROOTS:
        if r.exists():
            try:
                candidates += [p for p in r.rglob("*") if p.is_file() and is_ok(p)]
            except OSError as exc:
                report["warnings"].append(f"scan_error:{r}:{exc}")

    if not candidates:
        report["status"] = "no_candidates"
        report["warnings"].append("no_long_video_candidate_found_in_roots")
        _write_report(report)
        print("NO_LONG_VIDEO_CANDIDATE_FOUND")
        return 0

    src = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)[0]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / (src.stem + "_ambient_v2.mp4")

    report["input"] = str(src)
    report["output"] = str(out)

    if not GATE.is_file():
        report["warnings"].append(f"gate_script_missing:{GATE}")
        _write_report(report)
        return 0

    cmd = [
        sys.executable,
        str(GATE),
        "--input", str(src),
        "--output", str(out),
        "--preset", "nyc_calm_ambient_v2",
    ]

    print("SELECTED_INPUT=" + str(src))
    print("OUTPUT=" + str(out))

    try:
        rc = subprocess.run(cmd, check=False).returncode
    except FileNotFoundError:
        rc = 127
        report["warnings"].append("python_or_gate_not_found")
    except Exception as exc:
        rc = 1
        report["warnings"].append(f"subprocess_exception:{type(exc).__name__}:{exc}")

    report["returncode"] = rc
    report["status"] = "ok" if rc == 0 and out.exists() else "failed"
    report["finished_at"] = _utc()
    _write_report(report)

    print(json.dumps(report, indent=2))

    if report["status"] == "ok":
        try:
            matches = sorted(OUT_DIR.glob(OUT_GLOB), key=lambda p: p.stat().st_mtime, reverse=True)
            latest = matches[0] if matches else None
            if latest:
                print("LATEST_AMBIENT_V2=" + str(latest))
                subprocess.run(["ffprobe", "-hide_banner", str(latest)], check=False)
        except Exception as exc:
            report["warnings"].append(f"post_check_exception:{exc}")
        print("AMBIENT_V2_TEST_OK")
    else:
        print("AMBIENT_V2_TEST_FAILED")

    return 0


if __name__ == "__main__":
    sys.exit(main())
