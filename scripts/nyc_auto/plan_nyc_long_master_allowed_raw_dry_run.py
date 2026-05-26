#!/usr/bin/env python3
"""Build NYC long master dry-run plan from v2.1 allowed raw candidates (mono driving|ferry, mild audio preset, preview recipe)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
_NYC = Path(__file__).resolve().parent
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402
import diagnose_long_master_supply as lms  # noqa: E402

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


from nyc_ferry_prefer_date_v1 import (  # noqa: E402
    append_prefer_manifest_fields,
    count_preferred_ferry_files,
    matches_prefer_date,
    prefer_sort_key,
    resolve_prefer_dates,
)
from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    is_allowed_long_video169_candidate_path,
    is_non_raw_long_source_path,
    is_valid_nyc_long_source,
    is_video169_long_source_path,
    long_source_pool_origin,
    refresh_nyc_long_policy_caches,
)

REPORT_CANDIDATES_NAME = "long_raw_source_candidates_v2_1.json"
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
VALID_AUDIO_PRESETS = frozenset({"preserve", "very_light"})


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _resolve_report_path(cache: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    p = cache / "review_reports" / REPORT_CANDIDATES_NAME
    if p.is_file():
        return p
    fb = Path.home() / "StateVerge" / "data" / "review_reports" / REPORT_CANDIDATES_NAME
    return fb


def _integrity_gate_row(row: dict[str, Any], *, counters: dict[str, int]) -> tuple[bool, str]:
    """Reject walking / portrait / non-raw / path tokens / stale rows vs filesystem."""
    path = Path(str(row.get("path") or ""))
    if not path.is_file():
        counters["missing_file"] += 1
        return False, "missing_file"
    ex = lms._path_excluded_for_master(path)  # noqa: SLF001
    if ex:
        counters["path_token_excluded"] += 1
        return False, ex
    if is_non_raw_long_source_path(path):
        counters["non_raw_or_output_tree"] += 1
        return False, "non_raw_or_output_tree"
    if not is_video169_long_source_path(path) or not is_allowed_long_video169_candidate_path(path):
        counters["not_video169_allowed_path"] += 1
        return False, "not_video169_allowed_path"
    if long_source_pool_origin(path) != "raw_video169_source_pool":
        counters["not_raw_video169_source_pool"] += 1
        return False, "not_raw_video169_source_pool"
    st = str(row.get("source_type") or "").strip().lower()
    if st == "walking":
        counters["walking"] += 1
        return False, "walking_source_type"
    if str(row.get("orientation") or "").strip().lower() != "landscape":
        counters["portrait_or_non_landscape"] += 1
        return False, "portrait_or_non_landscape"
    return True, "ok"


def _choose_mono_source_type(rows: list[dict[str, Any]], mode: str) -> tuple[str, list[dict[str, Any]]]:
    drv = [r for r in rows if str(r.get("source_type") or "").strip().lower() == "driving"]
    fer = [r for r in rows if str(r.get("source_type") or "").strip().lower() == "ferry"]
    if mode == "driving":
        return "driving", drv
    if mode == "ferry":
        return "ferry", fer
    td = sum(float(r.get("duration_sec") or 0.0) for r in drv)
    tf = sum(float(r.get("duration_sec") or 0.0) for r in fer)
    if td >= tf and drv:
        return "driving", drv
    if fer:
        return "ferry", fer
    if drv:
        return "driving", drv
    return "ferry", fer


def _greedy_pick_mono_ar(
    pool: list[dict[str, Any]],
    idx: Any,
    warnings: list[str],
    *,
    mono_st: str,
    min_total_sec: float,
    prefer_dates: list | None = None,
    may15_ferry_only: bool = False,
) -> tuple[list[dict[str, Any]], float, str]:
    """Greedy prefer-date-first (mtime desc), same source_type/orientation, AR ±0.04; dedupe via supply classify."""
    prefer = list(prefer_dates or [])
    keyed: list[tuple[float, dict[str, Any]]] = []
    for r in pool:
        p = Path(str(r.get("path") or ""))
        if not p.is_file():
            continue
        try:
            keyed.append((float(p.stat().st_mtime), r))
        except OSError:
            keyed.append((0.0, r))
    if prefer:
        keyed.sort(key=lambda x: prefer_sort_key(Path(str(x[1].get("path") or "")), prefer, mtime=x[0]))
    else:
        keyed.sort(key=lambda x: (-x[0], str(x[1].get("path") or "")))

    if may15_ferry_only and prefer and mono_st == "ferry":
        keyed_pref = [x for x in keyed if matches_prefer_date(Path(str(x[1].get("path") or "")), prefer, mtime=x[0])]
        keyed_other = [x for x in keyed if x not in keyed_pref]
    else:
        keyed_pref = list(keyed)
        keyed_other = []

    def _greedy_keyed(keyed_in: list[tuple[float, dict[str, Any]]]) -> tuple[list[dict[str, Any]], float]:
        picked_l: list[dict[str, Any]] = []
        total_l = 0.0
        ref_ar_f_l: float | None = None
        ref_orient_l: str | None = None
        for _mt, r in keyed_in:
            if total_l >= min_total_sec:
                break
            p = Path(str(r.get("path") or ""))
            row = lms._classify_candidate(p, idx, min_duration_sec=float(apq.MIN_DURATION_SEC))  # noqa: SLF001
            if row.get("stage") != "eligible":
                continue
            probe = apq._ffprobe_json(p)  # noqa: SLF001
            pol = is_valid_nyc_long_source(p, ffprobe_meta=probe)
            st = str(pol.get("source_type") or "").strip().lower()
            if st != mono_st:
                continue
            orient = str(pol.get("orientation") or "")
            try:
                ar = float(pol.get("aspect_ratio") or 0.0)
            except (TypeError, ValueError):
                continue
            if ref_ar_f_l is None:
                ref_ar_f_l = ar
                ref_orient_l = orient
            else:
                if orient != ref_orient_l:
                    continue
                if ref_ar_f_l is not None and abs(ar - float(ref_ar_f_l)) > 0.04:
                    continue
            dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
            if not has_v or dur is None:
                continue
            picked_l.append(
                {
                    "path": str(p),
                    "duration_sec": float(dur),
                    "mtime": _mt,
                    "aspect_ratio": ar,
                    "source_type": st,
                    "orientation": orient,
                    "width": int(pol.get("width") or 0),
                    "height": int(pol.get("height") or 0),
                    "prefer_date_match": bool(prefer and matches_prefer_date(p, prefer, mtime=_mt)),
                }
            )
            total_l += float(dur)
        return picked_l, total_l

    picked, total = _greedy_keyed(keyed_pref)
    if total < min_total_sec and keyed_other:
        if may15_ferry_only and prefer:
            warnings.append("insufficient_may15_ferry_footage")
        more, more_total = _greedy_keyed(keyed_other)
        seen = {str(x.get("path")) for x in picked}
        for ent in more:
            if str(ent.get("path")) in seen:
                continue
            picked.append(ent)
            total += float(ent.get("duration_sec") or 0.0)
            if total >= min_total_sec:
                break

    note = "ok" if total >= min_total_sec else "insufficient_duration_after_mono_dedupe_ar_filter"
    if may15_ferry_only and prefer and mono_st == "ferry" and total < min_total_sec:
        note = "insufficient_may15_ferry_footage"
    if picked and len({str(x.get("source_type") or "") for x in picked}) > 1:
        warnings.append("plan_invariant_broken_mixed_source_type")
    return picked, total, note


def _preview_plan(first_source: str, *, preview_sec: int, ready: Path, ts: str) -> dict[str, Any]:
    out = ready / "nyc_long_clips" / f"_human_preview_allowed_raw_{ts}.mp4"
    sec = max(180, min(int(preview_sec), 300))
    # Stream-copy head segment for quick human review (may fail on odd streams; reencode fallback documented).
    cmd_copy = (
        f"{FFMPEG} -hide_banner -nostdin -y -i {json.dumps(first_source)} "
        f"-t {sec} -map 0:v:0 -map 0:a? -c:v copy -c:a copy "
        f"{json.dumps(str(out))}"
    )
    cmd_safe = (
        f"{FFMPEG} -hide_banner -nostdin -y -i {json.dumps(first_source)} "
        f"-t {sec} -map 0:v:0 -map 0:a:0 -c:v libx264 -preset veryfast -crf 20 "
        f"-c:a aac -b:a 192k -movflags +faststart {json.dumps(str(out))}"
    )
    return {
        "duration_sec": sec,
        "intent": "human_review_first_segment_before_full_master_encode",
        "suggested_output_path": str(out),
        "ffmpeg_head_segment_stream_copy": cmd_copy,
        "ffmpeg_head_segment_reencode_fallback": cmd_safe,
        "note": "Run one command locally; if stream copy errors, use the reencode fallback.",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--from-report",
        type=Path,
        default=None,
        help=f"Path to {REPORT_CANDIDATES_NAME} (default: SV_CACHE review_reports, then home fallback).",
    )
    ap.add_argument("--min-total-sec", type=float, default=lms.MIN_MASTER_SEC)
    ap.add_argument(
        "--source-type",
        choices=("auto", "driving", "ferry"),
        default="auto",
        help="Mono pool: only driving, only ferry, or auto by total duration.",
    )
    ap.add_argument(
        "--audio-preset",
        choices=sorted(VALID_AUDIO_PRESETS),
        default="very_light",
        help="Post-master real-sound chain preset (preserve | very_light); concat encode still AAC per create_next_long_master.",
    )
    ap.add_argument(
        "--preview-duration-sec",
        type=int,
        default=270,
        help="Human preview length (clamped 180–300 seconds).",
    )
    ap.add_argument(
        "--out-manifest",
        type=Path,
        default=None,
        help="Write plan JSON here (default: publish_pack/nyc_long_uploads/master_dry_run_plan_allowed_raw_<ts>.json).",
    )
    ap.add_argument(
        "--prefer-date",
        default=None,
        help="Prefer ferry sources from ISO date (STATEVERGE_PREFER_SOURCE_DATE). Default 2026-05-15.",
    )
    ap.add_argument(
        "--may15-ferry-only",
        action="store_true",
        help="For 3h ferry plans: use only prefer-date clips until duration met, else warn and fall back.",
    )
    args = ap.parse_args()

    warnings: list[str] = []
    refresh_nyc_long_policy_caches()

    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)

    report_path = _resolve_report_path(cache, args.from_report)
    if not report_path.is_file():
        print(f"ERROR: report_not_found:{report_path}", file=sys.stderr)
        return 2
    try:
        doc = json.loads(report_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: report_read_failed:{exc!r}", file=sys.stderr)
        return 2

    raw_rows = list(doc.get("long_usable_raw_candidates") or [])
    if not raw_rows:
        print(
            "ERROR: long_usable_raw_candidates_empty "
            "(plan requires video169-allowed rows only; re-run diagnose_long_raw_source_candidates.py; "
            "no fallback to all_evaluated or unknown).",
            file=sys.stderr,
        )
        emergency = apq._load_long_emergency_state(xfer, warnings)
        active = apq._long_upload_emergency_active(emergency)
        lad = bool(emergency.get("LONG_AUTOPUBLISH_DISABLED", True))
        safe = bool(emergency.get("SAFE_TO_ENABLE_LONG_UPLOAD", False))
        if active:
            safe = False
        rcnts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
        v169 = int(rcnts.get("video169_raw_candidates_count") or rcnts.get("raw_source_candidates_count") or 0)
        non169 = int(rcnts.get("non_video169_excluded_count") or 0)
        allowed169 = int(rcnts.get("long_allowed_video169_candidates_count") or rcnts.get("long_allowed_raw_candidates_count") or 0)
        rawc = int(rcnts.get("raw_source_candidates_count") or v169)
        long_allowed = int(rcnts.get("long_allowed_raw_candidates_count") or allowed169)
        print(f"VIDEO169_RAW_CANDIDATES_COUNT={v169}")
        print(f"NON_VIDEO169_EXCLUDED_COUNT={non169}")
        print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={allowed169}")
        print(f"RAW_SOURCE_CANDIDATES_COUNT={rawc}")
        print(f"LONG_ALLOWED_RAW_CANDIDATES_COUNT={long_allowed}")
        print(f"LONG_AUTOPUBLISH_DISABLED={str(lad).lower()}")
        print(f"SAFE_TO_ENABLE_LONG_UPLOAD={str(safe).lower()}")
        print(f"REPORT_PATH={report_path}")
        print("PLAN_PATH=")
        return 3

    counters: dict[str, int] = {
        "missing_file": 0,
        "path_token_excluded": 0,
        "non_raw_or_output_tree": 0,
        "not_video169_allowed_path": 0,
        "not_raw_video169_source_pool": 0,
        "walking": 0,
        "portrait_or_non_landscape": 0,
        "integrity_ok": 0,
    }
    gated: list[dict[str, Any]] = []
    for row in raw_rows:
        ok, _why = _integrity_gate_row(row, counters=counters)
        if ok:
            counters["integrity_ok"] += 1
            gated.append(row)

    if not gated:
        print("ERROR: no_rows_passed_integrity_gates", file=sys.stderr)
        return 4

    prefer_dates = resolve_prefer_dates(args.prefer_date)
    mono_st, mono_pool = _choose_mono_source_type(gated, str(args.source_type))
    if not mono_pool:
        print("ERROR: no_candidates_after_source_type_lock", file=sys.stderr)
        return 4

    mixed_other = len(gated) - len(mono_pool)
    ferry_paths = [Path(str(r.get("path") or "")) for r in mono_pool if r.get("path")]
    ferry_total, ferry_pref = count_preferred_ferry_files(ferry_paths, prefer_dates)
    may15_only = bool(args.may15_ferry_only) or (
        float(args.min_total_sec) >= 10800.0 and mono_st == "ferry"
    )

    idx, _ledger = lms._build_dedupe_index(warnings)  # noqa: SLF001
    picked, planned_total, pick_note = _greedy_pick_mono_ar(
        mono_pool,
        idx,
        warnings,
        mono_st=mono_st,
        min_total_sec=float(args.min_total_sec),
        prefer_dates=prefer_dates,
        may15_ferry_only=may15_only,
    )

    ts = _utc_compact()
    out_master = ready / "nyc_long_clips" / f"nyc_long_master_{ts}.mp4"
    default_manifest = xfer / "publish_pack" / "nyc_long_uploads" / f"master_dry_run_plan_allowed_raw_{ts}.json"
    manifest_path = args.out_manifest or default_manifest

    audio_preset = str(args.audio_preset).strip().lower()
    if audio_preset not in VALID_AUDIO_PRESETS:
        audio_preset = "very_light"

    preview = (
        _preview_plan(str(picked[0]["path"]), preview_sec=int(args.preview_duration_sec), ready=ready, ts=ts)
        if picked
        else {}
    )

    mixed_non = 0
    for ent in picked:
        pp = Path(str(ent.get("path") or ""))
        if not pp.is_file() or not is_allowed_long_video169_candidate_path(pp):
            mixed_non += 1
        elif long_source_pool_origin(pp) != "raw_video169_source_pool":
            mixed_non += 1

    plan: dict[str, Any] = {
        "plan_kind": "nyc_long_master_dry_run_allowed_raw_v2_1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": True,
        "source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "master_source_policy": "video169_only",
        "all_sources_video169": mixed_non == 0,
        "mixed_non_video169_sources_count": int(mixed_non),
        "upstream_report": str(report_path),
        "min_total_sec": float(args.min_total_sec),
        "source_type_mode_requested": str(args.source_type),
        "source_type_mono_locked": mono_st,
        "mixed_other_source_type_dropped_count": int(mixed_other),
        "real_sound_cleanup_preset": audio_preset,
        "real_sound_cleanup_preset_allowed": sorted(VALID_AUDIO_PRESETS),
        "notes": [
            "Master concat encode (create_next_long_master) uses single-pass H.264 + AAC; "
            "apply real_sound_cleanup_gate after encode with preset preserve|very_light only.",
            "This plan does not run ffmpeg or lift long autopublish emergency.",
        ],
        "planned_output_path": str(out_master),
        "planned_total_sec": round(planned_total, 3),
        "pick_note": pick_note,
        "sources": picked,
        "human_preview": preview,
        "integrity": {
            "confirms_single_source_type_only": bool(picked and len({str(x.get("source_type") or "") for x in picked}) <= 1),
            "confirms_no_walking_portrait_output_artifact_paths": bool(
                counters["walking"] == 0
                and counters["portrait_or_non_landscape"] == 0
                and counters["non_raw_or_output_tree"] == 0
            ),
            "gate_counters": counters,
            "upstream_usable_row_count": len(raw_rows),
            "post_integrity_pool_count": len(gated),
            "mono_pool_count": len(mono_pool),
        },
        "warnings": warnings,
        "prefer_source_dates": [d.isoformat() for d in prefer_dates],
        "ferry_prefer_date_match_count": ferry_pref,
        "ferry_mono_pool_count": ferry_total,
        "may15_ferry_only_attempted": may15_only,
    }
    append_prefer_manifest_fields(
        plan,
        prefer_dates=prefer_dates,
        may15_only_attempted=may15_only,
        may15_only_satisfied=may15_only and pick_note == "ok",
        pick_note=pick_note,
        warnings=warnings,
    )
    plan["status"] = "dry_run_ok" if pick_note == "ok" and picked else "blocked"

    try:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        fb = Path.home() / "StateVerge" / "data" / "publish_pack" / "nyc_long_uploads" / manifest_path.name
        try:
            fb.parent.mkdir(parents=True, exist_ok=True)
            fb.write_text(json.dumps(plan, indent=2, ensure_ascii=False), encoding="utf-8")
            manifest_path = fb
            warnings.append(f"manifest_write_fallback:{exc!r}")
        except OSError as exc2:
            print(f"ERROR: manifest_write_failed:{exc2!r}", file=sys.stderr)
            return 5

    emergency = apq._load_long_emergency_state(xfer, warnings)
    active = apq._long_upload_emergency_active(emergency)
    lad = bool(emergency.get("LONG_AUTOPUBLISH_DISABLED", True))
    safe = bool(emergency.get("SAFE_TO_ENABLE_LONG_UPLOAD", False))
    if active:
        safe = False

    rcnts = doc.get("counts") if isinstance(doc.get("counts"), dict) else {}
    v169 = int(rcnts.get("video169_raw_candidates_count") or rcnts.get("raw_source_candidates_count") or 0)
    non169 = int(rcnts.get("non_video169_excluded_count") or 0)
    allowed169 = int(rcnts.get("long_allowed_video169_candidates_count") or rcnts.get("long_allowed_raw_candidates_count") or 0)
    rawc = int(rcnts.get("raw_source_candidates_count") or v169)
    long_allowed = int(rcnts.get("long_allowed_raw_candidates_count") or allowed169)

    print(json.dumps({"ok": plan["status"] == "dry_run_ok", "manifest": str(manifest_path)}, indent=2, ensure_ascii=False))
    print(f"VIDEO169_RAW_CANDIDATES_COUNT={v169}")
    print(f"NON_VIDEO169_EXCLUDED_COUNT={non169}")
    print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={allowed169}")
    print(f"RAW_SOURCE_CANDIDATES_COUNT={rawc}")
    print(f"LONG_ALLOWED_RAW_CANDIDATES_COUNT={long_allowed}")
    print(f"LONG_AUTOPUBLISH_DISABLED={str(lad).lower()}")
    print(f"SAFE_TO_ENABLE_LONG_UPLOAD={str(safe).lower()}")
    print(f"REPORT_PATH={report_path}")
    print(f"PLAN_PATH={manifest_path}")
    print(f"FERRY_MAY15_FILES_FOUND={ferry_pref}")
    ready_flag = ferry_pref > 0 and plan["status"] == "dry_run_ok"
    print(f"FERRY_MAY15_PRIORITY_READY={str(ready_flag).lower()}")
    if picked:
        top = picked[0]
        print(f"TOP_CANDIDATE={top.get('path')} prefer_match={top.get('prefer_date_match')}")
    return 0 if plan["status"] == "dry_run_ok" else 6


if __name__ == "__main__":
    raise SystemExit(main())
