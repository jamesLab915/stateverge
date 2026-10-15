#!/usr/bin/env python3
"""Event desk — FRED and quotes faked. Numbers are illustrative."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from stateverge.trading.eventdesk import confirm, ff, journal, report  # noqa: E402
from stateverge.trading.eventdesk.events import BY_KEY, transform  # noqa: E402
from stateverge.trading.eventdesk.fred import Fred  # noqa: E402
from stateverge.trading.eventdesk.runner import DeskRunner  # noqa: E402


def fred_get(url):
    if "series/release" in url:
        return b'{"releases": [{"id": 46}]}' if "PPIFIS" in url else b'{"releases": [{"id": 10}]}'
    if "release/dates" in url:
        return b'{"release_dates": [{"date": "2026-10-15"}]}' if "release_id=46" in url else b'{"release_dates": []}'
    if "series/observations" in url:
        return json.dumps({"observations": [{"date": "2026-09-01", "value": "132.0"},
                                            {"date": "2026-08-01", "value": "130.3"},
                                            {"date": "2026-07-01", "value": "130.0"}]}).encode()
    raise AssertionError(url)


FF_ROWS = [
    {"title": "PPI m/m", "country": "USD", "date": "2026-10-15T08:30:00-04:00", "impact": "High",
     "forecast": "0.3%", "previous": "0.2%"},
    {"title": "Unemployment Claims", "country": "USD", "date": "2026-10-15T08:30:00-04:00", "impact": "High",
     "forecast": "221K", "previous": "218K"},
    {"title": "PPI m/m", "country": "EUR", "date": "2026-10-15T05:00:00-04:00", "impact": "Low",
     "forecast": "0.9%", "previous": ""},
]


def ff_get(url):
    return json.dumps(FF_ROWS).encode()


def quotes(table):
    return lambda s: table.get(s)


class TestReport(unittest.TestCase):
    def test_transforms(self):
        self.assertAlmostEqual(transform([100, 101.3], "pct_mom"), 1.3)
        self.assertEqual(transform([150, 200], "diff"), 50)

    def test_hot_ppi_is_risk_off_puts_one_otm(self):
        rep = report.build(BY_KEY["ppi"], 1.3, 0.3, "manual", ["RKLB", "MU"], {"RKLB": 59.3, "MU": 872.0})
        self.assertEqual(rep.direction, "risk_off")
        self.assertEqual([(leg.side, leg.strike) for leg in rep.legs], [("put", 58.0), ("put", 860.0)])
        self.assertIn("铁律", report.render(rep))

    def test_small_or_missing_consensus_no_trade(self):
        self.assertEqual(report.build(BY_KEY["ppi"], 0.35, 0.3, "manual", ["QQQ"], {}).direction, "none")
        self.assertEqual(report.build(BY_KEY["ppi"], 1.3, None, "none", ["QQQ"], {}).direction, "none")

    def test_unemployment_sign_inverted(self):
        self.assertEqual(report.build(BY_KEY["unrate"], 4.5, 4.2, "manual", [], {}).direction, "risk_on")

    def test_sizing(self):
        self.assertEqual(report.contracts_for(1.20), 8)
        self.assertEqual(report.contracts_for(4.50), 0)  # over $400 per contract


class TestForexFactory(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(ff.parse_value("-0.1%"), -0.1)
        self.assertEqual(ff.parse_value("50K"), 50)
        self.assertEqual(ff.parse_value("1.2M"), 1200)
        self.assertIsNone(ff.parse_value(""))

    def test_forecast_usd_only_and_by_day(self):
        rows = ff.week(ff_get)
        self.assertEqual(ff.forecast("ppi", "2026-10-15", rows)[0], 0.3)
        self.assertIsNone(ff.forecast("ppi", "2026-10-16", rows))
        self.assertIsNone(ff.forecast("cpi", "2026-10-15", rows))


class TestConfirm(unittest.TestCase):
    def test_all_three_must_agree(self):
        ok = confirm.check("risk_off", ["MU"], quotes({"NQ=F": (1, -1.2), "^VIX": (1, 8.0), "MU": (1, -3.1)}))
        self.assertTrue(ok["ok"])
        bad = confirm.check("risk_off", ["MU"], quotes({"NQ=F": (1, 0.4), "^VIX": (1, 8.0), "MU": (1, -3.1)}))
        self.assertFalse(bad["ok"])


class TestJournal(unittest.TestCase):
    def test_stats_and_phase2_gate(self):
        trades = [{"entry": 1.0, "exit": 2.0, "qty": 3}, {"entry": 2.0, "exit": 1.0, "qty": 1}]
        s = journal.stats(trades)
        self.assertEqual(s["trades"], 2)
        self.assertEqual(s["win_rate"], 0.5)
        self.assertEqual(s["payoff"], 3.0)
        self.assertFalse(s["phase2_ready"])


class TestRunner(unittest.TestCase):
    def test_calendar_report_confirm(self):
        root = Path(tempfile.mkdtemp())
        (root / "requests.json").write_text(json.dumps([
            {"status": "pending", "action": "calendar", "date": "2026-10-15"},
            {"status": "pending", "action": "report", "event": "ppi", "date": "2026-10-15", "pool": ["MU"]},
        ]))
        q = quotes({"MU": (872.0, -3.0), "NQ=F": (1, -1.0), "^VIX": (1, 6.0)})
        DeskRunner(root, Fred("k", fred_get), q, ff_get).run()
        items = json.loads((root / "requests.json").read_text())
        self.assertEqual(items[0]["events"], ["ppi"])
        cal = (root / "reports/2026-10-15-calendar.md").read_text()
        self.assertIn("预期 0.3%", cal)
        self.assertIn("Unemployment Claims", cal)
        self.assertEqual(items[1]["direction"], "risk_off")  # (132/130.3-1)=1.3% vs FF 0.3%
        self.assertIn("Moomoo 核对", (root / "reports/2026-10-15-ppi.md").read_text())
        rep_json = "reports/2026-10-15-ppi.json"
        (root / "requests.json").write_text(json.dumps([{"status": "pending", "action": "confirm", "report": rep_json}]))
        DeskRunner(root, Fred("k", fred_get), q, ff_get).run()
        self.assertTrue(json.loads((root / "requests.json").read_text())[0]["ok"])
        self.assertTrue((root / "stats.json").exists())


if __name__ == "__main__":
    unittest.main()
