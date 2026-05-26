#!/usr/bin/env python3
"""StateVerge Content Truth & Audio Routing v1 — theme inference, title guard, audio intent."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

METADATA_POLICY_VERSION = "stateverge_content_routing_v1"
_CONFIG_NAME = "stateverge_content_routing_v1.json"
_STRATEGY_CONFIG = "stateverge_nyc_channel_strategy_v1.json"
_TITLE_TEMPLATES_CONFIG = "stateverge_title_templates_v1.json"

_REPO = Path(__file__).resolve().parents[2]
_CONFIG_PATH = _REPO / "config" / _CONFIG_NAME
_STRATEGY_PATH = _REPO / "config" / _STRATEGY_CONFIG
_TITLE_TEMPLATES_PATH = _REPO / "config" / _TITLE_TEMPLATES_CONFIG

_DAY_TOD = frozenset({"day", "morning", "afternoon"})
_NIGHT_TITLE_TERMS = ("night", "evening", "sunset")
_FERRY_TERMS = ("ferry",)
_DRIVING_TERMS = ("driving", "drive")
_SKYLINE_TERMS = ("skyline",)
_RAIN_TERMS = ("rain", "rainy", "wet")


def load_content_routing_config() -> dict[str, Any]:
    try:
        if _CONFIG_PATH.is_file():
            return json.loads(_CONFIG_PATH.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "version": METADATA_POLICY_VERSION,
        "audio_intent_by_source_type": {},
        "title_truth_rules": {"default_safe_titles": ["Calm NYC Street View"]},
        "title_templates": {"unknown": ["Calm NYC Street View"]},
    }


def load_channel_strategy_config() -> dict[str, Any]:
    try:
        if _STRATEGY_PATH.is_file():
            return json.loads(_STRATEGY_PATH.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def load_title_templates_v1_config() -> dict[str, Any]:
    try:
        if _TITLE_TEMPLATES_PATH.is_file():
            return json.loads(_TITLE_TEMPLATES_PATH.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {"templates": {}}


def channel_keywords() -> list[str]:
    cfg = load_channel_strategy_config()
    seo = cfg.get("seo_keywords") if isinstance(cfg.get("seo_keywords"), dict) else {}
    primary = list(seo.get("primary") or [])
    secondary = list(seo.get("secondary") or [])
    return primary + secondary


def build_description_template(theme: str, *, location: str = "NYC") -> str:
    """Ambient-experience description aligned with channel positioning v1."""
    loc = (location or "NYC").strip()
    if theme == "ferry":
        return (
            f"A relaxing New York Harbor ferry experience with real water and skyline ambience from {loc}. "
            "Original harbor sounds, lightly cleaned for smooth long-form listening. "
            "Best enjoyed as background ambience — no narration or montage."
        )
    if theme == "rain_night":
        return (
            f"A calm rainy-night atmosphere across {loc} streets. "
            "Soft urban ambience for focus, rest, or background viewing. "
            "Relaxing NYC ambient experience — not a vlog or documentary."
        )
    if theme == "skyline_sequence":
        return (
            f"A peaceful Manhattan skyline atmosphere with cinematic calm energy. "
            "Designed as a relaxing NYC ambient background scene."
        )
    if theme == "driving":
        return (
            f"A relaxing {loc} drive through New York City with calm ambient energy. "
            "Ideal for background watching, focus, or unwinding. "
            "Relaxing NYC ambient experience — not a vlog or montage."
        )
    return (
        f"A quiet New York City ambient moment around {loc}. "
        "Relaxing urban atmosphere for background viewing."
    )


def _path_slash_lower(p: str) -> str:
    return str(p or "").replace("\\", "/").lower()


def _basename(p: str) -> str:
    return Path(str(p or "")).name.lower()


def _infer_path_source_type(low_path: str, basename: str) -> str:
    fn = basename.lower()
    if "/ferry/" in low_path or "staten_island_ferry" in low_path or "ferry" in fn:
        return "ferry"
    if "/driving/" in low_path or any(
        tok in low_path for tok in ("/drive/", "dashcam", "dash_cam", "driving_route")
    ):
        return "driving"
    return ""


def _infer_path_route_type(source_type: str, low_path: str, basename: str) -> str:
    fn = basename.lower()
    if source_type == "ferry" or "/ferry/" in low_path or "ferry" in fn or "staten_island_ferry" in low_path:
        return "ferry_route"
    if source_type == "driving" or "/driving/" in low_path:
        return "driving_route"
    return ""


def _collect_paths(asset: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("path", "source_path", "output_video", "video", "vkey", "upload_video"):
        v = asset.get(key)
        if v:
            out.append(str(v))
    for key in ("source_paths", "selected_assets", "paths", "sources"):
        v = asset.get(key)
        if isinstance(v, list):
            out.extend(str(x) for x in v if x)
        elif isinstance(v, str) and v.strip():
            out.append(v)
    if isinstance(asset.get("basename"), str):
        out.append(str(asset["basename"]))
    return out


def _skyline_signals(asset: dict[str, Any], low_blob: str) -> bool:
    rt = str(asset.get("inferred_route_type") or asset.get("route_type") or "").strip().lower()
    if rt == "skyline_sequence":
        return True
    for key in ("scene_type", "search_keywords"):
        val = asset.get(key)
        if isinstance(val, str) and "skyline" in val.lower():
            return True
        if isinstance(val, list) and any("skyline" in str(x).lower() for x in val):
            return True
    return "skyline" in low_blob or "skyscrapers" in low_blob or "timelapse" in low_blob


def _infer_rain_night(asset: dict[str, Any], path_blob: str) -> bool:
    for key in ("weather", "scene_type", "title_theme"):
        v = str(asset.get(key) or "").lower()
        if any(t in v for t in _RAIN_TERMS):
            return True
    low = path_blob.lower()
    return any(t in low for t in _RAIN_TERMS) and any(
        t in low for t in ("night", "evening", "midnight", "after dark")
    )


def infer_content_theme(asset: dict[str, Any] | str) -> str:
    """Priority: ferry → rain_night → driving → skyline_sequence → unknown."""
    if isinstance(asset, str):
        asset = {"path": asset}
    paths = _collect_paths(asset)
    if not paths and asset.get("path"):
        paths = [str(asset["path"])]

    ferry_hit = False
    driving_hit = False
    skyline_hit = _skyline_signals(asset, " ".join(_path_slash_lower(p) for p in paths))

    explicit_st = str(
        asset.get("inferred_source_type") or asset.get("source_type") or ""
    ).strip().lower()
    explicit_rt = str(asset.get("route_type") or asset.get("inferred_route_type") or "").strip().lower()

    for raw in paths or [""]:
        low = _path_slash_lower(raw)
        base = _basename(raw)
        st = _infer_path_source_type(low, base) or explicit_st
        rt = _infer_path_route_type(st, low, base) or explicit_rt

        if "/ferry/" in low or rt == "ferry_route" or st == "ferry":
            ferry_hit = True
            break
        if rt == "ferry_route" or st == "ferry" or "ferry" in base:
            ferry_hit = True
            break

    if not ferry_hit:
        if explicit_st == "ferry" or explicit_rt == "ferry_route":
            ferry_hit = True
        for raw in paths or [""]:
            low = _path_slash_lower(raw)
            base = _basename(raw)
            st = _infer_path_source_type(low, base) or explicit_st
            rt = _infer_path_route_type(st, low, base) or explicit_rt
            if "/driving/" in low or rt == "driving_route" or st == "driving":
                driving_hit = True
                break
        if not driving_hit and (explicit_st == "driving" or explicit_rt == "driving_route"):
            driving_hit = True

    blob = " ".join(_path_slash_lower(p) for p in paths)
    if ferry_hit:
        return "ferry"
    if _infer_rain_night(asset, blob):
        return "rain_night"
    if driving_hit:
        return "driving"
    if skyline_hit:
        return "skyline_sequence"
    return "unknown"


def _infer_time_of_day(asset: dict[str, Any], path_blob: str) -> str:
    for key in ("time_of_day", "timeofday", "time_of_day_hint"):
        v = str(asset.get(key) or "").strip().lower()
        if v:
            return v
    low = path_blob.lower()
    for tok in ("morning", "afternoon", "evening", "night", "sunset", "midnight", "day"):
        if tok in low:
            return tok
    return ""


def build_title_evidence(asset: dict[str, Any], theme: str) -> dict[str, Any]:
    paths = _collect_paths(asset)
    blob = " ".join(paths).lower()
    tod = _infer_time_of_day(asset, blob)
    return {
        "time_of_day": tod,
        "theme": theme,
        "has_ferry": theme == "ferry",
        "has_driving": theme == "driving",
        "has_skyline": theme == "skyline_sequence" or _skyline_signals(asset, blob),
        "path_blob": blob[:2000],
    }


def _title_violates_term(title: str, term: str) -> bool:
    t = title.lower()
    if term.lower() in ("drive", "driving"):
        return bool(re.search(r"\b(driving|drive)\b", t, re.I))
    return term.lower() in t


def apply_title_truth_guard(
    title: str,
    theme: str,
    evidence: dict[str, Any],
) -> tuple[str, list[str]]:
    """Return (safe_title, blocked_title_terms)."""
    cfg = load_content_routing_config()
    rules = cfg.get("title_truth_rules") if isinstance(cfg.get("title_truth_rules"), dict) else {}
    blocked: list[str] = []
    safe = (title or "").strip()
    tod = str(evidence.get("time_of_day") or "").strip().lower()
    has_ferry = bool(evidence.get("has_ferry"))
    has_driving = bool(evidence.get("has_driving"))
    has_skyline = bool(evidence.get("has_skyline"))

    def _block(term: str) -> None:
        if term not in blocked:
            blocked.append(term)

    if tod in _DAY_TOD:
        for term in _NIGHT_TITLE_TERMS:
            if _title_violates_term(safe, term):
                _block(term)

    if not has_ferry:
        for term in _FERRY_TERMS:
            if _title_violates_term(safe, term):
                _block(term)

    if not has_driving:
        for term in _DRIVING_TERMS:
            if _title_violates_term(safe, term):
                _block(term)

    if not has_skyline:
        for term in _SKYLINE_TERMS:
            if _title_violates_term(safe, term):
                _block(term)

    forbidden = list(rules.get("forbidden_without_evidence") or [])
    for term in forbidden:
        if not term:
            continue
        tl = term.lower()
        if tl in ("ferry",) and has_ferry:
            continue
        if tl in ("driving", "drive") and has_driving:
            continue
        if tl == "skyline" and has_skyline:
            continue
        if tl in ("night", "sunset", "evening") and tod not in _DAY_TOD:
            continue
        if tl in ("manhattan", "brooklyn") and theme != "unknown":
            continue
        if _title_violates_term(safe, term):
            _block(term)

    insufficient = theme == "unknown" or bool(blocked)
    if insufficient or not safe:
        tpl_theme = theme if theme in ("driving", "ferry", "skyline_sequence", "rain_night", "unknown") else "unknown"
        safe = pick_title_template(tpl_theme, str(evidence.get("seed") or "unknown"), evidence=evidence)
        if blocked and tpl_theme == "unknown":
            defaults = list(rules.get("default_safe_titles") or [])
            if defaults:
                safe = defaults[0]
    elif blocked:
        tpl_theme = theme if theme in ("driving", "ferry", "skyline_sequence", "rain_night", "unknown") else "unknown"
        safe = pick_title_template(tpl_theme, str(evidence.get("seed") or safe), evidence=evidence)

    return sanitize_title_v1(safe)[:100], blocked


def _resolve_location_token(path_blob: str) -> str:
    tpl_cfg = load_title_templates_v1_config()
    tokens = tpl_cfg.get("location_tokens") if isinstance(tpl_cfg.get("location_tokens"), dict) else {}
    low = path_blob.lower()
    for needle in ("brooklyn", "manhattan", "queens", "bronx", "ferry", "waterfront"):
        if needle in low and needle in tokens:
            return str(tokens[needle])
    return str(tokens.get("default") or "Manhattan")


def _title_bucket_for_theme(theme: str, evidence: dict[str, Any]) -> str:
    tod = str(evidence.get("time_of_day") or "").strip().lower()
    if theme == "ferry":
        if tod in ("sunset", "evening", "golden hour", "dusk"):
            return "sunset"
        return "default"
    if theme == "driving":
        if any(t in str(evidence.get("path_blob") or "").lower() for t in _RAIN_TERMS):
            return "rain"
        if tod in _NIGHT_TITLE_TERMS or tod == "night":
            return "night"
        return "day"
    if theme == "rain_night":
        return "default"
    if theme == "skyline_sequence":
        return "default"
    return "default"


def pick_title_template(theme: str, seed: str, *, evidence: dict[str, Any] | None = None) -> str:
    ev = evidence or {}
    v1 = load_title_templates_v1_config()
    v1_tpl = v1.get("templates") if isinstance(v1.get("templates"), dict) else {}
    loc = _resolve_location_token(str(ev.get("path_blob") or ""))
    bucket = _title_bucket_for_theme(theme, ev)

    if theme in v1_tpl and isinstance(v1_tpl[theme], dict):
        branch = v1_tpl[theme]
        opts = list(branch.get(bucket) or branch.get("default") or [])
        if opts:
            idx = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16) % len(opts)
            return str(opts[idx]).format(location=loc)

    cfg = load_content_routing_config()
    templates = cfg.get("title_templates") if isinstance(cfg.get("title_templates"), dict) else {}
    key = theme if theme in templates else "unknown"
    opts = list(templates.get(key) or templates.get("unknown") or ["Calm NYC Street View"])
    if not opts:
        opts = ["Calm NYC Street View"]
    idx = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16) % len(opts)
    return str(opts[idx])


def pick_shorts_title_template(theme: str, seed: str) -> str:
    v1 = load_title_templates_v1_config()
    v1_tpl = v1.get("templates") if isinstance(v1.get("templates"), dict) else {}
    shorts = v1_tpl.get("shorts") if isinstance(v1_tpl.get("shorts"), dict) else {}
    key = theme if theme in shorts else "unknown"
    opts = list(shorts.get(key) or shorts.get("unknown") or ["NYC Ambient Moment"])
    idx = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16) % len(opts)
    return str(opts[idx])


def sanitize_title_v1(title: str) -> str:
    """Strip resolution spam, raw filenames, and forbidden tokens from titles."""
    cfg = load_channel_strategy_config()
    policy = cfg.get("title_policy") if isinstance(cfg.get("title_policy"), dict) else {}
    forbid = list(policy.get("forbid_in_title") or [])
    tpl_cfg = load_title_templates_v1_config()
    forbid.extend(list(tpl_cfg.get("forbidden_title_tokens") or []))

    safe = (title or "").strip()
    if not safe:
        return safe
    # Drop path-like / camera filename stems
    if re.search(r"(?:^|[\s|])(?:IMG_|DSC_|GH0|PXL_|MVIMG_|DJI_)\d", safe, re.I):
        safe = re.sub(r"(?:^|[\s|])(?:IMG_|DSC_|GH0|PXL_|MVIMG_|DJI_)[^\s|]+", "", safe, flags=re.I).strip()
    if re.search(r"\.(?:mp4|mov|m4v|mkv)\b", safe, re.I):
        safe = re.sub(r"\.(?:mp4|mov|m4v|mkv)\b", "", safe, flags=re.I).strip()
    for tok in forbid:
        if not tok:
            continue
        safe = re.sub(re.escape(tok), "", safe, flags=re.I)
    safe = re.sub(r"\s{2,}", " ", safe).strip(" |-")
    max_len = int(policy.get("max_title_length") or 100)
    return safe[:max_len]


def get_audio_intent(theme: str) -> dict[str, Any]:
    cfg = load_content_routing_config()
    by_type = cfg.get("audio_intent_by_source_type")
    if not isinstance(by_type, dict):
        by_type = {}
    key = theme if theme in by_type else "unknown"
    intent = dict(by_type.get(key) or by_type.get("unknown") or {})
    intent.setdefault("audio_mode", "safe_neutral")
    intent.setdefault("music_required", False)
    intent.setdefault("music_disabled_by_default", False)
    return intent


def build_content_routing_bundle(
    asset: dict[str, Any] | str,
    *,
    job_id: str = "",
    title: str = "",
) -> dict[str, Any]:
    """Build title + metadata audio intent.

    **Shorts encode note:** ``shorts_cut_upload_job`` uses source/original audio (原声) by default.
    ``audio_mode`` here is metadata intent only; ffmpeg may set ``audio_intent_overridden=source_original``
    when encode does not add a music bed (see ``shorts_job_result.json``).
    """
    if isinstance(asset, str):
        asset = {"path": asset}
    theme = infer_content_theme(asset)
    evidence = build_title_evidence(asset, theme)
    if job_id:
        evidence["seed"] = job_id
    raw_title = (title or "").strip()
    if not raw_title:
        raw_title = pick_title_template(theme, job_id or str(asset.get("job_id") or "stateverge"), evidence=evidence)
    raw_title = sanitize_title_v1(raw_title)
    safe_title, blocked = apply_title_truth_guard(raw_title, theme, evidence)
    audio = get_audio_intent(theme)
    title_theme = theme if theme != "generic" else "unknown"
    desc_tpl = build_description_template(theme, location=_resolve_location_token(str(evidence.get("path_blob") or "")))
    return {
        "inferred_theme": theme,
        "title_theme": title_theme,
        "channel_keywords": channel_keywords()[:12],
        "description_template": desc_tpl,
        "audio_mode": str(audio.get("audio_mode") or ""),
        "music_required": bool(audio.get("music_required")),
        "music_disabled_by_default": bool(audio.get("music_disabled_by_default")),
        "real_sound_secondary": bool(audio.get("real_sound_secondary")),
        "allowed_audio_filter": str(audio.get("allowed_audio_filter") or ""),
        "audio_intent_description": str(audio.get("description") or ""),
        "shorts_encode_audio_default": "source_original",
        "title": safe_title,
        "title_evidence": evidence,
        "blocked_title_terms": blocked,
        "title_template_used": safe_title if safe_title == raw_title else pick_title_template(theme, job_id or "x"),
        "metadata_policy_version": METADATA_POLICY_VERSION,
        "content_routing_version": str(load_content_routing_config().get("version") or METADATA_POLICY_VERSION),
    }


def merge_content_routing_into_meta(meta: dict[str, Any], bundle: dict[str, Any]) -> dict[str, Any]:
    out = {**meta}
    for key in (
        "inferred_theme",
        "title_theme",
        "audio_mode",
        "music_required",
        "music_disabled_by_default",
        "title_evidence",
        "blocked_title_terms",
        "metadata_policy_version",
        "content_routing_version",
        "title_template_used",
        "real_sound_secondary",
        "allowed_audio_filter",
        "audio_intent_description",
        "channel_keywords",
        "description_template",
    ):
        if key in bundle and bundle[key] is not None and bundle[key] != "":
            out[key] = bundle[key]
    if bundle.get("title"):
        out["title"] = bundle["title"]
    out.setdefault("warnings", [])
    if isinstance(out["warnings"], list) and bundle.get("blocked_title_terms"):
        out["warnings"].append("title_truth_guard_applied")
    return out


def infer_shorts_title_routing(source_paths: list[str], job_id: str) -> dict[str, str]:
    """Backward-compatible wrapper for shorts_cut_upload_job."""
    asset: dict[str, Any] = {"source_paths": source_paths, "job_id": job_id}
    bundle = build_content_routing_bundle(asset, job_id=job_id)
    st = infer_content_theme(asset)
    rt = ""
    for raw in source_paths:
        low = _path_slash_lower(raw)
        base = _basename(raw)
        src = _infer_path_source_type(low, base)
        r = _infer_path_route_type(src, low, base)
        if r:
            rt = r
            break
    return {
        "inferred_theme": bundle["inferred_theme"],
        "title_theme": bundle["title_theme"],
        "source_type": st or bundle["inferred_theme"],
        "route_type": rt,
        "title_template_used": str(bundle.get("title_template_used") or bundle.get("title") or ""),
        "audio_mode": str(bundle.get("audio_mode") or ""),
        "music_required": str(bundle.get("music_required")),
        "music_disabled_by_default": str(bundle.get("music_disabled_by_default")),
        "title_evidence": json.dumps(bundle.get("title_evidence") or {}, ensure_ascii=False),
        "blocked_title_terms": ",".join(bundle.get("blocked_title_terms") or []),
        "metadata_policy_version": METADATA_POLICY_VERSION,
    }
