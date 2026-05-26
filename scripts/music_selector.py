#!/usr/bin/env python3
"""StateVerge Semantic Music Selection v1 (nyc_long only)."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SV_SRC = Path.home() / "StateVerge" / "src"
for _p in (_SCRIPT_DIR, _SV_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _xfer() -> Path:
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        return get_sv_transfer(verbose=False)
    except Exception:
        return Path("/Volumes/SV_TRANSFER")


def default_rules_path() -> Path:
    return Path.home() / "StateVerge" / "config" / "music_scene_rules.json"


def default_suno_categories_path() -> Path:
    return Path.home() / "StateVerge" / "config" / "stateverge_suno_music_categories_v1.json"


def load_suno_categories_config(path: Path | None = None) -> dict[str, Any]:
    p = path or default_suno_categories_path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}


def _suno_scene_bucket(signals: dict[str, Any], blob: str) -> str:
    low = blob.lower()
    if any(t in low for t in ("rain", "rainy", "wet")):
        return "rain"
    if signals.get("is_night") or "night" in low or "midnight" in low:
        return "night"
    return "daytime"


def resolve_suno_categories_for_scene(
    *,
    theme: str = "driving",
    signals: dict[str, Any] | None = None,
    blob: str = "",
    cfg: dict[str, Any] | None = None,
) -> list[str]:
    """Return ordered Suno category folder names for NYC long driving BGM."""
    c = cfg if cfg is not None else load_suno_categories_config()
    rules = c.get("selection_rules") if isinstance(c.get("selection_rules"), dict) else {}
    if theme == "ferry":
        return []
    sig = signals or {}
    bucket = _suno_scene_bucket(sig, blob)
    driving = rules.get("driving") if isinstance(rules.get("driving"), dict) else {}
    branch = driving.get(bucket) if isinstance(driving.get(bucket), dict) else {}
    primary = list(branch.get("primary") or [])
    fallback = list(branch.get("fallback") or [])
    out: list[str] = []
    for cat in primary + fallback:
        if cat not in out:
            out.append(str(cat))
    return out


def _collect_suno_audio_under(root: Path, *, category: str = "", recursive: bool = True) -> list[Path]:
    exts = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".aiff", ".aif"}
    if not root.is_dir():
        return []
    scan_root = root / category if category else root
    if not scan_root.is_dir():
        return []
    out: list[Path] = []
    try:
        it = scan_root.rglob("*") if recursive else scan_root.glob("*")
        for p in it:
            if len(out) >= 4000:
                break
            if p.is_file() and p.suffix.lower() in exts and not p.name.startswith("._"):
                try:
                    if p.stat().st_size > 4096:
                        out.append(p)
                except OSError:
                    continue
    except OSError:
        return out
    return out


def _suno_scan_roots_for_theme(
    roots: list[Path],
    *,
    theme: str = "",
    cfg: dict[str, Any] | None = None,
) -> list[Path]:
    """Ordered scan roots: inbox/suno first, then theme subdirs (driving/ferry)."""
    c = cfg if cfg is not None else load_suno_categories_config()
    theme_subs = c.get("theme_subdirs") if isinstance(c.get("theme_subdirs"), dict) else {}
    ordered: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for p in (root,):
            key = str(p)
            if key not in seen:
                seen.add(key)
                ordered.append(p)
        if theme and theme in theme_subs:
            for sub in theme_subs[theme]:
                if not sub:
                    continue
                cand = root / sub if not str(sub).startswith("suno/") else root.parent / sub
                if not cand.is_dir() and "/" in str(sub):
                    cand = root / Path(str(sub).split("/", 1)[-1])
                key = str(cand)
                if cand.is_dir() and key not in seen:
                    seen.add(key)
                    ordered.append(cand)
    return ordered


def pick_suno_track_for_categories(
    categories: list[str],
    *,
    cfg: dict[str, Any] | None = None,
    theme: str = "driving",
) -> tuple[Path | None, str, list[str]]:
    """Scan inbox/suno (recursive), theme subdirs, then category bins; return newest match."""
    c = cfg if cfg is not None else load_suno_categories_config()
    warnings: list[str] = []
    roots = [Path(str(r)) for r in (c.get("library_roots") or [])]
    scan_roots = _suno_scan_roots_for_theme(roots, theme=theme, cfg=c)
    for cat in categories:
        for root in scan_roots:
            if not root.is_dir():
                warnings.append(f"suno_root_missing:{root}")
                continue
            if root.name == "suno" or str(root).endswith("/inbox/suno"):
                cands = _collect_suno_audio_under(root, category="", recursive=True)
            else:
                cands = _collect_suno_audio_under(root, category=cat, recursive=False)
                if not cands:
                    cands = _collect_suno_audio_under(root, category="", recursive=True)
            if not cands:
                continue
            try:
                cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            except OSError:
                pass
            return cands[0], cat, warnings
    return None, "", warnings


def long_music_selection_fields(
    *,
    theme: str = "driving",
    media_entries: list[dict[str, Any]] | None = None,
    xfer: Path | None = None,
) -> dict[str, Any]:
    """Build job manifest fields: suno_first with Envato fallback for skyline/rain/night."""
    xfer = xfer or _xfer()
    theme_l = str(theme or "driving").strip().lower()
    signals = collect_scene_signals(media_entries or []) if media_entries else {"blob": ""}
    blob = str(signals.get("blob") or "")
    w: list[str] = []
    out: dict[str, Any] = {
        "music_priority": "suno_first",
        "content_theme": theme_l,
        "fallback_used": False,
    }
    if theme_l == "ferry":
        out.update(
            {
                "music_source": "none",
                "selected_music_path": "",
                "audio_mode": "real_ambience_primary",
            }
        )
        return out

    suno_cfg = load_suno_categories_config()
    cats = resolve_suno_categories_for_scene(theme=theme_l, signals=signals, blob=blob, cfg=suno_cfg)
    pick, cat, sw = pick_suno_track_for_categories(cats, cfg=suno_cfg, theme=theme_l)
    w.extend(sw)
    if pick:
        out.update(
            {
                "music_source": "suno",
                "selected_music_path": str(pick),
                "audio_mode": "music_first" if theme_l == "driving" else "cinematic_music_first",
                "suno_category": cat,
            }
        )
        return out

    envato_themes = ("skyline_sequence", "rain_night", "skyline", "rain", "night", "unknown")
    if theme_l in envato_themes or any(t in blob for t in ("skyline", "rain", "night")):
        sel = select_nyc_long_music(media_entries=media_entries or [], xfer=xfer, dry_run=True)
        pth = str(sel.get("selected_track_path") or "")
        if pth:
            out.update(
                {
                    "music_source": "envato",
                    "selected_music_path": pth,
                    "audio_mode": str(sel.get("selected_category") or "ambient"),
                    "fallback_used": True,
                    "fallback_reason": "suno_empty_envato_fallback",
                }
            )
            return out
        w.append("envato_fallback_empty")

    out.update(
        {
            "music_source": "none",
            "selected_music_path": "",
            "audio_mode": "music_first" if theme_l == "driving" else "safe_neutral",
            "fallback_used": True,
        }
    )
    out["warnings"] = w
    return out


def default_music_index_path(xfer: Path | None = None) -> Path:
    x = xfer or _xfer()
    return x / "04_AUDIO" / "music" / "nyc_long" / "metadata" / "music_index.json"


def default_output_path(xfer: Path | None = None) -> Path:
    x = xfer or _xfer()
    return x / "04_AUDIO" / "music" / "nyc_long" / "metadata" / "latest_music_selection.json"


def resolve_media_index_path(p: Path | None) -> Path | None:
    if p and p.is_file():
        return p
    x = _xfer()
    for cand in (
        x / "media_index" / "media_index_v3.json",
        x / "media_index" / "media_index.json",
    ):
        if cand.is_file():
            return cand
    return None


def resolve_media_scene_signals_path(xfer: Path | None = None) -> Path | None:
    x = xfer or _xfer()
    p = x / "media_index" / "media_scene_signals.json"
    return p if p.is_file() else None


def _entries_from_media_raw(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "entries", "media"):
            v = raw.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def load_media_entries(path: Path | None = None) -> list[dict[str, Any]]:
    """Prefer ``media_scene_signals.json`` when present; else media_index v3/json."""
    x = _xfer()
    if path and path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return []
        return _entries_from_media_raw(raw)
    sig = x / "media_index" / "media_scene_signals.json"
    if sig.is_file():
        try:
            raw = json.loads(sig.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            raw = None
        rows = _entries_from_media_raw(raw) if raw is not None else []
        if rows:
            return rows
    p = resolve_media_index_path(None)
    if not p or not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    return _entries_from_media_raw(raw)


def filter_entries_by_project(entries: list[dict[str, Any]], project_name: str | None) -> list[dict[str, Any]]:
    if not project_name or not str(project_name).strip():
        return list(entries)
    pn = str(project_name).strip().lower()
    out: list[dict[str, Any]] = []
    for e in entries:
        blob = json.dumps(e, ensure_ascii=False).lower()
        if pn in blob:
            out.append(e)
    return out if out else list(entries)


def _parse_hour_from_value(v: Any) -> int | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})", s)
    if m:
        try:
            return int(m.group(4))
        except ValueError:
            return None
    m2 = re.search(r"\b(\d{1,2}):(\d{2}):(\d{2})\b", s)
    if m2:
        try:
            return int(m2.group(1))
        except ValueError:
            return None
    return None


def collect_scene_signals(entries: list[dict[str, Any]]) -> dict[str, Any]:
    parts: list[str] = []
    hours: list[int] = []
    types: list[str] = []
    tag_ctr: Counter[str] = Counter()
    hint_ctr: Counter[str] = Counter()
    tb_ctr: Counter[str] = Counter()
    loc_counts = {"borough_present": 0, "neighborhood_present": 0, "landmark_present": 0, "core_location_unknown": 0}

    for e in entries:
        for key in (
            "borough",
            "neighborhood",
            "nearby_landmark",
            "landmark",
            "location_name",
            "likely_source_type",
            "file_path",
            "file_name",
            "path",
            "usable_for",
            "quality_hint",
            "orientation",
            "scene_type",
            "route_group",
        ):
            val = e.get(key)
            if val is None:
                continue
            if isinstance(val, (list, tuple)):
                parts.extend(str(x).lower() for x in val if x is not None)
            else:
                parts.append(str(val).lower())
        qc = e.get("quality_candidates")
        if isinstance(qc, list):
            parts.extend(str(x).lower() for x in qc if x is not None)
        if e.get("manual_timelapse") is True or e.get("is_manual_timelapse") is True:
            parts.append("timelapse")
        if e.get("is_timelapse") is True:
            parts.append("timelapse")
        hour_from_enrich = False
        ih = e.get("inferred_hour")
        if ih is not None:
            try:
                hi = int(ih)
                hours.append(hi)
                parts.append(f"hour_{hi}")
                hour_from_enrich = True
            except (TypeError, ValueError):
                pass
        if not hour_from_enrich:
            for tk in ("captured_at", "created_at", "modified_at", "mtime", "scanned_at", "gps_datetime"):
                h = _parse_hour_from_value(e.get(tk))
                if h is not None:
                    hours.append(h)
        lp = e.get("long_source_policy")
        if isinstance(lp, dict):
            st = lp.get("source_type")
            if st:
                types.append(str(st).lower())
                parts.append(str(st).lower())

        stags = e.get("scene_tags")
        if isinstance(stags, list):
            for t in stags:
                if isinstance(t, str) and t.strip():
                    tl = t.strip().lower()
                    parts.append(tl)
                    tag_ctr[tl] += 1
        mhint = e.get("music_scene_hint")
        if isinstance(mhint, str) and mhint.strip():
            hs = mhint.strip()
            hint_ctr[hs] += 1
            parts.append(hs.lower())
        tb = e.get("time_bucket")
        if isinstance(tb, str) and tb.strip():
            tbs = tb.strip().lower()
            tb_ctr[tbs] += 1
            parts.append(tbs)
        if str(e.get("borough") or "").strip():
            loc_counts["borough_present"] += 1
        if str(e.get("neighborhood") or "").strip():
            loc_counts["neighborhood_present"] += 1
        if str(e.get("nearby_landmark") or e.get("landmark") or "").strip():
            loc_counts["landmark_present"] += 1
        if not str(e.get("borough") or "").strip() and not str(e.get("neighborhood") or "").strip():
            if not str(e.get("nearby_landmark") or e.get("landmark") or "").strip():
                loc_counts["core_location_unknown"] += 1

    blob = " ".join(parts)
    avg_h: int | None = None
    if hours:
        avg_h = int(round(sum(hours) / len(hours)))
    is_night = False
    is_sunset_window = False
    is_daytime = False
    if avg_h is not None:
        is_night = avg_h >= 18 or avg_h < 6
        is_sunset_window = 16 <= avg_h < 20
        is_daytime = 10 <= avg_h < 16
    if "night" in blob or "midnight" in blob:
        is_night = True
    if "sunset" in blob or "golden hour" in blob or "dusk" in blob:
        is_sunset_window = True
    if "daytime" in blob or "day time" in blob:
        is_daytime = True

    if any(bool(x.get("is_night")) for x in entries if isinstance(x, dict)):
        is_night = True
    if any(bool(x.get("is_sunset_window")) for x in entries if isinstance(x, dict)):
        is_sunset_window = True
    if any(bool(x.get("is_daytime")) for x in entries if isinstance(x, dict)):
        is_daytime = True

    signal_src = "media_scene_signals.json" if any(
        isinstance(e.get("music_scene_hint"), str) and str(e.get("music_scene_hint") or "").strip() for e in entries
    ) else "media_index"
    top_scene_tags = [{"tag": a, "count": b} for a, b in tag_ctr.most_common(40)]

    return {
        "blob": blob,
        "hours_sample": hours[:12],
        "avg_hour_inferred": avg_h,
        "is_night": is_night,
        "is_sunset_window": is_sunset_window,
        "is_daytime": is_daytime,
        "likely_source_types": types,
        "scene_signals_source": signal_src,
        "top_scene_tags": top_scene_tags,
        "music_scene_hint_counts": dict(hint_ctr),
        "time_bucket_counts": dict(tb_ctr),
        "location_signal_counts": loc_counts,
    }


def _score_categories(blob: str, rules: dict[str, Any], signals: dict[str, Any]) -> dict[str, float]:
    cats = rules.get("categories") or {}
    scores: dict[str, float] = {k: 0.0 for k in ("night_drive", "calm_piano", "cinematic", "dark_documentary", "ambient")}
    for cat, kws in cats.items():
        if cat not in scores:
            continue
        if not isinstance(kws, list):
            continue
        for kw in kws:
            if not isinstance(kw, str):
                continue
            if kw.lower() in blob:
                scores[cat] += 1.0
    hr = rules.get("hard_rules") or {}
    ferry_sunset = ("ferry" in blob) or ("sunset" in blob) or ("golden hour" in blob) or signals.get("is_sunset_window")
    if ferry_sunset:
        scores["calm_piano"] += float(hr.get("ferry_sunset_golden_hour_boost_calm_piano", 120.0))
    ts = "times square" in blob
    if ts and signals.get("is_night"):
        scores["dark_documentary"] += float(hr.get("times_square_night_boost_dark_doc", 100.0))
        scores["cinematic"] += float(hr.get("times_square_night_boost_cinematic", 85.0))
    drv = "driving_fixed" in blob or ("driving" in blob and "fixed" in blob)
    if drv and signals.get("is_night"):
        scores["night_drive"] += float(hr.get("driving_fixed_night_boost_night_drive", 150.0))
    if signals.get("is_daytime"):
        scores["ambient"] += 0.5
    if signals.get("is_night"):
        scores["night_drive"] += 0.4
    hints = signals.get("music_scene_hint_counts") or {}
    if isinstance(hints, dict):
        for cat, cnt in hints.items():
            if cat in scores and isinstance(cnt, (int, float)):
                scores[cat] += 3.0 * float(cnt)
    return scores


def _pick_winner(scores: dict[str, float], tie_priority: list[str]) -> tuple[str, float]:
    cats = ["night_drive", "calm_piano", "cinematic", "dark_documentary", "ambient"]
    mx = max((scores.get(c, 0.0) for c in cats), default=0.0)
    if mx <= 0:
        return "ambient", 0.0
    top = [c for c in cats if abs(scores.get(c, 0.0) - mx) < 1e-6]
    if len(top) == 1:
        return top[0], mx
    close = [c for c in cats if scores.get(c, 0.0) >= mx - 0.75 and scores.get(c, 0.0) > 0]
    pool = close if len(close) > 1 else top
    pri = tie_priority or ["night_drive", "cinematic", "ambient", "calm_piano", "dark_documentary"]
    for p in pri:
        if p in pool:
            return p, mx
    return pool[0], mx


def _fallback_chain() -> dict[str, list[str]]:
    return {
        "night_drive": ["ambient", "dark_documentary", "calm_piano", "cinematic"],
        "calm_piano": ["ambient", "cinematic", "night_drive"],
        # Prefer mood-adjacent categories before generic ambient when cinematic bins are empty.
        "cinematic": ["night_drive", "dark_documentary", "ambient", "calm_piano"],
        "dark_documentary": ["cinematic", "ambient", "night_drive"],
        "ambient": ["calm_piano", "cinematic", "night_drive"],
    }


def _build_try_order(cat_win: str, xfer: Path, tracks: list[dict[str, Any]]) -> list[str]:
    """Winner first; fallbacks ordered with non-empty categories earlier (stable within ties)."""
    chain = list(_fallback_chain().get(cat_win, []))
    seen: set[str] = {cat_win}
    rest = [c for c in chain if c not in seen]

    def sort_key(c: str) -> tuple[int, int]:
        _, _, ok = pick_track_for_category(xfer, tracks, c)
        try:
            pri = chain.index(c)
        except ValueError:
            pri = 999
        return (0 if ok else 1, pri)

    rest_sorted = sorted(rest, key=sort_key)
    return [cat_win] + rest_sorted


def count_available_license_valid_main_by_category(xfer: Path, tracks: list[dict[str, Any]]) -> dict[str, int]:
    cats = ("night_drive", "calm_piano", "cinematic", "dark_documentary", "ambient")
    out: dict[str, int] = {c: 0 for c in cats}
    for t in tracks:
        if str(t.get("music_channel") or "") != "nyc_long" or t.get("license_valid") is not True:
            continue
        cat = str(t.get("category") or "").strip()
        if cat not in out:
            continue
        fn = str(t.get("filename") or "").strip()
        if not fn or "_stem_" in fn:
            continue
        if _track_path(xfer, cat, fn).is_file():
            out[cat] += 1
    return out


def load_rules(path: Path | None) -> dict[str, Any]:
    p = path or default_rules_path()
    if not p.is_file():
        return {
            "version": 1,
            "categories": {},
            "tie_break_priority": ["night_drive", "cinematic", "ambient", "calm_piano", "dark_documentary"],
            "hard_rules": {},
        }
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {
            "version": 1,
            "categories": {},
            "tie_break_priority": ["night_drive", "cinematic", "ambient", "calm_piano", "dark_documentary"],
            "hard_rules": {},
        }


def load_music_tracks(path: Path | None) -> list[dict[str, Any]]:
    p = path or default_music_index_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    if isinstance(data, dict) and isinstance(data.get("tracks"), list):
        return [x for x in data["tracks"] if isinstance(x, dict)]
    return []


def _track_path(xfer: Path, category: str, filename: str) -> Path:
    return xfer / "04_AUDIO" / "music" / "nyc_long" / category / filename


def pick_track_for_category(
    xfer: Path,
    tracks: list[dict[str, Any]],
    category: str,
) -> tuple[str | None, str | None, bool]:
    cands = [
        t
        for t in tracks
        if str(t.get("music_channel") or "") == "nyc_long"
        and t.get("license_valid") is True
        and str(t.get("category") or "") == category
    ]
    best_p: str | None = None
    best_name: str | None = None
    best_mt = -1.0
    for t in cands:
        fn = str(t.get("filename") or "").strip()
        if not fn:
            continue
        p = _track_path(xfer, category, fn)
        if not p.is_file():
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            continue
        if mt > best_mt:
            best_mt = mt
            best_p = str(p)
            best_name = fn
    return best_p, best_name, bool(best_p)


def select_nyc_long_music(
    *,
    media_entries: list[dict[str, Any]],
    xfer: Path | None = None,
    rules_path: Path | None = None,
    music_index_path: Path | None = None,
    output_json: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    xfer = xfer or _xfer()
    rules = load_rules(rules_path)
    tracks = load_music_tracks(music_index_path)
    tie_priority = list(rules.get("tie_break_priority") or [])
    signals = collect_scene_signals(media_entries)
    blob = str(signals.get("blob") or "")
    scores = _score_categories(blob, rules, signals)
    cat_soft, top_score = _pick_winner(scores, tie_priority)

    ferry_sunset = (
        ("ferry" in blob)
        or ("sunset" in blob)
        or ("golden hour" in blob)
        or bool(signals.get("is_sunset_window"))
    )
    ts_night = ("times square" in blob) and bool(signals.get("is_night"))
    drv_night = ("driving_fixed" in blob or ("driving" in blob and "fixed" in blob)) and bool(signals.get("is_night"))

    if ferry_sunset:
        cat_win = "calm_piano"
    elif ts_night:
        cat_win = (
            "dark_documentary"
            if scores.get("dark_documentary", 0.0) >= scores.get("cinematic", 0.0)
            else "cinematic"
        )
    elif drv_night:
        cat_win = "night_drive"
    else:
        cat_win = cat_soft

    total = sum(max(0.0, scores.get(c, 0.0)) for c in scores) + 1e-6
    confidence = min(1.0, max(0.0, max(scores.get(cat_win, 0.0), top_score) / total))

    fb_used = False
    try_order = _build_try_order(cat_win, xfer, tracks)

    selected_path: str | None = None
    selected_name: str | None = None
    selected_cat = cat_win
    reason_parts = [f"winner={cat_win}", f"top_score={top_score:.3f}"]

    for c in try_order:
        pth, name, ok = pick_track_for_category(xfer, tracks, c)
        if ok and pth:
            selected_path = pth
            selected_name = name
            selected_cat = c
            if c != cat_win:
                fb_used = True
                reason_parts.append(f"fallback_to={c}")
            break

    if not selected_path:
        fb_used = True
        confidence = 0.0
        reason_parts.append("no_track_found_all_categories_failopen")

    fallback_reason: str | None = None
    if cat_win == "cinematic" and fb_used:
        fallback_reason = "cinematic_category_empty"
        confidence = min(confidence, 0.65)

    out: dict[str, Any] = {
        "music_channel": "nyc_long",
        "winner_category": cat_win,
        "selected_category": selected_cat,
        "selected_track": selected_name or "",
        "selected_track_path": selected_path or "",
        "confidence": round(confidence, 4),
        "scene_signals": signals,
        "category_scores": scores,
        "fallback_used": fb_used,
        "fallback_reason": fallback_reason,
        "selection_reason": "; ".join(reason_parts),
        "generated_at": _utc_iso(),
    }

    dest = output_json if output_json is not None else default_output_path(xfer)
    if not dry_run and dest:
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(dest)
        except OSError:
            pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Semantic nyc_long music selection v1")
    ap.add_argument("--media-index", type=Path, default=None, help="media_index.json (or v3); optional, auto-resolve")
    ap.add_argument("--project-name", default=None)
    ap.add_argument("--channel", choices=("nyc_long",), default="nyc_long")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--output-json", type=Path, default=None)
    ap.add_argument("--rules", type=Path, default=None)
    ap.add_argument("--music-index", type=Path, default=None)
    ns = ap.parse_args()
    if ns.channel != "nyc_long":
        print(json.dumps({"ok": False, "error": "only_nyc_long_v1"}))
        return 2
    entries = load_media_entries(ns.media_index)
    entries = filter_entries_by_project(entries, ns.project_name)
    xfer = _xfer()
    sel = select_nyc_long_music(
        media_entries=entries,
        xfer=xfer,
        rules_path=ns.rules,
        music_index_path=ns.music_index,
        output_json=ns.output_json,
        dry_run=bool(ns.dry_run),
    )
    print(json.dumps({"ok": True, "selection": sel}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
