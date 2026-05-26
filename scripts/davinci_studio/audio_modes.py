#!/usr/bin/env python3
"""V1 strict audio protection for DaVinci Folder Studio (ffmpeg filters only).

Light processing only. Never aggressive denoise. Preserve ferry natural sound.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

AudioMode = Literal[
    "preserve_raw",
    "ferry_wind_light",
    "driving_music_first",
    "add_envato_music",
]

# Primary modes exposed in UI / API.
AUDIO_MODES: tuple[str, ...] = (
    "preserve_raw",
    "ferry_wind_light",
    "driving_music_first",
    "add_envato_music",
)

# Backward-compatible aliases (normalized away).
_MODE_ALIASES: dict[str, str] = {
    "ferry_preserve_raw": "preserve_raw",
    "ferry_preserve_v1": "ferry_wind_light",
}

MODE_LABELS: dict[str, str] = {
    "preserve_raw": "Preserve raw (AAC 192k, no filters)",
    "ferry_wind_light": "Ferry wind light (highpass 28 Hz, vol 0.75)",
    "ferry_preserve_raw": "Preserve raw (ferry alias)",
    "ferry_preserve_v1": "Ferry wind light (legacy alias)",
    "driving_music_first": "Driving music first (NYC long BGM)",
    "add_envato_music": "Add Envato music (mute original)",
}

AUDIO_PROTECTION_POLICY = "davinci_folder_studio_v1_strict_light_only"

_FERRY_MODES = frozenset({"ferry_wind_light"})
_FERRY_PATH_MARKERS = ("/ferry/", "/video169/ferry", "staten_island_ferry")

# Global forbidden processing (documented in metadata; never applied).
FORBIDDEN_FILTERS: tuple[str, ...] = (
    "afftdn",
    "anlmdn",
    "arnndn",
    "demucs",
    "ai_voice_isolation",
    "lowpass",
    "compand",
    "heavy_compressor",
)

SKIPPED_AGGRESSIVE_FILTERS: tuple[str, ...] = FORBIDDEN_FILTERS + (
    "highpass_above_40hz_on_ferry",
    "loudnorm_I-14_on_ferry_raw",
)

_FERRY_WIND_LIGHT_AF = "highpass=f=28,volume=0.75"

DEFAULT_ORIGINAL_AUDIO_VOLUME = 0.30
DEFAULT_MUSIC_VOLUME = 0.85
ORIGINAL_VOLUME_MIN = 0.25
ORIGINAL_VOLUME_MAX = 0.35
MUSIC_VOLUME_MIN = 0.75
MUSIC_VOLUME_MAX = 0.90

LOUDNORM_FILTER = "loudnorm=I=-16:LRA=14:TP=-1.5"
LOUDNORM_TARGET = "-16"


def _clamp(vol: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(vol)))


def canonical_mode(mode: str) -> str:
    m = (mode or "preserve_raw").strip().lower()
    m = _MODE_ALIASES.get(m, m)
    if m not in AUDIO_MODES:
        return "preserve_raw"
    return m


def is_ferry_mode(mode: str) -> bool:
    return canonical_mode(mode) in _FERRY_MODES


def clips_suggest_ferry(clip_paths: list[str] | None) -> bool:
    if not clip_paths:
        return False
    for raw in clip_paths:
        low = str(raw).lower().replace("\\", "/")
        if any(marker in low for marker in _FERRY_PATH_MARKERS):
            return True
        base = Path(raw).name.lower()
        if "ferry" in base or "staten_island_ferry" in base:
            return True
    return False


def resolve_ferry_defaults(
    *,
    audio_mode: str,
    add_music: bool,
    clip_paths: list[str] | None,
) -> tuple[str, bool, list[str]]:
    """Apply ferry channel defaults before normalize_mode."""
    warnings: list[str] = []
    mode = canonical_mode(audio_mode)
    music = bool(add_music)
    if not clips_suggest_ferry(clip_paths):
        return mode, music, warnings

    if mode == "preserve_raw":
        warnings.append("ferry_auto_audio_mode:ferry_wind_light")
        mode = "ferry_wind_light"
    if music and is_ferry_mode(mode):
        warnings.append("ferry_add_music_discouraged_ambience_primary")
    elif not music:
        warnings.append("ferry_add_music_default_false")

    return mode, music, warnings


def normalize_mode(mode: str, *, add_music: bool) -> str:
    m = canonical_mode(mode)
    if not add_music and m in ("driving_music_first", "add_envato_music"):
        return "preserve_raw"
    return m


def per_clip_audio_filter(
    mode: str,
    *,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
) -> str | None:
    """Return ffmpeg -af filter for a single clip, or None (preserve / AAC only)."""
    m = canonical_mode(mode)
    if m in ("preserve_raw",):
        return None
    if m == "ferry_wind_light":
        return _FERRY_WIND_LIGHT_AF
    if m == "driving_music_first":
        vol = _clamp(original_audio_volume, ORIGINAL_VOLUME_MIN, ORIGINAL_VOLUME_MAX)
        return f"volume={vol:.2f}"
    if m == "add_envato_music":
        return "volume=0"
    return None


def audio_filter_chain(
    mode: str,
    *,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    stage: Literal["per_clip", "bgm_mix", "encode"] = "per_clip",
) -> str | None:
    """Human-readable / logged filter chain for the given stage."""
    m = canonical_mode(mode)
    if stage == "per_clip":
        return per_clip_audio_filter(m, original_audio_volume=original_audio_volume)
    if stage == "bgm_mix":
        if m == "driving_music_first":
            ov = _clamp(original_audio_volume, ORIGINAL_VOLUME_MIN, ORIGINAL_VOLUME_MAX)
            mv = _clamp(music_volume, MUSIC_VOLUME_MIN, MUSIC_VOLUME_MAX)
            return (
                f"[0:a]volume={ov:.2f}[a0];"
                f"[1:a]aloop=loop=-1:size=2e+09,volume={mv:.2f},"
                "afade=t=in:st=0:d=3,afade=t=out:d=3[a1];"
                "[a0][a1]amix=inputs=2:duration=first:dropout_transition=2[aout]"
            )
        if m == "add_envato_music":
            mv = _clamp(music_volume, MUSIC_VOLUME_MIN, MUSIC_VOLUME_MAX)
            return (
                f"[1:a]aloop=loop=-1:size=2e+09,volume={mv:.2f},"
                "afade=t=in:st=0:d=3,afade=t=out:d=3[a1]"
            )
        return None
    if stage == "encode" and m in ("preserve_raw", "ferry_wind_light"):
        return f"-c:a aac -b:a 192k"
    return None


def needs_bgm_mix(mode: str, *, add_music: bool = False) -> bool:
    m = canonical_mode(mode)
    if m in ("driving_music_first", "add_envato_music"):
        return True
    return bool(add_music and is_ferry_mode(m))


def bgm_source(mode: str, *, add_music: bool = False) -> str:
    m = canonical_mode(mode)
    if m == "driving_music_first":
        return "nyc_long"
    if m == "add_envato_music":
        return "envato"
    if add_music and is_ferry_mode(m):
        return "nyc_long"
    return "none"


def allows_loudnorm(mode: str) -> bool:
    """Loudnorm only on driving/envato BGM paths — never ferry raw."""
    return canonical_mode(mode) in ("driving_music_first", "add_envato_music")


def build_audio_metadata(
    mode: str,
    *,
    add_music: bool,
    clip_paths: list[str] | None = None,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    use_loudnorm: bool = False,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    m = normalize_mode(mode, add_music=add_music)
    ov = _clamp(original_audio_volume, ORIGINAL_VOLUME_MIN, ORIGINAL_VOLUME_MAX)
    mv = _clamp(music_volume, MUSIC_VOLUME_MIN, MUSIC_VOLUME_MAX)
    per_clip = per_clip_audio_filter(m, original_audio_volume=ov)
    mix_chain = audio_filter_chain(
        m,
        original_audio_volume=ov,
        music_volume=mv,
        stage="bgm_mix",
    )
    chains: list[str] = []
    if per_clip:
        chains.append(f"per_clip:{per_clip}")
    if mix_chain:
        chains.append(f"bgm_mix:{mix_chain}")
    if not chains:
        chains.append("encode:-c:a aac -b:a 192k (no -af)")

    loudnorm_used = bool(use_loudnorm and allows_loudnorm(m))
    warnings = list(extra_warnings or [])
    warnings.append("forbidden_never_applied:" + ",".join(FORBIDDEN_FILTERS))

    return {
        "audio_mode": m,
        "add_music": bool(add_music),
        "original_audio_volume": round(ov, 3),
        "music_volume": round(mv, 3),
        "audio_filter_chain": " | ".join(chains),
        "per_clip_audio_filter": per_clip,
        "bgm_mix_filter_complex": mix_chain,
        "audio_protection_policy": AUDIO_PROTECTION_POLICY,
        "audio_was_heavily_processed": False,
        "denoise_used": False,
        "demucs_used": False,
        "voice_isolation_used": False,
        "loudnorm_used": loudnorm_used,
        "loudnorm_target": LOUDNORM_TARGET if loudnorm_used else None,
        "ferry_detected": clips_suggest_ferry(clip_paths),
        "skipped_aggressive_filters": list(SKIPPED_AGGRESSIVE_FILTERS),
        "warnings": warnings,
    }


def build_audio_policy(
    mode: str,
    *,
    add_music: bool,
    clip_paths: list[str] | None = None,
    original_audio_volume: float = DEFAULT_ORIGINAL_AUDIO_VOLUME,
    music_volume: float = DEFAULT_MUSIC_VOLUME,
    use_loudnorm: bool = False,
    extra_warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Alias for build_audio_metadata (backward compatible key layout)."""
    return build_audio_metadata(
        mode,
        add_music=add_music,
        clip_paths=clip_paths,
        original_audio_volume=original_audio_volume,
        music_volume=music_volume,
        use_loudnorm=use_loudnorm,
        extra_warnings=extra_warnings,
    )
