#!/usr/bin/env python3
"""Unit tests for strict audio protection policy (no ffmpeg required)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from audio_modes import (  # noqa: E402
    AUDIO_MODES,
    DEFAULT_MUSIC_VOLUME,
    DEFAULT_ORIGINAL_AUDIO_VOLUME,
    FORBIDDEN_FILTERS,
    build_audio_metadata,
    canonical_mode,
    normalize_mode,
    per_clip_audio_filter,
    resolve_ferry_defaults,
)


class AudioModesPolicyTests(unittest.TestCase):
    def test_primary_modes(self) -> None:
        self.assertEqual(
            AUDIO_MODES,
            ("preserve_raw", "ferry_wind_light", "driving_music_first", "add_envato_music"),
        )

    def test_aliases_normalize(self) -> None:
        self.assertEqual(canonical_mode("ferry_preserve_v1"), "ferry_wind_light")
        self.assertEqual(canonical_mode("ferry_preserve_raw"), "preserve_raw")

    def test_preserve_raw_no_filter(self) -> None:
        self.assertIsNone(per_clip_audio_filter("preserve_raw"))

    def test_ferry_wind_light_chain(self) -> None:
        af = per_clip_audio_filter("ferry_wind_light")
        self.assertEqual(af, "highpass=f=28,volume=0.75")
        self.assertNotIn("afftdn", af or "")
        self.assertNotIn("loudnorm", af or "")

    def test_driving_volumes_default(self) -> None:
        af = per_clip_audio_filter("driving_music_first")
        self.assertEqual(af, f"volume={DEFAULT_ORIGINAL_AUDIO_VOLUME:.2f}")

    def test_music_modes_require_add_music_flag(self) -> None:
        self.assertEqual(normalize_mode("driving_music_first", add_music=False), "preserve_raw")
        self.assertEqual(normalize_mode("driving_music_first", add_music=True), "driving_music_first")

    def test_ferry_auto_mode(self) -> None:
        mode, music, _ = resolve_ferry_defaults(
            audio_mode="preserve_raw",
            add_music=False,
            clip_paths=["/Volumes/SV_CACHE/inbox/video169/ferry/clip.mov"],
        )
        self.assertEqual(mode, "ferry_wind_light")
        self.assertFalse(music)

    def test_metadata_flags(self) -> None:
        meta = build_audio_metadata("driving_music_first", add_music=True)
        self.assertFalse(meta["audio_was_heavily_processed"])
        self.assertFalse(meta["denoise_used"])
        self.assertFalse(meta["demucs_used"])
        self.assertFalse(meta["voice_isolation_used"])
        self.assertEqual(meta["original_audio_volume"], DEFAULT_ORIGINAL_AUDIO_VOLUME)
        self.assertEqual(meta["music_volume"], DEFAULT_MUSIC_VOLUME)
        self.assertIn("audio_protection_policy", meta)
        for forbidden in FORBIDDEN_FILTERS:
            self.assertIn(forbidden, meta["warnings"][0])

    def test_envato_mutes_original(self) -> None:
        self.assertEqual(per_clip_audio_filter("add_envato_music"), "volume=0")


if __name__ == "__main__":
    unittest.main()
