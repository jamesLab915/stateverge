#!/usr/bin/env python3
"""Unit tests for per-clip normalize timeout scaling."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from render_job import normalize_timeout_sec  # noqa: E402
from resolve_bridge import folder_studio_project_name, sanitize_resolve_name  # noqa: E402


class NormalizeTimeoutTests(unittest.TestCase):
    def test_short_clip_floor(self) -> None:
        self.assertEqual(normalize_timeout_sec(30.0, encode_timeout_sec=7200.0), 450.0)

    def test_ferry_329s_clip(self) -> None:
        # IMG_2395 ~329s → max(450, 658) = 658
        self.assertEqual(normalize_timeout_sec(329.0, encode_timeout_sec=7200.0), 658.0)

    def test_capped_by_encode_budget(self) -> None:
        self.assertEqual(normalize_timeout_sec(600.0, encode_timeout_sec=500.0), 500.0)


class ResolveNameTests(unittest.TestCase):
    def test_sanitize_strips_invalid(self) -> None:
        self.assertEqual(sanitize_resolve_name("ferry/2026#test"), "ferry_2026_test")

    def test_project_name_length(self) -> None:
        long = "a" * 80
        name = folder_studio_project_name(long)
        self.assertLessEqual(len(name), 64)
        self.assertTrue(name.startswith("SV_FolderStudio_"))


if __name__ == "__main__":
    raise SystemExit(unittest.main())
