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

    def chain(self, symbol, side, expiry):
        base = {"MU": 860, "RKLB": 55, "QQQ": 728}[symbol]
        return [(float(k), f"US.{symbol}{expiry}{side[0].upper()}{k}") for k in range(base - 5, base + 6)]

    stock = {"US.RKLB": 55.75, "US.MU": 870.0, "US.QQQ": 728.4}

    def quote(self, code):
        if code in self.stock:
            p = self.stock[code]
            return p, p, p
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
    def test_take_profit_all_out(self):
        r = Exit()
        p = exits.Position("x", 3, 1.0, 1.0)
        self.assertEqual(exits.decide(p, 1.2, r)[0], 0)
        self.assertEqual(exits.decide(p, 1.31, r), (3, "止盈 +31% 全走"))

    def test_stop_and_time(self):
        self.assertEqual(exits.decide(exits.Position("x", 3, 2.0, 2.0), 1.55, Exit(), stop_pct=25)[0], 0)
        self.assertEqual(exits.decide(exits.Position("x", 3, 2.0, 2.0), 1.5, Exit(), stop_pct=25)[0], 3)
        self.assertEqual(exits.decide(exits.Position("x", 3, 2.0, 2.0), 2.1, Exit(), "时间止损")[1], "时间止损")


class TestBridge(unittest.TestCase):
    def run_day(self, bids, hhmm="09:31", plan=PLAN):
        c = Clock(hhmm)
        j = Path(tempfile.mkdtemp()) / "journal.json"
        logs = []
        code = Bridge(Plan.from_dict(plan), DryRun(Quotes(bids)), j, now=c.now, sleep=c.sleep,
                      log=logs.append).run()
        rows = json.loads(j.read_text()) if j.exists() else []
        return code, rows, logs

    def test_take_profit(self):
        code, rows, _ = self.run_day([2.0, 2.2, 2.6, 2.75])
        self.assertEqual(code, 0)
        self.assertEqual([(r["qty"], r["exit"]) for r in rows], [(2, 2.75)])  # entry at ask 2.08; 2.6 is only +25%
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

    def test_underlying_range_blocks_entry(self):
        d = json.loads(json.dumps(PLAN))
        d["legs"][0].update(underlying_min=880, underlying_max=900)
        code, rows, logs = self.run_day([2.0], plan=d)
        self.assertTrue(any("不在入场区" in x for x in logs))

    def test_this_week_stop_is_25(self):
        # PLAN expiry 10/23 is this week for 10/19 → −25%: 2.08 → 1.56 = −25%
        code, rows, _ = self.run_day([2.0, 1.7, 1.56])
        self.assertEqual(rows[0]["reason"][:2], "止损")
        d = json.loads(json.dumps(PLAN))
        d["exit"] = {"stop_loss_pct": 30}
        self.assertIn("止损要在", " ".join(problems(Plan.from_dict(d))))
        d["exit"] = {"take_profit_pct": 100}
        self.assertIn("止盈", " ".join(problems(Plan.from_dict(d))))

    def test_add_on_same_symbol_refused(self):
        d = json.loads(json.dumps(PLAN))
        d["legs"].append(dict(d["legs"][0], strike=850, qty=1, max_price=1.0))
        self.assertIn("摊平", " ".join(problems(Plan.from_dict(d))))

    def test_overnight_then_resume(self):
        d = json.loads(json.dumps(PLAN))
        d["date"] = "2026-10-16"
        d["exit"] = {"hold_until": "2026-10-23"}
        root = Path(tempfile.mkdtemp())
        j = root / "journal.json"
        c = Clock("09:31")
        c.t = c.t.replace(day=16)
        logs = []
        Bridge(Plan.from_dict(d), DryRun(Quotes([2.0, 2.1])), j, now=c.now, sleep=c.sleep, log=logs.append).run()
        self.assertTrue(any("过夜" in x for x in logs))
        state = json.loads((root / "open_positions.json").read_text())
        self.assertEqual(state[0]["qty"], 2)
        # Monday: second batch must not double the same symbol, and must respect remaining budget
        d2 = dict(PLAN, legs=[dict(PLAN["legs"][0], qty=1)])
        c2 = Clock("09:31")
        msgs = []
        self.assertEqual(Bridge(Plan.from_dict(d2), DryRun(Quotes([2.0])), j, now=c2.now, sleep=c2.sleep,
                                log=msgs.append).run(), 2)
        self.assertTrue(any("摊平" in x for x in msgs))
        # resume on Monday: stop-loss fires
        Bridge(Plan.from_dict(d), DryRun(Quotes([0.9])), j, now=c2.now, sleep=c2.sleep, log=msgs.append).resume()
        self.assertFalse((root / "open_positions.json").exists())
        self.assertEqual(json.loads(j.read_text())[0]["reason"][:2], "止损")

    def test_0dte_otm1_quick_exit(self):
        d = {"date": "2026-10-19", "event": "radar", "thesis": "雷达偏空", "require_confirm": False,
             "legs": [{"symbol": "QQQ", "side": "put", "strike": "otm1", "expiry": "0dte", "qty": 2,
                       "max_price": 2.0}],
             "exit": {"max_hold_min": 1}}
        code, rows, logs = self.run_day([1.5, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6, 1.6],
                                        plan=d)
        self.assertEqual(rows[0]["strike"], 728.0)  # QQQ 728.4 → first put strike below
        self.assertEqual(rows[0]["expiry"], "2026-10-19")
        self.assertIn("1 分钟", rows[0]["reason"])

    def test_0dte_cannot_hold_overnight(self):
        d = json.loads(json.dumps(PLAN))
        d["legs"][0]["expiry"] = "0dte"
        d["exit"] = {"hold_until": "2026-10-20"}
        self.assertIn("0DTE", " ".join(problems(Plan.from_dict(d))))

    def test_wrong_day_refused(self):
        d = dict(PLAN, date="2026-10-20")
        code, rows, logs = self.run_day([2.0], plan=d)
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
