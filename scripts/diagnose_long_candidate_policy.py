#!/usr/bin/env python3
"""Long Candidate Policy Diagnose v1 — read-only; no uploads, no token changes."""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
_SCRIPTS = _REPO / "scripts"
_NYC = _REPO / "scripts" / "nyc_auto"
for p in (_SRC, _SCRIPTS, _NYC):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


from auto_publish_queue import (  # noqa: E402
    MIN_BYTES,
    MIN_DURATION_SEC,
    VIDEO_EXTS,
    DedupeIndexV2,
    _canonical_media_path,
    _duration_and_has_video,
    _ffprobe_json,
    _ingest_ledger_entries,
    _is_shorts_path,
    _is_used_candidate,
    _load_ledger,
    _primary_video_dims,
    _scan_history_job_jsons,
    collect_long_candidates,
)
from media_quick_hash import content_key_v2, normalize_basename, triple_chunk_sha256  # noqa: E402
from nyc_long_source_policy import LONG_SOURCE_POLICY_VERSION, is_valid_nyc_long_source  # noqa: E402

CONTROL = Path.home() / "StateVerge_Control_Center"
LOGS = CONTROL / "logs"
OUT_JSON = LOGS / "long_candidate_policy_diagnose.json"
OUT_MD = LOGS / "long_candidate_policy_diagnose.md"
LEDGER_NAME = "long_used_assets.json"
HOME_LONG_UPLOADS = Path.home() / "StateVerge" / "data" / "long_runtime" / "long_uploads"
FALSE_REJECT_MIN_DURATION_SEC = 1800.0


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _has_audio(probe: dict[str, Any] | None) -> bool:
    if not probe:
        return False
    for st in probe.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "audio":
            return True
    return False


def _resolution_str(w: int, h: int) -> str:
    if w > 0 and h > 0:
        return f"{w}x{h}"
    return ""


def _finalize_rejected_reason(
    *,
    suffix_ok: bool,
    in_candidate_root: bool,
    is_shorts_path: bool,
    probe_ok: bool,
    has_video: bool,
    size_ok: bool,
    duration_ok: bool,
    policy_allowed: bool,
    dup_ledger: bool,
    dup_hist: bool,
    pol: dict[str, Any],
) -> str:
    if not suffix_ok:
        return "unsupported_extension"
    if is_shorts_path:
        return "rejected_shorts_path"
    if not in_candidate_root:
        return "not_in_candidate_root"
    if not probe_ok:
        return "ffprobe_failed"
    if not has_video:
        return "no_video_stream"
    if not size_ok:
        return "file_too_small"
    if not duration_ok:
        return "duration_lt_180"
    if dup_ledger:
        return "duplicate_ledger"
    if dup_hist:
        return "duplicate_history"
    if not policy_allowed:
        rrs = [str(x) for x in (pol.get("reject_reasons") or [])]
        if "rejected_vertical_video" in rrs or str(pol.get("orientation") or "") == "portrait":
            return "rejected_vertical_video"
        if "rejected_bad_aspect_ratio" in rrs:
            return "rejected_bad_aspect_ratio"
        if "rejected_shorts_path" in rrs or any("shorts_asset" in x for x in rrs):
            return "rejected_shorts_path"
        if any("walking" in x for x in rrs):
            return "walking_not_allowed_for_nyc_long_channel"
        return "policy_skip_unknown"
    return ""


