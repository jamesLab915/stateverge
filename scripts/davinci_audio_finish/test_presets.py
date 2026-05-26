#!/usr/bin/env python3
"""Unit tests for davinci_audio_finish presets (no ffmpeg)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from presets import infer_preset_from_path, resolve_preset  # noqa: E402


def test_ferry_preset() -> None:
    p = Path("/Volumes/SV_TRANSFER/ready_to_upload/nyc_long_clips/nyc_long_ferry_3h_souno.mp4")
    assert resolve_preset(p, "auto") == "youtube_ferry_real"
    assert infer_preset_from_path(p) == "youtube_ferry_real"


def test_driving_preset() -> None:
    p = Path("/tmp/nyc_long_master_night_drive.mp4")
    assert infer_preset_from_path(p) == "youtube_long_calm"


def test_shorts_preset() -> None:
    p = Path("/tmp/shorts_clip_abc_clean_real_sound.mp4")
    assert infer_preset_from_path(p) == "youtube_shorts_cinematic"


def main() -> int:
    test_ferry_preset()
    test_driving_preset()
    test_shorts_preset()
    print("test_presets: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
