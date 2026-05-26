#!/usr/bin/env python3
"""Diagnose DaVinci YouTube Audio Finishing Gate v1 (dry-run, no upload)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

MARKER_READY = "DAVINCI_YOUTUBE_AUDIO_FINISHING_GATE_READY=true"
from config import load_config  # noqa: E402
from gate_v1 import run_gate  # noqa: E402
from gate_paths import FINISHED_FOR_YOUTUBE_ROOT, GATE_ROOT, REPORTS_ROOT  # noqa: E402
from presets import PRESET_IDS, infer_preset_from_path  # noqa: E402
from upload_path import find_existing_finished  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose davinci audio finish gate v1")
    ap.add_argument("--input-video", default="", help="Optional sample video for dry-run")
    ap.add_argument("--dry-run-gate", action="store_true", help="Run gate dry-run on input")
    args = ap.parse_args()

    cfg = load_config()
    out: dict[str, object] = {
        "config": cfg,
        "paths": {
            "gate_root": str(GATE_ROOT),
            "reports_root": str(REPORTS_ROOT),
            "finished_for_youtube": str(FINISHED_FOR_YOUTUBE_ROOT),
            "gate_root_exists": GATE_ROOT.is_dir(),
            "finished_dir_exists": FINISHED_FOR_YOUTUBE_ROOT.is_dir(),
        },
        "presets": list(PRESET_IDS),
    }

    if args.input_video.strip():
        p = Path(args.input_video).expanduser()
        out["input_video"] = str(p)
        out["inferred_preset"] = infer_preset_from_path(p)
        out["existing_finished"] = str(find_existing_finished(p) or "")
        if args.dry_run_gate and p.is_file():
            out["gate_dry_run"] = run_gate(p, dry_run=True)

    print(json.dumps(out, indent=2, ensure_ascii=False))
    print(MARKER_READY)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
