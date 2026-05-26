#!/usr/bin/env python3
"""Tests for Always Deliver Scheduler v2."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from always_publish.daily_delivery_state import (  # noqa: E402
    DAILY_LONG_REQUIRED,
    DAILY_SHORTS_REQUIRED,
    _default_state,
)
from always_publish.failure_recovery import classify_failure  # noqa: E402
from always_publish.quota_guard import check_quota, long_upload_allowed  # noqa: E402


class TestAlwaysDeliverV2(unittest.TestCase):
    def test_default_state_fields(self) -> None:
        st = _default_state("2026-05-18")
        self.assertEqual(st["long_required"], DAILY_LONG_REQUIRED)
        self.assertEqual(st["shorts_required"], DAILY_SHORTS_REQUIRED)
        self.assertIn("delivery_complete", st)

    def test_hard_vs_soft_failure(self) -> None:
        hard = classify_failure("duplicate content_key match")
        self.assertEqual(hard["class"], "hard")
        soft = classify_failure("ffmpeg encode timeout")
        self.assertEqual(soft["class"], "soft")
        self.assertTrue(soft.get("retry"))

    def test_quota_guard_mode(self) -> None:
        q = check_quota("2026-05-18", {})
        self.assertEqual(q.get("mode"), "quota_guaranteed")

    def test_long_gate_uses_upload_counts(self) -> None:
        gate = long_upload_allowed(kind="1h", force=False)
        self.assertIn("long_uploaded", gate)
        self.assertIn("schedule_mode", gate)


if __name__ == "__main__":
    unittest.main()
