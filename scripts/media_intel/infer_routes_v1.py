#!/usr/bin/env python3
"""StateVerge Route Inference v1 — lightweight deterministic sessions + route hints.

Read-only on media files. Writes only ``media_index_v3_routes*.json`` and reports
under ``get_sv_transfer()/media_index/`` (same parent as the chosen index).
Does **not** modify ``media_index_v3.json`` or enriched index unless
``--merge-enriched-out`` is set to a separate output path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


IMG_STEM_RE = re.compile(r"\b(IMG|VID|DJI|PXL)_(\d{3,5})\b", re.I)
INBOX_DATE_RE = re.compile(r"/inbox/([^/]+)/", re.I)


def default_index_candidates() -> tuple[Path, Path]:
    d = get_sv_transfer(verbose=False) / "media_index"
    return d / "media_index_v3_enriched.json", d / "media_index_v3.json"


def resolve_default_index_path() -> Path:
    enriched, base = default_index_candidates()
    if enriched.is_file():
        return enriched
    return base


def _norm_str(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _lower_hay(item: dict[str, Any]) -> str:
    parts: list[str] = []
    p = _norm_str(item.get("path"))
    fn = _norm_str(item.get("filename"))
    parts.extend([p.lower(), fn.lower()])
    for k in ("scene_type", "mood", "usable_for", "borough", "landmark", "neighborhood"):
        v = item.get(k)
        if isinstance(v, list):
            parts.extend(str(x).lower() for x in v)
        elif isinstance(v, str):
            parts.append(v.lower())
    lst = item.get("likely_source_type")
    if isinstance(lst, str) and lst:
        parts.append(lst.lower())
    return " ".join(x for x in parts if x)


def source_bucket(path_str: str) -> str:
    cur = path_str.replace("\\", "/")
    if "/00_INBOX/iphone" in cur:
        return "premium_inbox"
    low = cur.lower()
    if "sv_cache" in low and "/inbox/" in low:
        return "sv_cache_inbox"
    if "/sv_cache/" in low and "inbox" in low:
        return "sv_cache_inbox"
    return "other"


def inbox_date_folder(path_str: str) -> str | None:
    m = INBOX_DATE_RE.search(path_str.replace("\\", "/"))
    if not m:
        return None
    return m.group(1)


def parse_created_ts(item: dict[str, Any], path_str: str) -> float:
    for key in ("created_at", "created_time"):
        raw = item.get(key)
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            try:
                return float(raw)
            except (TypeError, ValueError):
                continue
        if isinstance(raw, str) and raw.strip():
            s = raw.strip()
            try:
                if s.isdigit():
                    return float(s)
            except (TypeError, ValueError):
                pass
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%fZ",
                "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%dT%H:%M:%S%z",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(s.replace("Z", "+00:00")[:32], fmt)
                    if dt.tzinfo is None:
                        return dt.replace(tzinfo=timezone.utc).timestamp()
                    return dt.timestamp()
                except ValueError:
                    continue
            try:
                d2 = datetime.fromisoformat(s.replace("Z", "+00:00"))
                return d2.timestamp()
            except ValueError:
                pass
    p = Path(path_str)
    try:
        if p.is_file():
            return float(p.stat().st_mtime)
    except OSError:
        pass
    return 0.0


def calendar_date_key(ts: float, path_str: str) -> str:
    if ts > 0:
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        except (OSError, OverflowError, ValueError):
            pass
    try:
        st = Path(path_str).stat()
        return datetime.fromtimestamp(float(st.st_mtime), tz=timezone.utc).date().isoformat()
    except OSError:
        return "unknown-date"


def _gps_ok(item: dict[str, Any]) -> bool:
    try:
        lat = float(item.get("gps_lat"))
        lon = float(item.get("gps_lon"))
    except (TypeError, ValueError):
        return False
    return abs(lat) <= 90.0 and abs(lon) <= 180.0 and not (lat == 0.0 and lon == 0.0)


def _borough_nonempty(item: dict[str, Any]) -> bool:
    return bool(_norm_str(item.get("borough")))


def _landmark_nonempty(item: dict[str, Any]) -> bool:
    return bool(_norm_str(item.get("landmark")))


def _img_stem_num(filename: str) -> tuple[str, int] | None:
    m = IMG_STEM_RE.search(filename or "")
    if not m:
        return None
    try:
        return m.group(1).upper(), int(m.group(2))
    except (TypeError, ValueError):
        return None


def filename_continuity_streak(paths_sorted: list[str]) -> int:
    """Longest chain of consecutive IMG/VID numbers in session order."""
    best = 0
    cur = 0
    prev_pair: tuple[str, int] | None = None
    for pth in paths_sorted:
        fn = Path(pth).name
        pair = _img_stem_num(fn)
        if pair is None:
            cur = 0
            prev_pair = None
            continue
        if prev_pair and pair[0] == prev_pair[0] and pair[1] == prev_pair[1] + 1:
            cur += 1
        else:
            cur = 1
        best = max(best, cur)
        prev_pair = pair
    return best


def infer_route_type_for_session(sess_items: list[dict[str, Any]]) -> str:
    ferry = skyline = timelapse = driving = walking = 0
    fps_vals: list[float] = []
    for it in sess_items:
        hay = _lower_hay(it)
        if any(k in hay for k in ("ferry", "waterfront", "staten island", "east river crossing")):
            ferry += 3
        if "path" in hay and "ferry" in _norm_str(it.get("path")).lower():
            ferry += 2
        if any(k in hay for k in ("skyline", "manhattan skyline", "empire state", "brooklyn bridge view", "epic view")):
            skyline += 3
        if any(k in hay for k in ("timelapse", "time lapse", "time-lapse", "hyperlapse")):
            timelapse += 3
        if any(k in hay for k in ("driving", "dashcam", "dashboard", "car mount", "vehicle")):
            driving += 2
        orient = _norm_str(it.get("orientation")).lower()
        if orient in ("portrait", "vertical", "9:16") and any(
            k in hay for k in ("walking", "walk", "street walk", "pov walk")
        ):
            walking += 4
        elif any(k in hay for k in ("walking", "walk", "hike")):
            walking += 2
        try:
            fps_vals.append(float(it.get("fps") or 0.0))
        except (TypeError, ValueError):
            pass
    mean_fps = sum(fps_vals) / len(fps_vals) if fps_vals else 0.0
    if mean_fps >= 48.0 and timelapse >= driving:
        timelapse += 2
    elif mean_fps >= 48.0 and driving == 0 and timelapse == 0:
        timelapse += 1

    scored: list[tuple[str, int]] = [
        ("ferry_route", ferry),
        ("skyline_sequence", skyline),
        ("stationary_timelapse", timelapse),
        ("driving_route", driving),
        ("walking_route", walking),
    ]
    priority = {
        "ferry_route": 0,
        "skyline_sequence": 1,
        "stationary_timelapse": 2,
        "driving_route": 3,
        "walking_route": 4,
    }
    scored.sort(key=lambda x: (-x[1], priority[x[0]]))
    if scored[0][1] <= 0:
        return "unknown_route"
    return scored[0][0]


ROUTE_FIELD_KEYS: tuple[str, ...] = (
    "inferred_route_id",
    "inferred_route_type",
    "inferred_location_confidence",
    "inferred_from_neighbor",
    "inferred_neighbor_distance_sec",
    "inferred_neighbor_ids",
    "inferred_borough",
    "inferred_landmark",
    "inferred_skyline_continuity",
)


def stable_route_id_v1(
    calendar_date: str,
    bucket: str,
    paths: list[str],
    t0: float,
    t1: float,
) -> str:
    joined = "\n".join(paths)
    raw = f"v1|{calendar_date}|{bucket}|{t0:.6f}|{t1:.6f}|{len(paths)}|{joined}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def base_location_confidence(item: dict[str, Any]) -> float:
    b = _borough_nonempty(item)
    g = _gps_ok(item)
    if b and g:
        return 0.88
    if b:
        return 0.72
    if g:
        return 0.55
    return 0.28


def neighbor_confidence(delta_sec: float) -> float:
    # 0.50 at 0s down to 0.35 at >=1200s
    t = min(max(delta_sec, 0.0), 1200.0)
    return 0.5 - (t / 1200.0) * 0.15


@dataclass
class WorkItem:
    item: dict[str, Any]
    path: str
    item_id: str
    ts: float
    calendar_date: str
    bucket: str
    inbox_folder: str | None
    filename: str


def load_index_items(index_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        data = json.loads(index_path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Failed to read index: {index_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("Index root must be a JSON object")
    items = data.get("items")
    if not isinstance(items, list):
        raise SystemExit("Index missing items array")
    out = [x for x in items if isinstance(x, dict) and _norm_str(x.get("path"))]
    return data, out


def build_work_items(raw_items: list[dict[str, Any]]) -> list[WorkItem]:
    work: list[WorkItem] = []
    for it in raw_items:
        path = _norm_str(it.get("path"))
        ts = parse_created_ts(it, path)
        cd = calendar_date_key(ts, path)
        bucket = source_bucket(path)
        iid = _norm_str(it.get("id")) or path
        fn = _norm_str(it.get("filename")) or Path(path).name
        work.append(
            WorkItem(
                item=it,
                path=path,
                item_id=iid,
                ts=ts,
                calendar_date=cd,
                bucket=bucket,
                inbox_folder=inbox_date_folder(path),
                filename=fn,
            )
        )
    work.sort(key=lambda w: (w.calendar_date, w.bucket, w.ts, w.filename.lower(), w.path))
    return work


def split_sessions(
    ordered: list[WorkItem],
    *,
    gap_sec: float,
) -> list[list[WorkItem]]:
    if not ordered:
        return []
    sessions: list[list[WorkItem]] = []
    cur: list[WorkItem] = [ordered[0]]
    for prev, this in zip(ordered, ordered[1:]):
        gap = this.ts - prev.ts if this.ts > 0 and prev.ts > 0 else 0.0
        split = False
        if gap > gap_sec:
            split = True
        if prev.bucket == "sv_cache_inbox" and this.bucket == "sv_cache_inbox":
            pf, tf = prev.inbox_folder, this.inbox_folder
            if pf and tf and pf != tf:
                split = True
        if split:
            sessions.append(cur)
            cur = [this]
        else:
            cur.append(this)
    sessions.append(cur)
    return sessions


def propagate_borough_landmark(
    session: list[WorkItem],
    *,
    neighbor_window_sec: float,
) -> tuple[dict[str, dict[str, Any]], int]:
    """Returns per-path annotation updates and borough_recovery_count."""
    n = len(session)
    by_path: dict[str, dict[str, Any]] = {}
    recovery = 0
    times = [w.ts for w in session]

    def donor_scan(idx: int) -> tuple[WorkItem | None, float]:
        best: tuple[WorkItem | None, float] = (None, float("inf"))
        for j in range(n):
            if j == idx:
                continue
            wj = session[j]
            if not (_borough_nonempty(wj.item) and _gps_ok(wj.item)):
                continue
            dt = abs(times[j] - times[idx]) if times[j] > 0 and times[idx] > 0 else float("inf")
            if dt > neighbor_window_sec:
                continue
            if dt < best[1]:
                best = (wj, dt)
        return best

    for i, w in enumerate(session):
        orig_b = _norm_str(w.item.get("borough"))
        updates: dict[str, Any] = {}
        donor, dt = donor_scan(i)
        if not orig_b and donor is not None and dt < float("inf"):
            updates["inferred_borough"] = _norm_str(donor.item.get("borough"))
            lm = _norm_str(donor.item.get("landmark"))
            if lm and not _landmark_nonempty(w.item):
                updates["inferred_landmark"] = lm
            elif _landmark_nonempty(w.item):
                updates["inferred_landmark"] = _norm_str(w.item.get("landmark"))
            else:
                updates["inferred_landmark"] = lm
            updates["inferred_from_neighbor"] = True
            updates["inferred_neighbor_distance_sec"] = round(float(dt), 3)
            updates["inferred_neighbor_ids"] = [donor.item_id]
            updates["inferred_location_confidence"] = neighbor_confidence(dt)
            recovery += 1
        else:
            updates["inferred_from_neighbor"] = False
            updates["inferred_neighbor_distance_sec"] = None
            updates["inferred_neighbor_ids"] = []
            updates["inferred_borough"] = orig_b or _norm_str(w.item.get("borough"))
            updates["inferred_landmark"] = _norm_str(w.item.get("landmark"))
            updates["inferred_location_confidence"] = base_location_confidence(w.item)
        by_path[w.path] = updates
    return by_path, recovery


def skyline_propagation_flags(session: list[WorkItem]) -> dict[str, bool]:
    """Neighbor prev/next in session: weak skyline / continuity flag."""
    keys = ("skyline", "ferry", "night", "waterfront", "dusk", "golden hour")
    out: dict[str, bool] = {}
    for i, w in enumerate(session):
        hay_self = _lower_hay(w.item)
        strong = any(k in hay_self for k in keys)
        prev_h = _lower_hay(session[i - 1].item) if i > 0 else ""
        next_h = _lower_hay(session[i + 1].item) if i + 1 < len(session) else ""
        neighbor_tag = any(k in prev_h for k in keys) or any(k in next_h for k in keys)
        out[w.path] = bool((not strong) and neighbor_tag)
    return out


def merge_enriched_with_routes(
    index_data: dict[str, Any],
    route_by_path: dict[str, dict[str, Any]],
    route_keys: Iterable[str],
) -> dict[str, Any]:
    merged = json.loads(json.dumps(index_data, ensure_ascii=False))
    items = merged.get("items")
    if not isinstance(items, list):
        return merged
    rk = set(route_keys)
    for it in items:
        if not isinstance(it, dict):
            continue
        p = _norm_str(it.get("path"))
        extra = route_by_path.get(p)
        if not extra:
            continue
        for k in rk:
            if k in extra:
                it[k] = extra[k]
    return merged


def main() -> int:
    enriched_def, base_def = default_index_candidates()
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Default --index: use media_index_v3_enriched.json when present under "
            "get_sv_transfer()/media_index/; otherwise fall back to media_index_v3.json."
        ),
    )
    ap.add_argument(
        "--index",
        default="",
        help=(
            f"Path to media index JSON (default: enriched if exists: {enriched_def} "
            f"else {base_def})"
        ),
    )
    ap.add_argument("--gap-sec", type=float, default=45 * 60.0, help="Max gap within a session (default 2700)")
    ap.add_argument(
        "--neighbor-minutes",
        type=float,
        default=20.0,
        help="GPS/borough inheritance window within a session (default 20)",
    )
    ap.add_argument(
        "--merge-enriched-out",
        default="",
        help="Optional path to write index JSON merged with route fields (never overwrites --index).",
    )
    args = ap.parse_args()

    index_path = Path(args.index).expanduser() if _norm_str(args.index) else resolve_default_index_path()
    if not index_path.is_file():
        print(f"error: index not found: {index_path}", file=sys.stderr)
        return 1

    _, raw_items = load_index_items(index_path)
    work = build_work_items(raw_items)

    # Group by (calendar_date, bucket) preserving global sort order
    groups: dict[tuple[str, str], list[WorkItem]] = {}
    order_keys: list[tuple[str, str]] = []
    for w in work:
        k = (w.calendar_date, w.bucket)
        if k not in groups:
            groups[k] = []
            order_keys.append(k)
        groups[k].append(w)

    neighbor_sec = float(args.neighbor_minutes) * 60.0
    gap_sec = float(args.gap_sec)

    sessions_out: list[dict[str, Any]] = []
    items_out: list[dict[str, Any]] = []
    route_type_counts: dict[str, int] = {}
    borough_recovery_total = 0
    skyline_prop_total = 0

    for gkey in order_keys:
        chunk = groups[gkey]
        for sess in split_sessions(chunk, gap_sec=gap_sec):
            paths = [w.path for w in sess]
            rtype = infer_route_type_for_session([w.item for w in sess])
            route_type_counts[rtype] = route_type_counts.get(rtype, 0) + 1
            streak = filename_continuity_streak(paths)
            t0 = min((w.ts for w in sess if w.ts > 0), default=0.0)
            t1 = max((w.ts for w in sess if w.ts > 0), default=0.0)
            rid = stable_route_id_v1(gkey[0], gkey[1], paths, t0, t1)
            sessions_out.append(
                {
                    "inferred_route_id": rid,
                    "inferred_route_type": rtype,
                    "calendar_date": gkey[0],
                    "source_bucket": gkey[1],
                    "item_count": len(sess),
                    "start_ts": t0,
                    "end_ts": t1,
                    "filename_continuity_streak": streak,
                    "paths_sample": paths[:3],
                }
            )
            prop_maps, br_count = propagate_borough_landmark(sess, neighbor_window_sec=neighbor_sec)
            borough_recovery_total += br_count
            sky_flags = skyline_propagation_flags(sess)
            for w in sess:
                pm = prop_maps.get(w.path, {})
                sky = bool(sky_flags.get(w.path, False))
                if sky:
                    skyline_prop_total += 1
                if not pm.get("inferred_from_neighbor"):
                    pm["inferred_location_confidence"] = base_location_confidence(w.item)
                    pm["inferred_borough"] = _norm_str(w.item.get("borough"))
                    pm["inferred_landmark"] = _norm_str(w.item.get("landmark"))
                conf = float(pm.get("inferred_location_confidence") or 0.28)
                entry: dict[str, Any] = {
                    "path": w.path,
                    "id": w.item_id,
                    "inferred_route_id": rid,
                    "inferred_route_type": rtype,
                    "inferred_location_confidence": round(conf, 4),
                    "inferred_from_neighbor": bool(pm.get("inferred_from_neighbor")),
                    "inferred_neighbor_distance_sec": pm.get("inferred_neighbor_distance_sec"),
                    "inferred_neighbor_ids": pm.get("inferred_neighbor_ids") or [],
                    "inferred_borough": _norm_str(pm.get("inferred_borough")),
                    "inferred_landmark": _norm_str(pm.get("inferred_landmark")),
                    "inferred_skyline_continuity": sky,
                }
                items_out.append(entry)

    generated_at = datetime.now(timezone.utc).isoformat()
    out_dir = index_path.parent
    routes_path = out_dir / "media_index_v3_routes.json"
    report_json_path = out_dir / "media_index_v3_route_report.json"
    report_md_path = out_dir / "media_index_v3_route_report.md"

    routes_doc: dict[str, Any] = {
        "schema_version": "media_index_v3_routes_v1",
        "generated_at": generated_at,
        "index_source": str(index_path),
        "gap_sec": gap_sec,
        "neighbor_window_sec": neighbor_sec,
        "sessions": sessions_out,
        "items": items_out,
    }

    examples: list[dict[str, Any]] = []
    for s in sessions_out[:12]:
        examples.append(
            {
                "route_id": s.get("inferred_route_id"),
                "type": s.get("inferred_route_type"),
                "sample_paths": s.get("paths_sample") or [],
                "item_count": s.get("item_count"),
            }
        )

    report_doc: dict[str, Any] = {
        "schema_version": "media_index_v3_route_report_v1",
        "generated_at": generated_at,
        "index_source": str(index_path),
        "total_sessions": len(sessions_out),
        "route_type_counts": dict(sorted(route_type_counts.items(), key=lambda x: (-x[1], x[0]))),
        "inferred_borough_recovery_count": borough_recovery_total,
        "skyline_continuity_propagations_count": skyline_prop_total,
        "items_annotated_count": len(items_out),
        "examples": examples,
    }

    routes_path.write_text(json.dumps(routes_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_json_path.write_text(json.dumps(report_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md_lines = [
        "# Media index route inference report (v1)",
        "",
        f"- **Generated (UTC)**: `{generated_at}`",
        f"- **Index source**: `{index_path}`",
        f"- **Total sessions**: {len(sessions_out)}",
        f"- **Items annotated**: {len(items_out)}",
        f"- **Borough recovery (neighbor propagation)**: {borough_recovery_total}",
        f"- **Skyline continuity flags**: {skyline_prop_total}",
        "",
        "## Route type counts",
        "",
    ]
    for k, v in sorted(route_type_counts.items(), key=lambda x: (-x[1], x[0])):
        md_lines.append(f"- `{k}`: {v}")
    md_lines.extend(["", "## Example sessions", ""])
    for ex in examples[:8]:
        md_lines.append(f"- **{ex.get('type')}** (`{ex.get('route_id')}`) — {ex.get('item_count')} clips")
        for sp in ex.get("sample_paths") or []:
            md_lines.append(f"  - `{sp}`")
    report_md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    merge_out = _norm_str(args.merge_enriched_out)
    if merge_out:
        mpath = Path(merge_out).expanduser()
        if mpath.resolve() == index_path.resolve():
            print("error: --merge-enriched-out must differ from --index", file=sys.stderr)
            return 1
        index_full, _ = load_index_items(index_path)
        by_path = {x["path"]: {k: x[k] for k in ROUTE_FIELD_KEYS if k in x} for x in items_out}
        merged = merge_enriched_with_routes(index_full, by_path, ROUTE_FIELD_KEYS)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote merge-enriched-out: {mpath}")

    print(
        json.dumps(
            {
                "ok": True,
                "routes_json": str(routes_path),
                "report_json": str(report_json_path),
                "report_md": str(report_md_path),
                "total_sessions": len(sessions_out),
                "route_type_counts": route_type_counts,
                "inferred_borough_recovery_count": borough_recovery_total,
                "skyline_continuity_propagations_count": skyline_prop_total,
                "items_annotated_count": len(items_out),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
