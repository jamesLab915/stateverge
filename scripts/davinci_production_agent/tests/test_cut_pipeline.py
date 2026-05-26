#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_production_agent.cut_pipeline import forbid_ffmpeg_copy_trim, is_iphone_vfr_source  # noqa: E402


def test_iphone_vfr_detected():
    p = Path("/Volumes/SV_TRANSFER/00_INBOX/iphone/video169/foo.MOV")
    assert is_iphone_vfr_source(p)
    guard = forbid_ffmpeg_copy_trim(p)
    assert guard["blocked"] is True


def test_non_iphone_not_blocked():
    p = Path("/Volumes/SV_CACHE/davinci_studio/renders/out.mp4")
    guard = forbid_ffmpeg_copy_trim(p)
    assert guard["blocked"] is False