def _iter_scan_files(roots: list[Path], warnings: list[str]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        if not root.is_dir():
            warnings.append(f"scan_skip_missing_dir:{root}")
            continue
        try:
            for p in root.rglob("*"):
                if not p.is_file() or p.name.startswith("._"):
                    continue
                suf = p.suffix.lower()
                if suf not in VIDEO_EXTS:
                    continue
                try:
                    key = str(p.resolve())
                except OSError:
                    key = str(p)
                if key in seen:
                    continue
                seen.add(key)
                out.append(p)
        except OSError as exc:
            warnings.append(f"scan_oserror:{root}:{exc!r}")
    return out


def _under_ready_tree(path: Path, ready: Path) -> bool:
    try:
        rp = path.resolve()
        rr = ready.resolve()
    except OSError:
        return False
    for anc in rp.parents:
        if anc == rr:
            return True
    return rp == rr


def main() -> int:
    warnings: list[str] = []
    xfer = get_sv_transfer(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)
    cache = get_sv_cache(verbose=False)

    scan_roots = [
        ready / "nyc_long_clips",
        ready,
        cache / "renders",
        HOME_LONG_UPLOADS,
    ]

    primary_ledger = xfer / "publish_pack" / "nyc_long_uploads" / LEDGER_NAME
    home_ledger = HOME_LONG_UPLOADS / LEDGER_NAME
    hist_roots = [xfer / "publish_pack" / "nyc_long_uploads", HOME_LONG_UPLOADS]

    idx_ledger = DedupeIndexV2()
    for lp in (primary_ledger, home_ledger):
        if lp.is_file():
            _ingest_ledger_entries(_load_ledger(lp), idx_ledger)

    idx_hist = DedupeIndexV2()
    _scan_history_job_jsons(hist_roots, idx_hist, warnings)

    collected = collect_long_candidates(ready, warnings)
    cand_keys: set[str] = set()
    for p in collected:
        try:
            cand_keys.add(_canonical_media_path(str(p.resolve())))
        except OSError:
            cand_keys.add(_canonical_media_path(str(p)))

    all_paths = _iter_scan_files(scan_roots, warnings)

    rows: list[dict[str, Any]] = []
    total_in_rtu = 0
    total_in_nyc_long_clips = 0
    nyc_clips_root = ready / "nyc_long_clips"

    for vid in all_paths:
        suf = vid.suffix.lower()
        suffix_ok = suf in VIDEO_EXTS
        try:
            sz = int(vid.stat().st_size)
        except OSError:
            sz = 0
        try:
            mtime = int(vid.stat().st_mtime)
        except OSError:
            mtime = 0

        in_rtu = _under_ready_tree(vid, ready)
        in_clips = _under_ready_tree(vid, nyc_clips_root)
        if in_rtu:
            total_in_rtu += 1
        if in_clips:
            total_in_nyc_long_clips += 1

        try:
            ckey = _canonical_media_path(str(vid.resolve()))
        except OSError:
            ckey = _canonical_media_path(str(vid))
        in_candidate_root = bool(ckey and ckey in cand_keys)

        shorts_path = _is_shorts_path(vid)
        probe = _ffprobe_json(vid) if suffix_ok else None
        probe_ok = probe is not None
        dur, has_video = _duration_and_has_video(probe)
        duration_sec = float(dur) if dur is not None else None
        has_audio = _has_audio(probe)
        vw, vh = _primary_video_dims(probe)
        resolution = _resolution_str(vw, vh)

        size_ok = sz >= MIN_BYTES
        duration_ok = duration_sec is not None and duration_sec >= float(MIN_DURATION_SEC)

        pol = (
            is_valid_nyc_long_source(vid, ffprobe_meta=probe)
            if probe_ok and has_video
            else is_valid_nyc_long_source(vid, ffprobe_meta=None)
        )
        policy_allowed = bool(pol.get("long_allowed"))

        try:
            vkey = str(vid.resolve())
        except OSError:
            vkey = str(vid)
        qh = ""
        ck = ""
        if probe_ok and has_video and size_ok and duration_sec is not None:
            try:
                qh = triple_chunk_sha256(vid)
                ck = content_key_v2(
                    size_bytes=sz,
                    duration_sec=float(duration_sec),
                    quick_hash=qh,
                    basename_normalized=normalize_basename(vid.name),
                )
            except OSError:
                warnings.append(f"quick_hash_failed:{vid}")

        meta = {
            "resolved_path": vkey,
            "source_path": str(vid),
            "basename": vid.name,
            "quick_hash": qh,
            "content_key": ck,
            "size_bytes": sz,
            "duration_sec": float(duration_sec) if duration_sec is not None else 0.0,
        }
        rp_k = _canonical_media_path(vkey)
        src_k = _canonical_media_path(str(vid))
        if probe_ok and has_video and size_ok and duration_sec is not None and qh:
            dup_ledger, _dl = _is_used_candidate(meta, idx_ledger)
            dup_hist, _dh = _is_used_candidate(meta, idx_hist)
        else:
            dup_ledger = bool(rp_k and rp_k in idx_ledger.resolved_path_keys) or bool(
                src_k and src_k in idx_ledger.resolved_path_keys
            )
            dup_hist = bool(rp_k and rp_k in idx_hist.resolved_path_keys) or bool(
                src_k and src_k in idx_hist.resolved_path_keys
            )

        rejected_reason = _finalize_rejected_reason(
            suffix_ok=suffix_ok,
            in_candidate_root=in_candidate_root,
            is_shorts_path=shorts_path,
            probe_ok=probe_ok,
            has_video=has_video,
            size_ok=size_ok,
            duration_ok=duration_ok,
            policy_allowed=policy_allowed,
            dup_ledger=dup_ledger,
            dup_hist=dup_hist,
            pol=pol,
        )

        is_too_short = duration_sec is None or duration_sec < float(MIN_DURATION_SEC)

        eligible = (
            suffix_ok
            and in_candidate_root
            and not shorts_path
            and probe_ok
            and has_video
            and size_ok
            and duration_ok
            and policy_allowed
            and not dup_ledger
            and not dup_hist
        )

        rows.append(
            {
                "path": str(vid),
                "basename": vid.name,
                "size_bytes": sz,
                "duration_sec": duration_sec,
                "has_video": has_video,
                "has_audio": has_audio,
                "resolution": resolution,
                "width": int(pol.get("width") or vw),
                "height": int(pol.get("height") or vh),
                "aspect_ratio": float(pol.get("aspect_ratio") or 0.0),
                "display_aspect_ratio": str(pol.get("display_aspect_ratio") or ""),
                "aspect_policy": str(pol.get("aspect_policy") or ""),
                "mtime": mtime,
                "rejected_reason": rejected_reason if not eligible else "",
                "policy_reason": str(pol.get("reason") or ""),
                "policy_uncertain": bool(pol.get("long_candidate_uncertain")),
                "is_shorts_path": shorts_path,
                "is_too_short": is_too_short,
                "is_duplicate_by_ledger": dup_ledger,
                "is_duplicate_by_history": dup_hist,
                "is_candidate_root": in_candidate_root,
                "eligible_for_long_upload": eligible,
            }
        )

    eligible_count = sum(1 for r in rows if r["eligible_for_long_upload"])
    rejected_count = len(rows) - eligible_count
    reason_ctr = Counter(str(r["rejected_reason"]) for r in rows if not r["eligible_for_long_upload"])
    rejected_by_reason = dict(sorted(reason_ctr.items(), key=lambda kv: (-kv[1], kv[0])))

    bad_aspect_candidates = [
        {
            "path": r["path"],
            "basename": r["basename"],
            "aspect_ratio": r.get("aspect_ratio"),
            "display_aspect_ratio": r.get("display_aspect_ratio"),
        }
        for r in rows
        if r.get("rejected_reason") == "rejected_bad_aspect_ratio"
    ]
    vertical_candidates = [
        {
            "path": r["path"],
            "basename": r["basename"],
            "width": r.get("width"),
            "height": r.get("height"),
            "aspect_ratio": r.get("aspect_ratio"),
        }
        for r in rows
        if r.get("rejected_reason") == "rejected_vertical_video"
    ]
    ar_vals = [float(r["aspect_ratio"]) for r in rows if float(r.get("aspect_ratio") or 0) > 0]
    aspect_ratio_summary: dict[str, Any] = {}
    if ar_vals:
        aspect_ratio_summary = {
            "count": len(ar_vals),
            "min": round(min(ar_vals), 6),
            "max": round(max(ar_vals), 6),
            "mean": round(sum(ar_vals) / len(ar_vals), 6),
        }
    aspect_rejects_sorted = [r for r in rows if r.get("rejected_reason") == "rejected_bad_aspect_ratio"]
    aspect_rejects_sorted.sort(key=lambda x: abs(float(x.get("aspect_ratio") or 0) - 1.777778), reverse=True)
    top_rejected_aspect = aspect_rejects_sorted[:20]

    false_rejects: list[dict[str, Any]] = []
    for r in rows:
        if r["eligible_for_long_upload"]:
            continue
        if not r.get("is_candidate_root"):
            continue
        if r.get("rejected_reason") != "policy_skip_unknown":
            continue
        d = r.get("duration_sec")
        if d is None or float(d) < FALSE_REJECT_MIN_DURATION_SEC:
            continue
        if r.get("is_shorts_path"):
            continue
        false_rejects.append(
            {
                "path": r["path"],
                "basename": r["basename"],
                "duration_sec": d,
                "rejected_reason": r.get("rejected_reason"),
                "policy_reason": r.get("policy_reason"),
            }
        )

    long_like_rejected = [r for r in rows if not r["eligible_for_long_upload"]]
    long_like_rejected.sort(
        key=lambda x: (float(x["duration_sec"] or 0.0), int(x["size_bytes"] or 0)),
        reverse=True,
    )
    top_20 = long_like_rejected[:20]

    generate_new = eligible_count == 0
    recommended_roots = [
        str(xfer / "00_INBOX" / "iphone"),
        str(cache / "inbox"),
    ]
    if generate_new:
        suggested = (
            "No file passes NYC long upload gates under the current policy and dedupe state. "
            "Ingest new driving-oriented landscape 16:9 masters (≥30 min) from the recommended inbox roots, "
            "export to `ready_to_upload/nyc_long_clips`, then re-run this diagnose and `auto_publish_queue.py --dry-run`."
        )
    else:
        suggested = (
            f"{eligible_count} file(s) are eligible for long upload (dry-run / queue will still apply runtime locks). "
            "Review `rejected_by_reason` for the rest."
        )

    summary = {
        "generated_at": _utc(),
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "min_duration_sec": float(MIN_DURATION_SEC),
        "min_size_bytes": int(MIN_BYTES),
        "scan_roots": [str(x) for x in scan_roots],
        "total_video_files_scanned": len(rows),
        "total_in_ready_to_upload": total_in_rtu,
        "total_in_nyc_long_clips": total_in_nyc_long_clips,
        "eligible_count": eligible_count,
        "rejected_count": rejected_count,
        "rejected_by_reason": rejected_by_reason,
        "bad_aspect_candidates": bad_aspect_candidates,
        "vertical_candidates": vertical_candidates,
        "aspect_ratio_summary": aspect_ratio_summary,
        "top_rejected_by_aspect_ratio": [
            {
                "path": r["path"],
                "basename": r["basename"],
                "aspect_ratio": r.get("aspect_ratio"),
                "display_aspect_ratio": r.get("display_aspect_ratio"),
            }
            for r in top_rejected_aspect
        ],
        "auto_provenance_recovered_candidates": [],
        "policy_false_reject_count": len(false_rejects),
        "policy_false_reject_candidates": false_rejects,
        "generate_new_long_video_required": generate_new,
        "recommended_source_roots": recommended_roots,
        "suggested_next_action": suggested,
        "warnings": warnings,
    }

    payload: dict[str, Any] = {"summary": summary, "files": rows}

    LOGS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md_lines = [
        f"# Long Candidate Policy Diagnose v1 ({summary['generated_at']})",
        "",
        "## Summary counts",
        "",
        f"- **total_video_files_scanned**: {summary['total_video_files_scanned']}",
        f"- **total_in_ready_to_upload**: {summary['total_in_ready_to_upload']}",
        f"- **total_in_nyc_long_clips**: {summary['total_in_nyc_long_clips']}",
        f"- **eligible_count**: {summary['eligible_count']}",
        f"- **rejected_count**: {summary['rejected_count']}",
        f"- **policy_false_reject_count**: {len(false_rejects)}",
        f"- **generate_new_long_video_required**: {summary['generate_new_long_video_required']}",
        "",
        f"- **bad_aspect_count**: {len(bad_aspect_candidates)}",
        f"- **vertical_rejected_count**: {len(vertical_candidates)}",
        "",
        "## aspect_ratio_summary",
        "",
        "```json",
        json.dumps(aspect_ratio_summary, indent=2, ensure_ascii=False),
        "```",
        "",
        "## top_rejected_by_aspect_ratio",
        "",
        "\n".join(
            f"- `{r['basename']}` ar={r.get('aspect_ratio')} DAR=`{r.get('display_aspect_ratio')}` — `{r['path']}`"
            for r in top_rejected_aspect
        )
        or "- (none)",
        "",
        "## bad_aspect_candidates",
        "",
        "```json",
        json.dumps(bad_aspect_candidates[:40], indent=2, ensure_ascii=False),
        "```",
        "",
        "## vertical_candidates",
        "",
        "```json",
        json.dumps(vertical_candidates[:40], indent=2, ensure_ascii=False),
        "```",
        "",
        "## rejected_by_reason",
        "",
        "```json",
        json.dumps(rejected_by_reason, indent=2, ensure_ascii=False),
        "```",
        "",
        "## top_20_rejected_long_like_files (by duration, then size)",
        "",
    ]
    for i, r in enumerate(top_20, 1):
        md_lines.append(
            f"{i}. `{r['basename']}` — {float(r['duration_sec'] or 0):.1f}s — "
            f"{r.get('rejected_reason') or '(unknown)'} — `{r['path']}`"
        )
    md_lines.extend(
        [
            "",
            "## policy_false_reject_candidates (duration≥1800s, long-queue candidate root, policy_skip_unknown / uncertain)",
            "",
            "```json",
            json.dumps(false_rejects, indent=2, ensure_ascii=False),
            "```",
            "",
            "## recommended_source_roots",
            "",
            "\n".join(f"- `{x}`" for x in recommended_roots),
            "",
            "## suggested_next_action",
            "",
            suggested,
            "",
        ]
    )
    OUT_MD.write_text("\n".join(md_lines), encoding="utf-8")

    print(f"DIAGNOSE_SCRIPT={Path(__file__).resolve()}")
    print(f"DIAGNOSE_JSON={OUT_JSON}")
    print(f"DIAGNOSE_MD={OUT_MD}")
    print(f"TOTAL_VIDEO_FILES_SCANNED={len(rows)}")
    print(f"ELIGIBLE_COUNT={eligible_count}")
    print(f"REJECTED_COUNT={rejected_count}")
    print(f"REJECTED_BY_REASON={json.dumps(rejected_by_reason, ensure_ascii=False)}")
    print(f"POLICY_FALSE_REJECT_COUNT={len(false_rejects)}")
    print(f"GENERATE_NEW_LONG_VIDEO_REQUIRED={'true' if generate_new else 'false'}")
    print(f"SUGGESTED_NEXT_ACTION={suggested}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
