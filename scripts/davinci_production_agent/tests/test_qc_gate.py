#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_production_agent.qc_gate import validate_render  # noqa: E402


def test_qc_missing_file():
    r = validate_render(Path("/nonexistent/video.mp4"), kind="long")
    assert r["ok"] is False
    assert "file_missing" in r["errors"]
