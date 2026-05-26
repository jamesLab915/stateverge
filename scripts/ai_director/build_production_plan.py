#!/usr/bin/env python3
"""Apply content-mapping rules and build validated production plan JSON."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .director_prompt import DIRECTOR_AI_VERSION
from .production_plan_schema import PLAN_SCHEMA_VERSION, apply_defaults, validate_plan
from .prompt_parser import parse_natural_language, try_llm_parse

_REPO = Path(__file__).resolve().parents[2]
_PLANS_DIR = _REPO / "data" / "production_plans"


def _apply_content_mapping(plan: dict[str, Any]) -> dict[str, Any]:
    """Map content_type / mood / output_type → audio, music, davinci preset."""
    out = dict(plan)
    ct = str(out.get("content_type") or out.get("source_type") or "").strip().lower()
    st = str(out.get("source_type") or ct or "unknown").strip().lower()
    mood = str(out.get("mood") or "").strip().lower()
    output_type = str(out.get("output_type") or "long").strip().lower()

    if ct == "ferry" or st == "ferry":
        out["content_type"] = "ferry"
        out["source_type"] = "ferry"
        out["audio_mode"] = "real_ambience_primary"
        out["music_enabled"] = False
        out["davinci_preset"] = "youtube_ferry_real"
        if not str(out.get("route_type") or "").strip():
            out["route_type"] = "ferry_route"

    elif ct == "driving" or st == "driving":
        out["content_type"] = "driving"
        out["source_type"] = "driving"
        out["audio_mode"] = "music_first"
        out["music_enabled"] = True
        out["davinci_preset"] = "youtube_long_calm"
        if not str(out.get("route_type") or "").strip():
            out["route_type"] = "driving_route"

    elif ct == "cinematic" or st == "skyline_sequence":
        out["content_type"] = "cinematic"
        out["source_type"] = "skyline_sequence"
        out["audio_mode"] = "cinematic_music_first"
        out["music_enabled"] = True
        out["music_style"] = "envato_cinematic"
        out["davinci_preset"] = "youtube_long_calm"

    if mood in ("sleep", "relax", "relaxing") or "sleep" in mood:
        out["mood"] = "sleep"
        out["music_style"] = "piano_soft_synth"
        if str(out.get("content_type")) == "driving":
            out["music_style"] = "calm_ambient"
            out["environment_priority"] = "calm_loop"

    if mood == "calm" and str(out.get("content_type")) == "driving":
        if str(out.get("music_style") or "") in ("", "neutral"):
            out["music_style"] = "calm_ambient"

    if output_type == "shorts":
        out["output_type"] = "shorts"
        if str(out.get("content_type") or "") in ("", "unknown"):
            out["content_type"] = "shorts"
        if str(out.get("source_type") or "") in ("", "unknown"):
            out["source_type"] = "urban"
        sec = int(out.get("duration_target_sec") or 30)
        out["duration_target_sec"] = max(15, min(45, sec))
        out["davinci_preset"] = "youtube_shorts_cinematic"
        if str(out.get("shorts_style") or "") in ("", "none"):
            out["shorts_style"] = "default"
        if not out.get("music_enabled"):
            out["music_enabled"] = True
        if str(out.get("audio_mode") or "") in ("", "safe_neutral"):
            out["audio_mode"] = "add_envato_music"
        if str(out.get("music_style") or "") in ("", "neutral"):
            out["music_style"] = "envato_cinematic"

    if bool(out.get("wants_real_sound")) and ct == "ferry":
        out["audio_mode"] = "real_ambience_primary"
        out["music_enabled"] = False

    if bool(out.get("has_rain")) and str(out.get("time_of_day")) == "rainy_night":
        out["environment_priority"] = "weather_rain"

    out.pop("wants_real_sound", None)
    out.pop("has_rain", None)
    out.pop("is_short", None)
    if str(out.get("output_type") or "").strip().lower() != "shorts":
        out["shorts_style"] = "none"
    return out


def build_production_plan(
    text: str,
    *,
    use_llm: bool = False,
) -> dict[str, Any]:
    partial = parse_natural_language(text)
    llm_data = try_llm_parse(text, enabled=use_llm)
    if isinstance(llm_data, dict):
        for k, v in llm_data.items():
            if k in partial and v:
                partial[k] = v
            elif v is not None:
                partial[k] = v

    merged = apply_defaults(partial)
    merged = _apply_content_mapping(merged)
    merged["plan_schema_version"] = PLAN_SCHEMA_VERSION
    merged["director_ai_version"] = DIRECTOR_AI_VERSION
    merged["source_prompt"] = (text or "").strip()
    if not str(merged.get("parser_mode") or "").strip():
        merged["parser_mode"] = partial.get("parser_mode", "rules_v1")

    errors = validate_plan(merged)
    if errors:
        merged["validation_errors"] = errors
    return merged


def plan_output_path(*, now: datetime | None = None) -> Path:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return _PLANS_DIR / f"{ts}.json"


def save_production_plan(plan: dict[str, Any], path: Path | None = None) -> Path:
    out_path = path or plan_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path
