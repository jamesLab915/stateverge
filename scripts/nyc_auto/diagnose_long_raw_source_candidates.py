#!/usr/bin/env python3
"""Scan Long raw source pools only; emit v2.1 candidate report (no upload, no master, no emergency lift)."""
from __future__ import annotations

import json
import sys
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
    discover_long_video169_scan_directories,
    extract_gps_summary,
    is_valid_nyc_long_source,
    long_source_pool_origin,
    refresh_nyc_long_policy_caches,
)

REPORT_NAME = "long_raw_source_candidates_v2_1.json"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return ""


def _is_long_usable_raw_v2_1(row: dict[str, Any]) -> bool:
    """Explicit gates per Long Source Pool v2.1 (video169 raw pool + policy pass)."""
    if str(row.get("source_pool_origin") or "") != "raw_video169_source_pool":
        return False
    if not row.get("long_allowed"):
        return False
    st = str(row.get("source_type") or "").strip().lower()
    if st not in ("driving", "ferry"):
        return False
    if str(row.get("orientation") or "") != "landscape":
        return False
    try:
        ar = float(row.get("aspect_ratio") or 0.0)
    except (TypeError, ValueError):
        return False
    if not (1.70 <= ar <= 1.90):
        return False
    try:
        w = int(row.get("width") or 0)
        h = int(row.get("height") or 0)
    except (TypeError, ValueError):
        return False
    if w < 1280 or h < 720:
        return False
    return True


