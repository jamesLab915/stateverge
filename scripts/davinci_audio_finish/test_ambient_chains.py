#!/usr/bin/env python3
"""Unit tests — ambient chain JSON schema + ferry forbidden filters."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from ambient_chains.ffmpeg_fallback import build_ffmpeg_af_for_chain  # noqa: E402
from ambient_chains.loader import (  # noqa: E402
    FERRY_FORBIDDEN_FILTERS,
    load_chains_document,
    validate_chains_document,
    youtube_preset_to_ambient_chain,
)
from presets import PRESET_RULES, ambient_chain_for_preset  # noqa: E402


def test_chains_schema_valid() -> None:
    doc = load_chains_document()
    errs = validate_chains_document(doc)
    assert errs == [], f"schema errors: {errs}"


def test_all_five_chains_present() -> None:
    doc = load_chains_document()
    chains = doc["chains"]
    for cid in (
        "broadcast_ambient_master",
        "streaming_immersive_ambient",
        "ferry_preserve_atmosphere",
        "driving_music_first_chain",
        "sleep_calm_ambient",
    ):
        assert cid in chains, cid


def test_ferry_forbidden_filters() -> None:
    doc = load_chains_document()
    ferry = doc["chains"]["ferry_preserve_atmosphere"]
    forbidden = set(ferry["fairlight"].get("forbidden") or [])
    forbidden |= set(ferry["ffmpeg_fallback"].get("forbidden") or [])
    for f in FERRY_FORBIDDEN_FILTERS:
        assert f in forbidden or f.replace("ai_voice_isolation", "voice_isolation") in str(forbidden)


def test_ferry_ffmpeg_has_no_afftdn() -> None:
    af, _ = build_ffmpeg_af_for_chain("ferry_preserve_atmosphere")
    low = af.lower()
    for banned in ("afftdn", "demucs", "anlmdn"):
        assert banned not in low, banned


def test_youtube_preset_mapping() -> None:
    assert youtube_preset_to_ambient_chain("youtube_ferry_real") == "ferry_preserve_atmosphere"
    assert youtube_preset_to_ambient_chain("youtube_long_calm") == "driving_music_first_chain"
    assert youtube_preset_to_ambient_chain("youtube_shorts_cinematic") == "streaming_immersive_ambient"


def test_preset_rules_have_ambient_chain() -> None:
    for pid in ("youtube_long_calm", "youtube_ferry_real", "youtube_shorts_cinematic"):
        assert "ambient_chain_id" in PRESET_RULES[pid]
        assert ambient_chain_for_preset(pid) == PRESET_RULES[pid]["ambient_chain_id"]


def main() -> int:
    test_chains_schema_valid()
    test_all_five_chains_present()
    test_ferry_forbidden_filters()
    test_ferry_ffmpeg_has_no_afftdn()
    test_youtube_preset_mapping()
    test_preset_rules_have_ambient_chain()
    print("test_ambient_chains: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
