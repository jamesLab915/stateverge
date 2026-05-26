#!/usr/bin/env python3
"""Create video169 manual-sort folders and CSV from reject review JSON (no moves, no labels, no upload)."""
from __future__ import annotations

import csv
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
    extract_gps_summary,
    is_valid_nyc_long_source,
    refresh_nyc_long_policy_caches,
)

import diagnose_video169_long_candidate_reject_review as v169rev  # noqa: E402

REVIEW_JSON = "video169_long_candidate_reject_review.json"
SORT_CSV = "video169_manual_sort_sheet.csv"


def _mkdir_sort_targets() -> list[Path]:
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    rel = ("driving", "ferry", "quarantine_review")
    out: list[Path] = []
    base_t = xfer / "00_INBOX" / "iphone" / "video169"
    base_c = cache / "inbox" / "video169"
    for sub in rel:
        p = base_t / sub
        p.mkdir(parents=True, exist_ok=True)
        out.append(p)
    for sub in rel:
        p = base_c / sub
        p.mkdir(parents=True, exist_ok=True)
        out.append(p)
    return out


def _mtime_utc(p: Path) -> str:
    try:
        ts = float(p.stat().st_mtime)
    except OSError:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _suggested_action(suggested_move: str) -> str:
    return (
        "Preview clip; rename or move under video169/driving|ferry|quarantine_review on the same volume; "
        f"default_hint={suggested_move}"
    )


def _row_for_path(
    vid: Path,
    *,
    source_path: str,
    detail_by_path: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    d: dict[str, Any] | None = None
    if source_path in detail_by_path:
        d = detail_by_path[source_path]
    else:
        try:
            rk = str(vid.resolve()) if vid.exists() else ""
            if rk and rk in detail_by_path:
                d = detail_by_path[rk]
        except OSError:
            pass
    if d is not None:
        pol = {
            "source_type": d.get("source_type"),
            "reject_reasons": d.get("reject_reasons"),
            "width": d.get("width"),
            "height": d.get("height"),
            "aspect_ratio": d.get("aspect_ratio"),
            "long_allowed": d.get("long_allowed"),
            "orientation": d.get("orientation"),
        }
        probe = None
        dur = d.get("duration_sec")
        gps = d.get("gps_summary") or {}
        low = str(d.get("path_lower") or str(vid).replace("\\", "/").lower())
        toks = d.get("path_token_evidence") or []
        flags = {k: d.get(k) for k in ("is_unknown_only", "is_walking_or_handheld", "is_portrait", "is_timelapse")}
        flags["lacks_driving_or_ferry_evidence"] = d.get("lacks_driving_or_ferry_evidence")
        sm = str(d.get("suggested_move") or "")
    else:
        low = str(vid).replace("\\", "/").lower()
        toks = v169rev._path_tokens(low)
        probe = apq._ffprobe_json(vid) if vid.is_file() else None
        dur, _has_v = apq._duration_and_has_video(probe) if probe else (None, False)
        pol = is_valid_nyc_long_source(vid, ffprobe_meta=probe) if probe else {}
        gps = {k: v for k, v in (extract_gps_summary(probe) or {}).items() if k != "creation_time"} if probe else {}
        flags = v169rev._flags_from_policy(pol, low)
        row_tmp: dict[str, Any] = {
            "path_lower": low,
            "long_allowed": bool(pol.get("long_allowed")),
            "path_token_evidence": toks,
            **flags,
        }
        sm = v169rev._suggested_move(row_tmp)
    rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
    return {
        "source_path": source_path,
        "basename": vid.name,
        "duration_sec": dur if dur is not None else "",
        "width": pol.get("width", ""),
        "height": pol.get("height", ""),
        "aspect_ratio": pol.get("aspect_ratio", ""),
        "gps_summary": gps,
        "mtime_utc": _mtime_utc(vid) if vid.is_file() else "",
        "current_source_type": str(pol.get("source_type") or "unknown"),
        "reject_reasons": rrs,
        "suggested_move": sm,
    }


def main() -> int:
    refresh_nyc_long_policy_caches()
    cache = get_sv_cache(verbose=False)
    review_path = cache / "review_reports" / REVIEW_JSON
    if not review_path.is_file():
        print(f"ERROR:missing_review_json:{review_path}", file=sys.stderr)
        return 1

    _mkdir_sort_targets()

    doc = json.loads(review_path.read_text(encoding="utf-8"))
    expected = int(doc.get("video169_raw_candidates_count") or 0)
    long_allowed = int(doc.get("long_allowed_video169_candidates_count") or 0)

    paths_raw = doc.get("unknown_only_sample_paths") or doc.get("lacks_driving_ferry_evidence_sample_paths") or []
    paths = [Path(str(p)) for p in paths_raw]
    if len(paths) != expected:
        print(
            f"ERROR:path_count_mismatch:expected={expected} got={len(paths)} (check review JSON keys)",
            file=sys.stderr,
        )
        return 1

    detail_by_path: dict[str, dict[str, Any]] = {}
    for d in doc.get("detail_top_30") or []:
        p = str(d.get("path") or "")
        if not p:
            continue
        detail_by_path[p] = d
        try:
            detail_by_path[str(Path(p).resolve())] = d
        except OSError:
            pass

    out_csv = cache / "review_reports" / SORT_CSV
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = [
        "index",
        "source_path",
        "basename",
        "duration_sec",
        "width",
        "height",
        "aspect_ratio",
        "gps_summary",
        "mtime_utc",
        "current_source_type",
        "reject_reasons",
        "suggested_action",
        "manual_target_folder",
        "notes",
    ]

    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for i, vid in enumerate(paths, start=1):
            sp = str(paths_raw[i - 1])
            r = _row_for_path(vid, source_path=sp, detail_by_path=detail_by_path)
            sm = str(r.pop("suggested_move", "") or "quarantine_review")
            w.writerow(
                {
                    "index": i,
                    "source_path": r["source_path"],
                    "basename": r["basename"],
                    "duration_sec": r["duration_sec"],
                    "width": r["width"],
                    "height": r["height"],
                    "aspect_ratio": r["aspect_ratio"],
                    "gps_summary": json.dumps(r["gps_summary"], ensure_ascii=False) if r["gps_summary"] else "",
                    "mtime_utc": r["mtime_utc"],
                    "current_source_type": r["current_source_type"],
                    "reject_reasons": ";".join(r["reject_reasons"]),
                    "suggested_action": _suggested_action(sm),
                    "manual_target_folder": "",
                    "notes": "",
                }
            )

    print(f"SORT_SHEET_PATH={out_csv}")
    print(f"VIDEO169_RAW_CANDIDATES_COUNT={expected}")
    print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={long_allowed}")
    print("SAFE_TO_ENABLE_LONG_UPLOAD=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
