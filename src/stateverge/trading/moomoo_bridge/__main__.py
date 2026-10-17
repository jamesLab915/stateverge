"""python -m stateverge.trading.moomoo_bridge --plan data/trading/plans/2026-10-19.json [--mode dry|paper]

Only `dry` (quotes, no orders) and `paper` (Moomoo SIMULATE account) exist.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bridge import Bridge
from .broker import DryRun, MoomooPaper
from .plan import Plan, problems


def main() -> int:
    ap = argparse.ArgumentParser(prog="stateverge.trading.moomoo_bridge")
    ap.add_argument("--plan", required=True)
    ap.add_argument("--mode", choices=["dry", "paper"], default="dry")
    ap.add_argument("--check", action="store_true", help="only validate the plan, no OpenD needed")
    ap.add_argument("--resume", action="store_true", help="manage this plan's overnight positions only")
    ap.add_argument("--journal", default="data/trading/journal.json")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=11111)
    a = ap.parse_args()
    plan = Plan.from_dict(json.loads(Path(a.plan).read_text(encoding="utf-8")))
    if a.check:
        bad = problems(plan)
        print("\n".join("⛔ " + x for x in bad) or "✅ 计划符合铁律")
        return 2 if bad else 0
    quotes = MoomooPaper(a.host, a.port)
    broker = quotes if a.mode == "paper" else DryRun(quotes)
    try:
        br = Bridge(plan, broker, Path(a.journal))
        return br.resume() if a.resume else br.run()
    finally:
        broker.close()


raise SystemExit(main())
