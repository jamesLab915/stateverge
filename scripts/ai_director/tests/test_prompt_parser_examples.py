#!/usr/bin/env python3
"""Unit tests — four Director AI v1 example briefs."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ai_director.build_production_plan import build_production_plan  # noqa: E402
from ai_director.production_plan_schema import REQUIRED_FIELDS, validate_plan  # noqa: E402

_EXAMPLE_FERRY = "做一个1小时曼哈顿ferry日落原声视频"
_EXAMPLE_SLEEP_DRIVE = "做一个适合睡觉的纽约雨夜驾驶"
_EXAMPLE_CAFE_DRIVE = "做一个适合咖啡店循环播放的midtown drive"
_EXAMPLE_SHORT_TS = "做一个short，夜晚时代广场，节奏快一点"


class TestDirectorExamplePhrases(unittest.TestCase):
    def _plan(self, text: str) -> dict:
        plan = build_production_plan(text)
        self.assertEqual([], validate_plan(plan), msg=f"validation failed for: {text!r}")
        for field in REQUIRED_FIELDS:
            self.assertIn(field, plan, msg=field)
        return plan

    def test_ferry_sunset_one_hour_real_sound(self) -> None:
        plan = self._plan(_EXAMPLE_FERRY)
        self.assertEqual(plan["content_type"], "ferry")
        self.assertEqual(plan["source_type"], "ferry")
        self.assertIn(plan["time_of_day"], ("sunset", "evening"))
        self.assertEqual(plan["duration_target_sec"], 3600)
        self.assertEqual(plan["audio_mode"], "real_ambience_primary")
        self.assertFalse(plan["music_enabled"])
        self.assertEqual(plan["davinci_preset"], "youtube_ferry_real")
        self.assertEqual(plan["route_type"], "manhattan")

    def test_sleep_rain_night_driving(self) -> None:
        plan = self._plan(_EXAMPLE_SLEEP_DRIVE)
        self.assertEqual(plan["content_type"], "driving")
        self.assertEqual(plan["source_type"], "driving")
        self.assertEqual(plan["mood"], "sleep")
        self.assertIn(plan["time_of_day"], ("rainy_night", "night"))
        self.assertTrue(plan["music_enabled"])
        self.assertEqual(plan["audio_mode"], "music_first")
        self.assertIn(plan["music_style"], ("piano_soft_synth", "calm_ambient"))

    def test_cafe_loop_midtown_drive_calm(self) -> None:
        plan = self._plan(_EXAMPLE_CAFE_DRIVE)
        self.assertEqual(plan["content_type"], "driving")
        self.assertEqual(plan["route_type"], "midtown")
        self.assertEqual(plan["mood"], "calm")
        self.assertTrue(plan["music_enabled"])
        self.assertEqual(plan["audio_mode"], "music_first")

    def test_short_times_square_night_fast(self) -> None:
        plan = self._plan(_EXAMPLE_SHORT_TS)
        self.assertEqual(plan["content_type"], "shorts")
        self.assertEqual(plan["output_type"], "shorts")
        self.assertEqual(plan["route_type"], "times_square")
        self.assertEqual(plan["time_of_day"], "night")
        self.assertEqual(plan["mood"], "energetic")
        self.assertGreaterEqual(plan["duration_target_sec"], 15)
        self.assertLessEqual(plan["duration_target_sec"], 45)
        self.assertIn(plan["shorts_style"], ("fast_pace", "default"))


if __name__ == "__main__":
    unittest.main()
