#!/usr/bin/env python3
"""Production presets — map to davinci_audio_finish + davinci_studio modes."""

from __future__ import annotations

from typing import Any, Literal

ProductionPresetId = Literal["long_driving", "long_ferry", "shorts_cinematic"]

PRODUCTION_PRESET_IDS: tuple[str, ...] = ("long_driving", "long_ferry", "shorts_cinematic")
PRESET_IDS = PRODUCTION_PRESET_IDS

PRESET_LABELS: dict[str, str] = {
    "long_driving": "Long Driving (30fps, Suno music-first, mild EQ, YouTube loudness)",
    "long_ferry": "Long Ferry (real ambience, no default music, light wind, mild limiter)",
    "shorts_cinematic": "Shorts (Envato music-first, cinematic, 1080x1920, title truth guard)",
}

# Maps production preset → existing gate/studio identifiers.
PRODUCTION_PRESET_RULES: dict[str, dict[str, Any]] = {
    "long_driving": {
        "timeline_fps": 30,
        "davinci_audio_finish_preset": "youtube_long_calm",
        "ambient_chain_id": "driving_music_first_chain",
        "davinci_studio_audio_mode": "driving_music_first",
        "add_music_default": True,
        "ambience_bus_db_range": (-50, -70),
        "shorts": False,
        "resolution": "1920x1080",
        "forbidden": ("afftdn", "robotic_denoise", "ffmpeg_copy_trim_vfr"),
    },
    "long_ferry": {
        "timeline_fps": 30,
        "davinci_audio_finish_preset": "youtube_ferry_real",
        "ambient_chain_id": "ferry_preserve_atmosphere",
        "davinci_studio_audio_mode": "ferry_wind_light",
        "add_music_default": False,
        "preserve_atmosphere": True,
        "shorts": False,
        "resolution": "1920x1080",
        "forbidden": ("default_music", "afftdn", "demucs", "ffmpeg_copy_trim_vfr"),
    },
    "shorts_cinematic": {
        "timeline_fps": 30,
        "davinci_audio_finish_preset": "youtube_shorts_cinematic",
        "ambient_chain_id": "streaming_immersive_ambient",
        "davinci_studio_audio_mode": "add_envato_music",
        "add_music_default": True,
        "shorts": True,
        "resolution": "1080x1920",
        "title_truth_guard": True,
        "forbidden": ("ffmpeg_copy_trim_vfr",),
    },
}


def canonical_production_preset(preset: str | None) -> str:
    p = (preset or "long_driving").strip().lower()
    if p in PRODUCTION_PRESET_IDS:
        return p
    aliases = {
        "driving": "long_driving",
        "ferry": "long_ferry",
        "shorts": "shorts_cinematic",
        "youtube_long_calm": "long_driving",
        "youtube_ferry_real": "long_ferry",
        "youtube_shorts_cinematic": "shorts_cinematic",
    }
    return aliases.get(p, "long_driving")


def preset_rules(preset_id: str) -> dict[str, Any]:
    return dict(PRODUCTION_PRESET_RULES.get(canonical_production_preset(preset_id), {}))
