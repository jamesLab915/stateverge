#!/usr/bin/env python3
"""Emit full rows for long-queue rejects (source type), write review JSON + ensure manual label manifest template."""
from __future__ import annotations

import json
import subprocess
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
    from utils.storage_paths import get_sv_cache, get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    extract_gps_summary,
    is_valid_nyc_long_source,
    long_source_pool_origin,
    lookup_manual_long_label,
    lookup_media_index_row,
    refresh_nyc_long_policy_caches,
)

MANUAL_VERSION = "nyc_long_manual_source_labels_v1"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _heuristic_bucket(pol: dict[str, Any], low_path: str) -> str:
    rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
    if "walking_not_allowed_for_nyc_long_channel" in rrs:
        return "walking_or_handheld_signal"
    if "rejected_fixed_street_view_needs_manual_label_or_vehicle_evidence" in rrs:
        return "fixed_camera_ambiguous"
    st = str(pol.get("source_type") or "")
    if st == "ferry":
        return "ferry"
    if st == "walking":
        return "walking"
    if st == "driving":
        return "driving"
    if "rejected_shorts_path" in rrs:
        return "shorts_pool_signal"
    gps = pol.get("_gps_summary") or {}
    if gps.get("gps_in_nyc_bbox_guess"):
        return "landscape_unknown_nyc_gps_only"
    return "other_unknown"


def _why_unknown(pol: dict[str, Any], gps: dict[str, Any]) -> str:
    ev = pol.get("source_classification_evidence") or []
    if ev:
        return "no_auto_long_type_despite:" + ";".join(str(x) for x in ev[:10])
    if str(pol.get("media_index_origin") or ""):
        return "media_index_row_present_but_no_driving_or_ferry_proof_fields"
    if gps.get("gps_in_nyc_bbox_guess"):
        return "nyc_gps_only_insufficient_for_vehicle_class_without_strong_tokens_or_index"
    return "no_strong_tokens_no_media_index_driving_proof_no_manual_label"


def _ensure_manual_manifest(primary: Path, fallback: Path, warnings: list[str]) -> Path:
    template = {
        "version": MANUAL_VERSION,
        "updated_at": _utc(),
        "labels": [],
        "notes": "Add entries with allowed_for_long true and source_type driving|ferry only after human review.",
    }
    for target in (primary, fallback):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.append(f"manual_manifest_mkdir_failed:{target}:{exc!r}")
            continue
        if target.is_file():
            return target
        try:
            target.write_text(json.dumps(template, indent=2, ensure_ascii=False), encoding="utf-8")
            return target
        except OSError as exc:
            warnings.append(f"manual_manifest_write_failed:{target}:{exc!r}")
    return primary


