#!/usr/bin/env python3
"""Long Master Supply / Probe Diagnose v1 — mirrors auto_publish_queue probe + dedupe; plans inbox masters."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from media_quick_hash import content_key_v2, normalize_basename, triple_chunk_sha256  # noqa: E402

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


from nyc_ferry_prefer_date_v1 import (  # noqa: E402
    is_ferry_video169_path,
    matches_prefer_date,
    prefer_sort_key,
)
from nyc_long_source_policy import (  # noqa: E402
    LONG_SOURCE_POLICY_VERSION,
    is_allowed_long_video169_candidate_path,
    is_valid_nyc_long_source,
    long_source_pool_origin,
)

CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "long_master_supply_diagnose.json"
OUT_MD = CONTROL_LOGS / "long_master_supply_diagnose.md"

MIN_MASTER_SEC = 3600.0
EXCLUDE_PATH_SUBSTR = (
    "shorts",
    "youtube_shorts",
    "shorts_clips",
    "vertical",
    "portrait",
    "reels",
    "tiktok",
    "timelapse",
    "/test/",
    "_test_",
    "test_clip",
    "/verify/",
    "_verify_",
    "/debug/",
    "_debug_",
    "image_motion",
    "mixed_video_image",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_excluded_for_master(p: Path) -> str | None:
    low = str(p).replace("\\", "/").lower()
    for frag in EXCLUDE_PATH_SUBSTR:
        if frag in low:
            return f"excluded_path_token:{frag}"
    return None


def _usable_for_master(path: Path, probe: dict[str, Any] | None) -> tuple[bool, str]:
    ex = _path_excluded_for_master(path)
    if ex:
        return False, ex
    if not is_allowed_long_video169_candidate_path(path):
        return False, "not_video169_long_raw_path"
    if long_source_pool_origin(path) != "raw_video169_source_pool":
        return False, "not_raw_video169_source_pool"
    pol = is_valid_nyc_long_source(path, ffprobe_meta=probe)
    if pol.get("long_allowed"):
        return True, "long_policy_ok"
    rrs = {str(x) for x in (pol.get("reject_reasons") or [])}
    hard = {
        "walking_not_allowed_for_nyc_long_channel",
        "rejected_shorts_path",
        "rejected_vertical_video",
        "timelapse_whole_clip_not_allowed_for_long_channel",
        "image_not_allowed_for_long_channel",
        "rejected_long_source_type_not_allowed",
        "rejected_insufficient_resolution_for_long_channel",
    }
    if rrs & hard:
        return False, sorted(rrs & hard)[0]
    return False, str(pol.get("reason") or "not_usable_for_master")


def _scan_inbox_videos(xfer: Path, cache: Path, warnings: list[str]) -> list[Path]:
    """NYC Long master supply: **only** files under discovered ``**/video169/`` (same as upload queue raw scan)."""
    return apq.collect_long_raw_source_candidates(xfer, cache, warnings)


def _build_dedupe_index(warnings: list[str]) -> tuple[Any, Path]:
    pack_parent, ledger_path, _lock, _fb = apq._resolve_pack_ledger_lock(warnings)  # noqa: SLF001
    xfer = get_sv_transfer(verbose=False)
    hist_roots = [
        xfer / "publish_pack" / "nyc_long_uploads",
        apq.HOME_LONG_UPLOADS,
    ]
    primary_ledger = xfer / "publish_pack" / "nyc_long_uploads" / apq.LEDGER_NAME
    home_ledger = apq.HOME_LONG_UPLOADS / apq.LEDGER_NAME
    idx = apq.DedupeIndexV2()
    _seen: set[str] = set()
    for lp in (primary_ledger, home_ledger, ledger_path):
        key = str(lp)
        if key in _seen:
            continue
        _seen.add(key)
        if lp.is_file():
            apq._ingest_ledger_entries(apq._load_ledger(lp), idx)  # noqa: SLF001
    apq._scan_history_job_jsons(hist_roots, idx, warnings)  # noqa: SLF001
    return idx, ledger_path


def _classify_candidate(
    vid: Path,
    idx: Any,
    *,
    min_duration_sec: float,
) -> dict[str, Any]:
    path_str = str(vid)
    out: dict[str, Any] = {"path": path_str, "stage": "unknown"}
    try:
        vkey = str(vid.resolve())
    except OSError:
        vkey = path_str
    try:
        sz = int(vid.stat().st_size)
    except OSError as exc:
        out["stage"] = "skipped_probe"
        out["probe_reason"] = "stat_failed"
        out["detail"] = repr(exc)
        return out
    if sz < apq.MIN_BYTES:
        out["stage"] = "skipped_small"
        out["size_bytes"] = sz
        return out
    probe = apq._ffprobe_json(vid)  # noqa: SLF001
    if probe is None:
        out["stage"] = "skipped_probe"
        out["probe_reason"] = "ffprobe_failed_or_nonzero_exit"
        return out
    dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
    if not has_v:
        out["stage"] = "skipped_probe"
        out["probe_reason"] = "no_video_stream"
        return out
    if dur is None:
        out["stage"] = "skipped_probe"
        out["probe_reason"] = "duration_unknown"
        return out
    if dur < float(min_duration_sec):
        out["stage"] = "skipped_probe"
        out["probe_reason"] = "duration_below_queue_threshold"
        out["duration_sec"] = dur
        out["threshold_sec"] = float(min_duration_sec)
        return out
    pol_src = is_valid_nyc_long_source(vid, ffprobe_meta=probe)
    if not pol_src.get("long_allowed"):
        out["stage"] = "skipped_long_policy"
        out["policy_reason"] = pol_src.get("reason")
        out["reject_reasons"] = pol_src.get("reject_reasons") or []
        out["duration_sec"] = dur
        return out
    vw, vh = apq._primary_video_dims(probe)  # noqa: SLF001
    qh = triple_chunk_sha256(vid)
    bn_norm = normalize_basename(vid.name)
    ck = content_key_v2(size_bytes=sz, duration_sec=dur, quick_hash=qh, basename_normalized=bn_norm)
    meta = {
        "path": vid,
        "source_path": path_str,
        "resolved_path": vkey,
        "basename": vid.name,
        "stem": vid.stem,
        "size_bytes": sz,
        "mtime": int(vid.stat().st_mtime),
        "duration_sec": dur,
        "quick_hash": qh,
        "content_key": ck,
        "video_width": vw,
        "video_height": vh,
        "long_source_policy": pol_src,
    }
    used, reason = apq._is_used_candidate(meta, idx)  # noqa: SLF001
    if used:
        out["stage"] = "skipped_duplicate"
        out["dedupe_reason"] = reason
        out["content_key"] = ck
        out["quick_hash"] = qh
        out["duration_sec"] = dur
        return out
    out["stage"] = "eligible"
    out["duration_sec"] = dur
    out["content_key"] = ck
    out["quick_hash"] = qh
    out["video_width"] = vw
    out["video_height"] = vh
    return out


def _pick_master_sources(
    inbox_files: list[Path],
    warnings: list[str],
    *,
    min_total_sec: float | None = None,
    prefer_dates: list[date] | None = None,
    mono_source_type: str | None = None,
    may15_ferry_only: bool = False,
) -> tuple[list[dict[str, Any]], float, str]:
    """Greedy accumulation: prefer-date ferry first (mtime desc), same source_type + landscape AR band."""
    target = float(min_total_sec) if min_total_sec is not None else MIN_MASTER_SEC
    prefer = list(prefer_dates or [])
    pool = list(inbox_files)
    if mono_source_type == "ferry":
        pool = [p for p in pool if is_ferry_video169_path(p)]
        if not pool:
            return [], 0.0, "no_ferry_video169_sources"
    keyed: list[tuple[float, Path]] = []
    for p in pool:
        try:
            keyed.append((float(p.stat().st_mtime), p))
        except OSError:
            keyed.append((0.0, p))
    if prefer:
        keyed.sort(key=lambda x: prefer_sort_key(x[1], prefer, mtime=x[0]))
    else:
        keyed.sort(key=lambda x: (-x[0], str(x[1])))

    if may15_ferry_only and prefer and mono_source_type == "ferry":
        pref_paths = {str(x[1]) for x in keyed if matches_prefer_date(x[1], prefer, mtime=x[0])}
        if pref_paths:
            keyed_pref = [x for x in keyed if str(x[1]) in pref_paths]
            keyed_other = [x for x in keyed if str(x[1]) not in pref_paths]
        else:
            keyed_pref = []
            keyed_other = list(keyed)
    else:
        keyed_pref = list(keyed)
        keyed_other = []
    def _greedy_from_keyed(keyed_in: list[tuple[float, Path]]) -> tuple[list[dict[str, Any]], float, str | None]:
        picked_l: list[dict[str, Any]] = []
        total_l = 0.0
        ref_ar_l: float | None = None
        ref_ar_f_l: float | None = None
        ref_st_l: str | None = None
        ref_orient_l: str | None = None
        for _mt, p in keyed_in:
            if total_l >= target:
                break
            probe = apq._ffprobe_json(p)  # noqa: SLF001
            dur, has_v = apq._duration_and_has_video(probe)  # noqa: SLF001
            if not has_v or dur is None or dur < 1.0:
                continue
            ok, why = _usable_for_master(p, probe)
            if not ok:
                continue
            pol = is_valid_nyc_long_source(p, ffprobe_meta=probe)
            ar = float(pol.get("aspect_ratio") or 0.0)
            st = str(pol.get("source_type") or "")
            orient = str(pol.get("orientation") or "")
            if mono_source_type and st != mono_source_type:
                continue
            if ref_ar_l is None:
                ref_ar_f_l = ar
                ref_ar_l = round(ar, 3)
                ref_st_l = st
                ref_orient_l = orient
            else:
                if st != ref_st_l or orient != ref_orient_l:
                    continue
                if ref_ar_f_l is not None and abs(ar - float(ref_ar_f_l)) > 0.04:
                    continue
            prefer_hit = bool(prefer and matches_prefer_date(p, prefer, mtime=_mt))
            picked_l.append(
                {
                    "path": str(p),
                    "duration_sec": dur,
                    "usable_reason": why,
                    "mtime": _mt,
                    "aspect_ratio": ar,
                    "source_type": st,
                    "orientation": orient,
                    "width": int(pol.get("width") or 0),
                    "height": int(pol.get("height") or 0),
                    "prefer_date_match": prefer_hit,
                }
            )
            total_l += dur
        return picked_l, total_l, ref_st_l

    picked, total, ref_st = _greedy_from_keyed(keyed_pref)
    if total < target and keyed_other:
        if may15_ferry_only and prefer:
            warnings.append("insufficient_may15_ferry_footage")
        more, more_total, _ = _greedy_from_keyed(keyed_other)
        if more:
            if picked and ref_st and more[0].get("source_type") != ref_st:
                warnings.append("may15_fallback_skipped_mixed_source_type")
            else:
                seen = {str(x.get("path")) for x in picked}
                for ent in more:
                    if str(ent.get("path")) in seen:
                        continue
                    picked.append(ent)
                    total += float(ent.get("duration_sec") or 0.0)
                    if total >= target:
                        break

    note = "ok" if total >= target else "insufficient_usable_inbox_duration"
    if may15_ferry_only and prefer and mono_source_type == "ferry" and total < target:
        note = "insufficient_may15_ferry_footage"
    if picked and ref_st and len({str(x.get("source_type") or "") for x in picked}) > 1:
        warnings.append("master_pick_invariant_broken_mixed_source_type")
    return picked, total, note


def main() -> int:
    warnings: list[str] = []
    ready = get_transfer_ready_to_upload(verbose=False)
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    nyc_clips = ready / "nyc_long_clips"

    idx, ledger_path = _build_dedupe_index(warnings)
    raw = apq.collect_long_candidates(ready, warnings)

    ready_long_paths = apq._list_video_files(nyc_clips, warnings)  # noqa: SLF001
    ready_long_count = len(ready_long_paths)

    skipped_probe_files: list[dict[str, Any]] = []
    skipped_small_files: list[dict[str, Any]] = []
    skipped_dup_files: list[dict[str, Any]] = []
    skipped_policy_files: list[dict[str, Any]] = []
    eligible_rows: list[dict[str, Any]] = []

    min_dur = float(apq.MIN_DURATION_SEC)
    for vid in raw:
        row = _classify_candidate(vid, idx, min_duration_sec=min_dur)
        st = row.get("stage")
        if st == "skipped_probe":
            skipped_probe_files.append(row)
        elif st == "skipped_small":
            skipped_small_files.append({"path": row.get("path"), "size_bytes": row.get("size_bytes")})
        elif st == "skipped_duplicate":
            skipped_dup_files.append(row)
        elif st == "skipped_long_policy":
            skipped_policy_files.append(row)
        elif st == "eligible":
            try:
                vp = Path(str(row.get("path") or ""))
                row["source_pool_origin"] = long_source_pool_origin(vp)
            except Exception:
                row["source_pool_origin"] = ""
            eligible_rows.append(row)

    probe_reasons: dict[str, int] = Counter()
    for r in skipped_probe_files:
        probe_reasons[str(r.get("probe_reason") or "unknown")] += 1

    dup_reasons: dict[str, int] = Counter()
    for r in skipped_dup_files:
        dup_reasons[str(r.get("dedupe_reason") or "unknown")] += 1

    clip_set = {str(p) for p in ready_long_paths}
    try:
        clip_res = {str(p.resolve()) for p in ready_long_paths}
    except OSError:
        clip_res = set()

    def _under_clips(path_str: str) -> bool:
        if path_str in clip_set:
            return True
        try:
            return str(Path(path_str).resolve()) in clip_res
        except OSError:
            return False

    eligible_ready_long_count = sum(1 for e in eligible_rows if _under_clips(str(e.get("path") or "")))
    duplicate_ready_long_count = sum(1 for e in skipped_dup_files if _under_clips(str(e.get("path") or "")))

    inbox_videos = _scan_inbox_videos(xfer, cache, warnings)
    non_video169_excluded = apq.count_non_video169_inbox_long_videos_excluded(xfer, cache, warnings)
    inbox_video_count = len(inbox_videos)
    inbox_total_duration_sec = 0.0
    inbox_16_9_landscape_duration_sec = 0.0
    inbox_usable_master_duration_sec = 0.0
    for p in inbox_videos:
        pr = apq._ffprobe_json(p)  # noqa: SLF001
        d, hv = apq._duration_and_has_video(pr)  # noqa: SLF001
        if not hv or d is None or d <= 0:
            continue
        inbox_total_duration_sec += d
        pol = is_valid_nyc_long_source(p, ffprobe_meta=pr)
        w, h = apq._primary_video_dims(pr)  # noqa: SLF001
        if w > h > 0:
            ar = w / float(h)
            if 1.70 <= ar <= 1.90:
                inbox_16_9_landscape_duration_sec += d
        ok_u, _ = _usable_for_master(p, pr)
        if ok_u:
            inbox_usable_master_duration_sec += d

    picked_sources, planned_total, pick_note = _pick_master_sources(inbox_videos, warnings, min_total_sec=MIN_MASTER_SEC)
    long_allowed_video169 = len(eligible_rows)
    can_generate = bool(pick_note == "ok" and len(picked_sources) > 0)
    ts_guess = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    recommended_output_path = str(nyc_clips / f"nyc_long_master_{ts_guess}.mp4")
    manifest_path = str(xfer / "publish_pack" / "nyc_long_uploads" / f"master_generation_{ts_guess}.json")

    summary: dict[str, Any] = {
        "generated_at": _utc(),
        "source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "master_source_policy": "video169_only",
        "master_supply_video169_only": True,
        "video169_raw_candidates_count": len(inbox_videos),
        "non_video169_excluded_count": int(non_video169_excluded),
        "long_allowed_video169_candidates_count": int(long_allowed_video169),
        "ledger_path": str(ledger_path),
        "queue_min_duration_sec": min_dur,
        "queue_min_bytes": apq.MIN_BYTES,
        "candidate_roots_order": [str(nyc_clips), str(ready)],
        "candidate_count_total": len(raw),
        "ready_long_count": ready_long_count,
        "eligible_ready_long_count": eligible_ready_long_count,
        "duplicate_ready_long_count": duplicate_ready_long_count,
        "eligible_total_count": len(eligible_rows),
        "skipped_probe_count": len(skipped_probe_files),
        "skipped_probe_reasons": dict(probe_reasons),
        "skipped_probe_files": skipped_probe_files[:80],
        "skipped_small_count": len(skipped_small_files),
        "skipped_small_files": skipped_small_files[:40],
        "skipped_duplicate_count": len(skipped_dup_files),
        "skipped_dup_files": skipped_dup_files[:40],
        "skipped_dup_reasons": dict(dup_reasons),
        "skipped_long_policy_count": len(skipped_policy_files),
        "skipped_long_policy_sample": skipped_policy_files[:30],
        "inbox_video_count": inbox_video_count,
        "inbox_total_duration_sec": round(inbox_total_duration_sec, 3),
        "inbox_16_9_landscape_duration_sec": round(inbox_16_9_landscape_duration_sec, 3),
        "inbox_usable_master_duration_sec": round(inbox_usable_master_duration_sec, 3),
        "can_generate_new_long_master": can_generate,
        "master_pick_note": pick_note,
        "planned_master_total_sec": round(planned_total, 3),
        "recommended_source_files": [x["path"] for x in picked_sources][:50],
        "recommended_output_path": recommended_output_path,
        "recommended_manifest_path": manifest_path,
        "min_master_sec": MIN_MASTER_SEC,
        "warnings": warnings,
    }

    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    md_lines = [
        f"# Long master supply diagnose ({summary['generated_at']})",
        "",
        "## Summary counts",
        "",
        f"- ready_long_count (nyc_long_clips): **{ready_long_count}**",
        f"- eligible_ready_long_count: **{eligible_ready_long_count}**",
        f"- duplicate_ready_long_count: **{duplicate_ready_long_count}**",
        f"- skipped_probe: **{len(skipped_probe_files)}** — reasons: `{summary['skipped_probe_reasons']}`",
        f"- skipped_dup: **{len(skipped_dup_files)}** — reasons: `{summary['skipped_dup_reasons']}`",
        f"- skipped_small: **{len(skipped_small_files)}**",
        "",
        "### skipped_probe (matches `auto_publish_queue` gate)",
        "",
        "Each row is a candidate that reached ffprobe but failed the same checks as the upload queue "
        f"(min duration **{min_dur}s**, min size **{apq.MIN_BYTES}** bytes, then policy/dedupe are separate).",
        "",
    ]
    for r in skipped_probe_files[:25]:
        md_lines.append(
            f"- `{r.get('path')}` → **{r.get('probe_reason')}** "
            f"(duration={r.get('duration_sec', '—')}, threshold={r.get('threshold_sec', '—')})"
        )
    if len(skipped_probe_files) > 25:
        md_lines.append(f"- … plus **{len(skipped_probe_files) - 25}** more (see JSON `skipped_probe_files`).")
    md_lines.extend(
        [
            "",
            "### skipped_duplicate (ledger + history)",
            "",
            f"Ledger / history index: `{ledger_path}`. Dedupe reasons match `_is_used_candidate`: "
            "**content_key**, **quick_hash**, **resolved_path**, **source_path**, **basename_size_duration**.",
            "",
        ]
    )
    for r in skipped_dup_files[:20]:
        md_lines.append(
            f"- `{r.get('path')}` → **{r.get('dedupe_reason')}** "
            f"(duration_sec={r.get('duration_sec', '—')}, content_key={str(r.get('content_key', ''))[:16]}…)"
        )
    if len(skipped_dup_files) > 20:
        md_lines.append(f"- … plus **{len(skipped_dup_files) - 20}** more (see JSON `skipped_dup_files`).")
    md_lines.extend(
        [
            "",
            "## Inbox",
            "",
            f"- inbox_video_count: **{inbox_video_count}**",
            f"- inbox_total_duration_sec: **{summary['inbox_total_duration_sec']}**",
            f"- inbox_16_9_landscape_duration_sec: **{summary['inbox_16_9_landscape_duration_sec']}**",
            f"- inbox_usable_master_duration_sec (policy_ok + crop-safe): **{summary['inbox_usable_master_duration_sec']}**",
            f"- can_generate_new_long_master: **{can_generate}** ({pick_note})",
            "",
            "## Recommended master",
            "",
            f"- output: `{recommended_output_path}`",
            f"- manifest: `{manifest_path}`",
            f"- sources ({len(picked_sources)}): see JSON `recommended_source_files`",
            "",
        ]
    )
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")
    print(json.dumps({"ok": True, "wrote_json": str(OUT_JSON), "wrote_md": str(OUT_MD), "can_generate": can_generate}, indent=2))
    print(f"VIDEO169_RAW_CANDIDATES_COUNT={len(inbox_videos)}")
    print(f"NON_VIDEO169_EXCLUDED_COUNT={non_video169_excluded}")
    print(f"LONG_ALLOWED_VIDEO169_CANDIDATES_COUNT={long_allowed_video169}")
    print("MASTER_SUPPLY_VIDEO169_ONLY=true")
    emergency = apq._load_long_emergency_state(xfer, warnings)
    active = apq._long_upload_emergency_active(emergency)
    lad = bool(emergency.get("LONG_AUTOPUBLISH_DISABLED", True))
    safe = bool(emergency.get("SAFE_TO_ENABLE_LONG_UPLOAD", False))
    if active:
        safe = False
    print(f"LONG_AUTOPUBLISH_DISABLED={str(lad).lower()}")
    print(f"SAFE_TO_ENABLE_LONG_UPLOAD={str(safe).lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
