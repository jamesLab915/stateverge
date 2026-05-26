#!/usr/bin/env python3
"""StateVerge Auto Metadata Generation v1 — local templates (fail-open), optional OpenAI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

METADATA_GENERATOR_VERSION = "auto_metadata_generation_v1"

_LOCATION_ORDER = [
    ("financial district", "Financial District"),
    ("times square", "Times Square"),
    ("brooklyn", "Brooklyn"),
    ("manhattan", "Manhattan"),
    ("long island", "Long Island"),
    ("queens", "Queens"),
    ("bronx", "Bronx"),
    ("ferry", "Ferry"),
    ("waterfront", "Waterfront"),
    ("skyline", "Skyline"),
    ("nyc", "NYC"),
    ("new york", "NYC"),
]

_NIGHT_HINTS = ("night", "after dark", "evening", "midnight", "yewan")
_FERRY_HINTS = ("ferry", "waterfront", "harbor")
_SKYLINE_HINTS = ("skyline", "skyscrapers", "midtown")
_STREET_HINTS = ("street", "avenue", "boulevard", "drive", "traffic")
_RAIN_HINTS = ("rain", "rainy", "wet")
_BRIDGE_HINTS = ("bridge", "williamsburg", "manhattan bridge", "brooklyn bridge")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def safe_slugify(s: str, *, max_len: int = 80) -> str:
    raw = (s or "").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "-", raw)
    raw = re.sub(r"-+", "-", raw).strip("-")
    return raw[:max_len] if raw else "stateverge"


def ffprobe_json(path: Path) -> dict[str, Any]:
    exe = shutil.which("ffprobe") or "ffprobe"
    cmd = [exe, "-v", "error", "-show_entries", "format=duration:stream=width,height,avg_frame_rate,codec_name", "-of", "json", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return {}
        return json.loads(r.stdout or "{}")
    except (json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return {}


def _probe_dims_duration(probe: dict[str, Any]) -> tuple[int, int, float, str]:
    fmt = probe.get("format") if isinstance(probe.get("format"), dict) else {}
    dur = 0.0
    try:
        dur = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    streams = probe.get("streams")
    if not isinstance(streams, list):
        return 0, 0, dur, ""
    for st in streams:
        if not isinstance(st, dict):
            continue
        w = int(st.get("width") or 0)
        h = int(st.get("height") or 0)
        if w and h:
            afr = str(st.get("avg_frame_rate") or "")
            return w, h, dur, afr
    return 0, 0, dur, ""


def infer_location_from_path(path_str: str) -> str:
    low = path_str.lower()
    for needle, label in _LOCATION_ORDER:
        if needle in low:
            return label
    return "NYC"


def infer_content_type(path_str: str, *, style: str = "auto") -> str:
    st = (style or "auto").strip().lower()
    if st not in ("auto",):
        return st
    low = path_str.lower()
    if any(x in low for x in _FERRY_HINTS):
        return "ferry"
    if any(x in low for x in _SKYLINE_HINTS):
        return "skyline"
    if any(x in low for x in _RAIN_HINTS):
        return "street_view"
    if any(x in low for x in _BRIDGE_HINTS):
        return "street_view"
    if any(x in low for x in _NIGHT_HINTS):
        return "night_drive"
    if any(x in low for x in _STREET_HINTS):
        return "street_view"
    return "city_drive"


def _scan_text_for_audio_signals(*texts: str) -> tuple[bool, bool, bool]:
    """Returns (no_vocals, clean_ambient, real_sound_original)."""
    blob = " ".join(t.lower() for t in texts if t)
    no_v = "no_vocals" in blob or "no vocals" in blob
    clean = "clean ambient" in blob or "clean_ambient" in blob or "real_sound_gate" in blob
    rs = "real sound" in blob and "clean" not in blob[:200]  # loose
    if "original real" in blob:
        rs = True
    return no_v, clean, rs


def infer_audio_label(
    *,
    real_sound_report: Optional[dict[str, Any]] = None,
    extra_text: str = "",
    style: str = "auto",
) -> str:
    parts: list[str] = []
    rj = json.dumps(real_sound_report or {}, ensure_ascii=False).lower()
    no_v, clean, rs = _scan_text_for_audio_signals(rj, extra_text, style)
    st = (style or "auto").lower()
    if st == "no_vocals":
        no_v = True
    if st in ("clean_ambient", "ambient", "real_sound"):
        clean = True
    if no_v:
        parts.append("no_vocals")
    if clean or st in ("clean_ambient", "ambient"):
        parts.append("clean_ambient")
    elif rs or st == "real_sound":
        parts.append("real_sound")
    if not parts:
        return "ambient_processed"
    return "_".join(dict.fromkeys(parts))  # dedupe preserve order


def infer_duration_label(duration_sec: float) -> str:
    if duration_sec >= 3300:
        return "1_hour"
    if duration_sec >= 1800:
        return "30_plus_minute"
    return "long_drive"


def infer_quality_label(width: int) -> str:
    if width >= 3840:
        return "4k"
    if width >= 1920:
        return "1080p"
    return "hd_or_lower"


def load_existing_metadata(path: Path) -> dict[str, Any]:
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def merge_cli_overrides(
    meta: dict[str, Any],
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
) -> dict[str, Any]:
    out = {**meta}
    if title is not None and str(title).strip():
        out["title"] = str(title).strip()
        out.setdefault("warnings", [])
        if isinstance(out["warnings"], list):
            out["warnings"].append("cli_title_override")
    if description is not None and str(description).strip():
        out["description"] = str(description).strip()
        out.setdefault("warnings", [])
        if isinstance(out["warnings"], list):
            out["warnings"].append("cli_description_override")
    return out


def infer_package_video_type(package_dir: Path) -> str:
    s = str(package_dir).replace("\\", "/").lower()
    if "publish_pack/shorts_uploads" in s or "/shorts_uploads/" in s or s.rstrip("/").endswith("shorts_uploads"):
        return "short"
    if "manual_uploads" in s:
        return "manual"
    return "long"


def _long_duration_phrase(duration_sec: float) -> str:
    if duration_sec >= 3300:
        return "1 Hour"
    if duration_sec >= 1800:
        return "30+ Minute"
    return "Long Drive"


def _long_quality_phrase(width: int) -> str:
    if width >= 3840:
        return "4K"
    if width >= 1920:
        return "1080p"
    return ""


def _long_audio_title_bits(no_v: bool, clean: bool, rs: bool) -> list[str]:
    bits: list[str] = []
    if no_v:
        bits.append("No Vocals")
    if clean:
        bits.append("Clean Ambient")
    elif rs:
        bits.append("Real Sound")
    return bits


def apply_long_audio_policy_to_meta(
    meta: dict[str, Any],
    policy: dict[str, Any],
    *,
    loc: str,
    type_line: str,
    dphrase: str,
    qphrase: str,
    duration_sec: float,
) -> dict[str, Any]:
    """Align title/description/hashtags/tags with ``long_audio_policy`` v1."""
    out = {**meta}
    am = str(policy.get("audio_mode") or "music").strip().lower()
    reason = str(policy.get("reason") or "")
    ap_copy = {k: v for k, v in policy.items() if k != "warnings"}
    out["audio_mode"] = am
    out["audio_policy"] = ap_copy
    out["audio_policy_reason"] = reason
    out["matched_audio_cues"] = list(policy.get("matched_cues") or [])
    out["audio_label"] = {
        "music": "background_music_mix",
        "real_sound": "real_ambient_cleaned",
        "clean_ambient": "clean_ambient_master",
        "no_vocals_clean_ambient": "no_vocals_clean_ambient",
    }.get(am, "background_music_mix")

    dur_word = "one-hour " if duration_sec >= 3300 else "30+ minute " if duration_sec >= 1800 else "long-form "

    if am == "music":
        out["title"] = f"Relaxing {loc} Night Drive | Calm NYC Ambience".strip()[:100]
        out["description"] = (
            f"A {dur_word}{loc.lower()} night drive video with background music; original in-cabin audio is reduced "
            "for a calmer listening experience. Captured in New York City. "
            "Best for background viewing; no exaggerated claims."
        )
        extra_ht = ["#RelaxingDrive", "#NYCNightDrive"]
        extra_tags = ["NYC drive with music", "relaxing city drive", "background music drive"]
    elif am == "real_sound":
        if "ferry" in type_line.lower() or "ferry" in str(out.get("content_type", "")):
            out["title"] = f"Calm NYC Ferry Ride | Real Harbor Ambience".strip()[:100]
        else:
            out["title"] = f"{loc} Waterfront | Real City Ambience".strip()[:100]
        out["description"] = (
            f"A {dur_word}New York City experience featuring real ambient sound, lightly cleaned for smoother listening. "
            "Harbor and street atmosphere preserved; not a silent scenic plate."
        )
        extra_ht = ["#RealSound", "#AmbientDrive", "#NYCNightDrive"]
        extra_tags = ["NYC real sound", "city ambience", "ambient NYC drive"]
    elif am == "clean_ambient":
        out["title"] = f"Relaxing {loc} Night Drive | Clean Ambient".strip()[:100]
        out["description"] = (
            f"A {dur_word}{loc.lower()} night drive with cleaned ambient audio and reduced distractions. "
            "Suitable for immersive long-form viewing."
        )
        extra_ht = ["#AmbientDrive", "#NYCNightDrive"]
        extra_tags = ["clean ambient NYC", "ambient driving video", "NYC night drive"]
    else:  # no_vocals_clean_ambient
        out["title"] = f"Relaxing {loc} Night Drive | Clean Ambient | No Vocals".strip()[:100]
        out["description"] = (
            f"A {dur_word}{loc.lower()} drive where vocals are removed and cleaned ambient audio is preserved. "
            "Designed for distraction-reduced city sound."
        )
        extra_ht = ["#NoVocals", "#AmbientDrive", "#NYCNightDrive"]
        extra_tags = ["no vocals NYC drive", "clean ambient no vocals", "NYC street ambience"]

    ht = list(out.get("hashtags") or [])
    if isinstance(ht, list):
        for h in extra_ht:
            if h.lower() not in {x.lower() for x in ht if isinstance(x, str)}:
                ht.append(h)
        out["hashtags"] = ht

    tg = list(out.get("tags") or [])
    if isinstance(tg, list):
        for t in extra_tags:
            if t.lower() not in {x.lower() for x in tg if isinstance(x, str)}:
                tg.append(t)
        out["tags"] = tg[:20]

    try:
        from content_routing_v1 import sanitize_title_v1  # noqa: WPS433

        out["title"] = sanitize_title_v1(str(out.get("title") or ""))
    except Exception:  # noqa: BLE001
        pass
    if len(str(out.get("title") or "")) > 100:
        out["title"] = str(out["title"])[:97] + "..."
    return out


def generate_long_metadata(
    *,
    video_path: Path,
    package_dir: Path,
    duration_sec: float = 0.0,
    width: int = 0,
    height: int = 0,
    avg_frame_rate: str = "",
    real_sound_report: Optional[dict[str, Any]] = None,
    source_json: Optional[dict[str, Any]] = None,
    job_result_json: Optional[dict[str, Any]] = None,
    style: str = "auto",
    use_ai_metadata: bool = False,
    warnings: Optional[list[str]] = None,
    long_audio_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    w = warnings if warnings is not None else []
    path_blob = f"{video_path} {package_dir}"
    if source_json:
        path_blob += " " + json.dumps(source_json, ensure_ascii=False)[:4000]
    if job_result_json:
        path_blob += " " + json.dumps(job_result_json, ensure_ascii=False)[:2000]

    loc = infer_location_from_path(path_blob)
    content_type = infer_content_type(path_blob, style=style)
    rpt = real_sound_report or {}
    rtxt = json.dumps(rpt, ensure_ascii=False)
    no_v, clean, rs = _scan_text_for_audio_signals(rtxt, path_blob, style)

    # Optional sidecar in package tree
    for side in (package_dir, package_dir.parent):
        ap = side / "audio_separation_report.json"
        if ap.is_file():
            try:
                no_v2, clean2, rs2 = _scan_text_for_audio_signals(ap.read_text(encoding="utf-8", errors="replace"))
                no_v = no_v or no_v2
                clean = clean or clean2
                rs = rs or rs2
            except OSError:
                pass

    audio_label = infer_audio_label(real_sound_report=rpt, extra_text=path_blob, style=style)
    dur_l = infer_duration_label(duration_sec)
    qual_l = infer_quality_label(width)
    qphrase = _long_quality_phrase(width)
    dphrase = _long_duration_phrase(duration_sec)

    type_bits: list[str] = []
    if content_type == "night_drive" or any(x in path_blob.lower() for x in _NIGHT_HINTS):
        type_bits.append("Night Drive")
    elif content_type == "ferry":
        type_bits.append("Ferry Ride")
    elif content_type == "skyline":
        type_bits.append("NYC Drive")
    elif content_type == "street_view":
        type_bits.append("Street View")
    else:
        type_bits.append("NYC Drive")

    # Title refined via content_routing_v1 (no resolution / raw filename in title)
    title = f"Relaxing {loc} {type_bits[0]} | NYC Ambient Experience"

    if re.search(r"\bwalking\s+tour\b", title, re.I) and os.environ.get("STATEVERGE_LONG_WALK_CHANNEL") != "1":
        w.append("walking_source_in_long_metadata")
        title = re.sub(r"\bwalking\s+tour\b", "Night Drive", title, flags=re.I)
    if re.search(r"\bwalking\b", title, re.I) and os.environ.get("STATEVERGE_LONG_WALK_CHANNEL") != "1":
        w.append("walking_source_in_long_metadata")
        title = re.sub(r"\bwalking\b", "Driving", title, count=1, flags=re.I)

    try:
        from nyc_long_source_policy import is_valid_nyc_long_source

        mini = {
            "format": {"duration": str(duration_sec)},
            "streams": [{"codec_type": "video", "width": int(width), "height": int(height)}],
        }
        sp_chk = is_valid_nyc_long_source(video_path, mini)
        if str(sp_chk.get("source_type") or "") == "walking":
            w.append("walking_source_in_long_metadata")
        if not sp_chk.get("long_allowed"):
            w.append(f"long_source_policy_metadata_hint:{sp_chk.get('reason')}")
    except Exception:  # noqa: BLE001
        pass

    fps_note = ""
    if avg_frame_rate and avg_frame_rate not in ("0/0", "N/A"):
        fps_note = f"Rendered with stable frame timing (CFR-friendly pipeline). Nominal rate: {avg_frame_rate}. "

    audio_sentence = (
        "cleaned ambient audio with no vocals, reducing distracting noise while keeping street atmosphere."
        if (no_v or clean)
        else (
            "real street audio processed for a natural long-form listening experience."
            if rs
            else "ambient city audio suitable for background playback."
        )
    )

    desc = (
        f"A {('one-hour ' if duration_sec >= 3300 else '30+ minute ' if duration_sec >= 1800 else 'long-form ')}"
        f"{loc.lower()} {type_bits[0].lower()} relaxing NYC ambient experience. {fps_note}"
        f"This long-form video uses {audio_sentence} "
        f"Best enjoyed as background ambience — not a vlog or documentary."
    )
    desc = " ".join(desc.split())

    hashtags = ["#NYC", "#NewYorkCity", "#NightDrive", "#CityDrive", "#AmbientDrive"]
    if qual_l == "4k":
        hashtags.append("#4KDrive")
    else:
        hashtags.append("#DrivingVideo")
    if loc == "Brooklyn":
        hashtags.extend(["#Brooklyn", "#BrooklynDrive", "#NYCNightDrive"])
    elif loc == "Manhattan":
        hashtags.extend(["#Manhattan", "#NYCNightDrive"])
    elif loc == "Queens":
        hashtags.extend(["#Queens", "#NYCNightDrive"])
    elif loc == "Bronx":
        hashtags.extend(["#Bronx", "#NYCNightDrive"])
    else:
        hashtags.append("#NYCNightDrive")
    if "Times Square" in loc or "times" in path_blob.lower():
        hashtags.append("#TimesSquare")
    # dedupe case-insensitive
    seen: set[str] = set()
    ht2: list[str] = []
    for h in hashtags:
        k = h.lower()
        if k in seen:
            continue
        seen.add(k)
        ht2.append(h)
    hashtags = ht2

    tags = [
        "NYC drive",
        "New York City drive",
        f"{loc} night drive" if "night" in type_bits[0].lower() else f"{loc} drive",
        "NYC night drive",
        "4K city drive" if qual_l == "4k" else "city drive video",
        "ambient driving video",
        "no vocals drive" if no_v else "city ambience",
        "clean ambient audio" if clean else "urban soundscape",
        "New York street view",
        "city ambience",
    ]

    thumb_candidates = [f"{loc} Night Drive", "NYC Night Drive", "City Ambience", f"{loc} Drive"]
    thumbnail_text = thumb_candidates[0][:60]

    pinned = (
        "Thanks for watching. This video uses cleaned ambient audio with no vocals for a smoother long-form NYC driving experience."
        if (no_v or clean)
        else "Thanks for watching. Immersive NYC driving footage by StateVerge — best with headphones."
    )

    meta: dict[str, Any] = {
        "metadata_generated": True,
        "metadata_generator_version": METADATA_GENERATOR_VERSION,
        "video_type": "long",
        "status": "ready",
        "title": title,
        "description": desc,
        "hashtags": hashtags,
        "tags": tags[:15],
        "thumbnail_text": thumbnail_text,
        "pinned_comment": pinned,
        "location_hint": loc,
        "content_type": content_type,
        "audio_label": audio_label,
        "duration_label": dur_l,
        "quality_label": qual_l,
        "source_summary": f"{video_path.name} ({int(duration_sec)}s, {width}x{height})",
        "created_at": _utc_now_iso(),
        "warnings": list(w),
    }
    if avg_frame_rate:
        meta["input_avg_frame_rate"] = avg_frame_rate

    try:
        from long_audio_policy import infer_long_audio_policy

        pol = long_audio_policy if long_audio_policy is not None else infer_long_audio_policy(
            video_path,
            metadata=None,
            source_info={"basename": video_path.name, "package": str(package_dir)},
        )
        meta = apply_long_audio_policy_to_meta(
            meta,
            pol,
            loc=loc,
            type_line=type_bits[0],
            dphrase=dphrase,
            qphrase=qphrase,
            duration_sec=duration_sec,
        )
    except Exception:  # noqa: BLE001 — metadata must not fail publish
        pass

    try:
        from content_routing_v1 import build_content_routing_bundle, merge_content_routing_into_meta  # noqa: WPS433

        cr_asset: dict[str, Any] = {
            "path": str(video_path),
            "source_paths": [str(video_path)],
            "job_id": package_dir.name,
        }
        if source_json:
            cr_asset["source_json_hint"] = json.dumps(source_json, ensure_ascii=False)[:2000]
        if job_result_json:
            cr_asset.update({k: job_result_json[k] for k in ("route_type", "source_type", "time_of_day") if k in job_result_json})
        from content_routing_v1 import sanitize_title_v1  # noqa: WPS433

        cr_bundle = build_content_routing_bundle(cr_asset, job_id=package_dir.name)
        meta = merge_content_routing_into_meta(meta, cr_bundle)
        meta["title"] = sanitize_title_v1(str(cr_bundle.get("title") or meta.get("title") or ""))[:100]
        if cr_bundle.get("description_template"):
            meta["description"] = str(cr_bundle["description_template"])
        meta["encode_audio_policy_mode"] = str((long_audio_policy or {}).get("audio_mode") or meta.get("encode_audio_policy_mode") or "")
    except Exception as exc:  # noqa: BLE001
        w.append(f"content_routing_v1_long_failed:{type(exc).__name__}")

    return _maybe_enrich_with_openai(meta, "long", use_ai=use_ai_metadata, warnings=w)


def _shorts_paths_blob(job_result: dict[str, Any], video_path: Path) -> str:
    parts: list[str] = []
    for key in ("selected_assets", "source_paths", "output_video", "source_path"):
        val = job_result.get(key)
        if isinstance(val, list):
            parts.extend(str(x) for x in val)
        elif val:
            parts.append(str(val))
    parts.append(str(video_path))
    return " ".join(parts)


def _shorts_ferry_title_from_job(job_result: dict[str, Any]) -> str:
    preset = str(job_result.get("title_template_used") or "").strip()
    if preset:
        return preset
    templates = (
        "NYC Ferry Sunset Ambience",
        "Riding the Staten Island Ferry at Sunset",
        "Manhattan Skyline from the Ferry",
        "Calm NYC Ferry Ride",
        "Evening Ferry Into Manhattan",
    )
    jid = str(job_result.get("job_id") or "shorts")
    idx = int(hashlib.sha256(jid.encode()).hexdigest()[:8], 16) % len(templates)
    return templates[idx]


def _shorts_path_ferry_signals(paths: str, job_result: dict[str, Any]) -> bool:
    low = paths.replace("\\", "/").lower()
    if "/ferry/" in low:
        return True
    if str(job_result.get("inferred_source_type") or job_result.get("source_type") or "").strip().lower() == "ferry":
        return True
    if str(job_result.get("route_type") or "").strip().lower() == "ferry_route":
        return True
    for seg in re.split(r"[\s/]+", low):
        if seg == "ferry" or seg.endswith("_ferry") or seg.startswith("ferry_"):
            return True
    for token in paths.split():
        if "ferry" in Path(token).name.lower():
            return True
    return False


def generate_shorts_metadata(
    *,
    job_result: dict[str, Any],
    video_path: Path,
    use_ai_metadata: bool = False,
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    w = warnings if warnings is not None else []
    paths = _shorts_paths_blob(job_result, video_path)
    loc = infer_location_from_path(paths)
    sat = str(job_result.get("selected_asset_type") or "")
    title_theme = str(job_result.get("title_theme") or job_result.get("inferred_theme") or "").strip().lower()
    template_used = str(job_result.get("title_template_used") or "").strip()
    routing_bundle: dict[str, Any] = {}

    try:
        from content_routing_v1 import (  # noqa: WPS433
            build_content_routing_bundle,
            merge_content_routing_into_meta,
            pick_shorts_title_template,
            sanitize_title_v1,
        )

        asset: dict[str, Any] = dict(job_result)
        asset["source_paths"] = job_result.get("source_paths") or job_result.get("selected_assets") or [str(video_path)]
        preset = str(job_result.get("truth_guard_title") or job_result.get("title_template_used") or "").strip()
        routing_bundle = build_content_routing_bundle(
            asset,
            job_id=str(job_result.get("job_id") or ""),
            title=preset,
        )
        title_theme = str(routing_bundle.get("title_theme") or routing_bundle.get("inferred_theme") or "unknown")
        template_used = str(routing_bundle.get("title_template_used") or routing_bundle.get("title") or template_used)
        moment = sanitize_title_v1(
            str(routing_bundle.get("title") or "")
            or pick_shorts_title_template(title_theme, str(job_result.get("job_id") or paths))
            or "Calm NYC Street View"
        )
    except Exception as exc:  # noqa: BLE001
        w.append(f"content_routing_v1_failed:{type(exc).__name__}")
        if not title_theme:
            if _shorts_path_ferry_signals(paths, job_result):
                title_theme = "ferry"
            elif "/driving/" in paths.replace("\\", "/").lower() or str(job_result.get("source_type") or "").lower() == "driving":
                title_theme = "driving"
            else:
                title_theme = "unknown"
        if title_theme == "ferry":
            moment = _shorts_ferry_title_from_job(job_result)
            template_used = moment
        elif title_theme == "driving":
            moment = "Calm NYC Drive Moment"
        elif title_theme == "rain_night":
            moment = "Rainy NYC Night Ambience"
        else:
            moment = "Calm NYC Street View"

    content_type = "ferry" if title_theme == "ferry" else infer_content_type(paths, style="auto")
    if title_theme == "ferry":
        content_type = "ferry"

    title = f"{moment} #Shorts"
    if len(title) > 90:
        title = title[:87] + "..."

    if routing_bundle.get("description_template"):
        desc = str(routing_bundle["description_template"]) + " Vertical Short with original in-scene audio only."
    elif title_theme == "ferry":
        desc = (
            "A short NYC ferry ambience clip with original harbor audio. "
            "Relaxing waterfront moment — no added music."
        )
    elif title_theme == "rain_night":
        desc = (
            "A short rainy NYC night ambience clip with original street audio. "
            "Calm urban atmosphere for background viewing."
        )
    elif sat == "image_motion_short":
        desc = (
            "A short NYC ambient photo-motion clip with original audio. "
            "Relaxing urban moment for vertical Shorts."
        )
    else:
        desc = (
            "A short relaxing NYC ambient moment with original in-scene audio. "
            "No AI music — real city sound only."
        )

    hashtags = ["#Shorts", "#NYC", "#NewYorkCity"]
    if loc in ("Brooklyn", "Manhattan", "Queens", "Bronx"):
        hashtags.append(f"#{loc}")
    if title_theme == "ferry":
        hashtags.extend(["#NYCFerry", "#StatenIslandFerry", "#Waterfront"])
    else:
        hashtags.extend(["#NYCNight", "#StreetView", "#CityLights"])

    if title_theme == "ferry":
        tags = [
            "NYC ferry Shorts",
            "Staten Island Ferry",
            "NYC waterfront",
            "ferry ambience",
            "Manhattan skyline ferry",
            "vertical city video",
            "urban ambience",
        ]
    else:
        tags = [
            "NYC Shorts",
            "New York City Shorts",
            "Brooklyn night",
            "NYC street view",
            "city lights",
            "vertical city video",
            "urban ambience",
        ]

    if title_theme == "ferry":
        thumb = "NYC Ferry"
    elif "night" in moment.lower():
        thumb = "NYC Night"
    else:
        thumb = "City Lights"
    if loc == "Brooklyn" and title_theme != "ferry":
        thumb = "Brooklyn"

    meta = {
        "metadata_generated": True,
        "metadata_generator_version": METADATA_GENERATOR_VERSION,
        "video_type": "short",
        "status": "ready",
        "title": title,
        "description": desc,
        "hashtags": hashtags,
        "tags": tags,
        "thumbnail_text": thumb,
        "pinned_comment": "More NYC street moments and long-form drives on StateVerge.",
        "location_hint": loc,
        "content_type": content_type,
        "audio_label": infer_audio_label(extra_text=paths),
        "duration_label": "short_clip",
        "quality_label": "1080p_vertical",
        "source_summary": f"{sat or 'short'} {video_path.name}",
        "created_at": _utc_now_iso(),
        "warnings": list(w),
        "inferred_theme": title_theme,
        "title_theme": title_theme,
        "source_type": str(job_result.get("source_type") or "").strip(),
        "route_type": str(job_result.get("route_type") or "").strip(),
        "title_template_used": template_used,
    }
    if routing_bundle:
        try:
            from content_routing_v1 import merge_content_routing_into_meta  # noqa: WPS433

            from content_routing_v1 import pick_shorts_title_template, sanitize_title_v1  # noqa: WPS433

            meta = merge_content_routing_into_meta(meta, routing_bundle)
            st = str(routing_bundle.get("title_theme") or title_theme)
            base = sanitize_title_v1(str(routing_bundle.get("title") or pick_shorts_title_template(st, str(job_result.get("job_id") or ""))))
            meta["title"] = f"{base} #Shorts"[:100]
        except Exception:  # noqa: BLE001
            pass
    return _maybe_enrich_with_openai(meta, "short", use_ai=use_ai_metadata, warnings=w)


def generate_manual_upload_metadata(
    *,
    package_dir: Path,
    video_path: Path,
    duration_sec: float = 0.0,
    width: int = 0,
    height: int = 0,
    use_ai_metadata: bool = False,
    warnings: Optional[list[str]] = None,
    style: str = "auto",
    long_audio_policy: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    w = warnings if warnings is not None else []
    stem = package_dir.name.lower()
    # Explicit known manual package pattern (user spec)
    if "brooklyn_night_drive_yewan" in stem:
        loc = "Brooklyn"
        content_type = "night_drive"
        audio_label = "clean_ambient_no_vocals"
        title = "1 Hour Brooklyn Night Drive | Clean Ambient 4K | No Vocals"
        desc = (
            "A one-hour Brooklyn night drive video with cleaned ambient audio and no vocals. "
            "Captured in 4K and rendered safely to CFR 30fps."
        )
        hashtags = ["#Brooklyn", "#NYC", "#NewYorkCity", "#NYCNightDrive", "#4KDrive", "#AmbientDrive"]
        tags = [
            "Brooklyn night drive",
            "NYC night drive",
            "clean ambient",
            "no vocals",
            "4K drive",
            "New York City",
        ]
        meta = {
            "metadata_generated": True,
            "metadata_generator_version": METADATA_GENERATOR_VERSION,
            "video_type": "manual",
            "status": "ready",
            "title": title,
            "description": desc,
            "hashtags": hashtags,
            "tags": tags,
            "thumbnail_text": "Brooklyn Night Drive",
            "pinned_comment": "Thanks for watching this StateVerge NYC night drive.",
            "location_hint": loc,
            "content_type": content_type,
            "audio_label": audio_label,
            "duration_label": infer_duration_label(duration_sec),
            "quality_label": infer_quality_label(width),
            "source_summary": package_dir.name,
            "created_at": _utc_now_iso(),
            "warnings": list(w),
        }
        uo: dict[str, Any] = {}
        stl = (style or "auto").strip().lower()
        if stl in ("no_vocals", "no_vocals_clean_ambient", "clean_ambient", "real_sound", "music"):
            uo["audio_mode"] = (
                "no_vocals_clean_ambient"
                if stl in ("no_vocals", "no_vocals_clean_ambient")
                else stl
            )
        try:
            from long_audio_policy import infer_long_audio_policy

            pol = long_audio_policy if long_audio_policy is not None else infer_long_audio_policy(
                video_path, user_override=uo if uo else None
            )
            dph = _long_duration_phrase(duration_sec)
            qph = _long_quality_phrase(width)
            meta = apply_long_audio_policy_to_meta(
                meta,
                pol,
                loc=loc,
                type_line="Night Drive",
                dphrase=dph,
                qphrase=qph,
                duration_sec=duration_sec,
            )
        except Exception:  # noqa: BLE001
            pass
        return _maybe_enrich_with_openai(meta, "manual", use_ai=use_ai_metadata, warnings=w)

    # Generic manual: reuse long-style but mark manual
    loc = infer_location_from_path(f"{package_dir} {video_path}")
    base = generate_long_metadata(
        video_path=video_path,
        package_dir=package_dir,
        duration_sec=duration_sec,
        width=width,
        height=height,
        style="night_drive" if "night" in stem else "auto",
        use_ai_metadata=False,
        warnings=w,
        long_audio_policy=long_audio_policy,
    )
    base["video_type"] = "manual"
    base["source_summary"] = package_dir.name
    return _maybe_enrich_with_openai(base, "manual", use_ai=use_ai_metadata, warnings=w)


def _legacy_openai_enrich(
    meta: dict[str, Any],
    video_type: str,
    *,
    warnings: list[str],
) -> dict[str, Any]:
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        warnings.append("ai_metadata_skipped_no_api_key")
        return meta
    prompt_summary = f"Refine title+description JSON for video_type={video_type}; keep hashtags<=12; no URLs."
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {
                "role": "user",
                "content": (
                    f"{prompt_summary}\nReturn JSON object with keys title, description only. "
                    f"Current title: {meta.get('title','')[:200]}\n"
                    f"Current description: {str(meta.get('description',''))[:800]}"
                ),
            }
        ],
        "temperature": 0.4,
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        txt = (((raw.get("choices") or [{}])[0] or {}).get("message") or {}).get("content") or ""
        m = re.search(r"\{[\s\S]*\}", txt)
        if not m:
            warnings.append("ai_metadata_parse_failed")
            return meta
        patch = json.loads(m.group(0))
        if isinstance(patch.get("title"), str) and patch["title"].strip():
            meta["title"] = patch["title"].strip()[:100]
        if isinstance(patch.get("description"), str) and patch["description"].strip():
            meta["description"] = patch["description"].strip()
        meta.setdefault("warnings", [])
        if isinstance(meta["warnings"], list):
            meta["warnings"].append("ai_metadata_applied")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        err_k = f"ai_metadata_failed:{type(exc).__name__}"
        warnings.append(err_k)
        meta.setdefault("warnings", [])
        if isinstance(meta["warnings"], list):
            meta["warnings"].append(err_k)
    return meta


def _maybe_enrich_with_openai(
    meta: dict[str, Any],
    video_type: str,
    *,
    use_ai: bool,
    warnings: list[str],
) -> dict[str, Any]:
    if not use_ai:
        return meta
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    try:
        from ai.ai_client import ai_request
    except ImportError:
        return _legacy_openai_enrich(meta, video_type, warnings=warnings)

    safe_payload = {
        "video_type": video_type,
        "title": str(meta.get("title", ""))[:200],
        "description_preview": str(meta.get("description", ""))[:900],
        "location_hint": str(meta.get("location_hint", ""))[:200],
        "duration_label": str(meta.get("duration_label", ""))[:80],
        "quality_label": str(meta.get("quality_label", ""))[:80],
        "audio_mode": str(meta.get("audio_mode", ""))[:80],
        "content_type": str(meta.get("content_type", ""))[:80],
    }
    r = ai_request(
        "metadata_generation",
        json.dumps(safe_payload, ensure_ascii=False),
        system_hint="Return JSON with keys title, description only. No URLs, no credentials.",
    )
    meta["ai_task"] = "metadata_generation"
    meta["ai_cache_used"] = bool(r.get("cache_used"))
    if r.get("ok") and isinstance(r.get("data"), dict):
        patch = r["data"]
        meta["ai_enhanced"] = True
        meta["ai_fallback_used"] = False
        if isinstance(patch.get("title"), str) and patch["title"].strip():
            meta["title"] = patch["title"].strip()[:100]
        if isinstance(patch.get("description"), str) and patch["description"].strip():
            meta["description"] = patch["description"].strip()
        meta.setdefault("warnings", [])
        if isinstance(meta["warnings"], list):
            meta["warnings"].append("ai_metadata_applied_controlled_client")
    else:
        meta["ai_enhanced"] = False
        meta["ai_fallback_used"] = True
        warnings.append(str(r.get("error_type") or "ai_controlled_fallback"))
        return _legacy_openai_enrich(meta, video_type, warnings=warnings)
    return meta


def write_youtube_metadata(package_dir: Path, meta: dict[str, Any], *, merge: bool = True) -> Path:
    package_dir = package_dir.expanduser().resolve()
    out = package_dir / "youtube_metadata.json"
    prior = load_existing_metadata(out) if merge else {}
    merged = {**prior, **meta}
    merged["updated_at"] = _utc_now_iso()
    merged["report_path"] = str(out)
    out.write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def ensure_youtube_metadata_file(
    package_dir: Path,
    video_path: Path,
    *,
    video_type: Optional[str] = None,
    real_sound_report: Optional[dict[str, Any]] = None,
    source_json: Optional[dict[str, Any]] = None,
    job_result_json: Optional[dict[str, Any]] = None,
    style: str = "auto",
    use_ai_metadata: bool = False,
    dry_run: bool = False,
    extra_merge: Optional[dict[str, Any]] = None,
    long_audio_policy: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, Any], Path]:
    """Build metadata dict; write unless dry_run."""
    pkg = package_dir.expanduser().resolve()
    vp = video_path.expanduser().resolve()
    vt = (video_type or infer_package_video_type(pkg)).strip().lower()
    probe = ffprobe_json(vp)
    w, h, dur, afr = _probe_dims_duration(probe)
    if dur <= 0 and job_result_json:
        try:
            dur = float(job_result_json.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            dur = 0.0
    warns: list[str] = []

    if vt == "short":
        jr = job_result_json or {}
        meta = generate_shorts_metadata(
            job_result=jr,
            video_path=vp,
            use_ai_metadata=use_ai_metadata,
            warnings=warns,
        )
    elif vt == "manual":
        meta = generate_manual_upload_metadata(
            package_dir=pkg,
            video_path=vp,
            duration_sec=dur,
            width=w,
            height=h,
            use_ai_metadata=use_ai_metadata,
            warnings=warns,
            style=style,
            long_audio_policy=long_audio_policy,
        )
    else:
        meta = generate_long_metadata(
            video_path=vp,
            package_dir=pkg,
            duration_sec=dur,
            width=w,
            height=h,
            avg_frame_rate=afr,
            real_sound_report=real_sound_report,
            source_json=source_json,
            job_result_json=job_result_json,
            style=style,
            use_ai_metadata=use_ai_metadata,
            warnings=warns,
            long_audio_policy=long_audio_policy,
        )

    if extra_merge:
        meta = {**meta, **extra_merge}

    outp = pkg / "youtube_metadata.json"
    if not dry_run:
        write_youtube_metadata(pkg, meta, merge=True)
    meta = {**meta, "report_path": str(outp), "updated_at": _utc_now_iso()}
    return meta, outp


def _tags_list_to_txt(tags: Any) -> str:
    if isinstance(tags, list):
        return ", ".join(str(t) for t in tags if str(t).strip())
    return str(tags or "")


def sync_legacy_package_files(package_dir: Path, meta: dict[str, Any]) -> None:
    """Keep title_options.txt / description.txt / tags.txt in sync for older tooling."""
    pkg = package_dir.expanduser().resolve()
    try:
        tit = (meta.get("title") or "").strip()
        if tit:
            (pkg / "title_options.txt").write_text(tit + "\n", encoding="utf-8")
        desc = (meta.get("description") or "").strip()
        if desc:
            (pkg / "description.txt").write_text(desc + "\n", encoding="utf-8")
        tags_txt = _tags_list_to_txt(meta.get("tags"))
        if tags_txt:
            (pkg / "tags.txt").write_text(tags_txt + "\n", encoding="utf-8")
    except OSError:
        pass


def _read_selected_video(package_dir: Path) -> Optional[Path]:
    sel = package_dir / "selected_video_path.txt"
    try:
        line = sel.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        if line:
            p = Path(line[0].strip()).expanduser()
            return p if p.is_file() else None
    except OSError:
        pass
    return None


def _cli() -> int:
    ap = argparse.ArgumentParser(description="StateVerge metadata generator v1")
    ap.add_argument("--video", type=str, default="")
    ap.add_argument("--type", choices=("long", "short", "manual"), required=True)
    ap.add_argument("--package-dir", type=Path, default=None)
    ap.add_argument("--source-json", type=Path, default=None)
    ap.add_argument("--job-result-json", type=Path, default=None)
    ap.add_argument("--title", default=None)
    ap.add_argument("--description", default=None)
    ap.add_argument(
        "--style",
        default="auto",
        help="ambient|real_sound|clean_ambient|no_vocals|night_drive|street_view|ferry|skyline|auto",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--use-ai-metadata", action="store_true")
    ap.add_argument(
        "--long-audio-mode",
        choices=("auto", "music", "real_sound", "clean_ambient", "no_vocals_clean_ambient"),
        default=None,
        help="Optional: force long/manual NYC audio policy for metadata (non-auto only).",
    )
    args = ap.parse_args()

    pkg = args.package_dir.expanduser().resolve() if args.package_dir else Path.cwd()
    vp = Path(args.video).expanduser() if args.video else None
    if vp is None or not vp.is_file():
        vp = _read_selected_video(pkg)
    if vp is None or not vp.is_file():
        print("ERROR: need --video or package-dir/selected_video_path.txt", file=sys.stderr)
        return 2

    sj = None
    if args.source_json and args.source_json.is_file():
        try:
            sj = json.loads(args.source_json.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            sj = None
    jr = None
    if args.job_result_json and args.job_result_json.is_file():
        try:
            jr = json.loads(args.job_result_json.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            jr = None

    la: dict[str, Any] | None = None
    if args.long_audio_mode and str(args.long_audio_mode).lower() != "auto":
        from long_audio_policy import infer_long_audio_policy

        la = infer_long_audio_policy(vp, user_override={"audio_mode": str(args.long_audio_mode)})

    skip_disk = bool(args.dry_run) and not bool(args.write)
    meta, outp = ensure_youtube_metadata_file(
        pkg,
        vp,
        video_type=args.type,
        source_json=sj,
        job_result_json=jr,
        style=str(args.style or "auto"),
        use_ai_metadata=bool(args.use_ai_metadata),
        dry_run=skip_disk,
        long_audio_policy=la,
    )
    meta = merge_cli_overrides(meta, title=args.title, description=args.description)
    if args.title is not None or args.description is not None:
        meta["cli_override"] = True

    if bool(args.write) or not bool(args.dry_run):
        write_youtube_metadata(pkg, meta, merge=True)
        sync_legacy_package_files(pkg, meta)
        print(str(outp))
    else:
        print(json.dumps(meta, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
