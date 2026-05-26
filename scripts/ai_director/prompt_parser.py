#!/usr/bin/env python3
"""Rule-based natural-language → partial production intent (CN/EN). Optional LLM hook."""

from __future__ import annotations

import json
import re
from typing import Any

from .director_prompt import DIRECTOR_AI_VERSION, build_messages

_PARSER_MODE_RULES = "rules_v1"

# Content / route / mood signals (lowercase matching on normalized text).
_FERRY_TERMS = ("ferry", "渡轮", "轮渡", "staten island ferry")
_DRIVING_TERMS = ("driving", "drive", "驾驶", "开车", "驾车")
_SHORT_TERMS = ("short", "shorts", "短视频", "竖屏")
_SLEEP_TERMS = ("睡觉", "睡眠", "入眠", "sleep", "助眠", "入睡")
_CALM_TERMS = ("安静", "平静", "calm", "放松", "relax", "舒缓", "循环", "loop", "咖啡店", "咖啡")
_FAST_TERMS = ("快", "节奏快", "fast", "energetic", "upbeat", "动感")
_RAIN_TERMS = ("雨", "rain", "rainy", "雨夜")
_CINEMATIC_TERMS = ("cinematic", "电影感", "电影", "大片")
_REAL_SOUND_TERMS = ("原声", "真实声音", "real sound", "real ambience", "环境音", "ambience only")

_SUNSET_TERMS = ("日落", "sunset", "golden hour", "黄昏")
_NIGHT_TERMS = ("夜", "夜晚", "夜间", "night", "evening", "midnight", "雨夜")
_DAY_TERMS = ("白天", "日间", "daytime", "morning", "afternoon", "上午", "下午")

_MANHATTAN_TERMS = ("曼哈顿", "manhattan")
_MIDTOWN_TERMS = ("midtown", "中城")
_TIMES_SQ_TERMS = ("时代广场", "times square", "times sq")

_HOUR_RE = re.compile(
    r"(?:(\d+(?:\.\d+)?)\s*(?:小时|hrs?|hours?))|(?:(\d+)\s*(?:分钟|mins?|minutes?))",
    re.IGNORECASE,
)
_SEC_RE = re.compile(r"(\d+)\s*(?:秒|secs?|seconds?)", re.IGNORECASE)


def _norm(text: str) -> str:
    return (text or "").strip().lower()


def _contains_any(blob: str, terms: tuple[str, ...]) -> bool:
    return any(t in blob for t in terms)


def parse_duration_sec(text: str, *, is_short: bool) -> int | None:
    if is_short:
        return 30
    norm = _norm(text)
    m = _HOUR_RE.search(norm)
    if m:
        if m.group(1):
            try:
                return max(1, int(float(m.group(1)) * 3600))
            except ValueError:
                pass
        if m.group(2):
            try:
                return max(1, int(m.group(2)) * 60)
            except ValueError:
                pass
    sm = _SEC_RE.search(norm)
    if sm:
        try:
            return max(1, int(sm.group(1)))
        except ValueError:
            pass
    if "3小时" in norm or "3 小时" in norm or "3h" in norm:
        return 10800
    if "2小时" in norm or "2 小时" in norm or "2h" in norm:
        return 7200
    if "1小时" in norm or "1 小时" in norm or "1h" in norm or "一小时" in norm:
        return 3600
    return None