def main() -> int:
    warnings: list[str] = []
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)

    primary_manifest = xfer / "media_index" / "nyc_long_manual_source_labels.json"
    fallback_manifest = Path.home() / "StateVerge" / "data" / "media_index" / "nyc_long_manual_source_labels.json"
    manifest_written = _ensure_manual_manifest(primary_manifest, fallback_manifest, warnings)
    manifest_path_out = str(manifest_written) if manifest_written.is_file() else str(primary_manifest)

    review_dir = cache / "review_reports"
    try:
        review_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        review_dir = Path.home() / "StateVerge" / "data" / "review_reports"
        review_dir.mkdir(parents=True, exist_ok=True)
        warnings.append(f"review_dir_fallback:{exc!r}")
    review_path = review_dir / "long_source_classification_review.json"

    raw_paths = apq.collect_long_candidates(ready, warnings)
    rej_entries = apq._load_long_rejected_outputs(xfer, warnings)
    rej_keys = apq._long_rejected_path_keys(rej_entries)
    raw_kept: list[Path] = []
    for vid in raw_paths:
        try:
            vk = apq._canonical_media_path(str(vid.resolve()))
        except OSError:
            vk = apq._canonical_media_path(str(vid))
        sk = str(vid)
        if vk in rej_keys or sk in rej_keys:
            continue
        raw_kept.append(vid)
    raw_paths = raw_kept

    unknown_rows: list[dict[str, Any]] = []
    for vid in raw_paths:
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            continue
        if sz < apq.MIN_BYTES:
            continue
        probe = apq._ffprobe_json(vid)
        dur, has_v = apq._duration_and_has_video(probe)
        if not probe or not has_v or dur is None:
            continue
        if dur < float(apq.MIN_DURATION_SEC):
            continue
        pol = is_valid_nyc_long_source(vid, ffprobe_meta=probe)
        rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
        if "rejected_long_source_type_not_allowed" not in rrs:
            continue
        if str(pol.get("source_type") or "") != "unknown":
            continue

        try:
            mtime = datetime.fromtimestamp(vid.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime = ""
        gps = extract_gps_summary(probe)
        mi = lookup_media_index_row(vid)
        man = lookup_manual_long_label(vid)
        low = str(vid).replace("\\", "/").lower()
        hints = [t for t in ("inbox", "iphone", "dash", "drive", "ferry", "tripod", "gopro", "img_", "ta", "nyc") if t in low]

        row: dict[str, Any] = {
            "path": str(vid),
            "basename": vid.name,
            "source_pool_origin": long_source_pool_origin(vid),
            "width": pol.get("width"),
            "height": pol.get("height"),
            "aspect_ratio": pol.get("aspect_ratio"),
            "aspect_policy": pol.get("aspect_policy"),
            "duration_sec": float(dur),
            "mtime_utc": mtime,
            "creation_time_tag": gps.get("creation_time"),
            "gps": {k: v for k, v in gps.items() if k != "creation_time"},
            "current_source_type": pol.get("source_type"),
            "inferred_source_type": pol.get("source_type"),
            "reject_reasons": rrs,
            "reason_primary": pol.get("reason"),
            "why_unknown": _why_unknown(pol, gps),
            "filename_path_hints": hints,
            "heuristic_review_bucket": _heuristic_bucket({**pol, "_gps_summary": gps}, low),
            "media_index_origin": pol.get("media_index_origin"),
            "media_index_fields_used": sorted([k for k, v in (mi or {}).items() if v not in (None, "", [], {})])[:60]
            if mi
            else [],
            "media_index_row_sample": {k: mi.get(k) for k in list(mi.keys())[:25]} if mi else {},
            "manual_label_hit": pol.get("manual_label_hit"),
            "manual_label_row": man,
            "source_classification_evidence": pol.get("source_classification_evidence"),
        }
        unknown_rows.append(row)

    doc = {
        "generated_at": _utc(),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "unknown_long_candidates_count": len(unknown_rows),
        "candidates": unknown_rows,
        "warnings": warnings,
        "manual_label_manifest_primary": str(primary_manifest),
        "manual_label_manifest_fallback": str(fallback_manifest),
        "review_report_path": str(review_path),
    }
    try:
        review_path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: cannot_write_review:{exc!r}", file=sys.stderr)
        return 1

    refresh_nyc_long_policy_caches()

    dr = subprocess.run(
        [sys.executable or "python3", str(_NYC / "auto_publish_queue.py"), "--dry-run", "--max-count", "1"],
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    tail_lines = (dr.stdout or "").splitlines()
    pick: dict[str, str] = {}
    for line in tail_lines:
        if "=" in line and not line.strip().startswith("{"):
            k, _, v = line.partition("=")
            if k in (
                "LONG_AVAILABLE_NEW_CANDIDATES",
                "LONG_DRY_RUN_STATUS",
                "SAFE_TO_ENABLE_LONG_UPLOAD",
                "LONG_AUTOPUBLISH_DISABLED",
                "RAW_SOURCE_CANDIDATES_COUNT",
                "OUTPUT_ARTIFACTS_EXCLUDED_COUNT",
                "UNKNOWN_LONG_CANDIDATES_COUNT",
            ):
                pick[k] = v

    print(f"RAW_SOURCE_CANDIDATES_COUNT={pick.get('RAW_SOURCE_CANDIDATES_COUNT', '')}")
    print(f"OUTPUT_ARTIFACTS_EXCLUDED_COUNT={pick.get('OUTPUT_ARTIFACTS_EXCLUDED_COUNT', '')}")
    print(f"UNKNOWN_LONG_CANDIDATES_COUNT={pick.get('UNKNOWN_LONG_CANDIDATES_COUNT', '') or len(unknown_rows)}")
    print(f"LONG_AVAILABLE_NEW_CANDIDATES={pick.get('LONG_AVAILABLE_NEW_CANDIDATES', '')}")
    print(f"LONG_DRY_RUN_STATUS={pick.get('LONG_DRY_RUN_STATUS', '')}")
    print(f"SAFE_TO_ENABLE_LONG_UPLOAD={pick.get('SAFE_TO_ENABLE_LONG_UPLOAD', '')}")
    print(f"LONG_AUTOPUBLISH_DISABLED={pick.get('LONG_AUTOPUBLISH_DISABLED', '')}")
    print(f"UNKNOWN_LONG_CANDIDATES_REPORT={review_path}")
    print(f"MANUAL_LABEL_MANIFEST_PATH={manifest_path_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
