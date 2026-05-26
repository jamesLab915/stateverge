#!/usr/bin/env python3
"""YouTube audio finish presets — documented semantics + ffmpeg/Resolve mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

PresetId = Literal[
    "youtube_long_calm",
    "youtube_ferry_real",
    "youtube_shorts_cinematic",
    "auto",
]

PRESET_IDS: tuple[str, ...] = (
    "youtube_long_calm",
    "youtube_ferry_real",
    "youtube_shorts_cinematic",
    "auto",
)

PRESET_LABELS: dict[str, str] = {
    "youtube_long_calm": "YouTube long calm (music-first, -14 to -16 LUFS)",
    "youtube_ferry_real": "YouTube ferry real (ambience, light wind, no music)",
    "youtube_shorts_cinematic": "YouTube Shorts cinematic (music-first, louder)",
    "auto": "Auto (infer from path / content)",
}

# Preset rules (documented in code — ffmpeg fallback mirrors Fairlight intent).
PRESET_RULES: dict[str, dict[str, Any]] = {
    "youtube_long_calm": {
        "description": "Driving/music-first, low ambience bus, stable music, mild EQ, limiter -1.5dB, -14 to -16 LUFS",
        "davinci_studio_audio_mode": "driving_music_first",
        "ambient_chain_id": "driving_music_first_chain",
        "add_music_default": True,
        "use_loudnorm": True,
        "loudnorm_i": -15,
        "limiter_tp_db": -1.5,
        "music_volume": 0.85,
        "original_audio_volume": 0.30,
        "shorts": False,
    },
    "youtube_ferry_real": {
        "description": "Preserve ambience, light wind cleanup, no music, mild limiter, no aggressive voice isolation",
        "davinci_studio_audio_mode": "ferry_wind_light",
        "ambient_chain_id": "ferry_preserve_atmosphere",
        "add_music_default": False,
        "use_loudnorm": False,
        "loudnorm_i": None,
        "limiter_tp_db": -2.0,
        "music_volume": 0.0,
        "original_audio_volume": 1.0,
        "shorts": False,
        "forbidden": ("afftdn", "demucs", "ai_voice_isolation"),
    },
    "youtube_shorts_cinematic": {
        "description": "Music-first, stronger loudness, limiter -1.5dB, Shorts friendly",
        "davinci_studio_audio_mode": "add_envato_music",
        "ambient_chain_id": "streaming_immersive_ambient",
        "add_music_default": True,
        "use_loudnorm": True,
        "loudnorm_i": -14,
        "limiter_tp_db": -1.5,
        "music_volume": 0.88,
        "original_audio_volume": 0.28,
        "shorts": True,
    },
}


def ambient_chain_for_preset(preset: str) -> str:
    """Map youtube finish preset → Fairlight ambient chain id."""
    rules = preset_rules(preset)
    cid = rules.get("ambient_chain_id")
    if cid:
        return str(cid)
    try:
        from ambient_chains.loader import youtube_preset_to_ambient_chain  # noqa: WPS433

        return youtube_preset_to_ambient_chain(preset)
    except ImportError:
        return "broadcast_ambient_master"

_FERRY_MARKERS = ("ferry", "staten_island_ferry", "waterfront", "souno")
_DRIVING_MARKERS = ("drive", "driving", "night_drive", "highway", "long_master", "nyc_long")
_SHORTS_MARKERS = ("shorts", "short_", "_vertical_", "tiktok", "reels")


def canonical_preset(preset: str | None) -> str:
    p = (preset or "auto").strip().lower()
    if p in PRESET_IDS:
        return p
    return "auto"


def infer_preset_from_path(video_path: Path, *, content_kind: str = "auto") -> str:
    """Map driving/long calm → youtube_long_calm; ferry → youtube_ferry_real; shorts → youtube_shorts_cinematic."""
    kind = (content_kind or "auto").strip().lower()
    if kind == "short" or kind == "shorts":
        return "youtube_shorts_cinematic"

    low = str(video_path).lower().replace("\\", "/")
    name = video_path.name.lower()

    if any(m in low or m in name for m in _SHORTS_MARKERS):
        return "youtube_shorts_cinematic"
    if any(m in low or m in name for m in _FERRY_MARKERS):
        return "youtube_ferry_real"
    if any(m in low or m in name for m in _DRIVING_MARKERS):
        return "youtube_long_calm"
    return "youtube_long_calm"


def resolve_preset(
    video_path: Path,
    preset: str | None = None,
    *,
    content_kind: str = "auto",
) -> str:
    p = canonical_preset(preset)
    if p == "auto":
        return infer_preset_from_path(video_path, content_kind=content_kind)
    return p


def preset_rules(preset: str) -> dict[str, Any]:
    return dict(PRESET_RULES.get(preset, PRESET_RULES["youtube_long_calm"]))
