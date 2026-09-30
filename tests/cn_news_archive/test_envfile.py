#!/usr/bin/env python3
"""envfile loader tests."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from stateverge.cn_news.archive import envfile  # noqa: E402


class TestEnvFile(unittest.TestCase):
    def setUp(self):
        self.keys = ["SV_T_A", "SV_T_B", "SV_T_C", "SV_T_D", "SV_T_E"]
        for k in self.keys:
            os.environ.pop(k, None)

    tearDown = setUp

    def test_parse(self):
        vals = envfile.parse('# c\nexport SV_T_A=1\nSV_T_B="x y"\nSV_T_C=\'q#\'\nSV_T_D=v # note\nbad line\n')
        self.assertEqual(vals, {"SV_T_A": "1", "SV_T_B": "x y", "SV_T_C": "q#", "SV_T_D": "v"})

    def test_precedence_and_empty(self):
        root = Path(tempfile.mkdtemp())
        (root / ".env.local").write_text("SV_T_A=local\nSV_T_E=\n")
        (root / ".env").write_text("SV_T_A=base\nSV_T_B=base\nSV_T_C=base\n")
        os.environ["SV_T_C"] = "real"
        loaded = envfile.load(root)
        self.assertEqual(os.environ["SV_T_A"], "local")
        self.assertEqual(os.environ["SV_T_B"], "base")
        self.assertEqual(os.environ["SV_T_C"], "real")  # real env wins
        self.assertNotIn("SV_T_E", os.environ)  # empty placeholders are skipped
        self.assertEqual(sorted(loaded), ["SV_T_A", "SV_T_B"])

    def test_missing_files_ok(self):
        self.assertEqual(envfile.load(Path(tempfile.mkdtemp())), [])

    def test_env_files_are_gitignored(self):
        import subprocess

        root = Path(__file__).resolve().parents[2]
        r = subprocess.run(["git", "check-ignore", ".env", ".env.local"], cwd=root, capture_output=True, text=True)
        self.assertEqual(r.stdout.split(), [".env", ".env.local"])


if __name__ == "__main__":
    unittest.main()
