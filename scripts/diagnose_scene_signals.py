#!/usr/bin/env python3
"""Diagnose Scene Signal Enrichment v1 — aggregate stats + scene_signal_status.json."""

from __future__ import annotations

import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_enrich_mod():
    path = _SCRIPT_DIR / "enrich_scene_signals.py"
    spec = importlib.util.spec_from_file_location("enrich_scene_signals_v1", path)
    if not spec or not spec.loader:
        raise RuntimeError("enrich_scene_signals_load_failed")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    mod = _load_enrich_mod()
    xfer = mod._xfer()
    scene_path = xfer / "media_index" / "media_scene_signals.json"
    err_count = 0
    status: dict[str, Any]

    if not scene_path.is_file():
        err_count += 1
        status = {
            "ok": False,
            "error": "media_scene_signals_missing",
            "path": str(scene_path),
            "total_items": 0,
            "time_bucket_counts": {},
            "top_scene_tags": [],
            "music_scene_hint_counts": {},
            "unknown_location_count": 0,
            "unknown_hour_count": 0,
            "generated_at": mod._utc_iso(),
        }
    else:
        try:
            raw = json.loads(scene_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as exc:  # noqa: BLE001
            err_count += 1
            status = {
                "ok": False,
                "error": repr(exc),
                "path": str(scene_path),
                "total_items": 0,
                "time_bucket_counts": {},
                "top_scene_tags": [],
                "music_scene_hint_counts": {},
                "unknown_location_count": 0,
                "unknown_hour_count": 0,
                "generated_at": mod._utc_iso(),
            }
        else:
            items: list[dict[str, Any]] = []
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                items = [x for x in raw["items"] if isinstance(x, dict)]
            if not items:
                err_count += 1
                status = {
                    "ok": False,
                    "error": "no_items_in_media_scene_signals",
                    "path": str(scene_path),
                    "total_items": 0,
                    "time_bucket_counts": {},
                    "top_scene_tags": [],
                    "music_scene_hint_counts": {},
                    "unknown_location_count": 0,
                    "unknown_hour_count": 0,
                    "generated_at": mod._utc_iso(),
                }
            else:
                tag_ctr: Counter[str] = Counter()
                tb_ctr: Counter[str] = Counter()
                hint_ctr: Counter[str] = Counter()
                unknown_loc = 0
                unknown_hour = 0
                for it in items:
                    tb = str(it.get("time_bucket") or "unknown")
                    tb_ctr[tb] += 1
                    h = it.get("music_scene_hint")
                    if isinstance(h, str) and h.strip():
                        hint_ctr[h.strip()] += 1
                    for t in it.get("scene_tags") or []:
                        if isinstance(t, str) and t.strip():
                            tag_ctr[t.strip()] += 1
                    if it.get("inferred_hour") is None:
                        unknown_hour += 1
                    bn = str(it.get("borough") or "").strip()
                    nh = str(it.get("neighborhood") or "").strip()
                    lm = str(it.get("nearby_landmark") or "").strip()
                    if not bn and not nh and not lm:
                        unknown_loc += 1

                top_tags = [{"tag": a, "count": b} for a, b in tag_ctr.most_common(40)]
                status = {
                    "ok": True,
                    "path": str(scene_path),
                    "source_index_path": raw.get("source_index_path") if isinstance(raw, dict) else None,
                    "enrichment_generated_at": raw.get("generated_at") if isinstance(raw, dict) else None,
                    "total_items": len(items),
                    "time_bucket_counts": dict(tb_ctr),
                    "top_scene_tags": top_tags,
                    "music_scene_hint_counts": dict(hint_ctr),
                    "unknown_location_count": unknown_loc,
                    "unknown_hour_count": unknown_hour,
                    "generated_at": mod._utc_iso(),
                }

    out_p = xfer / "media_index" / "scene_signal_status.json"
    try:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        t = out_p.with_suffix(".json.tmp")
        t.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        t.replace(out_p)
    except OSError as exc:
        status["write_error"] = repr(exc)
        err_count += 1

    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0 if err_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
