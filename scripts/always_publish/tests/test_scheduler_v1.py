#!/usr/bin/env python3
"""Tests for Always Publish Scheduler v1."""

from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from always_publish.scheduler import build_today_plan, load_config  # noqa: E402


class TestAlwaysPublishSchedulerV1(unittest.TestCase):
    def test_long_plan_returns_one_slot(self) -> None:
        cfg = load_config()
        plan = build_today_plan(cfg, dry_run=True, on_date=date.today())
        self.assertTrue(plan.get("ok"))
        self.assertIsNotNone(plan.get("long_plan"))
        quota = plan.get("quota") or {}
        self.assertGreaterEqual(int(quota.get("max_daily_long") or 1), 1)

    def test_shorts_plan_returns_four_slots(self) -> None:
        cfg = load_config()
        plan = build_today_plan(cfg, dry_run=True, on_date=date.today())
        shorts = plan.get("shorts_plans") or []
        self.assertEqual(len(shorts), 4)

    def test_simulate_failures_still_returns_plan_with_fallback(self) -> None:
        cfg = load_config()
        plan = build_today_plan(
            cfg,
            dry_run=True,
            simulate_no_new_materials=True,
        )
        self.assertTrue(plan.get("ok"))
        lp = plan.get("long_plan") or {}
        self.assertIn("fallback_level", lp)
        shorts = plan.get("shorts_plans") or []
        self.assertEqual(len(shorts), 4)
        for sp in shorts:
            self.assertIn("fallback_level", sp)


if __name__ == "__main__":
    unittest.main()
