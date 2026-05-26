#!/usr/bin/env python3
"""
Publish-time dedupe + cross-channel rules + asset reuse cooling.

Phase-1 safe:
- never reads/scans/copies/moves/deletes inside /Volumes/StateVerge/私人别碰
- never deletes/moves any original media

This module is designed to be called by NYC pipelines right before upload,
and after publish success to append published index and update usage fields
inside assets_master.jsonl.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import sys
from typing import Any, Iterable

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import SSD_ROOT  # noqa: E402
PRIVATE_ROOT = SSD_ROOT / "私人别碰"

PUBLISHED_JSONL = SSD_ROOT / "05_INDEX" / "published_assets.jsonl"
ASSETS_MASTER_JSONL = SSD_ROOT / "05_INDEX" / "assets_master.jsonl"

NYC_PUBLISH_LOG = Path.home() / "Library/Logs/StateVerge/stateverge_nyc_publish.log"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def log_line(line: str) -> None:
    NYC_PUBLISH_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(NYC_PUBLISH_LOG, "a", encoding="utf-8") as f:
        f.write(f"{_now_iso()} {line}\n")


def is_private_path(p: str | Path) -> bool:
    try:
        rp = Path(p).expanduser().resolve()
    except OSError:
        return False
    r = str(PRIVATE_ROOT.resolve())
    s = str(rp)
    return s == r or s.startswith(r + os.sep)


def protected_skip(path: str | Path) -> bool:
    if is_private_path(path):
        log_line(f"PROTECTED_SKIP path={path} reason=private_do_not_touch")
        return True
    return False


def sha256_quick_file(path: Path, head_bytes: int = 4 * 1024 * 1024) -> str:
    st = path.stat()
    h = hashlib.sha256()
    h.update(str(st.st_size).encode())
    h.update(str(int(st.st_mtime)).encode())
    with open(path, "rb") as f:
        h.update(f.read(head_bytes))
    return h.hexdigest()


def title_hash(title: str) -> str:
    t = (title or "").strip().lower()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


_punct = re.compile(r"[^\w\s]+", re.UNICODE)
_spaces = re.compile(r"\s+")


def _title_tokens(title: str) -> set[str]:
    t = _punct.sub(" ", (title or "").lower())
    t = _spaces.sub(" ", t).strip()
    if not t:
        return set()
    return set(t.split(" "))


def title_similarity(a: str, b: str) -> float:
    ta = _title_tokens(a)
    tb = _title_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    uni = len(ta | tb)
    return inter / uni if uni else 0.0


def first_3_clips_hash(asset_ids: list[str]) -> str:
    s = "|".join((asset_ids or [])[:3])
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_content_id(
    *, video_hash: str, audio_hash: str, duration_seconds: float, first3_hash: str
) -> str:
    blob = f"{video_hash}|{audio_hash}|{duration_seconds:.3f}|{first3_hash}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


LONG_DEFAULT_THRESHOLD_SEC = 3300.0
LONG_PREFERRED_TARGET_SEC = 3600.0


def long_duration_threshold_sec(requested_duration_sec: float | None) -> float:
    """Minimum output/source seconds for a valid long (v1 duration policy)."""
    if requested_duration_sec is not None and float(requested_duration_sec) > 0:
        return max(1800.0, float(requested_duration_sec) * 0.9)
    return LONG_DEFAULT_THRESHOLD_SEC


def _ffprobe_duration_quick(media_path: str | Path, *, timeout_sec: float = 120.0) -> float:
    exe = shutil.which("ffprobe") or "ffprobe"
    p = Path(media_path).expanduser()
    if not p.is_file():
        return 0.0
    cmd = [
        exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(p),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return 0.0


@dataclass
class PublishCandidate:
    video_path: Path
    audio_path: Path | None
    title: str
    channel: str
    publish_date: str  # YYYY-MM-DD
    source_assets: list[str]
    is_long: bool
    is_short: bool
    derived_from_long: str | None
    duration_seconds: float
    video_hash: str
    audio_hash: str
    first3_hash: str
    content_id: str
    title_hash: str
    requested_duration_sec: float | None = None
    # Legacy flags (optional); long validation is duration-based (v1).
    single_take_long: bool = False
    repaired_single_source_long: bool = False


def load_published_entries(path: Path = PUBLISHED_JSONL, *, tail: int | None = None) -> list[dict[str, Any]]:
    if protected_skip(path):
        return []
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if tail is not None and tail > 0:
        return out[-tail:]
    return out


def append_published(entry: dict[str, Any], path: Path = PUBLISHED_JSONL) -> None:
    if protected_skip(path):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_assets_master_index(path: Path = ASSETS_MASTER_JSONL) -> dict[str, dict[str, Any]]:
    if protected_skip(path):
        return {}
    if not path.is_file():
        return {}
    out: dict[str, dict[str, Any]] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            aid = r.get("asset_id")
            if isinstance(aid, str) and aid:
                out[aid] = r
    return out


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def update_assets_usage(
    *, content_id: str, asset_ids: Iterable[str], today: str, path: Path = ASSETS_MASTER_JSONL
) -> None:
    if protected_skip(path):
        return
    if not path.is_file():
        log_line(f"ERROR assets_master_missing path={path}")
        return
    aset = set(a for a in asset_ids if a)
    tmp = path.with_suffix(".jsonl.tmp")
    with open(path, encoding="utf-8") as rf, open(tmp, "w", encoding="utf-8") as wf:
        for line in rf:
            raw = line.strip()
            if not raw:
                continue
            try:
                r = json.loads(raw)
            except json.JSONDecodeError:
                continue
            aid = r.get("asset_id")
            if isinstance(aid, str) and aid in aset:
                r["usage_count"] = int(r.get("usage_count") or 0) + 1
                r["last_used_date"] = today
                used = r.get("used_in_content_ids") or []
                if not isinstance(used, list):
                    used = []
                if content_id not in used:
                    used.append(content_id)
                r["used_in_content_ids"] = used
            wf.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def _same_assets_signature(asset_ids: list[str]) -> str:
    ids = [x for x in (asset_ids or []) if isinstance(x, str) and x]
    ids = sorted(set(ids))
    return hashlib.sha256("|".join(ids).encode("utf-8")).hexdigest()


def check_publish_allowed(
    cand: PublishCandidate,
    *,
    published: list[dict[str, Any]] | None = None,
    assets_index: dict[str, dict[str, Any]] | None = None,
    source_total_duration_sec: float | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    """
    Returns (allowed, reason_or_ok, long_source_policy).

    Long-form (v1): duration-based validation; no minimum asset count of 5.
    """
    empty_policy: dict[str, Any] = {}

    if protected_skip(cand.video_path):
        return False, "private_do_not_touch", empty_policy
    if cand.audio_path and protected_skip(cand.audio_path):
        return False, "private_do_not_touch", empty_policy

    published = published if published is not None else load_published_entries()

    # 1) video_hash duplicate
    for e in published:
        if str(e.get("video_hash") or "") == cand.video_hash:
            log_line("SKIP_DUPLICATE_CONTENT reason=video_hash")
            return False, "video_hash", empty_policy

    # 2) content_id duplicate
    for e in published:
        if str(e.get("content_id") or "") == cand.content_id:
            log_line("SKIP_DUPLICATE_CONTENT reason=content_id")
            return False, "content_id", empty_policy

    # 3) same source_assets in recent 30
    sig = _same_assets_signature(cand.source_assets)
    recent30 = published[-30:] if len(published) > 30 else published
    for e in recent30:
        es = e.get("source_assets") or []
        if isinstance(es, list) and _same_assets_signature([str(x) for x in es]) == sig:
            log_line("SKIP_DUPLICATE_CONTENT reason=same_assets")
            return False, "same_assets", empty_policy

    # 4) title similarity against recent 20
    recent20 = published[-20:] if len(published) > 20 else published
    for e in recent20:
        t = str(e.get("title") or "")
        sim = title_similarity(cand.title, t)
        if sim > 0.8:
            log_line("SKIP_TITLE_DUPLICATE")
            return False, "title_duplicate", empty_policy

    # 5) cross-channel content_id constraints
    for e in published:
        if str(e.get("content_id") or "") != cand.content_id:
            continue
        if str(e.get("channel") or "") == cand.channel:
            continue
        if cand.is_long:
            log_line("SKIP_CROSS_CHANNEL_DUPLICATE")
            return False, "cross_channel_long", empty_policy
        # short: allowed only if duration differs AND title_hash differs AND derived_from_long exists
        if not cand.is_short:
            log_line("SKIP_CROSS_CHANNEL_DUPLICATE")
            return False, "cross_channel_unknown", empty_policy
        if cand.derived_from_long is None or not cand.derived_from_long.strip():
            log_line("SKIP_CROSS_CHANNEL_DUPLICATE")
            return False, "short_missing_derived_from_long", empty_policy
        if abs(float(e.get("duration_seconds") or 0) - cand.duration_seconds) < 0.5:
            log_line("SKIP_CROSS_CHANNEL_DUPLICATE")
            return False, "short_same_duration", empty_policy
        if str(e.get("title_hash") or "") == cand.title_hash:
            log_line("SKIP_CROSS_CHANNEL_DUPLICATE")
            return False, "short_same_title_hash", empty_policy

    # cooling rules: need assets_master
    assets_index = assets_index if assets_index is not None else load_assets_master_index()
    today = _parse_date(cand.publish_date) or date.today()
    if cand.is_long:
        cooldown = timedelta(days=7)
        for aid in cand.source_assets:
            a = assets_index.get(aid)
            if not a:
                continue
            last = _parse_date(a.get("last_used_date"))
            if last and (today - last) < cooldown:
                log_line("SKIP_RECENTLY_USED")
                return False, "recently_used_long", empty_policy
    if cand.is_short:
        cooldown = timedelta(days=3)
        for aid in cand.source_assets:
            a = assets_index.get(aid)
            if not a:
                continue
            last = _parse_date(a.get("last_used_date"))
            if last and (today - last) < cooldown:
                log_line("SKIP_RECENTLY_USED")
                return False, "recently_used_short", empty_policy

    # Long-form v1: duration + provenance (no minimum 5 assets).
    # ``is_long`` may be true for >=600s "medium-long"; full duration policy applies only when
    # output meets the long threshold (default 3300s or 0.9 * requested_duration).
    if cand.is_long:
        thresh = long_duration_threshold_sec(cand.requested_duration_sec)
        out_d = float(cand.duration_seconds or 0.0)
        if out_d < thresh:
            log_line(
                f"LONG_LIGHT skip_full_duration_policy out={out_d:.1f}s < thresh={thresh:.1f}s "
                "(no 3300s source/output gate)"
            )
        else:
            src_paths = [str(x) for x in cand.source_assets if isinstance(x, str) and str(x).strip()]
            n_assets = len(set(src_paths))
            if not src_paths:
                log_line("SKIP_DUPLICATE_CONTENT reason=missing_source_assets_long")
                return False, "source_provenance_missing", {"long_source_policy": {"source_asset_count": 0}}

            src_total = float(source_total_duration_sec) if source_total_duration_sec is not None else 0.0
            if src_total <= 0.0:
                src_total = sum(_ffprobe_duration_quick(Path(p)) for p in sorted(set(src_paths)))

            duration_pass = out_d >= thresh and src_total >= thresh
            delta_ok = True
            if src_total > 0.0 and out_d > 0.0:
                delta_ok = abs(out_d - src_total) <= max(2.0, src_total * 0.01)

            primary_rg = ""
            route_groups: list[str] = []
            for aid in src_paths:
                a = assets_index.get(aid) or {}
                rg = str(a.get("route_group") or a.get("captured_date") or "").strip()
                if rg:
                    route_groups.append(rg)
            if route_groups:
                primary_rg = sorted(set(route_groups))[0]

            policy: dict[str, Any] = {
                "long_source_policy": {
                    "min_asset_count_required": False,
                    "duration_based_validation": True,
                    "single_source_long_allowed": True,
                    "two_source_long_allowed": True,
                    "long_duration_threshold_sec": thresh,
                    "preferred_target_duration_sec": LONG_PREFERRED_TARGET_SEC,
                },
                "source_asset_count": n_assets,
                "source_total_duration_sec": round(src_total, 3),
                "output_duration_sec": round(out_d, 3),
                "duration_validation_passed": bool(duration_pass and delta_ok),
                "same_day_preferred": True,
                "same_day_required": False,
                "primary_route_group": primary_rg,
                "selected_route_groups": sorted(set(route_groups)),
                "chronological_sort": True,
                "video_type": "repaired_single_source_long" if cand.repaired_single_source_long else "long",
            }

            if cand.repaired_single_source_long:
                log_line("ALLOW_REPAIRED_SINGLE_SOURCE_LONG duration_policy=v1")
            elif cand.single_take_long:
                log_line("ALLOW_SINGLE_TAKE_LONG duration_policy=v1")

            if not duration_pass:
                log_line("SKIP_LONG reason=long_duration_too_short")
                policy["duration_validation_passed"] = False
                return False, "long_duration_too_short", policy
            if not delta_ok:
                log_line("SKIP_LONG reason=output_duration_mismatch")
                policy["duration_validation_passed"] = False
                return False, "output_duration_mismatch", policy

            # Same-day diversity: only when >=3 indexed sources with real shoot_date metadata
            if n_assets >= 3:
                pairs: set[tuple[str, str]] = set()
                indexed = 0
                for aid in sorted(set(src_paths)):
                    a = assets_index.get(aid)
                    if not isinstance(a, dict) or not a:
                        continue
                    sd = str(a.get("shoot_date") or "").strip()
                    if not sd:
                        continue
                    indexed += 1
                    pairs.add((sd, str(a.get("location_slug") or "")))
                if indexed >= 3 and len(pairs) <= 1:
                    log_line("SKIP_DUPLICATE_CONTENT reason=same_day_location")
                    return False, "long_same_day_location", policy

            return True, "ok", policy

    if cand.is_short:
        if not (5 <= cand.duration_seconds <= 60):
            log_line("SKIP_DUPLICATE_CONTENT reason=short_duration_out_of_range")
            return False, "short_duration", empty_policy

    return True, "ok", empty_policy


def build_candidate_from_meta(
    *,
    video_path: Path,
    meta: dict[str, Any],
    channel: str,
    publish_date: str,
) -> PublishCandidate | None:
    """
    Build candidate from NYC meta.json.
    Requires meta['source_assets'] list for cooling and same_assets checks.
    """
    if protected_skip(video_path):
        return None
    title = str(meta.get("title") or "").strip()
    if not title:
        title = video_path.stem
    src_assets = meta.get("source_assets") or []
    if not isinstance(src_assets, list) or not src_assets:
        log_line(f"SKIP_DUPLICATE_CONTENT reason=missing_source_assets path={video_path}")
        return None
    src_assets = [str(x) for x in src_assets if str(x).strip()]

    req_raw = meta.get("requested_duration_sec") or meta.get("requested_duration_seconds")
    try:
        requested_duration_sec = float(req_raw) if req_raw is not None and str(req_raw).strip() != "" else None
    except (TypeError, ValueError):
        requested_duration_sec = None

    duration = float(meta.get("duration_seconds") or meta.get("duration") or 0.0)
    is_long = bool(meta.get("video_type") == "long" or meta.get("is_long") is True)
    is_short = bool(meta.get("video_type") == "short" or meta.get("is_short") is True)
    derived = meta.get("derived_from_long")
    derived_from_long = str(derived).strip() if isinstance(derived, str) and derived.strip() else None

    audio_path: Path | None = None
    ap = meta.get("audio_file") or meta.get("audio_path")
    if isinstance(ap, str) and ap.strip():
        audio_path = Path(ap)
        if protected_skip(audio_path):
            audio_path = None

    vhash = sha256_quick_file(video_path)
    ahash = sha256_quick_file(audio_path) if audio_path and audio_path.is_file() else "no_audio"
    f3 = first_3_clips_hash(src_assets)
    cid = build_content_id(video_hash=vhash, audio_hash=ahash, duration_seconds=duration, first3_hash=f3)
    th = title_hash(title)

    return PublishCandidate(
        video_path=video_path,
        audio_path=audio_path,
        title=title,
        channel=channel,
        publish_date=publish_date,
        source_assets=src_assets,
        is_long=is_long,
        is_short=is_short,
        derived_from_long=derived_from_long,
        duration_seconds=duration,
        video_hash=vhash,
        audio_hash=ahash,
        first3_hash=f3,
        content_id=cid,
        title_hash=th,
        requested_duration_sec=requested_duration_sec,
        repaired_single_source_long=False,
    )

