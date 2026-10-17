#!/usr/bin/env python3
"""Moomoo bridge with a scripted fake broker and clock — no OpenD, no orders."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.trading.moomoo_bridge import exits  # noqa: E402
from stateverge.trading.moomoo_bridge.bridge import ET, Bridge  # noqa: E402
from stateverge.trading.moomoo_bridge.broker import DryRun  # noqa: E402
from stateverge.trading.moomoo_bridge.plan import Exit, Plan, problems  # noqa: E402

PLAN = {"date": "2026-10-19", "event": "CPI", "thesis": "hot CPI", "require_confirm": False,
        "entry_window_et": ["09:30", "10:00"],
        "legs": [{"symbol": "MU", "side": "put", "strike": 860, "expiry": "2026-10-23", "qty": 2, "max_price": 3.0}]}


class Clock:
    def __init__(self, hhmm="09:31"):
        h, m = map(int, hhmm.split(":"))
        self.t = datetime(2026, 10, 19, h, m, tzinfo=ET)

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += timedelta(seconds=s)


class Quotes:
    """bid path per call; ask = bid * 1.04."""

    def __init__(self, bids):
        self.bids = list(bids)

    def option_code(self, symbol, side, strike, expiry):
        return f"US.{symbol}261023P{int(strike * 1000)}"

    def quote(self, code):
        b = self.bids.pop(0) if len(self.bids) > 1 else self.bids[0]
        return b, round(b * 1.04, 2), b


class TestPlan(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(problems(Plan.from_dict(PLAN)), [])

    def test_rule_breaks(self):
        d = json.loads(json.dumps(PLAN))
        d["legs"][0]["max_price"] = 4.5
        d["legs"].append({"symbol": "QQQ", "side": "call", "strike": 600, "expiry": "2026-10-19", "qty": 9,
                          "max_price": 2.0})
        msgs = " ".join(problems(Plan.from_dict(d)))
        for want in ("单张", "反手", "总仓位"):
            self.assertIn(want, msgs)


class TestExits(unittest.TestCase):
    def test_sequence(self):
        r = Exit()
        p = exits.Position("x", 4, 1.0, 1.0)
        self.assertEqual(exits.decide(p, 1.5, r, False)[0], 0)
        self.assertEqual(exits.decide(p, 2.1, r, False)[0], 2)
        p.qty, p.half_taken = 2, True
        self.assertEqual(exits.decide(p, 3.0, r, False)[0], 0)
        self.assertEqual(exits.decide(p, 1.7, r, False)[0], 2)  # 3.0 → 1.7 = −43%

    def test_stop_and_time(self):
        self.assertEqual(exits.decide(exits.Position("x", 3, 2.0, 2.0), 0.95, Exit(), False)[0], 3)
        self.assertEqual(exits.decide(exits.Position("x", 3, 2.0, 2.0), 2.1, Exit(), True)[1][:4], "时间止损")


class TestBridge(unittest.TestCase):
    def run_day(self, bids, hhmm="09:31", plan=PLAN):
        c = Clock(hhmm)
        j = Path(tempfile.mkdtemp()) / "journal.json"
        logs = []
        code = Bridge(Plan.from_dict(plan), DryRun(Quotes(bids)), j, now=c.now, sleep=c.sleep,
                      log=logs.append).run()
        rows = json.loads(j.read_text()) if j.exists() else []
        return code, rows, logs

    def test_double_then_trail(self):
        code, rows, _ = self.run_day([2.0, 2.0, 2.5, 4.1, 5.0, 2.9])
        self.assertEqual(code, 0)
        self.assertEqual([(r["qty"], r["exit"]) for r in rows], [(1, 5.0), (1, 2.9)])  # entry at ask 2.08; 4.1 is only +97%
        self.assertTrue(all(r["paper"] for r in rows))

    def test_wide_spread_skipped(self):
        class Wide(Quotes):
            def quote(self, code):
                return 1.0, 1.5, 1.2
        c = Clock()
        logs = []
        Bridge(Plan.from_dict(PLAN), DryRun(Wide([1])), Path(tempfile.mkdtemp()) / "j.json",
               now=c.now, sleep=c.sleep, log=logs.append).run()
        self.assertTrue(any("价差" in x for x in logs))

    def test_late_no_entry(self):
        code, rows, logs = self.run_day([2.0], hhmm="10:05")
        self.assertEqual(rows, [])
        self.assertTrue(any("入场时间窗口" in x for x in logs))

    def test_wrong_day_refused(self):
        d = dict(PLAN, date="2026-10-20")
        code, rows, logs = self.run_day([2.0], plan=d)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
