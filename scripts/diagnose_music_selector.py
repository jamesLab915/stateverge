#!/usr/bin/env python3
"""Diagnose semantic music selector (dry-run + status JSON)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_selector():
    path = _SCRIPT_DIR / "music_selector.py"
    spec = importlib.util.spec_from_file_location("music_selector_v1", path)
    if not spec or not spec.loader:
        raise RuntimeError("music_selector_load_failed")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_selector()
    xfer = mod._xfer()
    tracks = mod.load_music_tracks(None)
    entries = mod.load_media_entries(None)
    sel = mod.select_nyc_long_music(
        media_entries=entries,
        xfer=xfer,
        dry_run=True,
        output_json=None,
    )
    available = mod.count_available_license_valid_main_by_category(xfer, tracks)
    missing_categories = [c for c, n in available.items() if n == 0]
    fallback_warnings: list[str] = []
    if available.get("cinematic", 0) == 0:
        fallback_warnings.append("cinematic_category_empty")
    if available.get("cinematic", 0) < 5:
        fallback_warnings.append("needs_more_cinematic_tracks")
    if sel.get("fallback_reason") == "cinematic_category_empty":
        fallback_warnings.append("semantic_selection_cinematic_fallback")
    elif sel.get("winner_category") == "cinematic" and sel.get("fallback_used") is True:
        fallback_warnings.append("semantic_selection_cinematic_fallback")
    fallback_warnings = sorted(set(fallback_warnings))

    ss = sel.get("scene_signals") or {}
    status = {
        "ok": True,
        "generated_at": sel.get("generated_at"),
        "winner_category": sel.get("winner_category"),
        "selected_category": sel.get("selected_category"),
        "selected_track": sel.get("selected_track"),
        "selected_track_path": sel.get("selected_track_path"),
        "confidence": sel.get("confidence"),
        "scene_signals": {
            "scene_signals_source": ss.get("scene_signals_source"),
            "avg_hour_inferred": ss.get("avg_hour_inferred"),
            "is_night": ss.get("is_night"),
            "is_sunset_window": ss.get("is_sunset_window"),
            "top_scene_tags": ss.get("top_scene_tags"),
            "music_scene_hint_counts": ss.get("music_scene_hint_counts"),
            "time_bucket_counts": ss.get("time_bucket_counts"),
            "location_signal_counts": ss.get("location_signal_counts"),
            "blob_sample": (ss.get("blob") or "")[:400],
        },
        "category_scores": sel.get("category_scores"),
        "fallback_used": sel.get("fallback_used"),
        "fallback_reason": sel.get("fallback_reason"),
        "available_tracks_by_category": available,
        "missing_categories": missing_categories,
        "fallback_warnings": fallback_warnings,
        "selection_reason": sel.get("selection_reason"),
        "latest_selection_path": str(
            xfer / "04_AUDIO" / "music" / "nyc_long" / "metadata" / "latest_music_selection.json"
        ),
    }
    out_p = xfer / "04_AUDIO" / "music" / "nyc_long" / "metadata" / "music_selector_status.json"
    try:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        t = out_p.with_suffix(".json.tmp")
        t.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        t.replace(out_p)
    except OSError as exc:
        status["write_error"] = repr(exc)
    print(json.dumps(status, ensure_ascii=False, indent=2))

    last_path = xfer / "04_AUDIO" / "music" / "nyc_long" / "metadata" / "reclassify_music_library_last.json"
    reclassified_count = "n/a"
    try:
        if last_path.is_file():
            last = json.loads(last_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(last, dict) and "reclassified_count" in last:
                reclassified_count = str(last.get("reclassified_count"))
    except Exception:
        pass

    need_more = str(available.get("cinematic", 0) < 5).lower()
    missing_joined = ",".join(missing_categories) if missing_categories else ""
    fw_joined = ",".join(fallback_warnings) if fallback_warnings else ""
    err_count = 1 if status.get("write_error") else 0

    scene_stat: dict[str, Any] = {}
    try:
        sp = xfer / "media_index" / "scene_signal_status.json"
        if sp.is_file():
            scene_stat = json.loads(sp.read_text(encoding="utf-8", errors="replace"))
            if not isinstance(scene_stat, dict):
                scene_stat = {}
    except Exception:
        err_count += 1
        scene_stat = {}

    print("", flush=True)
    print("SEMANTIC_MUSIC_LIBRARY_RECLASSIFY_DONE", flush=True)
    print(f"RECLASSIFIED_COUNT={reclassified_count}", flush=True)
    print(f"CINEMATIC_TRACK_COUNT={available.get('cinematic', 0)}", flush=True)
    print(f"AMBIENT_TRACK_COUNT={available.get('ambient', 0)}", flush=True)
    print(f"NIGHT_DRIVE_TRACK_COUNT={available.get('night_drive', 0)}", flush=True)
    print(f"MISSING_CATEGORIES={missing_joined}", flush=True)
    print(f"NEED_MORE_CINEMATIC_TRACKS={need_more}", flush=True)
    print(f"FALLBACK_WARNINGS={fw_joined}", flush=True)

    top_tags_line = json.dumps(scene_stat.get("top_scene_tags") or [], ensure_ascii=False)[:500]
    hints_line = json.dumps(scene_stat.get("music_scene_hint_counts") or {}, ensure_ascii=False)
    print("", flush=True)
    print("SCENE_SIGNAL_ENRICHMENT_V1_DONE", flush=True)
    print(f"SCENE_SIGNALS_JSON={xfer / 'media_index' / 'media_scene_signals.json'}", flush=True)
    print(f"SCENE_STATUS_JSON={xfer / 'media_index' / 'scene_signal_status.json'}", flush=True)
    print(f"TOTAL_ITEMS={scene_stat.get('total_items', '')}", flush=True)
    print(f"UNKNOWN_HOUR_COUNT={scene_stat.get('unknown_hour_count', '')}", flush=True)
    print(f"UNKNOWN_LOCATION_COUNT={scene_stat.get('unknown_location_count', '')}", flush=True)
    print(f"TOP_SCENE_TAGS={top_tags_line}", flush=True)
    print(f"MUSIC_SCENE_HINT_COUNTS={hints_line}", flush=True)
    print(f"SELECTED_CATEGORY={sel.get('selected_category', '')}", flush=True)
    print(f"SELECTED_TRACK={sel.get('selected_track', '')}", flush=True)
    print(f"CONFIDENCE={sel.get('confidence', '')}", flush=True)
    print(f"ERROR_COUNT={err_count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
