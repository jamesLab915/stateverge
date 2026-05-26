"""Generate fixed HeyGen presenter insertion timeline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import ensure_topic_dirs, log_step

INSERTS = [
    ("00:00", 8),
    ("03:30", 10),
    ("08:30", 10),
    ("11:30", 8),
]


def generate_heygen_plan(topic: str, root: Path | None = None) -> Path:
    paths = ensure_topic_dirs(topic, root)
    rows = [
        {
            "id": f"heygen_{i:02d}",
            "timecode": timecode,
            "duration_seconds": min(duration, 10),
            "purpose": purpose,
            "script_hint": hint,
        }
        for i, (timecode, duration, purpose, hint) in enumerate(
            [
                (INSERTS[0][0], INSERTS[0][1], "opening presenter hook", "Set up the central question."),
                (INSERTS[1][0], INSERTS[1][1], "technical explanation bridge", "Clarify the core system."),
                (INSERTS[2][0], INSERTS[2][1], "future stakes bridge", "Connect future scenario to business and society."),
                (INSERTS[3][0], INSERTS[3][1], "closing presenter statement", "End with a memorable thesis."),
            ],
            start=1,
        )
    ]
    out = paths["final"] / "heygen_timeline.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log_step("generate_heygen_plan", topic, out)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    args = ap.parse_args(argv)
    generate_heygen_plan(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
