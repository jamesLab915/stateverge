#!/usr/bin/env python3
"""Append one row to the milestone table in docs/tracking/dev_milestones.md"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MILE_PATH = REPO_ROOT / "docs" / "tracking" / "dev_milestones.md"


def _esc_cell(s: str) -> str:
    return s.replace("|", "/").replace("\n", " ").strip()


def main() -> int:
    ap = argparse.ArgumentParser(description="Append milestone table row")
    ap.add_argument("--date", required=True)
    ap.add_argument("--milestone", required=True)
    ap.add_argument("--area", default="", dest="area")
    ap.add_argument("--contribution", default="")
    ap.add_argument("--tools", default="")
    ap.add_argument("--output", default="", dest="output")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--relevance", default="")
    args = ap.parse_args()

    row = (
        f"| {_esc_cell(args.date)} | {_esc_cell(args.milestone)} | {_esc_cell(args.area)} | "
        f"{_esc_cell(args.contribution)} | {_esc_cell(args.tools)} | {_esc_cell(args.output)} | "
        f"{_esc_cell(args.evidence)} | {_esc_cell(args.relevance)} |"
    )

    text = MILE_PATH.read_text(encoding="utf-8") if MILE_PATH.is_file() else ""
    if not text.strip():
        MILE_PATH.write_text(
            "# 研发里程碑\n\n| Date | Milestone | Project Area | Technical Contribution | "
            "Tools Used | Output | Evidence Path | Business / Immigration Relevance |\n"
            "|------|------------|--------------|------------------------|"
            "------------|--------|---------------|----------------------------------|\n",
            encoding="utf-8",
        )
        text = MILE_PATH.read_text(encoding="utf-8")

    lines = text.splitlines()
    insert_at = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^\|[-— ]+\|", line):
            insert_at = i + 1
            break
    # Insert after header separator; if not found, append at end
    new_lines = lines[:insert_at] + [row] + lines[insert_at:]
    MILE_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    print(f"[tracking] action=append file={MILE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
