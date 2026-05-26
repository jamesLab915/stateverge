#!/usr/bin/env python3
"""FFmpeg fallback filters matching Fairlight ambient chain intent."""

from __future__ import annotations

from typing import Any

from ambient_chains.loader import (
    FERRY_FORBIDDEN_FILTERS,
    GLOBAL_FORBIDDEN_FILTERS,
    chain_spec,
    load_chains_document,
)


def _assert_no_forbidden(filters: list[str], *, chain_id: str) -> list[str]:
    joined = ",".join(filters).lower()
    forbidden = list(GLOBAL_FORBIDDEN_FILTERS)
    if chain_id == "ferry_preserve_atmosphere":
        forbidden = list(FERRY_FORBIDDEN_FILTERS)
    hits = [f for f in forbidden if f.lower() in joined]
    if hits:
        raise ValueError(f"forbidden_filters_in_chain:{chain_id}:{hits}")
    return filters


def build_ffmpeg_af_for_chain(
    chain_id: str,
    *,
    shorts: bool = False,
) -> tuple[str, dict[str, Any]]:
    """Return comma-joined -af filter string and metadata."""
    spec = chain_spec(chain_id)
    fb = spec.get("ffmpeg_fallback") or {}
    meta: dict[str, Any] = {
        "ambient_chain": chain_id,
        "ffmpeg_fallback": True,
        "shorts": shorts,
    }

    if chain_id == "driving_music_first_chain":
        meta["note"] = "per_clip_and_bgm_mix_via_audio_modes"
        meta["per_clip_volume"] = fb.get("per_clip_volume", 0.30)
        meta["music_volume"] = fb.get("music_volume", 0.85)
        meta["loudnorm"] = fb.get("loudnorm")
        return "", meta

    filters: list[str] = []
    if chain_id == "streaming_immersive_ambient":
        key = "filters_shorts" if shorts else "filters_long"
        filters = list(fb.get(key) or fb.get("filters") or [])
        meta["integrated_lufs"] = fb.get(
            "integrated_lufs_shorts" if shorts else "integrated_lufs_long"
        )
    else:
        filters = list(fb.get("filters") or [])
        meta["integrated_lufs"] = fb.get("integrated_lufs")
    meta["true_peak_db"] = fb.get("true_peak_db")

    filters = _assert_no_forbidden(filters, chain_id=chain_id)
    af = ",".join(filters) if filters else ""
    meta["ffmpeg_filters"] = filters
    return af, meta


def merge_with_music_mix(
    chain_id: str,
    mix_filter_complex: str,
    *,
    use_loudnorm: bool = True,
) -> str:
    """Append loudnorm/limiter to an existing amix filter_complex when appropriate."""
    if chain_id in ("ferry_preserve_atmosphere", "sleep_calm_ambient"):
        return mix_filter_complex
    spec = chain_spec(chain_id)
    fb = spec.get("ffmpeg_fallback") or {}
    ln = fb.get("loudnorm") or "loudnorm=I=-15:LRA=11:TP=-1.5"
    if not use_loudnorm or not ln:
        return mix_filter_complex
    if mix_filter_complex.endswith("[aout]"):
        base = mix_filter_complex[: -len("[aout]")]
        return f"{base},{ln}[aout]"
    return mix_filter_complex
