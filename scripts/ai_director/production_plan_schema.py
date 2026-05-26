#!/usr/bin/env python3
"""Production plan schema — required fields, defaults, validation."""

from __future__ import annotations

from typing import Any

PLAN_SCHEMA_VERSION = "stateverge_production_plan_v1"

REQUIRED_FIELDS: tuple[str, ...] = (
    "content_type",
    "route_type",
    "time_of_day",
    "mood",
    "duration_target_sec",
    "audio_mode",
    "music_enabled",
    "music_style",
    "source_type",
    "davinci_preset",
    "upload_privacy",
    "video_format",
    "output_type",
    "shorts_style",
    "environment_priority",
    "requires_manual_review",
)

SAFE_DEFAULTS: dict[str, Any] = {
    "content_type": "unknown",
    "route_type": "",
    "time_of_day": "day",
    "mood": "calm",
    "duration_target_sec": 3600,
    "audio_mode": "safe_neutral",
    "music_enabled": False,
    "music_style": "neutral",
    "source_type": "unknown",
    "davinci_preset": "youtube_long_calm",
    "upload_privacy": "unlisted",
    "video_format": "1920x1080_30fps_cfr",
    "output_type": "long",
    "shorts_style": "none",
    "environment_priority": "balanced",
    "requires_manual_review": True,
}

_METADATA_FIELDS: tuple[str, ...] = (
    "plan_schema_version",
    "director_ai_version",
    "source_prompt",
    "parser_mode",
)


def apply_defaults(plan: dict[str, Any]) -> dict[str, Any]:
    out = dict(SAFE_DEFAULTS)
    if isinstance(plan, dict):
        for k, v in plan.items():
            if v is None:
                continue
            if k in REQUIRED_FIELDS or k in _METADATA_FIELDS:
                out[k] = v
    out["plan_schema_version"] = PLAN_SCHEMA_VERSION
    out["requires_manual_review"] = bool(out.get("requires_manual_review", True))
    out["music_enabled"] = bool(out.get("music_enabled", False))
    try:
        out["duration_target_sec"] = int(out.get("duration_target_sec") or SAFE_DEFAULTS["duration_target_sec"])
    except (TypeError, ValueError):
        out["duration_target_sec"] = int(SAFE_DEFAULTS["duration_target_sec"])
    return out


def validate_plan(plan: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan_not_dict"]
    for field in REQUIRED_FIELDS:
        if field not in plan:
            errors.append(f"missing:{field}")
            continue
        val = plan[field]
        if field == "duration_target_sec":
            try:
                sec = int(val)
                if sec <= 0:
                    errors.append("invalid:duration_target_sec")
            except (TypeError, ValueError):
                errors.append("invalid:duration_target_sec")
            continue
        if field in ("music_enabled", "requires_manual_review"):
            if not isinstance(val, bool):
                errors.append(f"invalid_type:{field}")
            continue
        if val is None or (isinstance(val, str) and not str(val).strip()):
            errors.append(f"empty:{field}")
    ot = str(plan.get("output_type") or "").strip().lower()
    if ot == "shorts":
        try:
            sec = int(plan.get("duration_target_sec") or 0)
            if sec < 15 or sec > 45:
                errors.append("shorts_duration_out_of_range")
        except (TypeError, ValueError):
            errors.append("shorts_duration_out_of_range")
    return errors