def main() -> int:
    warnings: list[str] = []
    refresh_nyc_long_policy_caches()

    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)

    emergency = apq._load_long_emergency_state(xfer, warnings)
    active = apq._long_upload_emergency_active(emergency)
    long_autopublish_disabled = bool(emergency.get("LONG_AUTOPUBLISH_DISABLED", True))
    safe_to_enable = bool(emergency.get("SAFE_TO_ENABLE_LONG_UPLOAD", False))
    if active:
        safe_to_enable = False

    roots = discover_long_video169_scan_directories(xfer, cache, warnings)

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
            warnings.append(f"skipped_long_rejected_outputs_json:{vid.name}")
            continue
        kept.append(vid)

    skipped_small = 0
    skipped_probe = 0
    skipped_duration = 0
    candidates: list[dict[str, Any]] = []

    for vid in kept:
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            skipped_probe += 1
            warnings.append(f"skipped_probe/stat_failed:{vid.name}")
            continue
        if sz < apq.MIN_BYTES:
            skipped_small += 1
            continue
        probe = apq._ffprobe_json(vid)
        dur, has_v = apq._duration_and_has_video(probe)
        if probe is None or not has_v or dur is None:
            skipped_probe += 1
            warnings.append(f"skipped_probe/invalid_video:{vid.name}")
            continue
        if float(dur) < float(apq.MIN_DURATION_SEC):
            skipped_duration += 1
            continue

        pol = is_valid_nyc_long_source(vid, ffprobe_meta=probe)
        gps = extract_gps_summary(probe)
        origin = long_source_pool_origin(vid)
        try:
            ar = float(pol.get("aspect_ratio") or 0.0)
        except (TypeError, ValueError):
            ar = 0.0
        row: dict[str, Any] = {
            "path": str(vid),
            "basename": vid.name,
            "width": pol.get("width"),
            "height": pol.get("height"),
            "aspect_ratio": ar,
            "duration_sec": float(dur),
            "orientation": pol.get("orientation"),
            "source_type": pol.get("source_type"),
            "source_pool_origin": origin,
            "long_allowed": bool(pol.get("long_allowed")),
            "reject_reasons": list(pol.get("reject_reasons") or []),
            "gps": {k: v for k, v in gps.items() if k != "creation_time"},
            "capture_time": str(gps.get("creation_time") or ""),
            "mtime_utc": _mtime_iso(vid),
            "classification_evidence": list(pol.get("source_classification_evidence") or []),
            "long_source_policy_version": pol.get("long_source_policy_version"),
            "aspect_policy": pol.get("aspect_policy"),
            "warnings": list(pol.get("warnings") or []),
        }
        candidates.append(row)

    long_usable = [r for r in candidates if _is_long_usable_raw_v2_1(r)]
    driving_n = sum(1 for r in long_usable if str(r.get("source_type") or "").lower() == "driving")
    ferry_n = sum(1 for r in long_usable if str(r.get("source_type") or "").lower() == "ferry")
    rejected_n = len(candidates) - len(long_usable)

    artifact_excluded = apq.count_long_output_artifact_pool_videos(xfer, cache, warnings)
    non_video169_excluded = apq.count_non_video169_inbox_long_videos_excluded(xfer, cache, warnings)

    doc: dict[str, Any] = {
        "report_version": "long_raw_source_candidates_v2_1",
        "generated_at": _utc(),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "roots_scanned": [str(r) for r in roots],
        "report_path_resolved": "",
        "counts": {
            "video169_raw_candidates_count": len(candidates),
            "non_video169_excluded_count": int(non_video169_excluded),
            "long_allowed_video169_candidates_count": len(long_usable),
            "raw_source_candidates_count": len(candidates),
            "long_allowed_raw_candidates_count": len(long_usable),
            "driving_raw_candidates_count": driving_n,
            "ferry_raw_candidates_count": ferry_n,
            "rejected_raw_candidates_count": rejected_n,
            "output_artifacts_excluded_count": int(artifact_excluded),
            "skipped_small_bytes": skipped_small,
            "skipped_probe_failed": skipped_probe,
            "skipped_below_min_duration": skipped_duration,
        },
        "long_usable_raw_candidates": long_usable,
        "all_evaluated_raw_candidates": candidates,
        "warnings": list(warnings),
    }

    primary_review = cache / "review_reports"
    report_path = primary_review / REPORT_NAME
    doc["report_path_resolved"] = str(report_path)
    payload = json.dumps(doc, indent=2, ensure_ascii=False)
    try:
        primary_review.mkdir(parents=True, exist_ok=True)
        report_path.write_text(payload, encoding="utf-8")
    except OSError as exc:
        fallback_review = Path.home() / "StateVerge" / "data" / "review_reports"
        try:
            fallback_review.mkdir(parents=True, exist_ok=True)
        except OSError as exc2:
            print(f"ERROR: cannot_create_review_dir:{exc2!r}", file=sys.stderr)
            return 1
        report_path = fallback_review / REPORT_NAME
        warnings.append(f"report_write_fallback_from_primary:{exc!r}")
        doc["report_path_resolved"] = str(report_path)
        doc["warnings"] = list(warnings)
        try:
            report_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError as exc3:
            print(f"ERROR: cannot_write_report:{exc3!r}", file=sys.stderr)
            return 1

    lad = str(long_autopublish_disabled).lower()
    safe = str(safe_to_enable).lower()

    print(f"VIDEO169_RAW_CANDIDATES_COUNT={len(candidates)}")
    print(f"NON_VIDEO169_EXCLUDED_COUNT={non_video169_excluded}")
    print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={len(long_usable)}")
    print(f"RAW_SOURCE_CANDIDATES_COUNT={len(candidates)}")
    print(f"LONG_ALLOWED_RAW_CANDIDATES_COUNT={len(long_usable)}")
    print(f"DRIVING_RAW_CANDIDATES_COUNT={driving_n}")
    print(f"FERRY_RAW_CANDIDATES_COUNT={ferry_n}")
    print(f"REJECTED_RAW_CANDIDATES_COUNT={rejected_n}")
    print(f"OUTPUT_ARTIFACTS_EXCLUDED_COUNT={artifact_excluded}")
    print(f"REPORT_PATH={report_path}")
    print(f"LONG_AUTOPUBLISH_DISABLED={lad}")
    print(f"SAFE_TO_ENABLE_LONG_UPLOAD={safe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
