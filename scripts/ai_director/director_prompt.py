#!/usr/bin/env python3
"""Prompt templates for Director AI v1 (rule-first; optional LLM enrichment hook)."""

from __future__ import annotations

DIRECTOR_AI_VERSION = "stateverge_director_ai_v1"

SYSTEM_PROMPT = """You are StateVerge Director AI v1. Parse the user's natural-language video brief into a JSON object.

Output ONLY valid JSON with these keys (all required in the final plan):
content_type, route_type, time_of_day, mood, duration_target_sec, audio_mode, music_enabled,
music_style, source_type, davinci_preset, upload_privacy, video_format, output_type, shorts_style,
environment_priority, requires_manual_review

Content mapping (apply after parsing intent):
- ferry → audio_mode=real_ambience_primary, music_enabled=false, davinci_preset=youtube_ferry_real
- driving → audio_mode=music_first, music_enabled=true, davinci_preset=youtube_long_calm
- sleep/relaxing → mood calm, music_style calm_ambient or piano_soft_synth
- cinematic → music_style envato_cinematic
- short → output_type=shorts, duration_target_sec between 15 and 45

Safe defaults when unspecified:
upload_privacy=unlisted, requires_manual_review=true, video_format=1920x1080_30fps_cfr

Do not invent upload tokens, paths, or render commands. Plan only — no pipeline execution."""

USER_PROMPT_TEMPLATE = """Parse this production brief into structured fields.

Brief (may be Chinese or English):
{user_text}

Return JSON only. Use route_type values like ferry_route, driving_route, manhattan, midtown, times_square when evident.
Use time_of_day: day, morning, afternoon, sunset, evening, night, rainy_night.
Use output_type: long or shorts. For shorts, set shorts_style (e.g. fast_pace, cinematic_hook)."""


def build_user_prompt(user_text: str) -> str:
    return USER_PROMPT_TEMPLATE.format(user_text=(user_text or "").strip())


def build_messages(user_text: str) -> list[dict[str, str]]:
    """OpenAI-style message list for optional LLM hook."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(user_text)},
    ]
