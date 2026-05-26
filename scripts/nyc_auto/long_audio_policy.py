#!/usr/bin/env python3
"""NYC long-form automatic audio policy v1 (path/metadata cues, no Demucs here)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

AUDIO_POLICY_VERSION = "long_audio_policy_v1"

_VALID_MODES = frozenset({"music", "real_sound", "clean_ambient", "no_vocals_clean_ambient"})

_NOISE_CUES = (
    "truck",
    "rattle",
    "metal",
    "bad_audio",
    "noisy",
    "road_noise",
    "cab_noise",
    "frame_noise",
)

_REAL_CUES = (
    "ferry",
    "waterfront",
    "sunset",
    "rain",
    "ambient",
    "real_sound",
    "street_ambient",
    "harbor",
    "skyline",
)

_VOICE_CUES = (
    "vocals",
    "people_talking",
    "conversation",
    "voice",
    "talking",
    "no_vocals",
)

_CLEAN_CUES = (
    "clean_ambient",
    "no_vocals_cfr",
    "fixed",
    "final_real_sound_video",
    "audio_clean",
    "final_audio_clean",
)


def _blob(video_path: Path, metadata: Optional[dict[str, Any]], source_info: Optional[dict[str, Any]]) -> str:
    parts = [str(video_path), video_path.name.lower()]
    if metadata:
        parts.append(str(metadata))
    if source_info:
        parts.append(str(source_info))
    return " ".join(parts).lower()


def _matches(blob: str, cues: tuple[str, ...]) -> list[str]:
    return [c for c in cues if c.replace("_", " ") in blob or c in blob]


def path_suggests_cleaned_audio(video_path: Path) -> bool:
    s = str(video_path).lower()
    return any(c.replace("_", " ") in s or c in s for c in _CLEAN_CUES)


def find_no_vocals_asset(video_path: Path) -> Optional[Path]:
    """Prefer an existing no-vocals video/audio sibling; no Demucs."""
    p = video_path.expanduser()
    if not p.is_file():
        return None
    low = p.name.lower()
    if "no_vocals" in low and p.suffix.lower() in (".mp4", ".mov", ".m4v", ".wav", ".m4a"):
        return p.resolve()
    parent = p.parent
    for pattern in ("*no_vocals*.mp4", "*no_vocals*.mov", "*novocals*.mp4"):
        for hit in sorted(parent.glob(pattern), key=lambda x: len(x.name)):
            if hit.is_file():
                return hit.resolve()
    fixed = parent / "fixed"
    if fixed.is_dir():
        for pattern in ("*no_vocals*.mp4", "*no_vocals*.mov", "*no_vocals*.wav"):
            for hit in sorted(fixed.glob(pattern), key=lambda x: len(x.name)):
                if hit.is_file():
                    return hit.resolve()
    return None


def _base_policy(
    *,
    audio_mode: str,
    reason: str,
    matched: list[str],
    warnings: list[str],
    original_volume: float = 0.0,
    music_volume: float = 0.30,
    music_category: str = "night_drive",
    use_real_sound_gate: bool = False,
    use_no_vocals: bool = False,
    use_music: bool = False,
) -> dict[str, Any]:
    return {
        "audio_mode": audio_mode,
        "audio_policy_version": AUDIO_POLICY_VERSION,
        "original_volume": float(original_volume),
        "music_volume": float(music_volume),
        "music_category": str(music_category),
        "use_real_sound_gate": bool(use_real_sound_gate),
        "use_no_vocals": bool(use_no_vocals),
        "use_music": bool(use_music),
        "reason": reason,
        "matched_cues": list(matched),
        "warnings": list(warnings),
    }


def _flags_for_mode(am: str) -> dict[str, Any]:
    am = am.strip().lower()
    if am == "music":
        return {
            "use_music": True,
            "use_real_sound_gate": False,
            "use_no_vocals": False,
            "original_volume": 0.0,
            "music_volume": 0.30,
        }
    if am == "real_sound":
        return {
            "use_music": False,
            "use_real_sound_gate": True,
            "use_no_vocals": False,
            "original_volume": 1.0,
            "music_volume": 0.0,
        }
    if am == "clean_ambient":
        return {
            "use_music": False,
            "use_real_sound_gate": False,
            "use_no_vocals": False,
            "original_volume": 1.0,
            "music_volume": 0.0,
        }
    if am == "no_vocals_clean_ambient":
        return {
            "use_music": False,
            "use_real_sound_gate": True,
            "use_no_vocals": True,
            "original_volume": 0.85,
            "music_volume": 0.0,
        }
    return _flags_for_mode("music")


def infer_long_audio_policy(
    video_path: Path,
    metadata: Optional[dict[str, Any]] = None,
    source_info: Optional[dict[str, Any]] = None,
    user_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    blob = _blob(video_path, metadata, source_info)

    cleaned_hits = _matches(blob, _CLEAN_CUES)
    if cleaned_hits:
        pol = _base_policy(
            audio_mode="clean_ambient",
            reason="already_cleaned_audio",
            matched=cleaned_hits,
            warnings=warnings,
            **_flags_for_mode("clean_ambient"),
        )
    else:
        voice_hits = _matches(blob, _VOICE_CUES)
        if voice_hits and "no_vocals_cfr" not in blob and "no_vocals_cfr30" not in blob:
            pol = _base_policy(
                audio_mode="no_vocals_clean_ambient",
                reason="voice_removal_clean_ambient",
                matched=voice_hits,
                warnings=warnings,
                **_flags_for_mode("no_vocals_clean_ambient"),
            )
        else:
            noise_hits = _matches(blob, _NOISE_CUES)
            if noise_hits:
                fl = _flags_for_mode("music")
                mv = 0.32 if len(noise_hits) > 1 else 0.30
                fl["music_volume"] = mv
                pol = _base_policy(
                    audio_mode="music",
                    reason="noise_heavy_default_music",
                    matched=noise_hits,
                    warnings=warnings,
                    **fl,
                )
            else:
                real_hits = _matches(blob, _REAL_CUES)
                if real_hits:
                    pol = _base_policy(
                        audio_mode="real_sound",
                        reason="valuable_real_sound_cues",
                        matched=real_hits,
                        warnings=warnings,
                        **_flags_for_mode("real_sound"),
                    )
                else:
                    pol = _base_policy(
                        audio_mode="music",
                        reason="default_nyc_drive_music",
                        matched=[],
                        warnings=warnings,
                        **_flags_for_mode("music"),
                    )

    if user_override:
        am_ov = str(user_override.get("audio_mode") or "").strip().lower()
        if am_ov and am_ov != "auto":
            if am_ov not in _VALID_MODES:
                warnings.append(f"invalid_audio_mode_override:{am_ov}")
            else:
                fl = _flags_for_mode(am_ov)
                pol.update(fl)
                pol["audio_mode"] = am_ov
                pol["reason"] = "user_override"
        if user_override.get("music_volume") is not None:
            try:
                pol["music_volume"] = float(user_override["music_volume"])
            except (TypeError, ValueError):
                warnings.append("invalid_music_volume_override")
        if user_override.get("original_volume") is not None:
            try:
                pol["original_volume"] = float(user_override["original_volume"])
            except (TypeError, ValueError):
                warnings.append("invalid_original_volume_override")
        if str(user_override.get("music_category") or "").strip():
            pol["music_category"] = str(user_override["music_category"]).strip()

    return pol
