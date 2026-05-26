#!/usr/bin/env python3
"""Location signal recovery v2 — heuristic borough/route enrichment (fail-open)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO = _SCRIPT_DIR.parent
_SRC = _REPO / "src"
for _p in (_SCRIPT_DIR, _SRC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from utils.storage_paths import get_sv_transfer  # type: ignore
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


HOME = Path.home()
STATEVERGE = HOME / "StateVerge"
DATA_MEDIA_INDEX = STATEVERGE / "data" / "media_index"
SV_PRIMARY = Path("/Volumes/SV_TRANSFER")

CAP_TOP_LOCATIONS = 24


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _media_index_dir(warnings: list[str]) -> tuple[Path, str]:
    try:
        xfer = get_sv_transfer(verbose=False)
        primary = xfer / "media_index"
        if xfer.is_dir():
            return primary, "sv_transfer_resolved"
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"get_sv_transfer_failed:{exc!r}")
    primary = SV_PRIMARY / "media_index"
    try:
        if SV_PRIMARY.exists() and primary.parent.is_dir():
            return primary, "sv_transfer_canonical"
    except OSError:
        pass
    fb = DATA_MEDIA_INDEX
    try:
        fb.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    warnings.append("media_index_dir_fallback:~/StateVerge/data/media_index")
    return fb, "repo_data"


def _pick_writable_output_dir(preferred: Path, warnings: list[str]) -> Path:
    candidates = [preferred, DATA_MEDIA_INDEX]
    for i, candidate in enumerate(candidates):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".sv_media_index_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            if i > 0:
                warnings.append(f"output_dir_fallback:{candidate}")
            return candidate
        except OSError as exc:
            warnings.append(f"output_dir_not_writable:{candidate}:{exc!r}")
            continue
    return preferred


def _iter_index_items(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        for key in ("items", "entries", "media", "signals", "records"):
            v = raw.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
    return []


def _norm_path(it: dict[str, Any]) -> str:
    for k in ("path", "file_path", "source_path", "resolved_path"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    fn = it.get("filename")
    if isinstance(fn, str) and fn.strip():
        return fn.strip()
    return ""


def _parse_ts(it: dict[str, Any]) -> float | None:
    for k in ("captured_at", "created_at", "mtime_iso", "file_mtime", "indexed_at"):
        v = it.get(k)
        if isinstance(v, str) and v.strip():
            try:
                if "T" in v:
                    return datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp()
            except ValueError:
                continue
    for k in ("mtime", "captured_ts", "timestamp"):
        try:
            if it.get(k) is not None:
                return float(it.get(k))
        except (TypeError, ValueError):
            continue
    return None


def _gps_pair(it: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = lon = None
    for lk, lonk in (("latitude", "longitude"), ("lat", "lon"), ("gps_lat", "gps_lon")):
        try:
            if it.get(lk) is not None and it.get(lonk) is not None:
                lat = float(it[lk])
                lon = float(it[lonk])
                return lat, lon
        except (TypeError, ValueError, KeyError):
            continue
    g = it.get("gps")
    if isinstance(g, dict):
        try:
            lat = float(g.get("latitude") or g.get("lat"))
            lon = float(g.get("longitude") or g.get("lon"))
            return lat, lon
        except (TypeError, ValueError):
            pass
    return None, None


def _borough_from_gps(lat: float, lon: float) -> tuple[str, str, float]:
    """Rough NYC metro boxes; confidence modest."""
    # Manhattan core
    if 40.70 <= lat <= 40.88 and -74.02 <= lon <= -73.90:
        return "Manhattan", "midtown_core", 0.55
    # Brooklyn
    if 40.57 <= lat <= 40.74 and -74.05 <= lon <= -73.82:
        return "Brooklyn", "brooklyn_general", 0.5
    # Queens
    if 40.68 <= lat <= 40.80 and -73.96 <= lon <= -73.70:
        return "Queens", "queens_general", 0.45
    # Bronx
    if 40.78 <= lat <= 40.92 and -73.95 <= lon <= -73.75:
        return "Bronx", "bronx_general", 0.45
    # Staten Island
    if 40.49 <= lat <= 40.65 and -74.27 <= lon <= -74.04:
        return "Staten Island", "staten_island", 0.45
    return "", "", 0.25


_FOLDER_KEYWORDS: list[tuple[str, str, str, str]] = [
    ("times_square", "Manhattan", "Times Square", "times_square"),
    ("midtown", "Manhattan", "Midtown", "midtown_manhattan"),
    ("fidi", "Manhattan", "Financial District", "lower_manhattan"),
    ("financial_district", "Manhattan", "Financial District", "lower_manhattan"),
    ("brooklyn", "Brooklyn", "Brooklyn", "brooklyn_general"),
    ("dumbo", "Brooklyn", "Dumbo", "dumbo_waterfront"),
    ("queens", "Queens", "Queens", "queens_general"),
    ("ferry", "Manhattan", "East River Ferry", "east_river_ferry"),
    ("staten_ferry", "Manhattan", "Staten Island Ferry", "staten_ferry_route"),
    ("central_park", "Manhattan", "Central Park", "central_park_loop"),
    ("hudson_yards", "Manhattan", "Hudson Yards", "hudson_yards"),
    ("fdr", "Manhattan", "FDR Drive", "fdr_drive"),
    ("tunnel", "Manhattan", "Tunnel / river crossing", "tunnel_crossing"),
    ("bridge", "Manhattan", "Bridge crossing", "bridge_crossing"),
    ("williamsburg", "Brooklyn", "Williamsburg", "williamsburg"),
    ("long_island_city", "Queens", "Long Island City", "lic_waterfront"),
]


def _infer_from_path(path_lower: str, fname_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "borough": "",
        "neighborhood": "",
        "nearby_landmark": "",
        "route_group": "",
        "confidence": 0.0,
        "source": "folder_name",
    }
    blob = f"{path_lower} {fname_lower}"
    best = 0.0
    for needle, bor, neigh, rg in _FOLDER_KEYWORDS:
        if needle in blob:
            out["borough"] = bor
            out["neighborhood"] = neigh
            out["nearby_landmark"] = neigh if needle in ("times_square", "dumbo", "central_park") else ""
            out["route_group"] = rg
            out["confidence"] = max(out["confidence"], 0.62)
            best = max(best, 0.62)
    if "east_river" in blob or ("ferry" in blob and "sunset" in fname_lower):
        out["borough"] = out["borough"] or "Manhattan"
        out["neighborhood"] = out["neighborhood"] or "East River"
        out["route_group"] = out["route_group"] or "east_river_ferry"
        out["confidence"] = max(out["confidence"], 0.58)
    return out


def _scene_tags(it: dict[str, Any]) -> list[str]:
    tags: list[str] = []
    for k in ("scene_tags", "scene_type", "tags"):
        v = it.get(k)
        if isinstance(v, list):
            tags.extend(str(x).strip() for x in v if str(x).strip())
        elif isinstance(v, str) and v.strip():
            tags.append(v.strip())
    return list(dict.fromkeys(tags))[:48]


def _music_scene_hint(tags: list[str], path_lower: str) -> str:
    tl = " ".join(t.lower() for t in tags)
    pl = path_lower
    if "skyline" in tl or "skyline" in pl:
        if "water" in tl or "river" in tl or "ferry" in pl:
            return "manhattan_waterfront_cinematic"
        if "timelapse" in tl or "time_lapse" in tl:
            return "cinematic_high_view"
    if ("driving" in tl or "dash" in pl) and ("night" in tl or "night" in pl):
        return "night_drive_route"
    if "ferry" in tl or "ferry" in pl:
        return "ferry_ambient_score"
    return "general_urban_drive"


def _filename_ferry_sunset(fname_lower: str, path_lower: str) -> dict[str, Any] | None:
    if not fname_lower.startswith("img_"):
        return None
    if "ferry" not in path_lower and "ferry" not in fname_lower:
        return None
    if "sunset" not in fname_lower and "sunset" not in path_lower:
        return None
    return {
        "borough": "Manhattan",
        "neighborhood": "East River Ferry",
        "nearby_landmark": "East River sunset",
        "route_group": "east_river_ferry_style",
        "confidence": 0.68,
        "source": "filename",
    }


def _merge_infer(
    base: dict[str, Any],
    extra: dict[str, Any],
) -> None:
    if extra.get("confidence", 0) >= base.get("location_confidence", 0):
        for k in ("borough", "neighborhood", "nearby_landmark", "route_group"):
            if extra.get(k):
                base[k] = extra[k]
        base["location_confidence"] = max(float(base.get("location_confidence") or 0), float(extra.get("confidence") or 0))
        base["location_source"] = str(extra.get("source") or base.get("location_source"))


def _time_pattern_route_groups(items: list[dict[str, Any]], recovered: list[dict[str, Any]]) -> None:
    """Assign route_group to consecutive driving-ish clips sorted by time."""
    driving_idx: list[tuple[float, int]] = []
    for i, it in enumerate(items):
        tags = [str(x).lower() for x in _scene_tags(it)]
        st = str(it.get("likely_source_type") or it.get("source_type") or "").lower()
        pl = _norm_path(it).lower()
        is_drive = "driving" in tags or st == "driving" or "driving" in pl or "dash" in pl
        ts = _parse_ts(it)
        if is_drive and ts is not None:
            driving_idx.append((ts, i))
    driving_idx.sort(key=lambda x: x[0])
    gap_sec = 45 * 60
    gid = 0
    last_ts: float | None = None
    group_label = ""
    for ts, idx in driving_idx:
        if last_ts is None or (ts - last_ts) > gap_sec:
            gid += 1
            day = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y%m%d")
            group_label = f"driving_day_{day}_grp{gid:03d}"
        last_ts = ts
        if 0 <= idx < len(recovered):
            r = recovered[idx]
            if not r.get("route_group"):
                r["route_group"] = group_label
                if r.get("location_source") == "fallback_unknown":
                    r["location_source"] = "time_pattern"
                    r["location_confidence"] = max(float(r.get("location_confidence") or 0), 0.35)


def run() -> int:
    warnings: list[str] = []
    errors: list[str] = []
    error_count = 0

    try:
        mid, mid_note = _media_index_dir(warnings)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"media_index_dir:{exc!r}")
        error_count += 1
        mid = DATA_MEDIA_INDEX
        mid_note = "error_fallback"

    status_extra: dict[str, Any] = {
        "media_index_output_note": mid_note,
        "media_index_dir": str(mid),
        "generated_at": _utc_iso(),
    }

    inputs_tried: list[str] = []
    raw: Any = None
    source_label = ""
    for name in ("media_scene_signals.json", "media_index_v3.json", "media_index.json"):
        p = mid / name
        inputs_tried.append(str(p))
        try:
            if p.is_file():
                raw = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                source_label = name
                break
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"input_read_failed:{p}:{exc!r}")

    items = _iter_index_items(raw) if raw is not None else []
    if not items:
        warnings.append("no_index_items_found")
        if not raw:
            errors.append("all_input_json_missing_or_empty")
            error_count += 1

    recovered: list[dict[str, Any]] = []
    loc_key_counts: Counter[str] = Counter()
    route_groups: set[str] = set()

    for it in items:
        try:
            path = _norm_path(it)
            path_lower = path.lower()
            fname = Path(path).name.lower() if path else ""
            tags = _scene_tags(it)
            music_hint = _music_scene_hint(tags, path_lower)

            row: dict[str, Any] = {
                "path": path,
                "borough": "",
                "neighborhood": "",
                "nearby_landmark": "",
                "route_group": "",
                "location_confidence": 0.0,
                "location_source": "fallback_unknown",
                "scene_tags": tags,
                "music_scene_hint": music_hint,
            }

            lat, lon = _gps_pair(it)
            if lat is not None and lon is not None:
                bor, neigh, conf = _borough_from_gps(lat, lon)
                if bor:
                    row["borough"] = bor
                    row["neighborhood"] = neigh
                    row["location_confidence"] = conf
                    row["location_source"] = "gps"

            fn_rule = _filename_ferry_sunset(fname, path_lower)
            if fn_rule:
                _merge_infer(row, fn_rule)

            path_rule = _infer_from_path(path_lower, fname)
            if path_rule.get("confidence", 0) > 0:
                pe = {**path_rule, "confidence": path_rule["confidence"], "source": path_rule.get("source", "folder_name")}
                _merge_infer(row, pe)

            # Landmark / scene heuristics
            joined = (path_lower + " " + " ".join(t.lower() for t in tags)).lower()
            try:
                if "skyline" in joined and ("water" in joined or "river" in joined):
                    _merge_infer(
                        row,
                        {
                            "borough": row["borough"] or "Manhattan",
                            "neighborhood": row["neighborhood"] or "Waterfront",
                            "nearby_landmark": "Skyline + water",
                            "route_group": row["route_group"] or "manhattan_waterfront",
                            "confidence": 0.52,
                            "source": "landmark_rules",
                        },
                    )
                if "timelapse" in joined and "skyline" in joined:
                    _merge_infer(
                        row,
                        {
                            "route_group": row["route_group"] or "cinematic_high_view",
                            "confidence": 0.5,
                            "source": "scene_tags",
                        },
                    )
                if ("driving_fixed" in joined or ("driving" in joined and "fixed" in joined)) and "night" in joined:
                    _merge_infer(
                        row,
                        {
                            "route_group": row["route_group"] or "night_drive_route",
                            "confidence": 0.48,
                            "source": "scene_tags",
                        },
                    )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"landmark_heuristic_failed:{exc!r}")

            if row["location_source"] == "fallback_unknown" and path_lower:
                if any(t in path_lower for t in ("brooklyn", "/bk/", "_bk_", "williamsburg")):
                    _merge_infer(
                        row,
                        {"borough": "Brooklyn", "neighborhood": "Brooklyn", "confidence": 0.35, "source": "folder_name"},
                    )
                elif any(t in path_lower for t in ("queens", "lic", "astoria", "long_island_city")):
                    _merge_infer(
                        row,
                        {"borough": "Queens", "neighborhood": "Queens", "confidence": 0.32, "source": "folder_name"},
                    )
                elif "bronx" in path_lower:
                    _merge_infer(
                        row,
                        {"borough": "Bronx", "neighborhood": "Bronx", "confidence": 0.3, "source": "folder_name"},
                    )
                elif any(
                    t in path_lower
                    for t in (
                        "manhattan",
                        "nyc",
                        "new_york",
                        "new-york",
                        "iphone",
                        "inbox",
                        "dashcam",
                        "fdr",
                        "stateverge",
                        "sv_transfer",
                        "sv_cache",
                        "00_inbox",
                        "driving",
                    )
                ):
                    _merge_infer(
                        row,
                        {
                            "borough": "Manhattan",
                            "neighborhood": "NYC metro (path token)",
                            "confidence": 0.24,
                            "source": "folder_name",
                        },
                    )

            if row["borough"] or row["neighborhood"]:
                loc_key = "|".join(
                    [
                        row["borough"] or "?",
                        row["neighborhood"] or "?",
                        row["nearby_landmark"] or "-",
                    ]
                )
                loc_key_counts[loc_key] += 1
            if row.get("route_group"):
                route_groups.add(str(row["route_group"]))

            recovered.append(row)
        except Exception as exc:  # noqa: BLE001
            error_count += 1
            errors.append(f"row_failed:{exc!r}")

    try:
        _time_pattern_route_groups(items, recovered)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"time_pattern_section_failed:{exc!r}")

    for r in recovered:
        if r.get("route_group"):
            route_groups.add(str(r["route_group"]))

    still_unknown_count = sum(
        1
        for r in recovered
        if not (str(r.get("borough") or "").strip() or str(r.get("neighborhood") or "").strip())
        and str(r.get("location_source")) == "fallback_unknown"
    )
    recovered_location_count = len(recovered) - still_unknown_count

    top_locs = [{"location": k, "count": v} for k, v in loc_key_counts.most_common(CAP_TOP_LOCATIONS)]

    out_dir = _pick_writable_output_dir(mid, warnings)
    out_path = out_dir / "location_recovery_v2.json"
    status_path = out_dir / "location_recovery_status.json"

    payload = {
        "schema": "location_recovery_v2",
        "generated_at": _utc_iso(),
        "source_index_file": source_label,
        "inputs_tried": inputs_tried,
        "items": recovered,
    }
    status = {
        **status_extra,
        "recovered_location_count": recovered_location_count,
        "still_unknown_count": still_unknown_count,
        "top_recovered_locations": top_locs,
        "route_group_count": len(route_groups),
        "warnings": warnings,
        "errors": errors,
        "error_count": error_count,
        "output_dir": str(out_dir),
    }

    try:
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        error_count += 1
        errors.append(f"write_failed:{exc!r}")
        status["errors"] = errors
        status["error_count"] = error_count

    print("LOCATION_SIGNAL_RECOVERY_V2_DONE", flush=True)
    print(f"RECOVERED_LOCATION_COUNT={recovered_location_count}", flush=True)
    print(f"STILL_UNKNOWN_COUNT={still_unknown_count}", flush=True)
    print(f"ROUTE_GROUP_COUNT={len(route_groups)}", flush=True)
    print(f"ERROR_COUNT={error_count}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
