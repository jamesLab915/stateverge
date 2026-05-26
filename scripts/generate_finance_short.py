#!/usr/bin/env python3
"""
Pull FMP fundamentals for a ticker and generate finance Shorts inputs:

    topics/<SYMBOL>/finance/data.json
    topics/<SYMBOL>/finance/analysis.json
    topics/<SYMBOL>/brief/narration_script.txt   (60s template Chinese, no LLM)

Existing outputs are moved to archive/<timestamp>/ before overwrite.

Usage (from repo root)::

    python scripts/generate_finance_short.py --symbol AAPL
    python scripts/generate_finance_short.py --symbol NOK

Requires FMP_API_KEY in .env (see .env.example).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.integrations.fmp_client import FMPClient, FMPError, normalize_symbol
from src.integrations.media_sources import load_env_from_dotenv_file
from src.finance.analyzer import (
    build_analysis,
    compute_metrics,
    fetch_fmp_bundle,
    template_narration_zh,
)


def _archive_if_any(paths: list[Path], archive_parent: Path) -> Path | None:
    existing = [p for p in paths if p.is_file()]
    if not existing:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = archive_parent / ts
    dest.mkdir(parents=True, exist_ok=True)
    for p in existing:
        shutil.move(str(p), str(dest / p.name))
    return dest


def main(argv: list[str] | None = None) -> int:
    load_env_from_dotenv_file(REPO_ROOT)
    ap = argparse.ArgumentParser(
        description="Generate finance/data.json, analysis.json, brief narrator from FMP",
    )
    ap.add_argument("--symbol", required=True, help="Ticker, e.g. AAPL, NOK, TSLA")
    args = ap.parse_args(argv)

    try:
        sym = normalize_symbol(args.symbol)
    except FMPError as e:
        print(f"[generate_finance_short] error: {e}", file=sys.stderr)
        return 1

    topic = REPO_ROOT / "topics" / sym
    fin = topic / "finance"
    brief = topic / "brief"
    fin.mkdir(parents=True, exist_ok=True)
    brief.mkdir(parents=True, exist_ok=True)

    data_path = fin / "data.json"
    analysis_path = fin / "analysis.json"
    narr_path = brief / "narration_script.txt"

    try:
        client = FMPClient()
    except FMPError as e:
        print(f"[generate_finance_short] error: {e}", file=sys.stderr)
        return 2

    try:
        bundle = fetch_fmp_bundle(client, sym)
    except FMPError as e:
        print(f"[generate_finance_short] FMP request failed: {e}", file=sys.stderr)
        if getattr(e, "body_snippet", ""):
            print(e.body_snippet, file=sys.stderr)
        return 3

    metrics = compute_metrics(bundle)
    analysis = build_analysis(sym, bundle, metrics)
    narration = template_narration_zh(sym, bundle, metrics, analysis)

    _archive_if_any(
        [data_path, analysis_path],
        fin / "archive",
    )
    _archive_if_any(
        [narr_path],
        brief / "archive",
    )

    data_path.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    analysis_path.write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    narr_path.write_text(narration, encoding="utf-8")

    takeaway = analysis.get("one_sentence_takeaway") or ""

    print(f"data.json          -> {data_path.relative_to(REPO_ROOT)}")
    print(f"analysis.json      -> {analysis_path.relative_to(REPO_ROOT)}")
    print(f"narration_script.txt -> {narr_path.relative_to(REPO_ROOT)}")
    print(f"核心结论: {takeaway}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
