#!/usr/bin/env python3
"""Tests for Auto Clip Cleaner cut parsing and merge logic."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from video_quality.clip_cleaner.cut_merge import (  # noqa: E402
    cuts_to_keep_segments,
    merge_cut_ranges,
    parse_cut_spec,
)


class TestParseCutSpec(unittest.TestCase):
    def test_hms_range(self) -> None:
        start, end = parse_cut_spec("00:01:05-00:01:20")
        self.assertEqual(start, 65.0)
        self.assertEqual(end, 80.0)

    def test_hour_optional(self) -> None:
        start, end = parse_cut_spec("01:30-02:45")
        self.assertEqual(start, 90.0)
        self.assertEqual(end, 165.0)

    def test_invalid_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_cut_spec("not-a-range")

    def test_end_before_start_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_cut_spec("00:02:00-00:01:00")


class TestMergeCutRanges(unittest.TestCase):
    def test_merge_overlapping(self) -> None:
        merged = merge_cut_ranges(
            [
                {"start_sec": 10.0, "end_sec": 20.0, "reason": "blur", "source": "auto"},
                {"start_sec": 18.0, "end_sec": 25.0, "reason": "shake", "source": "auto"},
            ]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["start_sec"], 10.0)
        self.assertEqual(merged[0]["end_sec"], 25.0)

    def test_manual_and_auto_mixed_source(self) -> None:
        merged = merge_cut_ranges(
            [
                {"start_sec": 1.0, "end_sec": 3.0, "reason": "manual_cut", "source": "manual"},
                {"start_sec": 2.5, "end_sec": 4.0, "reason": "too_dark", "source": "auto"},
            ]
        )
        self.assertEqual(merged[0]["source"], "mixed")

    def test_keep_segments_invert(self) -> None:
        cuts = [{"start_sec": 30.0, "end_sec": 40.0, "reason": "x", "source": "manual"}]
        keep = cuts_to_keep_segments(cuts, duration_sec=100.0)
        self.assertEqual(len(keep), 2)
        self.assertEqual(keep[0]["start_sec"], 0.0)
        self.assertEqual(keep[0]["end_sec"], 30.0)
        self.assertEqual(keep[1]["start_sec"], 40.0)
        self.assertEqual(keep[1]["end_sec"], 100.0)


if __name__ == "__main__":
    unittest.main()
