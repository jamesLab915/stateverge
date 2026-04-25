#!/usr/bin/env python3
"""Append a research log entry to docs/tracking/research_log.md"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
LOG_PATH = REPO_ROOT / "docs" / "tracking" / "research_log.md"


def main() -> int:
    ap = argparse.ArgumentParser(description="Append research log section")
    ap.add_argument("--date", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--project-area", default="", dest="project_area")
    ap.add_argument("--problem", default="")
    ap.add_argument("--why-matters", default="", dest="why_matters")
    ap.add_argument("--tools", default="")
    ap.add_argument("--approach", default="")
    ap.add_argument("--result", default="")
    ap.add_argument("--evidence", default="")
    ap.add_argument("--next-step", default="", dest="next_step")
    ap.add_argument("--eb1", default="", dest="eb1")
    ap.add_argument("--irs", default="", dest="irs")
    ap.add_argument("--files", default="", dest="files_changed")
    args = ap.parse_args()

    block = f"""
## {args.date} - {args.title}

- Project Area: {args.project_area}
- Problem: {args.problem}
- Why It Matters: {args.why_matters}
- Tools Used: {args.tools}
- Technical Approach: {args.approach}
- Files/Modules Changed: {args.files_changed}
- Result: {args.result}
- Evidence: {args.evidence}
- Next Step: {args.next_step}
- EB1/NIW Relevance: {args.eb1}
- IRS/Business Relevance: {args.irs}

---
""".lstrip()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not LOG_PATH.is_file():
        LOG_PATH.write_text("# StateVerge 研发日志\n\n", encoding="utf-8")

    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(block)

    print(f"[tracking] action=append file={LOG_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
