#!/usr/bin/env python3
"""Scene Signal Enrichment v1 — derive per-clip scene tags + music hints for music_selector."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_SV_SRC = Path.home() / "StateVerge" / "src"
for _p in (_SCRIPT_DIR, _SV_SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _xfer() -> Path:
    try:
        from utils.storage_paths import get_sv_transfer  # type: ignore

        return get_sv_transfer(verbose=False)
    except Exception:
        return Path("/Volumes/SV_TRANSFER")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _load_index_items(xfer: Path) -> tuple[list[dict[str, Any]], str]:
    mid = xfer / "media_index"
    v3 = mid / "media_index_v3.json"
    v1 = mid / "media_index.json"
    for p in (v3, v1):
        if p.is_file():
            raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
            items = _iter_index_items(raw)
            return [x for x in items if isinstance(x, dict)], str(p)
    return [], ""


def _iter_index_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "entries", "media"):
            v = raw.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _join_lower_parts(it: dict[str, Any]) -> str:
    chunks: list[str] = []

    def add(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, (list, tuple)):
            for x in v:
                if x is not None:
                    chunks.append(str(x).lower())
        else:
            chunks.append(str(v).lower())

    for k in (
        "borough",
        "neighborhood",
        "nearby_landmark",
        "landmark",
        "location_name",
        "ai_summary",
        "mood",
        "scene_type",
        "usable_for",
        "time_of_day",
        "route_group",
        "embedding_text",
        "search_keywords",
        "file_path",
        "path",
        "filename",
    ):
        add(it.get(k))
    lp = it.get("long_source_policy")
    if isinstance(lp, dict):
        add(lp.get("source_type"))
    add(it.get("likely_source_type"))
    return " ".join(chunks)


def _refine_time_bucket(h: int | None) -> str:
    if h is None:
        return "unknown"
    if h <= 5 or h >= 20:
        return "night"
    if 18 <= h <= 19:
        return "sunset"
    if 16 <= h <= 17:
        return "golden_hour"
    if 6 <= h < 8:
        return "dawn"
    if 8 <= h < 16:
        return "day"
    return "day"


def _flags_from_hour(h: int | None, it: dict[str, Any]) -> tuple[bool, bool, bool]:
    is_night = False
    is_sunset_window = False
    is_daytime = False
    if h is not None:
        is_night = h >= 18 or h < 6
        is_sunset_window = 16 <= h < 20
        is_daytime = 10 <= h < 16
    else:
        tod = str(it.get("time_of_day") or "").strip().lower()
        if tod in ("night", "late_night"):
            is_night = True
        elif tod in ("evening", "dusk"):
            is_night = True
            is_sunset_window = True
        elif tod == "day":
            is_daytime = True
    return is_night, is_sunset_window, is_daytime


def _scene_tags_for_item(
    it: dict[str, Any],
    inferred_hour: int | None,
    time_bucket: str,
    loc_blob: str,
    src_blob: str,
    orientation: str,
    usable_for: list[str],
    is_night: bool,
) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()

    def add(t: str) -> None:
        if t and t not in seen:
            seen.add(t)
            tags.append(t)

    if inferred_hour is not None and 18 <= inferred_hour <= 20:
        add("sunset")
        add("golden_hour")
    if inferred_hour is not None and (inferred_hour >= 20 or inferred_hour <= 5):
        add("night")

    if "driving" in src_blob:
        add("driving")
    if it.get("manual_timelapse") is True or it.get("is_manual_timelapse") is True:
        add("timelapse")
    if it.get("is_timelapse") is True:
        add("timelapse")

    if "ferry" in loc_blob:
        add("ferry")
    if "water" in loc_blob or "river" in loc_blob:
        add("water")
    if "statue" in loc_blob or "liberty" in loc_blob:
        add("skyline")

    if "times square" in loc_blob or "times_square" in loc_blob:
        add("times_square")
        add("neon")
        add("crowd")
        add("cinematic")

    if is_night and ("midtown" in loc_blob or "manhattan" in loc_blob):
        add("manhattan_night")
        add("night_drive")

    for needle in ("bridge", "tunnel", "fdr"):
        if needle in loc_blob or needle in src_blob:
            add("driving")
            if needle == "bridge":
                add("bridge")
            if needle == "tunnel":
                add("tunnel")
            add("night_drive")
            break

    for needle in ("skyline", "cloud", "high_view", "high view"):
        if needle.replace(" ", "_") in loc_blob.replace(" ", "_") or needle in loc_blob:
            add("skyline")
            add("cinematic")
            break
    if "skyline" in src_blob:
        add("skyline")
        add("cinematic")

    uf_l = [str(x).lower() for x in usable_for if x is not None]
    loc_unknown = (
        not str(it.get("borough") or "").strip()
        and not str(it.get("neighborhood") or "").strip()
        and not str(it.get("landmark") or it.get("nearby_landmark") or "").strip()
    )
    if inferred_hour is None and loc_unknown and orientation == "landscape":
        if any("premium" in u for u in uf_l) or any("archive_broll" in u or "archive" in u for u in uf_l):
            add("cinematic_broll")

    if time_bucket == "day" and ("walking" in src_blob or "park" in loc_blob or "park" in src_blob):
        add("walking")

    return tags


def _music_scene_hint(
    tags: list[str],
    inferred_hour: int | None,
    time_bucket: str,
    src_blob: str,
    loc_blob: str,
    it: dict[str, Any],
) -> str:
    tagset = set(tags)
    is_night, _, _ = _flags_from_hour(inferred_hour, it)

    if (
        "ferry" in tagset
        or "water" in tagset
        or "sunset" in tagset
        or "golden_hour" in tagset
        or time_bucket in ("sunset", "golden_hour")
    ):
        return "calm_piano"
    if "driving" in tagset and is_night:
        return "night_drive"
    if "times_square" in tagset and is_night:
        return "dark_documentary"
    if "timelapse" in tagset or "skyline" in tagset or "high_view" in tagset or "high view" in loc_blob:
        return "cinematic"
    if time_bucket in ("day", "dawn") and ("walking" in src_blob or "park" in loc_blob) and not is_night:
        return "ambient"
    if time_bucket in ("day", "dawn") and not is_night:
        return "ambient"
    return "cinematic"


def enrich_item(it: dict[str, Any]) -> dict[str, Any]:
    path = str(it.get("path") or it.get("file_path") or "")
    filename = str(it.get("filename") or it.get("file_name") or Path(path).name or "")

    captured_at = (
        str(it.get("captured_at") or "").strip()
        or str(it.get("gps_datetime") or "").strip()
        or str(it.get("created_at") or "").strip()
        or str(it.get("modified_at") or "").strip()
    )

    inferred_hour = _parse_hour_from_value(it.get("gps_datetime"))
    if inferred_hour is None:
        inferred_hour = _parse_hour_from_value(it.get("captured_at"))
    if inferred_hour is None:
        inferred_hour = _parse_hour_from_value(it.get("created_at"))
    if inferred_hour is None:
        inferred_hour = _parse_hour_from_value(it.get("modified_at"))

    time_bucket = _refine_time_bucket(inferred_hour)
    is_night, is_sunset_window, is_daytime = _flags_from_hour(inferred_hour, it)

    borough = str(it.get("borough") or "").strip()
    neighborhood = str(it.get("neighborhood") or "").strip()
    nearby = str(it.get("nearby_landmark") or it.get("landmark") or "").strip()
    location_name = str(it.get("location_name") or "").strip()
    if not location_name:
        parts = [neighborhood, borough, nearby]
        location_name = ", ".join(p for p in parts if p) or "unknown"

    st_list: list[str] = []
    for k in ("scene_type", "usable_for"):
        v = it.get(k)
        if isinstance(v, list):
            st_list.extend(str(x) for x in v if x is not None)
        elif v is not None:
            st_list.append(str(v))
    sk = it.get("search_keywords")
    if isinstance(sk, list):
        st_list.extend(str(x) for x in sk if x is not None)
    elif isinstance(sk, str) and sk.strip():
        st_list.append(sk)
    if it.get("likely_source_type"):
        st_list.append(str(it.get("likely_source_type")))
    lp = it.get("long_source_policy")
    if isinstance(lp, dict) and lp.get("source_type"):
        st_list.append(str(lp["source_type"]))
    source_type = " ".join(st_list).strip() or "unknown"

    orientation = str(it.get("orientation") or "unknown").strip() or "unknown"
    manual_tl = bool(it.get("manual_timelapse") is True or it.get("is_manual_timelapse") is True)

    loc_blob = _join_lower_parts(
        {
            "borough": borough,
            "neighborhood": neighborhood,
            "nearby_landmark": nearby,
            "location_name": location_name,
            "ai_summary": it.get("ai_summary"),
        }
    )
    src_blob = _join_lower_parts({"scene_type": it.get("scene_type"), "usable_for": it.get("usable_for"), "search_keywords": it.get("search_keywords"), "likely_source_type": it.get("likely_source_type")})

    usable_for_list: list[str] = []
    ufv = it.get("usable_for")
    if isinstance(ufv, list):
        usable_for_list = [str(x) for x in ufv if x is not None]

    scene_tags = _scene_tags_for_item(
        it, inferred_hour, time_bucket, loc_blob, src_blob, orientation, usable_for_list, is_night
    )
    music_scene_hint = _music_scene_hint(scene_tags, inferred_hour, time_bucket, src_blob, loc_blob, it)

    return {
        "path": path,
        "filename": filename,
        "captured_at": captured_at or None,
        "inferred_hour": inferred_hour,
        "time_bucket": time_bucket,
        "is_night": is_night,
        "is_sunset_window": is_sunset_window,
        "is_daytime": is_daytime,
        "borough": borough or None,
        "neighborhood": neighborhood or None,
        "nearby_landmark": nearby or None,
        "location_name": location_name,
        "scene_tags": scene_tags,
        "source_type": source_type,
        "manual_timelapse": manual_tl,
        "orientation": orientation,
        "music_scene_hint": music_scene_hint,
    }


def run(*, xfer: Path) -> dict[str, Any]:
    items_raw, src_path = _load_index_items(xfer)
    out_items = [enrich_item(it) for it in items_raw]
    out_path = xfer / "media_index" / "media_scene_signals.json"
    payload: dict[str, Any] = {
        "schema_version": "scene_signal_enrichment_v1",
        "generated_at": _utc_iso(),
        "source_index_path": src_path,
        "item_count": len(out_items),
        "items": out_items,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)
    return {"ok": True, "output_path": str(out_path), "source_index_path": src_path, "item_count": len(out_items)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Scene Signal Enrichment v1")
    ap.add_argument("--xfer-root", type=Path, default=None, help="SV_TRANSFER root (default: auto)")
    ns = ap.parse_args()
    xfer = Path(ns.xfer_root).expanduser() if ns.xfer_root else _xfer()
    meta = run(xfer=xfer)
    print(json.dumps({"ok": True, **meta}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
