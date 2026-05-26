#!/usr/bin/env python3
"""Review video169-only Long raw candidates: reject distributions, hints, suggested folders (no upload / no master)."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS = _REPO / "scripts"
_NYC = Path(__file__).resolve().parent
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")


from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    extract_gps_summary,
    is_valid_nyc_long_source,
    long_source_pool_origin,
    refresh_nyc_long_policy_caches,
)

REPORT_NAME = "video169_long_candidate_reject_review.json"

_PATH_DRIVING_STRONG = (
    "dashcam",
    "dash_cam",
    "dash-cam",
    "dvr",
    "driving",
    "drive_",
    "_drive",
    "car_cam",
    "vehicle_cam",
    "windshield",
    "rear_camera",
    "rearview",
    "rear_view",
    "interior_cam",
    "mounted_cam",
    "bridge_drive",
    "night_drive",
    "nyc_drive",
    "long_drive",
    "city_drive",
    "highway",
    "freeway",
    "fdr_drive",
    "west_side_highway",
    "belt_parkway",
    "bqe",
    "gopro",
    "hero11",
    "hero10",
    "hero9",
    "insta360",
    "osmo_action",
    "blackvue",
    "thinkware",
    "nextbase",
)
_PATH_DRIVING_WEAK = (
    "car",
    "vehicle",
    "automobile",
    "road",
    "route",
    "street",
    "avenue",
    "boulevard",
    "traffic",
    "lane",
    "tunnel",
    "bridge",
)
_PATH_FERRY = ("ferry", "water_taxi", "staten_island", "waterfront_ferry", "si_ferry")
_PATH_WALKING = (
    "walking",
    "walk_",
    "_walk",
    "handheld",
    "sidewalk",
    "pedestrian",
    "vlog",
    "foot_tour",
    "on_foot",
)
_PATH_PORTRAIT = ("portrait", "vertical", "9_16", "9-16", "reels", "tiktok", "phone_vertical")
_PATH_TIMELAPSE = ("timelapse", "time_lapse", "hyperlapse", "tlapse")


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_tokens(low: str) -> list[str]:
    found: list[str] = []
    for t in _PATH_DRIVING_STRONG:
        if t in low:
            found.append(f"driving_strong:{t}")
    for t in _PATH_DRIVING_WEAK:
        if t in low:
            found.append(f"driving_weak:{t}")
    for t in _PATH_FERRY:
        if t in low:
            found.append(f"ferry:{t}")
    for t in _PATH_WALKING:
        if t in low:
            found.append(f"walking:{t}")
    for t in _PATH_PORTRAIT:
        if t in low:
            found.append(f"portrait:{t}")
    for t in _PATH_TIMELAPSE:
        if t in low:
            found.append(f"timelapse:{t}")
    return sorted(set(found))


def _flags_from_policy(pol: dict[str, Any], low: str) -> dict[str, bool]:
    rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
    rset = set(rrs)
    st = str(pol.get("source_type") or "").lower()
    orient = str(pol.get("orientation") or "").lower()
    return {
        "is_unknown_only": st == "unknown",
        "is_walking_or_handheld": bool("walking_not_allowed_for_nyc_long_channel" in rset) or st == "walking",
        "is_portrait": bool("rejected_vertical_video" in rset or "rejected_bad_aspect_ratio" in rset)
        or orient == "portrait"
        or any(x in low for x in ("portrait", "vertical", "reels", "tiktok")),
        "is_timelapse": bool(
            "timelapse_whole_clip_not_allowed_for_long_channel" in rset
            or any(x in low for x in _PATH_TIMELAPSE)
        ),
        "lacks_driving_or_ferry_evidence": st not in ("driving", "ferry"),
    }


def _suggested_move(row: dict[str, Any]) -> str:
    low = str(row.get("path_lower") or "")
    if row.get("is_portrait"):
        return "picture169"
    if row.get("is_timelapse"):
        return "quarantine_review"
    if row.get("is_walking_or_handheld"):
        return "quarantine_review"
    toks = row.get("path_token_evidence") or []
    if any(str(t).startswith("ferry:") for t in toks):
        return "video169/ferry"
    if any(str(t).startswith("driving_strong:") or str(t).startswith("driving_weak:") for t in toks):
        return "video169/driving"
    if row.get("is_unknown_only") and row.get("long_allowed") is False:
        return "quarantine_review"
    return "video916"


def _top_reject_reasons(by_reason: dict[str, int], *, limit: int = 8) -> str:
    items = sorted(by_reason.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]
    return ";".join(f"{k}:{v}" for k, v in items)


def main() -> int:
    warnings: list[str] = []
    refresh_nyc_long_policy_caches()

    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)

    emergency = apq._load_long_emergency_state(xfer, warnings)
    active = apq._long_upload_emergency_active(emergency)
    safe_to_enable = bool(emergency.get("SAFE_TO_ENABLE_LONG_UPLOAD", False))
    if active:
        safe_to_enable = False

    rej_entries = apq._load_long_rejected_outputs(xfer, warnings)
    rej_keys = apq._long_rejected_path_keys(rej_entries)

    raw_paths = apq.collect_long_raw_source_candidates(xfer, cache, warnings)
    kept: list[Path] = []
    for vid in raw_paths:
        try:
            vk = apq._canonical_media_path(str(vid.resolve()))
        except OSError:
            vk = apq._canonical_media_path(str(vid))
        sk = str(vid)
        if vk in rej_keys or sk in rej_keys:
            continue
        kept.append(vid)

    rows: list[dict[str, Any]] = []
    reject_ctr: Counter[str] = Counter()
    source_type_ctr: Counter[str] = Counter()
    path_token_ctr: Counter[str] = Counter()
    long_allowed_n = 0

    driving_hint_weak = 0
    ferry_hint_weak = 0
    unknown_n = 0

    for vid in kept:
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            continue
        if sz < apq.MIN_BYTES:
            continue
        probe = apq._ffprobe_json(vid)
        dur, has_v = apq._duration_and_has_video(probe)
        if probe is None or not has_v or dur is None:
            continue
        if float(dur) < float(apq.MIN_DURATION_SEC):
            continue

        pol = is_valid_nyc_long_source(vid, ffprobe_meta=probe)
        low = str(vid).replace("\\", "/").lower()
        toks = _path_tokens(low)
        for t in toks:
            path_token_ctr[str(t)] += 1

        la = bool(pol.get("long_allowed"))
        st = str(pol.get("source_type") or "unknown").strip().lower() or "unknown"
        rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
        if rrs:
            for rr in rrs:
                reject_ctr[rr] += 1
        elif not la:
            reject_ctr["(no_reject_reasons)"] += 1
        source_type_ctr[st] += 1

        flags = _flags_from_policy(pol, low)
        if st == "unknown":
            unknown_n += 1

        ev = list(pol.get("source_classification_evidence") or [])
        has_drive_path = any(
            str(x).startswith("driving_strong:") or str(x).startswith("driving_weak:") for x in toks
        )
        has_ferry_path = any(str(x).startswith("ferry:") for x in toks)
        if flags["lacks_driving_or_ferry_evidence"] and has_drive_path and "rejected_long_source_type_not_allowed" in rrs:
            driving_hint_weak += 1
        if flags["lacks_driving_or_ferry_evidence"] and has_ferry_path and "rejected_long_source_type_not_allowed" in rrs:
            ferry_hint_weak += 1

        if la:
            long_allowed_n += 1

        row = {
            "path": str(vid),
            "basename": vid.name,
            "path_lower": low,
            "duration_sec": float(dur),
            "width": pol.get("width"),
            "height": pol.get("height"),
            "aspect_ratio": pol.get("aspect_ratio"),
            "orientation": pol.get("orientation"),
            "source_type": pol.get("source_type"),
            "source_pool_origin": long_source_pool_origin(vid),
            "long_allowed": la,
            "reject_reasons": rrs,
            "reason_primary": pol.get("reason"),
            "path_token_evidence": toks,
            "classification_evidence": ev[:24],
            "gps_summary": {k: v for k, v in (extract_gps_summary(probe) or {}).items() if k != "creation_time"},
            "capture_time": str((extract_gps_summary(probe) or {}).get("creation_time") or ""),
            **flags,
        }
        row["suggested_move"] = _suggested_move(row)
        rows.append(row)

    move_ctr: Counter[str] = Counter(str(r.get("suggested_move") or "") for r in rows)

    detail_top30 = rows[:30]
    by_reason = dict(sorted(reject_ctr.items(), key=lambda kv: (-kv[1], kv[0])))
    by_source_type = dict(sorted(source_type_ctr.items(), key=lambda kv: (-kv[1], kv[0])))
    by_path_token = dict(sorted(path_token_ctr.items(), key=lambda kv: (-kv[1], kv[0])))

    lack_evidence_paths = [r["path"] for r in rows if r.get("lacks_driving_or_ferry_evidence")][:200]
    unknown_only_paths = [r["path"] for r in rows if r.get("is_unknown_only")][:200]
    walking_portrait_tl = [
        {
            "path": r["path"],
            "is_walking_or_handheld": r.get("is_walking_or_handheld"),
            "is_portrait": r.get("is_portrait"),
            "is_timelapse": r.get("is_timelapse"),
        }
        for r in rows
        if r.get("is_walking_or_handheld") or r.get("is_portrait") or r.get("is_timelapse")
    ][:200]

    doc: dict[str, Any] = {
        "report_version": "video169_long_candidate_reject_review_v1",
        "generated_at": _utc(),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "video169_raw_candidates_count": len(rows),
        "long_allowed_video169_candidates_count": long_allowed_n,
        "rejected_by_reason": by_reason,
        "source_type_distribution": by_source_type,
        "path_token_evidence_distribution": by_path_token,
        "unknown_video169_count": unknown_n,
        "driving_hint_weak_count": driving_hint_weak,
        "ferry_hint_weak_count": ferry_hint_weak,
        "suggested_move_distribution": dict(move_ctr),
        "lacks_driving_ferry_evidence_sample_paths": lack_evidence_paths,
        "unknown_only_sample_paths": unknown_only_paths,
        "walking_portrait_timelapse_sample": walking_portrait_tl,
        "detail_top_30": detail_top30,
        "warnings": warnings,
    }

    primary = cache / "review_reports" / REPORT_NAME
    report_path = primary
    try:
        primary.parent.mkdir(parents=True, exist_ok=True)
        primary.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        fb = Path.home() / "StateVerge" / "data" / "review_reports" / REPORT_NAME
        try:
            fb.parent.mkdir(parents=True, exist_ok=True)
            fb.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
            report_path = fb
            warnings.append(f"report_write_fallback:{exc!r}")
        except OSError as exc2:
            print(f"ERROR: cannot_write_report:{exc2!r}", file=sys.stderr)
            return 1

    top_rej = _top_reject_reasons(by_reason, limit=8)
    print(f"VIDEO169_RAW_CANDIDATES_COUNT={len(rows)}")
    print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={long_allowed_n}")
    print(f"TOP_REJECT_REASONS={top_rej}")
    print(f"UNKNOWN_VIDEO169_COUNT={unknown_n}")
    print(f"DRIVING_HINT_WEAK_COUNT={driving_hint_weak}")
    print(f"FERRY_HINT_WEAK_COUNT={ferry_hint_weak}")
    print(f"REPORT_PATH={report_path}")
    print("SAFE_TO_ENABLE_LONG_UPLOAD=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