def parse_natural_language(text: str) -> dict[str, Any]:
    """Deterministic rule parser — returns partial intent dict (hints + flags)."""
    raw = (text or "").strip()
    blob = _norm(raw)
    blob_compact = blob.replace(" ", "")

    is_short = _contains_any(blob, _SHORT_TERMS) or _contains_any(blob_compact, _SHORT_TERMS)
    is_ferry = _contains_any(blob, _FERRY_TERMS) or _contains_any(blob_compact, _FERRY_TERMS)
    is_driving = _contains_any(blob, _DRIVING_TERMS) or _contains_any(blob_compact, _DRIVING_TERMS)
    is_cinematic = _contains_any(blob, _CINEMATIC_TERMS)
    is_sleep = _contains_any(blob, _SLEEP_TERMS)
    is_calm = _contains_any(blob, _CALM_TERMS)
    is_fast = _contains_any(blob, _FAST_TERMS)
    has_rain = _contains_any(blob, _RAIN_TERMS)
    wants_real_sound = _contains_any(blob, _REAL_SOUND_TERMS)

    content_type = "unknown"
    source_type = "unknown"
    if is_ferry:
        content_type = "ferry"
        source_type = "ferry"
    elif is_driving:
        content_type = "driving"
        source_type = "driving"
    elif is_cinematic:
        content_type = "cinematic"
        source_type = "skyline_sequence"
    elif is_short:
        content_type = "shorts"
        source_type = "urban"

    route_type = ""
    if is_ferry:
        route_type = "ferry_route"
        if _contains_any(blob, _MANHATTAN_TERMS):
            route_type = "manhattan"
    elif is_driving:
        route_type = "driving_route"
        if _contains_any(blob, _MIDTOWN_TERMS):
            route_type = "midtown"
    if _contains_any(blob, _TIMES_SQ_TERMS):
        route_type = "times_square"

    time_of_day = "day"
    if _contains_any(blob, _SUNSET_TERMS):
        time_of_day = "sunset"
    elif has_rain and _contains_any(blob, _NIGHT_TERMS):
        time_of_day = "rainy_night"
    elif _contains_any(blob, _NIGHT_TERMS):
        time_of_day = "night"
    elif _contains_any(blob, _DAY_TERMS):
        time_of_day = "day"

    mood = "calm"
    if is_sleep:
        mood = "sleep"
    elif is_fast:
        mood = "energetic"
    elif is_calm:
        mood = "calm"
    elif is_cinematic:
        mood = "cinematic"

    duration = parse_duration_sec(raw, is_short=is_short)
    if duration is None and not is_short:
        duration = 3600

    output_type = "shorts" if is_short else "long"
    shorts_style = "none"
    if is_short:
        shorts_style = "fast_pace" if is_fast else "default"

    environment_priority = "balanced"
    if wants_real_sound or is_ferry:
        environment_priority = "real_ambience"
    elif has_rain:
        environment_priority = "weather_rain"
    elif is_driving:
        environment_priority = "music_bed"

    music_style = "neutral"
    if is_sleep or (is_calm and is_driving):
        music_style = "calm_ambient"
        if is_sleep:
            music_style = "piano_soft_synth"
    elif is_cinematic:
        music_style = "envato_cinematic"
    elif is_fast and is_short:
        music_style = "envato_upbeat"

    partial: dict[str, Any] = {
        "content_type": content_type,
        "source_type": source_type,
        "route_type": route_type,
        "time_of_day": time_of_day,
        "mood": mood,
        "duration_target_sec": duration,
        "output_type": output_type,
        "shorts_style": shorts_style,
        "environment_priority": environment_priority,
        "music_style": music_style,
        "wants_real_sound": wants_real_sound,
        "has_rain": has_rain,
        "is_short": is_short,
        "parser_mode": _PARSER_MODE_RULES,
        "source_prompt": raw,
        "director_ai_version": DIRECTOR_AI_VERSION,
    }
    return partial


def try_llm_parse(text: str, *, enabled: bool = False) -> dict[str, Any] | None:
    """Optional LLM hook — disabled in v1 unless explicitly enabled and key present."""
    if not enabled:
        return None
    try:
        import sys
        from pathlib import Path

        scripts = Path(__file__).resolve().parents[1]
        if str(scripts) not in sys.path:
            sys.path.insert(0, str(scripts))
        from ai.ai_client import ai_request, resolve_openai_api_key  # noqa: WPS433
    except ImportError:
        return None

    if not resolve_openai_api_key():
        return None

    messages = build_messages(text)
    try:
        resp = ai_request(
            task="director_production_plan_v1",
            messages=messages,
            max_tokens=800,
            response_format={"type": "json_object"},
        )
    except Exception:
        return None

    content = ""
    if isinstance(resp, dict):
        content = str(resp.get("content") or resp.get("text") or "")
    if not content.strip():
        return None
    try:
        data = json.loads(content)
        if isinstance(data, dict):
            data["parser_mode"] = "llm_v1"
            return data
    except json.JSONDecodeError:
        return None
    return None
