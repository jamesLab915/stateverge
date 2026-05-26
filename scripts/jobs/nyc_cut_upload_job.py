#!/usr/bin/env python3
"""NYC cut / long ambient concat job: normalized CFR segments only; optional YouTube upload (NYC channel)."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
import shutil
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parent.parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from utils.storage_paths import (  # noqa: E402
        get_davinci_logs_dir,
        get_sv_cache,
        get_sv_transfer,
        get_transfer_ready_to_upload,
    )
except Exception:  # noqa: BLE001
    get_sv_cache = get_sv_transfer = get_davinci_logs_dir = get_transfer_ready_to_upload = None  # type: ignore

try:
    from utils.vfr_video_safety import requires_cfr_normalization  # noqa: E402
except Exception:  # noqa: BLE001

    def requires_cfr_normalization(_path: Path, **_kw: Any) -> bool:  # type: ignore
        return True


CODE_ROOT = Path.home() / "StateVerge"
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_NYC_AUTO = _SCRIPTS / "nyc_auto"
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

try:
    from nyc_long_source_policy import (  # type: ignore[import-not-found]
        LONG_SOURCE_POLICY_VERSION,
        is_valid_nyc_long_source,
    )
except Exception:  # noqa: BLE001

    LONG_SOURCE_POLICY_VERSION = "nyc_long_channel_source_policy_v1_fallback"  # type: ignore[misc]

    def is_valid_nyc_long_source(_p: Any, ffprobe_meta: Any = None, source_info: Any = None) -> dict[str, Any]:  # type: ignore[misc]
        return {"long_allowed": True, "source_type": "unknown", "orientation": "landscape", "reject_reasons": []}

DEFAULT_TOKEN = CODE_ROOT / "data" / "youtube" / "token.json"
FFMPEG = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"

SHORT_MODE_MAX_SEC = 120.0
LONG_PUBLISH_MIN_SEC = 3600.0
STALE_RUNNING_SEC = float(os.environ.get("NYC_JOB_STALE_RUNNING_SEC", "14400"))
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
MIN_CANDIDATE_BYTES = 1 * 1024 * 1024
MIN_DURATION_SEC = 5.0
MUSIC_SUBDIR = Path("04_AUDIO") / "music" / "nyc_long"
MUSIC_FALLBACK_ORDER = (
    "night_drive",
    "ambient",
    "dark_documentary",
    "calm_piano",
    "cinematic",
    "archive",
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _paths() -> tuple[Path, Path]:
    cache = Path("/Volumes/SV_CACHE")
    xfer = Path("/Volumes/SV_TRANSFER")
    try:
        if get_sv_cache:
            cache = get_sv_cache(verbose=False)
        if get_sv_transfer:
            xfer = get_sv_transfer(verbose=False)
    except Exception:
        pass
    return cache, xfer


def _primary_source_dirs(cache: Path, xfer: Path) -> list[tuple[str, Path]]:
    return [
        ("staging_input", xfer / "NYC_CUT_STAGING" / "input"),
        ("staging_sources", xfer / "NYC_CUT_STAGING" / "sources"),
        ("staging_pool", xfer / "NYC_CUT_STAGING" / "staging"),
        ("selected_clips", xfer / "selected_clips"),
        ("transfer_inbox_iphone", xfer / "00_INBOX" / "iphone"),
        ("transfer_inbox_airdrop", xfer / "00_INBOX" / "airdrop"),
        ("cache_inbox", cache / "inbox"),
        ("cache_inbox_iphone", cache / "inbox" / "iphone"),
        ("cache_inbox_airdrop", cache / "inbox" / "airdrop"),
    ]


def _legacy_source_dirs() -> list[tuple[str, Path]]:
    return [
        ("legacy_stateverge_nyc_auto", Path("/Volumes/StateVerge/NYC_AUTO")),
        ("legacy_sv_work_nyc_auto", Path("/Volumes/SV_WORK/NYC_AUTO")),
    ]


def _index_path_for_xfer(xfer: Path) -> Path:
    return xfer / "media_index" / "media_index.json"


def _load_media_index_map(xfer: Path) -> dict[str, dict[str, Any]]:
    p = _index_path_for_xfer(xfer)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    items = data.get("items")
    if not isinstance(items, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        fp = str(it.get("file_path") or "").strip()
        if not fp or "._" in Path(fp).name:
            continue
        try:
            key = str(Path(fp).expanduser().resolve())
        except Exception:
            key = fp
        out[key] = it
    return out


def _ffprobe_video_meta(path: Path) -> dict[str, Any]:
    j = _ffprobe_json(path)
    if not j:
        return {}
    fmt = j.get("format") or {}
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    w, h, dur = 0, 0, 0.0
    for st in j.get("streams") or []:
        if st.get("codec_type") != "video":
            continue
        try:
            w = int(st.get("width") or 0)
            h = int(st.get("height") or 0)
        except Exception:
            pass
        st_tags = st.get("tags") or {}
        for k, v in st_tags.items():
            tags[str(k).lower()] = str(v)
        break
    try:
        dur = float(fmt.get("duration") or 0.0)
    except Exception:
        dur = 0.0
    if dur <= 0:
        dur = _ffprobe_duration(path)
    br = 0
    try:
        br = int(float(fmt.get("bit_rate") or 0))
    except Exception:
        br = 0
    return {"w": w, "h": h, "duration": dur, "tags": tags, "bit_rate": br}


def _iphone_17_pro_max_score(tags: dict[str, str]) -> float:
    blob = " ".join(tags.values()).lower()
    if "iphone 17 pro max" in blob or "iphone17pro" in blob.replace(" ", ""):
        return 400.0
    if "iphone" in blob and "17" in blob:
        return 120.0
    return 0.0


def _landscape_score(w: int, h: int) -> float:
    if w <= 0 or h <= 0:
        return 0.0
    if w > h * 1.15:
        ar = w / max(h, 1)
        return 300.0 - abs(ar - 16.0 / 9.0) * 40.0
    return 0.0


def _score_file_candidate(
    path: Path,
    origin: str,
    origin_rank: int,
    idx_row: dict[str, Any] | None,
) -> float:
    meta = _ffprobe_video_meta(path)
    dur = float(meta.get("duration") or 0.0)
    w, h = int(meta.get("w") or 0), int(meta.get("h") or 0)
    tags = meta.get("tags") or {}
    score = 10000.0 - float(origin_rank) * 50.0
    score += _landscape_score(w, h)
    score += _iphone_17_pro_max_score(tags)
    if min(w, h) >= 2160:
        score += 200.0
    try:
        score += min(120.0, float(meta.get("bit_rate") or 0) / 50_000.0)
    except Exception:
        pass
    try:
        score += min(80.0, dur / 20.0)
    except Exception:
        pass
    try:
        score += path.stat().st_mtime / 1e10
    except OSError:
        pass
    if idx_row:
        qh = str(idx_row.get("quality_hint") or "").lower()
        if qh == "premium":
            score += 150.0
        qc = idx_row.get("quality_candidates")
        if isinstance(qc, list) and any("premium" in str(x).lower() for x in qc):
            score += 80.0
        uf = str(idx_row.get("usable_for") or "").lower()
        for tag in ("documentary", "city", "broll", "ambient", "cutaway"):
            if tag in uf:
                score += 25.0
        ori = str(idx_row.get("orientation") or "").lower()
        if ori == "landscape":
            score += 60.0
        loc = str(idx_row.get("location_name") or "")
        if loc in ("Times Square", "Midtown Manhattan"):
            score += 40.0
    return score


def _is_acceptable_video_file(path: Path) -> bool:
    try:
        if not path.is_file():
            return False
    except OSError:
        return False
    name = path.name
    if name.startswith("._") or name.startswith(".") or name == ".DS_Store":
        return False
    if path.suffix.lower() not in VIDEO_EXT:
        return False
    try:
        if path.stat().st_size < MIN_CANDIDATE_BYTES:
            return False
    except OSError:
        return False
    meta = _ffprobe_video_meta(path)
    if meta.get("duration", 0) <= MIN_DURATION_SEC:
        return False
    j = _ffprobe_json(path)
    if not j:
        return False
    for st in j.get("streams") or []:
        if st.get("codec_type") == "video":
            return True
    return False


def _classifier_script_path() -> Path:
    return CODE_ROOT / "scripts" / "classify_nyc_sources_by_time_policy.py"


def _run_source_classifier() -> None:
    sp = _classifier_script_path()
    if sp.is_file():
        subprocess.run(
            [sys.executable or "python3", str(sp)],
            timeout=7200,
            check=False,
        )


def _load_long_driving_pool(xfer: Path) -> list[dict[str, Any]]:
    p = xfer / "media_index" / "nyc_long_driving_sources.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        items = data.get("items")
        if not isinstance(items, list):
            return []
        return [x for x in items if isinstance(x, dict) and str(x.get("path") or "").strip()]
    except Exception:
        return []


def _parse_iso_dt_job(s: str) -> datetime | None:
    s = (s or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _pool_row_sort_key(row: dict[str, Any], path: Path) -> str:
    sk = str(row.get("sort_key") or "").strip()
    if sk:
        return sk
    ca = str(row.get("captured_at") or "").strip()
    if ca:
        dt = _parse_iso_dt_job(ca)
        if dt:
            local = dt.astimezone() if dt.tzinfo else dt
            return f"{local.strftime('%Y-%m-%dT%H:%M:%S.%f')}|{path}"
        return f"{ca}|{path}"
    try:
        mt = path.stat().st_mtime
        local = datetime.fromtimestamp(mt)
        return f"{local.strftime('%Y-%m-%dT%H:%M:%S.%f')}|{path}"
    except OSError:
        return f"1970-01-01T00:00:00.000000|{path}"


def _sort_long_pool_chronological(pool: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed: list[tuple[str, dict[str, Any]]] = []
    for row in pool:
        try:
            pth = Path(str(row.get("path") or "")).expanduser()
        except Exception:
            continue
        keyed.append((_pool_row_sort_key(row, pth), row))
    keyed.sort(key=lambda x: x[0])
    return [x[1] for x in keyed]


def _row_duration_for_plan(row: dict[str, Any], path: Path) -> float:
    try:
        d = float(row.get("duration_sec") or 0.0)
        if d > 0:
            return d
    except Exception:
        pass
    return float(_ffprobe_duration(path))


def _plan_long_sources_route_groups(
    sorted_rows: list[dict[str, Any]],
    requested_sec: float,
    lines: list[str],
) -> tuple[list[dict[str, Any]], str, list[str]]:
    """
    Prefer the calendar day with the largest total source duration; if it covers requested_sec,
    use only that day. Otherwise append later calendar days, then earlier days, preserving
    time order within each day (sorted_rows is already global chronological).
    """
    strategy = "prefer_largest_single_day_then_next_days"
    if not sorted_rows:
        return [], "", []

    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    day_order: list[str] = []
    for row in sorted_rows:
        d = str(row.get("route_group") or row.get("captured_date") or "").strip()
        if not d:
            d = "__nodate__"
        if d not in day_order:
            day_order.append(d)
        by_day[d].append(row)

    dated_days = [d for d in day_order if d != "__nodate__"]
    totals: dict[str, float] = {}
    for d, rs in by_day.items():
        tot = 0.0
        for row in rs:
            try:
                p = Path(str(row.get("path") or "")).expanduser()
            except Exception:
                continue
            if not p.is_file():
                continue
            tot += _row_duration_for_plan(row, p)
        totals[d] = tot

    if not dated_days:
        primary = "__nodate__"
        day_sequence = ["__nodate__"] if "__nodate__" in by_day else list(by_day.keys())
    else:
        primary = max(dated_days, key=lambda dd: (totals.get(dd, 0.0), dd))
        od = sorted(set(dated_days))
        after = [d for d in od if d > primary]
        before = [d for d in od if d < primary]
        day_sequence = [primary] + after + before
        if "__nodate__" in by_day:
            day_sequence.append("__nodate__")

    selected: list[dict[str, Any]] = []
    acc = 0.0
    for d in day_sequence:
        if d not in by_day:
            continue
        for row in by_day[d]:
            if acc >= requested_sec:
                break
            try:
                p = Path(str(row.get("path") or "")).expanduser()
            except Exception:
                continue
            if not p.is_file():
                continue
            selected.append(row)
            acc += _row_duration_for_plan(row, p)
        if acc >= requested_sec:
            break

    # Spec: after primary → later calendar days → earlier days, **re-sort globally** by sort_key
    # so concat order is true capture-time order (no afternoon-before-morning cross-day shuffle).
    keyed_sel: list[tuple[str, dict[str, Any]]] = []
    for row in selected:
        try:
            p = Path(str(row.get("path") or "")).expanduser()
        except Exception:
            continue
        if not p.is_file():
            continue
        keyed_sel.append((_pool_row_sort_key(row, p), row))
    keyed_sel.sort(key=lambda x: x[0])
    selected_sorted = [r for _, r in keyed_sel]

    route_groups_out: list[str] = []
    seen_rg: set[str] = set()
    for row in selected_sorted:
        rg = str(row.get("route_group") or row.get("captured_date") or "__nodate__").strip() or "__nodate__"
        if rg not in seen_rg:
            seen_rg.add(rg)
            route_groups_out.append(rg)

    lines.append(
        f"route_group_plan: strategy={strategy} primary={primary} "
        f"selected_clips={len(selected_sorted)} planned_duration_sec={acc:.1f} route_groups={route_groups_out} "
        f"global_sort_key_sort=applied"
    )
    return selected_sorted, primary, route_groups_out


def _discover_long_policy_sources(
    xfer: Path,
    lines: list[str],
) -> tuple[list[tuple[Path, str, dict[str, Any] | None]], list[str]]:
    """Mon–Sat 05:00–18:00 driving pool only (see classifier); chronological order, no shuffle."""
    roots_checked: list[str] = [
        str(xfer / "media_index" / "nyc_long_driving_sources.json"),
        str(xfer / "00_INBOX" / "iphone"),
        str(Path("/Volumes/SV_CACHE") / "inbox"),
    ]
    pool = _load_long_driving_pool(xfer)
    if not pool:
        lines.append("long_policy: index empty; running classifier")
        _run_source_classifier()
        pool = _load_long_driving_pool(xfer)

    pool_sorted = _sort_long_pool_chronological(pool)
    out: list[tuple[Path, str, dict[str, Any] | None]] = []
    for row in pool_sorted:
        try:
            pth = Path(str(row.get("path") or "")).expanduser()
        except Exception:
            continue
        if not pth.is_file():
            continue
        j = _ffprobe_json(pth)
        src_info: dict[str, Any] = {}
        for k in (
            "likely_source_type",
            "allow_full_duration_single_use",
            "orientation",
            "usable_for",
            "is_manual_timelapse",
        ):
            if row.get(k) is not None:
                src_info[k] = row.get(k)
        pol = is_valid_nyc_long_source({**src_info, "path": str(pth)}, ffprobe_meta=j)
        if not pol.get("long_allowed"):
            continue
        merged = {**row, "long_source_policy": pol}
        out.append((pth, "nyc_long_driving_policy", merged))

    lines.append(f"long_policy: candidates={len(out)} chronological_order=ascending")
    lines.append("NYC_LONG_ASSEMBLY_ORDER=chronological")
    return out, roots_checked


def _selected_sort_keys_from_rows(rows_sel: list[dict[str, Any]]) -> list[str]:
    keys: list[str] = []
    for row in rows_sel:
        try:
            pth = Path(str(row.get("path") or "")).expanduser()
        except Exception:
            pth = Path(".")
        keys.append(_pool_row_sort_key(row, pth))
    return keys


def _verify_selected_chronological(rows_sel: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    w: list[str] = []
    keys = _selected_sort_keys_from_rows(rows_sel)
    for i in range(len(keys) - 1):
        if keys[i] > keys[i + 1]:
            w.append("chronological_order_warning: selected_sources not non-decreasing by capture sort_key")
            return False, w
    return True, []


def _discover_automatic_sources(
    cache: Path,
    xfer: Path,
    lines: list[str],
) -> tuple[list[tuple[Path, str, dict[str, Any] | None]], list[str]]:
    """Return [(path, origin, index_row_or_none), ...] sorted by score desc; and roots checked."""
    roots_checked: list[str] = []
    index_map = _load_media_index_map(xfer)
    collected: dict[str, tuple[Path, str, dict[str, Any] | None, int]] = {}

    def add_tree(origin: str, root: Path, rank: int) -> None:
        roots_checked.append(str(root))
        if not root.is_dir():
            return
        try:
            for p in root.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                    if not _is_acceptable_video_file(p):
                        continue
                    key = str(p.resolve())
                    if key in collected:
                        continue
                    row = index_map.get(key)
                    collected[key] = (p, origin, row, rank)
                except OSError:
                    continue
        except OSError:
            return

    for rank, (origin, root) in enumerate(_primary_source_dirs(cache, xfer)):
        add_tree(origin, root, rank)

    idx_path = _index_path_for_xfer(xfer)
    roots_checked.append(str(idx_path))
    mi_rank = len(_primary_source_dirs(cache, xfer))
    if idx_path.is_file():
        try:
            idx_payload = json.loads(idx_path.read_text(encoding="utf-8", errors="replace"))
            items = idx_payload.get("items")
            if isinstance(items, list):
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    if it.get("media_type") != "video" and it.get("is_video") is not True:
                        continue
                    fp = str(it.get("file_path") or "").strip()
                    if not fp:
                        continue
                    try:
                        p = Path(fp).expanduser()
                    except Exception:
                        continue
                    if not _is_acceptable_video_file(p):
                        continue
                    key = str(p.resolve())
                    if key in collected:
                        continue
                    collected[key] = (p, "media_index", it, mi_rank)
        except Exception:
            pass

    legacy_base = len(_primary_source_dirs(cache, xfer)) + 1
    for j, (origin, root) in enumerate(_legacy_source_dirs()):
        add_tree(origin, root, legacy_base + j)

    ranked: list[tuple[float, Path, str, dict[str, Any] | None]] = []
    for _key, (p, origin, row, rk) in collected.items():
        sc = _score_file_candidate(p, origin, rk, row)
        ranked.append((sc, p, origin, row))
    ranked.sort(key=lambda x: x[0], reverse=True)
    out = [(t[1], t[2], t[3]) for t in ranked]
    lines.append(f"source_discovery: candidates={len(out)} roots_checked={len(roots_checked)}")
    return out, roots_checked


def _pick_music_file(xfer: Path, category: str) -> tuple[Path | None, str, list[str]]:
    tried: list[str] = []
    cats = [category] + [c for c in MUSIC_FALLBACK_ORDER if c != category]
    base = xfer / MUSIC_SUBDIR
    for cat in cats:
        d = base / cat
        tried.append(str(d))
        if not d.is_dir():
            continue
        cands: list[Path] = []
        try:
            for pat in ("*.m4a", "*.mp3", "*.aac", "*.wav", "*.flac", "*.aiff", "*.aif"):
                for p in d.glob(pat):
                    if p.is_file() and not p.name.startswith("._"):
                        cands.append(p)
        except OSError:
            continue
        if not cands:
            continue
        cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return cands[0], cat, tried
    return None, "", tried


def _gather_semantic_media_rows(
    *,
    source_discovery_mode: str,
    discovered_pool: list[tuple[Path, str, dict[str, Any] | None]],
    sel_meta: list[dict[str, Any]] | None,
    src: Path | None,
    xfer: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if source_discovery_mode == "automatic" and discovered_pool:
        for _p, _o, rw in discovered_pool:
            if isinstance(rw, dict):
                rows.append(rw)
        return rows
    if sel_meta:
        mp = _load_media_index_map(xfer)
        for m in sel_meta:
            im = m.get("index_meta")
            if isinstance(im, dict):
                rows.append(im)
                continue
            fp = str(m.get("file_path") or "").strip()
            if not fp:
                continue
            try:
                k = str(Path(fp).expanduser().resolve())
            except Exception:
                k = fp
            if k in mp:
                rows.append(mp[k])
            elif fp in mp:
                rows.append(mp[fp])
        if rows:
            return rows
    if src is not None and src.is_file():
        mp = _load_media_index_map(xfer)
        for key_try in (str(src.resolve()), str(src)):
            if key_try in mp:
                rows.append(mp[key_try])
                break
    return rows


def _semantic_pick_music(
    xfer: Path,
    media_rows: list[dict[str, Any]],
    log_lines: list[str],
) -> tuple[Path | None, str, dict[str, Any]]:
    try:
        import importlib.util

        sp = _SCRIPTS / "music_selector.py"
        spec = importlib.util.spec_from_file_location("music_selector_v1", sp)
        if not spec or not spec.loader:
            raise RuntimeError("music_selector_spec_missing")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sel = mod.select_nyc_long_music(media_entries=media_rows, xfer=xfer, dry_run=False)
        p = str(sel.get("selected_track_path") or "")
        pp = Path(p) if p else None
        cat = str(sel.get("selected_category") or MUSIC_FALLBACK_ORDER[0])
        if pp and pp.is_file():
            log_lines.append(f"semantic_music: path={pp} cat={cat}")
            return pp, cat, sel
        mp2, c2, _tried = _pick_music_file(xfer, cat)
        log_lines.append(f"semantic_music: index_miss_pick cat={cat}")
        return mp2, c2 if mp2 else cat, sel
    except Exception as exc:  # noqa: BLE001
        log_lines.append(f"semantic_music_exception:{exc!r}")
        fb = MUSIC_FALLBACK_ORDER[0]
        mp3, c3, _t3 = _pick_music_file(xfer, fb)
        return mp3, c3 if mp3 else fb, {"error": repr(exc), "selection_reason": "exception_fallback"}


def _resolve_music_for_job(
    xfer: Path,
    music_category: str,
    media_rows: list[dict[str, Any]],
    log_lines: list[str],
) -> tuple[Path | None, str, dict[str, Any]]:
    if str(music_category).strip().lower() != "auto":
        mp, cat, tried = _pick_music_file(xfer, str(music_category).strip())
        return mp, cat, {}
    mp_s, cat_s, sem = _semantic_pick_music(xfer, media_rows, log_lines)
    if mp_s is None:
        mp_s, cat_s, _t = _pick_music_file(xfer, MUSIC_FALLBACK_ORDER[0])
        if isinstance(sem, dict):
            sem = {
                **sem,
                "selection_reason": str(sem.get("selection_reason", "")) + ";file_pick_fallback",
            }
    return mp_s, cat_s, sem


def _mux_music_onto_video(
    video_in: Path,
    video_out: Path,
    *,
    music: Path | None,
    music_volume: float,
    target_duration: float,
    warnings: list[str],
    result: dict[str, Any],
) -> bool:
    # IMPORTANT:
    # Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
    # It can stretch 1-hour footage into multi-hour slow motion.
    # Always normalize to CFR 30/60fps before final muxing.
    if not video_in.is_file():
        return False
    dur = float(target_duration) or float(_ffprobe_duration(video_in))
    if dur <= 0:
        return False
    video_out.parent.mkdir(parents=True, exist_ok=True)
    need_cfr = bool(requires_cfr_normalization(video_in))
    timeout_mux = max(7200.0, dur * 2)

    enc_attempts: list[list[str]] = [
        ["-c:v", "h264_videotoolbox", "-b:v", "35M", "-tag:v", "avc1"],
        ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-tag:v", "avc1"],
    ]

    if music and music.is_file():
        filt_audio = (
            f"[1:a]aloop=loop=-1:start=0:size=2e+09,atrim=0:{dur},asetpts=PTS-STARTPTS,"
            f"volume={float(music_volume)}[aout]"
        )
        if need_cfr:
            fc = f"[0:v]fps=30,format=yuv420p[vout];{filt_audio}"
            for enc in enc_attempts:
                cmd = [
                    FFMPEG,
                    "-hide_banner",
                    "-nostdin",
                    "-y",
                    "-fflags",
                    "+genpts",
                    "-i",
                    str(video_in),
                    "-i",
                    str(music),
                    "-filter_complex",
                    fc,
                    "-map",
                    "[vout]",
                    "-map",
                    "[aout]",
                    *enc,
                    "-c:a",
                    "aac",
                    "-ar",
                    "48000",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    "-t",
                    str(dur),
                    str(video_out),
                ]
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_mux, check=False)
                    if r.returncode == 0 and video_out.is_file() and video_out.stat().st_size > 1024:
                        result["audio_fallback"] = "music_track"
                        result["has_music_track"] = True
                        result["has_original_audio"] = False
                        result["final_audio_codec"] = "aac"
                        result["mux_video_cfr_normalized"] = True
                        return True
                except Exception:
                    pass
        else:
            filt = filt_audio
            cmd = [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-i",
                str(video_in),
                "-i",
                str(music),
                "-filter_complex",
                filt,
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                "-t",
                str(dur),
                str(video_out),
            ]
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_mux, check=False)
                if r.returncode == 0 and video_out.is_file() and video_out.stat().st_size > 1024:
                    result["audio_fallback"] = "music_track"
                    result["has_music_track"] = True
                    result["has_original_audio"] = False
                    result["final_audio_codec"] = "aac"
                    result["mux_video_cfr_normalized"] = False
                    return True
            except Exception:
                pass
        warnings.append("music_mux_failed_trying_silent")
    warnings.append("music_not_found_using_silent_audio")
    if need_cfr:
        fc2 = "[0:v]fps=30,format=yuv420p[vout]"
        for enc in enc_attempts:
            cmd2 = [
                FFMPEG,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-fflags",
                "+genpts",
                "-i",
                str(video_in),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-filter_complex",
                fc2,
                "-map",
                "[vout]",
                "-map",
                "1:a",
                *enc,
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-b:a",
                "192k",
                "-t",
                str(dur),
                "-movflags",
                "+faststart",
                str(video_out),
            ]
            try:
                r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600, check=False)
                ok = r2.returncode == 0 and video_out.is_file() and video_out.stat().st_size > 1024
                if ok:
                    result["audio_fallback"] = "silent_aac"
                    result["has_music_track"] = False
                    result["has_original_audio"] = False
                    result["final_audio_codec"] = "aac"
                    result["mux_video_cfr_normalized"] = True
                    return ok
            except Exception:
                pass
        return False

    cmd2 = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(video_in),
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        "-b:a",
        "192k",
        "-t",
        str(dur),
        "-movflags",
        "+faststart",
        str(video_out),
    ]
    try:
        r2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=600, check=False)
        ok = r2.returncode == 0 and video_out.is_file() and video_out.stat().st_size > 1024
        if ok:
            result["audio_fallback"] = "silent_aac"
            result["has_music_track"] = False
            result["has_original_audio"] = False
            result["final_audio_codec"] = "aac"
            result["mux_video_cfr_normalized"] = False
        return ok
    except Exception:
        return False


def _ready_upload_root() -> Path:
    try:
        if get_transfer_ready_to_upload:
            return get_transfer_ready_to_upload(verbose=False)
    except Exception:
        pass
    return Path("/Volumes/SV_TRANSFER/ready_to_upload")


def _write_job(path: Path, data: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        data.setdefault("errors", []).append(f"job_json_write:{exc!r}")


def _ffprobe_duration(path: Path) -> float:
    cmd = [
        FFPROBE,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return 0.0
        return float((r.stdout or "").strip() or 0.0)
    except Exception:
        return 0.0


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0:
            return None
        return json.loads(r.stdout or "{}")
    except Exception:
        return None


def _recover_stale_running_jobs(jobs_root: Path) -> None:
    """Mark old running jobs as failed (fail-open, bounded scan)."""
    try:
        if not jobs_root.is_dir():
            return
        dirs = sorted(jobs_root.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)[:80]
    except OSError:
        return
    now = datetime.now(timezone.utc)
    for d in dirs:
        jp = d / "job.json"
        if not jp.is_file():
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if data.get("status") != "running":
            continue
        started = str(data.get("started_at") or "")
        try:
            st = datetime.fromisoformat(started.replace("Z", "+00:00"))
            if (now - st).total_seconds() < STALE_RUNNING_SEC:
                continue
        except Exception:
            continue
        data["status"] = "failed"
        data.setdefault("errors", []).append("stale_running_recovered")
        data["finished_at"] = _utc()
        _write_job(jp, data)


def _normalize_segment(
    src: Path,
    dst: Path,
    *,
    audio_volume: float,
    timeout_sec: float,
    trim_sec: float | None = None,
    strip_audio: bool = False,
) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    vf = "fps=30,scale=1920:-2,format=yuv420p"
    cmd: list[str] = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        str(src),
    ]
    if trim_sec is not None and trim_sec > 0:
        cmd += ["-t", str(float(trim_sec))]
    if strip_audio:
        cmd += [
            "-vf",
            vf,
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    else:
        cmd += [
            "-vf",
            vf,
            "-af",
            f"volume={float(audio_volume)}",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dst),
        ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 1024
    except Exception:
        return False


def _find_prenormalized(src: Path, cache: Path) -> Path | None:
    """Reuse SV_CACHE/normalized/** file if name suggests CFR normalize of this source."""
    if not src.stem:
        return None
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", src.stem)[:60]
    try:
        root = cache / "normalized"
        if not root.is_dir():
            return None
        for p in root.rglob("*.mp4"):
            if p.name.startswith("._"):
                continue
            if stem in p.name and ("cfr30" in p.name or "__cfr" in p.name):
                return p
    except OSError:
        return None
    return None


def _build_concat_list(files: list[Path], list_path: Path) -> None:
    lines: list[str] = []
    for f in files:
        p = str(f.resolve())
        p = p.replace("'", r"'\''")
        lines.append(f"file '{p}'")
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ffmpeg_concat_copy(inputs: list[Path], out: Path, timeout_sec: float) -> bool:
    if not inputs:
        return False
    work = out.parent
    lst = work / f"_concat_{uuid.uuid4().hex[:8]}.txt"
    _build_concat_list(inputs, lst)
    cmd = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        str(out),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec, check=False)
        try:
            lst.unlink(missing_ok=True)
        except OSError:
            pass
        return r.returncode == 0 and out.is_file() and out.stat().st_size > 1024
    except Exception:
        return False


def _load_media_index_videos(xfer: Path) -> list[dict[str, Any]]:
    p = xfer / "media_index" / "media_index.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return []
    items = data.get("items")
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("media_type") != "video" and it.get("is_video") is not True:
            continue
        fp = str(it.get("file_path") or "").strip()
        if not fp or "._" in Path(fp).name:
            continue
        try:
            dur = float(it.get("duration") or 0.0)
        except Exception:
            dur = 0.0
        if dur < 3.0:
            continue
        out.append(it)
    return out


def _score_ambient_item(it: dict[str, Any]) -> float:
    s = 0.0
    ori = str(it.get("orientation") or "").lower()
    if ori == "landscape":
        s += 10.0
    loc = str(it.get("location_name") or "")
    if loc in ("Times Square", "Midtown Manhattan"):
        s += 8.0
    st = str(it.get("likely_source_type") or "")
    if st in ("driving_fixed", "walking_handheld", "timelapse"):
        s += 5.0
    qh = str(it.get("quality_hint") or "").lower()
    if qh == "premium":
        s += 4.0
    uf = str(it.get("usable_for") or "").lower()
    for tag in ("documentary", "city", "cutaway", "broll", "ambient"):
        if tag in uf:
            s += 2.0
    try:
        s += min(20.0, float(it.get("duration") or 0.0) / 30.0)
    except Exception:
        pass
    try:
        s += float(it.get("modified_time") or 0.0) / 1e12
    except Exception:
        pass
    return s


def _select_long_sources(
    xfer: Path,
    cache: Path,
    target_sec: float,
    lines: list[str],
) -> tuple[list[Path], list[dict[str, Any]], float, list[str]]:
    """Returns (source_paths, selected_meta, total_available_sec, warnings)."""
    warnings: list[str] = []
    items = _load_media_index_videos(xfer)
    items.sort(key=_score_ambient_item, reverse=True)
    if not items:
        # fallback: iphone inbox glob (still normalize; never concat MOV raw)
        inbox = xfer / "00_INBOX" / "iphone"
        found: list[Path] = []
        if inbox.is_dir():
            try:
                for pat in ("*.mov", "*.mp4", "*.m4v"):
                    for p in inbox.glob(pat):
                        if p.name.startswith("._"):
                            continue
                        found.append(p)
            except OSError:
                pass
        found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        sources = []
        meta = []
        for p in found[:400]:
            jf = _ffprobe_json(p)
            pol_f = is_valid_nyc_long_source(p, jf)
            if not pol_f.get("long_allowed"):
                continue
            sources.append(p)
            meta.append({"file_path": str(p), "duration": _ffprobe_duration(p), "long_source_policy": pol_f})
            if len(sources) >= 200:
                break
        total_avail = sum(_ffprobe_duration(p) for p in sources)
        lines.append(f"long_mode: media_index empty; inbox_fallback count={len(sources)} total_avail={total_avail:.1f}s")
        return sources, meta, total_avail, warnings

    sources: list[Path] = []
    meta_out: list[dict[str, Any]] = []
    total_avail = 0.0
    seen: set[str] = set()
    for it in items:
        fp = str(it.get("file_path") or "")
        try:
            p = Path(fp).expanduser()
        except Exception:
            continue
        key = str(p)
        if key in seen:
            continue
        if not p.is_file():
            continue
        jidx = _ffprobe_json(p)
        src_info = {k: it.get(k) for k in ("likely_source_type", "allow_full_duration_single_use", "orientation", "is_manual_timelapse", "usable_for") if it.get(k) is not None}
        pol_i = is_valid_nyc_long_source({**src_info, "path": str(p)}, ffprobe_meta=jidx)
        if not pol_i.get("long_allowed"):
            continue
        seen.add(key)
        sources.append(p)
        meta_out.append(
            {
                "file_path": key,
                "duration": float(it.get("duration") or _ffprobe_duration(p)),
                "location_name": it.get("location_name"),
                "likely_source_type": it.get("likely_source_type"),
                "long_source_policy": pol_i,
            }
        )
        total_avail += float(meta_out[-1]["duration"])
        if total_avail >= target_sec * 1.5 and len(sources) >= 40:
            break

    lines.append(
        f"long_mode: selected {len(sources)} index sources, total_available_duration={total_avail:.1f}s "
        f"target={target_sec:.1f}s"
    )
    if total_avail < target_sec:
        warnings.append("source_pool_shorter_than_requested")
    return sources, meta_out, total_avail, warnings


def _run_publish_gate(out_path: Path) -> dict[str, Any]:
    try:
        from davinci_publish_gate import (  # noqa: E402
            davinci_bypass_enabled,
            evaluate,
            load_manifest_resolved_paths,
        )

        ready_root = _ready_upload_root()
        log_dir = get_davinci_logs_dir() if get_davinci_logs_dir else CODE_ROOT / "logs" / "davinci"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        bypass_paths = load_manifest_resolved_paths(
            log_dir / "davinci_bypass_manifest.csv",
            "target_path",
            ok_statuses=frozenset({"ok"}),
        )
        export_paths = load_manifest_resolved_paths(
            log_dir / "davinci_export_manifest.csv",
            "ready_to_upload_path",
            ok_statuses=frozenset({"ok"}),
        )
        ev = evaluate(
            out_path,
            ready_root,
            bypass_mode=davinci_bypass_enabled(),
            bypass_manifest_paths=bypass_paths,
            export_manifest_paths=export_paths,
        )
        return {"passed": bool(ev.get("pass")), "checks": ev.get("checks"), "error": ev.get("error")}
    except Exception as exc:  # noqa: BLE001
        return {"passed": False, "error": repr(exc)}


def _nyc_long_clips_root_resolved() -> Path:
    if get_transfer_ready_to_upload:
        return (get_transfer_ready_to_upload(verbose=False) / "nyc_long_clips").resolve()
    return Path("/Volumes/SV_TRANSFER/ready_to_upload/nyc_long_clips").resolve()


def _validate_repaired_single_source_long_for_dedupe(
    out_path: Path,
    *,
    allow_single_source_long: bool,
    source_path: Path | None,
    no_vocals_wav: Path | None,
    source_duration_sec: float,
    expected_fps: int,
    video_copy_used: bool,
    cfr_normalized: bool,
    selected_paths: list[str],
) -> tuple[bool, str, dict[str, Any]]:
    """Pre-flight for ``repaired_single_source_long`` dedupe exception (defense in depth)."""
    detail: dict[str, Any] = {
        "video_type": "repaired_single_source_long",
        "source_asset_count": len([x for x in selected_paths if x]),
    }
    if video_copy_used:
        return False, "video_copy_used_must_be_false", detail
    if not cfr_normalized:
        return False, "cfr_normalized_must_be_true", detail
    if len([x for x in selected_paths if x]) != 1:
        return False, "single_source_requires_one_asset_path", detail
    if not source_path or not source_path.is_file():
        return False, "source_path_missing", detail
    if not no_vocals_wav or not no_vocals_wav.is_file():
        return False, "no_vocals_wav_missing", detail
    if not out_path.is_file():
        return False, "output_missing", detail
    try:
        root_r = _nyc_long_clips_root_resolved()
        out_r = out_path.resolve()
        if root_r not in out_r.parents and out_r != root_r:
            return False, "output_not_under_nyc_long_clips", detail
    except OSError as exc:
        return False, f"path_resolve_error:{exc}", detail
    try:
        from utils.davinci_ffprobe import ffprobe_json, stream_summary  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        return False, f"ffprobe_helpers:{exc!r}", detail
    data, err = ffprobe_json(out_path, timeout_sec=120.0)
    if err or not data:
        return False, f"ffprobe_output_failed:{err}", detail
    has_v, has_a, odur = stream_summary(data)
    if not has_v:
        return False, "output_missing_video_stream", detail
    if not has_a:
        return False, "output_missing_audio_stream", detail
    if odur < 3300.0:
        return False, "long_duration_too_short", detail
    if odur < 3600.0:
        detail["duration_notice"] = "prefer_output_duration_gte_3600s"
    sd = float(source_duration_sec or 0.0)
    if sd <= 0:
        return False, "source_duration_invalid", detail
    delta = abs(odur - sd)
    if delta > max(2.0, sd * 0.01):
        return False, "duration_mismatch_source_vs_output", detail
    vs = None
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            vs = s
            break
    if not vs:
        return False, "no_video_stream", detail
    exp = f"{int(expected_fps)}/1"
    afr = str(vs.get("avg_frame_rate") or "")
    rfr = str(vs.get("r_frame_rate") or "")
    if afr != exp:
        return False, f"avg_frame_rate_want_{exp}_got_{afr}", detail
    if rfr != afr:
        return False, f"cfr_mismatch_r_frame_rate_{rfr}_vs_avg_{afr}", detail
    detail["output_duration_sec"] = odur
    detail["avg_frame_rate"] = afr
    return True, "ok", detail


def _run_dedupe_check(
    out_path: Path,
    *,
    title: str,
    eff_dur: float,
    selected_paths: list[str],
    single_take_long: bool = False,
    allow_single_source_long: bool = False,
    repaired_single_source_ctx: dict[str, Any] | None = None,
    requested_duration_sec: float | None = None,
) -> dict[str, Any]:
    try:
        from stateverge_publish_dedupe import (  # noqa: E402
            PublishCandidate,
            build_content_id,
            check_publish_allowed,
            first_3_clips_hash,
            sha256_quick_file,
            title_hash,
        )
    except Exception as exc:  # noqa: BLE001
        return {"skipped": True, "duplicate_uploaded": False, "error": repr(exc)}

    try:
        vh = sha256_quick_file(out_path)
    except Exception as exc:  # noqa: BLE001
        return {"skipped": True, "duplicate_uploaded": False, "error": repr(exc)}
    assets = [str(x) for x in selected_paths[:30] if x]
    first3 = first_3_clips_hash(assets)
    ah = vh
    cid = build_content_id(
        video_hash=vh, audio_hash=ah, duration_seconds=float(eff_dur), first3_hash=first3
    )
    is_long = eff_dur >= 600.0
    src_total_dur = 0.0
    for p in assets:
        try:
            src_total_dur += float(_ffprobe_duration(Path(p).expanduser()))
        except OSError:
            continue
    rep_ok = False
    rep_reason = ""
    rep_detail: dict[str, Any] = {}
    ctx = repaired_single_source_ctx if isinstance(repaired_single_source_ctx, dict) else {}
    if is_long and len(set(assets)) == 1 and ctx:
        sp_raw = ctx.get("source_path")
        nw_raw = ctx.get("no_vocals_wav")
        try:
            sp = Path(str(sp_raw)).expanduser().resolve() if sp_raw else None
        except OSError:
            sp = None
        try:
            nw = Path(str(nw_raw)).expanduser().resolve() if nw_raw else None
        except OSError:
            nw = None
        rep_ok, rep_reason, rep_detail = _validate_repaired_single_source_long_for_dedupe(
            out_path,
            allow_single_source_long=True,  # CFR repair path; duration policy also applies separately
            source_path=sp,
            no_vocals_wav=nw,
            source_duration_sec=float(ctx.get("source_duration_sec") or 0.0),
            expected_fps=int(ctx.get("expected_fps") or 30),
            video_copy_used=bool(ctx.get("video_copy_used")),
            cfr_normalized=bool(ctx.get("cfr_normalized")),
            selected_paths=assets,
        )
    cand = PublishCandidate(
        video_path=out_path,
        audio_path=None,
        title=title or out_path.stem,
        channel="NYC",
        publish_date=date.today().isoformat(),
        source_assets=assets,
        is_long=is_long,
        is_short=False,
        derived_from_long=None,
        duration_seconds=float(eff_dur),
        video_hash=vh,
        audio_hash=ah,
        first3_hash=first3,
        content_id=cid,
        title_hash=title_hash(title or out_path.stem),
        requested_duration_sec=requested_duration_sec,
        single_take_long=bool(single_take_long and is_long),
        repaired_single_source_long=bool(rep_ok and is_long),
    )
    allowed, reason, pol = check_publish_allowed(
        cand,
        source_total_duration_sec=(src_total_dur if src_total_dur > 0.05 else None),
    )
    video_type = (
        "repaired_single_source_long"
        if (is_long and len(assets) == 1)
        else ("long" if is_long else "other")
    )
    dedupe_exception = "repaired_single_source_long" if rep_ok else ""
    out: dict[str, Any] = {
        "skipped": False,
        "duplicate_uploaded": not allowed and reason in ("video_hash", "content_id", "title_duplicate", "same_assets"),
        "allowed": allowed,
        "reason": reason,
        "content_id": cid,
        "single_take_long": bool(cand.single_take_long),
        "video_type": video_type,
        "single_source_long_allowed": bool(rep_ok),
        "single_source_long_reason": rep_reason or ("ok" if rep_ok else ""),
        "source_asset_count": len(assets),
        "source_total_duration_sec": round(src_total_dur, 3),
        "dedupe_exception": dedupe_exception,
        "repaired_single_source_detail": rep_detail,
    }
    if pol:
        out.update(pol)
    return out


def _verify_nyc_channel() -> tuple[bool, dict[str, Any]]:
    info: dict[str, Any] = {"channels": [], "reason": ""}
    token_path = Path(os.environ.get("STATEVERGE_YOUTUBE_TOKEN", str(DEFAULT_TOKEN))).expanduser()
    expected_id = (os.environ.get("STATEVERGE_NYC_YOUTUBE_CHANNEL_ID") or "").strip()
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        info["reason"] = "google_libs_missing"
        return False, info
    if not token_path.is_file():
        info["reason"] = "token_missing"
        return False, info
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nyc_auto"))
        from youtube_scopes import SCOPES  # type: ignore
    except Exception:
        SCOPES = [  # noqa: N806
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        if not creds.valid:
            info["reason"] = "credentials_invalid"
            return False, info
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = youtube.channels().list(part="snippet", mine=True).execute()
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"api_error:{exc!r}"
        return False, info
    for it in resp.get("items") or []:
        sn = it.get("snippet") or {}
        info["channels"].append(
            {"id": it.get("id") or "", "title": sn.get("title") or "", "customUrl": sn.get("customUrl") or ""}
        )
    if not info["channels"]:
        info["reason"] = "no_channel"
        return False, info
    ch0 = info["channels"][0]
    cid = str(ch0.get("id") or "")
    title = str(ch0.get("title") or "")
    custom = str(ch0.get("customUrl") or "")
    if expected_id and cid == expected_id:
        info["reason"] = "matched_channel_id"
        return True, info
    if "nyc" in f"{title} {custom}".lower():
        info["reason"] = "matched_nyc_heuristic_title_or_handle"
        return True, info
    info["reason"] = "nyc_channel_not_confirmed"
    return False, info


def _upload_video(
    video_path: Path,
    *,
    title: str,
    description: str,
    tags: list[str],
    privacy: str,
    allow_public: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "video_id": "", "error": "", "privacy_used": privacy}
    priv = (privacy or "unlisted").strip().lower()
    if priv not in ("public", "private", "unlisted"):
        priv = "unlisted"
    if priv == "public" and not allow_public:
        priv = "unlisted"
        out["privacy_used"] = "unlisted"
        out["public_downgraded"] = True
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        out["error"] = repr(exc)
        return out
    token_path = Path(os.environ.get("STATEVERGE_YOUTUBE_TOKEN", str(DEFAULT_TOKEN))).expanduser()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nyc_auto"))
        from youtube_scopes import SCOPES  # type: ignore
    except Exception:
        SCOPES = [  # noqa: N806
            "https://www.googleapis.com/auth/youtube.upload",
            "https://www.googleapis.com/auth/youtube.readonly",
        ]
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
        return out
    body = {
        "snippet": {"title": title[:100], "description": description, "tags": tags[:30], "categoryId": "22"},
        "status": {"privacyStatus": priv, "selfDeclaredMadeForKids": False},
    }
    try:
        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/mp4")
        try:
            req = youtube.videos().insert(part="snippet,status", body=body, media_body=media, notifySubscribers=False)
        except TypeError:
            req = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
        out["ok"] = bool(resp and resp.get("id"))
        out["video_id"] = str(resp.get("id") or "")
    except Exception as exc:  # noqa: BLE001
        out["error"] = repr(exc)
    return out


def _clamp_duration(x: float | None) -> float:
    try:
        v = float(x)
    except Exception:
        v = 3600.0
    return max(30.0, min(7200.0, v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-video", type=Path, default=None)
    ap.add_argument("--cut-start", default="00:00:00")
    ap.add_argument("--duration-seconds", type=float, default=3600.0)
    ap.add_argument("--audio-volume", type=float, default=0.6)
    ap.add_argument("--audio-mode", default="music", choices=["music", "original"])
    ap.add_argument("--music-category", default="auto")
    ap.add_argument("--music-volume", type=float, default=0.32)
    ap.add_argument("--original-volume", type=float, default=0.0)
    ap.add_argument("--channel", default="NYC")
    ap.add_argument("--privacy-status", default="unlisted")
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--upload", action="store_true")
    ap.add_argument("--job-id", default="")
    args = ap.parse_args()

    _rq = _clamp_duration(args.duration_seconds)
    _short = _rq <= SHORT_MODE_MAX_SEC
    if not _short and _rq < LONG_PUBLISH_MIN_SEC:
        args.duration_seconds = LONG_PUBLISH_MIN_SEC
    if not _short:
        args.audio_mode = "music"
        args.original_volume = 0.0

    job_id = (args.job_id or "").strip() or uuid.uuid4().hex[:16]
    cache, xfer = _paths()
    jobs_root = cache / "jobs"
    _recover_stale_running_jobs(jobs_root)

    requested_sec = _clamp_duration(args.duration_seconds)
    short_mode = requested_sec <= SHORT_MODE_MAX_SEC

    job_dir = jobs_root / job_id
    renders_dir = cache / "renders" / job_id
    pack_dir = xfer / "publish_pack" / job_id

    job_path = job_dir / "job.json"
    warnings: list[str] = []
    errors: list[str] = []
    semantic_music_selection: dict[str, Any] = {}
    effective_music_category: str = str(args.music_category)
    log_lines: list[str] = [
        f"job_start job_id={job_id} requested_duration_sec={requested_sec} short_mode={short_mode}",
    ]

    src_arg = args.source_video.expanduser() if args.source_video else None
    src: Path | None = None
    source_discovery_mode = "manual"
    roots_checked_early: list[str] = []
    discovered_full: list[tuple[Path, str, dict[str, Any] | None]] = []
    discovered_pool: list[tuple[Path, str, dict[str, Any] | None]] = []
    route_primary_early = ""
    route_groups_early: list[str] = []

    strip_audio_encode = args.audio_mode == "music" and float(args.original_volume) <= 0.001

    if src_arg and Path(src_arg).is_file():
        src = Path(src_arg)
        source_discovery_mode = "manual"
        if not short_mode:
            jman = _ffprobe_json(src)
            pol_man = is_valid_nyc_long_source(src, jman)
            if not pol_man.get("long_allowed") or str(pol_man.get("source_type") or "") == "walking":
                br = "portrait_source_not_allowed_for_long_channel"
                if str(pol_man.get("source_type") or "") == "walking":
                    br = "walking_source_selected_for_long_channel"
                elif pol_man.get("reject_reasons"):
                    br = str(pol_man["reject_reasons"][0])
                job = {
                    "job_id": job_id,
                    "type": "nyc_cut_upload",
                    "status": "blocked",
                    "reason": br,
                    "created_at": _utc(),
                    "started_at": _utc(),
                    "finished_at": _utc(),
                    "request": {},
                    "input": {},
                    "output": {},
                    "result": {
                        "block_reason": br,
                        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
                        "policy": pol_man,
                        "source_video": str(src),
                    },
                    "warnings": warnings,
                    "errors": errors + [br],
                }
                _write_job(job_path, job)
                return 8
    else:
        discovered_full, roots_checked_early = _discover_long_policy_sources(xfer, log_lines)
        source_discovery_mode = "automatic"
        if not discovered_full:
            job = {
                "job_id": job_id,
                "type": "nyc_cut_upload",
                "status": "blocked",
                "reason": "no_long_driving_sources",
                "created_at": _utc(),
                "started_at": _utc(),
                "finished_at": _utc(),
                "request": {},
                "input": {},
                "output": {},
                "result": {
                    "source_discovery_mode": "automatic",
                    "source_policy": "weekday_5_to_18_driving_only",
                    "source_policy_index": str(xfer / "media_index" / "nyc_long_driving_sources.json"),
                    "source_roots_checked": roots_checked_early,
                    "candidate_count": 0,
                    "selected_sources": [],
                    "selected_source_origins": [],
                    "no_source_reason": "no_long_driving_sources",
                    "hint": (
                        "没有找到星期一到星期六 05:00–18:00 的驾驶视频候选（含 landscape、非 timelapse、duration>30s）。"
                        "请运行 classify_nyc_sources_by_time_policy.py 或刷新策略索引。"
                    ),
                },
                "warnings": warnings,
                "errors": errors,
            }
            _write_job(job_path, job)
            return 8
        planned_rows, route_primary_early, route_groups_early = _plan_long_sources_route_groups(
            [t[2] for t in discovered_full if t[2] is not None],
            float(requested_sec),
            log_lines,
        )
        if short_mode:
            discovered_pool = list(discovered_full)
            src = discovered_full[0][0]
        else:
            discovered_pool = []
            for row in planned_rows:
                try:
                    pth = Path(str(row.get("path") or "")).expanduser()
                except Exception:
                    continue
                if not pth.is_file():
                    continue
                discovered_pool.append((pth, "nyc_long_driving_policy", row))
            if not discovered_pool:
                discovered_pool = list(discovered_full)
                warnings.append("route_group_plan_yielded_no_files_using_full_chronological_pool")
            src = discovered_pool[0][0]
        if not short_mode:
            pol_types: set[str] = set()
            pol_oris: set[str] = set()
            for _pp, _oo, rw in discovered_pool:
                if isinstance(rw, dict):
                    lp = rw.get("long_source_policy")
                    if isinstance(lp, dict):
                        pol_types.add(str(lp.get("source_type") or "unknown"))
                        pol_oris.add(str(lp.get("orientation") or ""))
            br_auto = ""
            if "portrait" in pol_oris:
                br_auto = "portrait_source_not_allowed_for_long_channel"
            elif "walking" in pol_types and len(pol_types) > 1:
                br_auto = "mixed_driving_walking_not_allowed_for_long_channel"
            elif pol_types == {"walking"}:
                br_auto = "walking_source_selected_for_long_channel"
            elif "ferry" in pol_types and pol_types != {"ferry"}:
                br_auto = "mixed_ferry_driving_not_allowed_for_long_channel"
            if br_auto:
                blk2: dict[str, Any] = {
                    "job_id": job_id,
                    "type": "nyc_cut_upload",
                    "status": "blocked",
                    "reason": br_auto,
                    "created_at": _utc(),
                    "started_at": _utc(),
                    "finished_at": _utc(),
                    "request": {},
                    "input": {},
                    "output": {},
                    "result": {
                        "source_discovery_mode": "automatic",
                        "block_reason": br_auto,
                        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
                        "source_type_set": sorted(pol_types),
                        "orientation_set": sorted(pol_oris),
                        "candidate_count": len(discovered_pool),
                    },
                    "warnings": warnings,
                    "errors": errors + [br_auto],
                }
                _write_job(job_path, blk2)
                return 10
        ch_pool_ok, ch_pool_w = _verify_selected_chronological([t[2] for t in discovered_pool if t[2]])
        if not ch_pool_ok:
            warnings.extend(ch_pool_w)
            blk_job: dict[str, Any] = {
                "job_id": job_id,
                "type": "nyc_cut_upload",
                "status": "blocked",
                "reason": "chronological_order_violation",
                "created_at": _utc(),
                "started_at": _utc(),
                "finished_at": _utc(),
                "request": {},
                "input": {},
                "output": {},
                "result": {
                    "source_discovery_mode": "automatic",
                    "source_policy": "weekday_5_to_18_driving_only",
                    "source_policy_index": str(xfer / "media_index" / "nyc_long_driving_sources.json"),
                    "assembly_order": "chronological",
                    "chronological_sort": False,
                    "selected_sources_ordered": False,
                    "chronological_order_warning": ch_pool_w,
                    "candidate_count": len(discovered_full),
                    "hint": "Long pool sort order failed validation; re-run classifier.",
                },
                "warnings": warnings,
                "errors": errors + ["chronological_order_violation"],
            }
            _write_job(job_path, blk_job)
            return 9

    job: dict[str, Any] = {
        "job_id": job_id,
        "type": "nyc_cut_upload",
        "status": "running",
        "created_at": _utc(),
        "started_at": _utc(),
        "finished_at": "",
        "request": {
            "source_video": str(src) if src else "",
            "cut_start": args.cut_start,
            "duration_seconds": float(requested_sec),
            "audio_volume": float(args.audio_volume),
            "audio_mode": str(args.audio_mode),
            "music_category": str(args.music_category),
            "music_volume": float(args.music_volume),
            "original_volume": float(args.original_volume),
            "channel": args.channel,
            "privacy_status": args.privacy_status,
            "allow_public": bool(args.allow_public),
            "upload": bool(args.upload),
        },
        "input": {
            "source_video": str(src) if src else "",
            "cut_start": args.cut_start,
            "duration_seconds": float(requested_sec),
            "audio_volume": float(args.audio_volume),
            "audio_mode": str(args.audio_mode),
            "music_category": str(args.music_category),
            "music_volume": float(args.music_volume),
            "original_volume": float(args.original_volume),
            "channel": args.channel,
            "privacy_status": args.privacy_status,
            "allow_public": bool(args.allow_public),
            "upload": bool(args.upload),
        },
        "output": {},
        "result": {},
        "warnings": warnings,
        "errors": errors,
    }
    _write_job(job_path, job)

    result: dict[str, Any] = {
        "requested_duration_sec": float(requested_sec),
        "effective_duration_sec": 0.0,
        "duration_sec": 0.0,
        "selected_sources": [],
        "selected_source_origins": [],
        "source_discovery_mode": source_discovery_mode,
        "source_roots_checked": list(roots_checked_early),
        "candidate_count": len(discovered_full) if source_discovery_mode == "automatic" else 1,
        "source_policy": (
            "weekday_5_to_18_driving_only" if source_discovery_mode == "automatic" else "manual_override"
        ),
        "source_policy_index": (
            str(xfer / "media_index" / "nyc_long_driving_sources.json")
            if source_discovery_mode == "automatic"
            else ""
        ),
        "assembly_order": "chronological" if source_discovery_mode == "automatic" else "manual",
        "chronological_sort": source_discovery_mode == "automatic",
        "route_group_strategy": (
            "prefer_largest_single_day_then_next_days" if source_discovery_mode == "automatic" else ""
        ),
        "primary_route_group": route_primary_early if source_discovery_mode == "automatic" else "",
        "selected_route_groups": list(route_groups_early) if source_discovery_mode == "automatic" else [],
        "first_selected_captured_at": "",
        "last_selected_captured_at": "",
        "selected_source_count": 0,
        "selected_total_source_duration_sec": 0.0,
        "selected_sources_ordered": source_discovery_mode == "automatic",
        "selected_sources_ordered_list": [],
        "normalized_segments": [],
        "publish_gate": {},
        "dedupe": {},
        "upload_attempted": False,
        "lines": log_lines,
        "ready_to_upload": False,
        "youtube_url": "",
        "uploaded": False,
        "audio_mode": str(args.audio_mode),
        "music_category": str(effective_music_category),
        "music_path": "",
        "semantic_music_selection": semantic_music_selection,
        "music_channel": "nyc_long",
        "music_selection_mode": (
            "semantic_v1"
            if str(args.music_category).strip().lower() == "auto" and str(args.audio_mode) == "music"
            else "explicit_category"
        ),
        "music_track": "",
        "music_selection_reason": "",
        "music_volume": float(args.music_volume),
        "original_volume": float(args.original_volume),
        "audio_fallback": "",
        "has_music_track": False,
        "has_original_audio": False,
        "final_audio_codec": "",
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "source_type_set": [],
        "orientation_set": [],
        "rejected_sources": [],
        "selected_sources_ordered_paths": [],
    }

    ready_root = _ready_upload_root()
    if short_mode:
        if not src or not Path(src).is_file():
            job["status"] = "failed"
            errors.append("source_missing")
            result["lines"].append("short_mode: source_missing")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 2
        try:
            subdir = ready_root / "nyc_80s_clips"
            subdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            job["status"] = "failed"
            errors.append(f"mkdir:{exc!r}")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 3

        eff_sec = float(requested_sec)
        out_name = f"nyc_upload_clip_{int(eff_sec)}s_{_utc_compact()}.mp4"
        enc_path = renders_dir / out_name
        final_ready = subdir / out_name
        renders_dir.mkdir(parents=True, exist_ok=True)

        vf = "fps=30,scale=1920:-2,format=yuv420p"
        cmd = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            str(args.cut_start),
            "-i",
            str(src),
            "-t",
            str(eff_sec),
            "-vf",
            vf,
        ]
        if strip_audio_encode:
            cmd += [
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-movflags",
                "+faststart",
                str(enc_path),
            ]
        else:
            cmd += [
                "-af",
                f"volume={float(args.audio_volume)}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(enc_path),
            ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=max(3600, eff_sec * 3 + 120), check=False)
            if r.returncode != 0 or not enc_path.is_file() or enc_path.stat().st_size < 1024:
                job["status"] = "failed"
                errors.append(f"ffmpeg_rc={r.returncode}")
                if r.stderr:
                    errors.append((r.stderr or "")[-4000:])
                job["finished_at"] = _utc()
                job["result"] = result
                _write_job(job_path, job)
                return 4
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            errors.append(repr(exc))
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 4

        if args.audio_mode == "music":
            rows_sm = _gather_semantic_media_rows(
                source_discovery_mode=source_discovery_mode,
                discovered_pool=discovered_pool,
                sel_meta=None,
                src=src,
                xfer=xfer,
            )
            music_p, effective_music_category, semantic_music_selection = _resolve_music_for_job(
                xfer, args.music_category, rows_sm, log_lines
            )
            result["music_path"] = str(music_p) if music_p else ""
            result["music_category"] = effective_music_category
            result["semantic_music_selection"] = semantic_music_selection
            result["music_channel"] = "nyc_long"
            result["music_selection_mode"] = (
                "semantic_v1"
                if str(args.music_category).strip().lower() == "auto" and str(args.audio_mode) == "music"
                else "explicit_category"
            )
            result["music_track"] = music_p.name if music_p else ""
            result["music_selection_reason"] = str((semantic_music_selection or {}).get("selection_reason") or "")
            mux_out = enc_path.parent / f"{enc_path.stem}_audio.mp4"
            if _mux_music_onto_video(
                enc_path,
                mux_out,
                music=music_p,
                music_volume=float(args.music_volume),
                target_duration=eff_sec,
                warnings=warnings,
                result=result,
            ):
                try:
                    enc_path.unlink(missing_ok=True)
                except OSError:
                    pass
                enc_path = mux_out
        else:
            result["audio_fallback"] = "original_muxed"
            result["has_original_audio"] = True
            result["has_music_track"] = False
            result["final_audio_codec"] = "aac"

        try:
            shutil.copy2(enc_path, final_ready)
        except OSError as exc:
            job["status"] = "failed"
            errors.append(f"copy_ready:{exc!r}")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 5

        out_final = final_ready
        so = str(src) if src else ""
        ogn = discovered_pool[0][1] if source_discovery_mode == "automatic" and discovered_pool else "manual_override"
        sel0: dict[str, Any] = {"path": so, "file_path": so, "duration": eff_sec}
        if source_discovery_mode == "automatic" and discovered_pool:
            row0 = discovered_pool[0][2]
            if isinstance(row0, dict):
                sel0["index_meta"] = row0
                for k in ("captured_at", "sort_key", "route_group", "captured_date", "sequence_index"):
                    if row0.get(k) is not None and row0.get(k) != "":
                        sel0[k] = row0.get(k)
        result["selected_sources"] = [sel0]
        result["selected_source_origins"] = [ogn]
        result["normalized_segments"] = [{"path": str(enc_path), "duration": eff_sec, "from_cache": False}]
        result["effective_duration_sec"] = eff_sec
        result["duration_sec"] = _ffprobe_duration(out_final)
        if abs(result["duration_sec"] - eff_sec) > 2.0:
            result["effective_duration_sec"] = float(result["duration_sec"])
    else:
        # Long ambient: normalize segments then concat
        try:
            subdir = ready_root / "nyc_long_clips"
            subdir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            job["status"] = "failed"
            errors.append(f"mkdir:{exc!r}")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 3

        if source_discovery_mode == "manual":
            assert src is not None
            sources = [src]
            polm = is_valid_nyc_long_source(src, _ffprobe_json(src))
            sel_meta = [
                {
                    "file_path": str(src),
                    "duration": _ffprobe_duration(src),
                    "origin": "manual_override",
                    "long_source_policy": polm,
                }
            ]
            pool_dur = float(sel_meta[0]["duration"])
            wpool: list[str] = []
        else:
            sources = [t[0] for t in discovered_pool]
            sel_meta = []
            for p, origin, row in discovered_pool:
                dur = _ffprobe_duration(p)
                m: dict[str, Any] = {"file_path": str(p), "duration": dur, "origin": origin}
                if row:
                    for k in ("location_name", "likely_source_type", "quality_hint", "usable_for", "orientation"):
                        if row.get(k) is not None:
                            m[k] = row.get(k)
                    lp = row.get("long_source_policy")
                    if isinstance(lp, dict):
                        m["long_source_policy"] = lp
                sel_meta.append(m)
            pool_dur = sum(float(m["duration"]) for m in sel_meta)
            wpool = []
        warnings.extend(wpool)
        if pool_dur < requested_sec:
            warnings.append("source_pool_shorter_than_requested")

        work = renders_dir / "_work"
        work.mkdir(parents=True, exist_ok=True)
        norm_files: list[Path] = []
        norm_meta: list[dict[str, Any]] = []
        acc = 0.0
        tmo_norm = max(600.0, min(3600.0, requested_sec / 10 + 120))
        seg_audio_vol = float(args.audio_volume) if args.audio_mode == "original" else float(args.original_volume)

        for i, spath in enumerate(sources):
            if acc >= requested_sec:
                break
            remain = requested_sec - acc
            if remain <= 0.05:
                break
            pre = _find_prenormalized(spath, cache)
            seg_work = work / f"seg_{i:04d}.mp4"
            use_path: Path | None = None
            src_for_dur = pre if pre and pre.is_file() else spath
            dur_full = _ffprobe_duration(src_for_dur)
            if dur_full <= 0:
                continue
            need = min(dur_full, remain)
            partial = need < dur_full - 0.05

            src_norm_in = pre if pre and pre.is_file() else spath
            ok = _normalize_segment(
                src_norm_in,
                seg_work,
                audio_volume=seg_audio_vol,
                timeout_sec=tmo_norm,
                trim_sec=need if partial else None,
                strip_audio=strip_audio_encode,
            )
            use_path = seg_work if ok else None

            if use_path is None or not use_path.is_file():
                continue
            d = _ffprobe_duration(use_path)
            if d < 0.5:
                continue
            norm_files.append(use_path)
            meta_row = sel_meta[i] if i < len(sel_meta) else {"file_path": str(spath), "duration": dur_full}
            norm_meta.append(
                {
                    "path": str(use_path),
                    "duration": d,
                    "source": str(spath),
                    "from_prenormalized": bool(pre and pre.is_file()),
                    "index_meta": meta_row,
                }
            )
            acc += d
            log_lines.append(f"segment {i}: source={spath.name} dur={d:.2f}s acc={acc:.2f}s")

        if not norm_files:
            job["status"] = "failed"
            errors.append("no_segments_normalized")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 4

        concat_mid = work / "_concat_mid.mp4"
        if not _ffmpeg_concat_copy(norm_files, concat_mid, timeout_sec=max(7200.0, requested_sec * 2)):
            job["status"] = "failed"
            errors.append("concat_failed")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 4

        eff_sec = min(acc, requested_sec)
        out_name = f"nyc_upload_clip_{int(round(eff_sec))}s_{_utc_compact()}.mp4"
        enc_path = renders_dir / out_name
        # Trim to exact effective if concat overshot
        cmd_trim = [
            FFMPEG,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-i",
            str(concat_mid),
            "-t",
            str(eff_sec),
            "-c",
            "copy",
            str(enc_path),
        ]
        try:
            r = subprocess.run(cmd_trim, capture_output=True, text=True, timeout=600, check=False)
            if r.returncode != 0:
                shutil.copy2(concat_mid, enc_path)
        except Exception:
            shutil.copy2(concat_mid, enc_path)

        if args.audio_mode == "music":
            rows_sm = _gather_semantic_media_rows(
                source_discovery_mode=source_discovery_mode,
                discovered_pool=discovered_pool,
                sel_meta=sel_meta,
                src=src,
                xfer=xfer,
            )
            music_p, effective_music_category, semantic_music_selection = _resolve_music_for_job(
                xfer, args.music_category, rows_sm, log_lines
            )
            result["music_path"] = str(music_p) if music_p else ""
            result["music_category"] = effective_music_category
            result["semantic_music_selection"] = semantic_music_selection
            result["music_channel"] = "nyc_long"
            result["music_selection_mode"] = (
                "semantic_v1"
                if str(args.music_category).strip().lower() == "auto" and str(args.audio_mode) == "music"
                else "explicit_category"
            )
            result["music_track"] = music_p.name if music_p else ""
            result["music_selection_reason"] = str((semantic_music_selection or {}).get("selection_reason") or "")
            mux_out = enc_path.parent / f"{enc_path.stem}_audio.mp4"
            if _mux_music_onto_video(
                enc_path,
                mux_out,
                music=music_p,
                music_volume=float(args.music_volume),
                target_duration=float(eff_sec),
                warnings=warnings,
                result=result,
            ):
                try:
                    enc_path.unlink(missing_ok=True)
                except OSError:
                    pass
                enc_path = mux_out
        else:
            result["audio_fallback"] = "original_muxed"
            result["has_original_audio"] = True
            result["has_music_track"] = False
            result["final_audio_codec"] = "aac"

        final_ready = subdir / out_name
        try:
            shutil.copy2(enc_path, final_ready)
        except OSError as exc:
            job["status"] = "failed"
            errors.append(f"copy_ready:{exc!r}")
            job["finished_at"] = _utc()
            job["result"] = result
            _write_job(job_path, job)
            return 5

        out_final = final_ready
        result["selected_sources"] = [m.get("index_meta") or {"file_path": m.get("source")} for m in norm_meta]
        result["selected_source_origins"] = [
            str((meta.get("index_meta") or {}).get("origin") or "unknown") for meta in norm_meta
        ]
        result["normalized_segments"] = norm_meta
        result["effective_duration_sec"] = float(min(acc, requested_sec))
        result["duration_sec"] = _ffprobe_duration(out_final)

    if source_discovery_mode == "automatic":
        path_to_row: dict[str, dict[str, Any]] = {}
        for t in discovered_pool:
            if not t[2]:
                continue
            try:
                path_to_row[str(t[0].resolve())] = t[2]
            except Exception:
                path_to_row[str(t[0])] = t[2]
        sel_out = result.get("selected_sources")
        rows_agg: list[dict[str, Any]] = []
        total_src_dur = 0.0
        if isinstance(sel_out, list):
            result["selected_source_count"] = len(sel_out)
            for item in sel_out:
                if not isinstance(item, dict):
                    continue
                fp = str(item.get("file_path") or item.get("path") or "").strip()
                if fp:
                    try:
                        k = str(Path(fp).expanduser().resolve())
                    except Exception:
                        k = fp
                    row_d = path_to_row.get(k) or path_to_row.get(fp)
                    if isinstance(row_d, dict):
                        rows_agg.append(row_d)
                    else:
                        rows_agg.append(item)
                try:
                    d_part = float(item.get("duration") or 0)
                except Exception:
                    d_part = 0.0
                if d_part > 0:
                    total_src_dur += d_part
                elif fp:
                    try:
                        total_src_dur += _ffprobe_duration(Path(fp).expanduser())
                    except Exception:
                        pass
        else:
            result["selected_source_count"] = 0
        result["selected_total_source_duration_sec"] = total_src_dur
        caps = [str(r.get("captured_at") or "").strip() for r in rows_agg]
        caps = [c for c in caps if c]
        result["first_selected_captured_at"] = caps[0] if caps else ""
        result["last_selected_captured_at"] = caps[-1] if caps else ""
        ord_ok, ow = _verify_selected_chronological(rows_agg)
        result["selected_sources_ordered"] = ord_ok
        result["chronological_sort"] = ord_ok
        ol: list[dict[str, Any]] = []
        for r in rows_agg:
            if not isinstance(r, dict):
                continue
            try:
                pth = Path(str(r.get("path") or "")).expanduser()
            except Exception:
                pth = Path(".")
            dur = float(r.get("duration_sec") or 0.0)
            if dur <= 0 and pth.is_file():
                dur = float(_row_duration_for_plan(r, pth))
            ol.append(
                {
                    "path": str(r.get("path") or ""),
                    "captured_at": str(r.get("captured_at") or ""),
                    "sort_key": _pool_row_sort_key(r, pth),
                    "route_group": str(r.get("route_group") or r.get("captured_date") or ""),
                    "duration_sec": round(dur, 3),
                }
            )
        result["selected_sources_ordered_list"] = ol
        if not ord_ok:
            warnings.extend(ow)
            warnings.append("chronological_order_warning")
    else:
        result["assembly_order"] = "manual"
        result["chronological_sort"] = False
        result["route_group_strategy"] = ""
        result["primary_route_group"] = ""
        result["selected_route_groups"] = []
        ss = result.get("selected_sources") if isinstance(result.get("selected_sources"), list) else []
        result["selected_source_count"] = len(ss) if ss else (1 if src else 0)
        result["selected_total_source_duration_sec"] = float(result.get("effective_duration_sec") or 0.0)
        result["selected_sources_ordered"] = True
        result["selected_sources_ordered_list"] = []

    log_lines.append(
        f"NYC_LONG_ASSEMBLY_ORDER={result.get('assembly_order', 'unknown')} "
        f"selected_sources_ordered={result.get('selected_sources_ordered')}"
    )

    if (
        source_discovery_mode == "automatic"
        and not short_mode
        and result.get("selected_sources_ordered") is not True
    ):
        job["status"] = "blocked"
        job["reason"] = "chronological_order_violation"
        errors.append("chronological_order_violation")
        job["finished_at"] = _utc()
        job["result"] = result
        job["warnings"] = warnings
        job["errors"] = errors
        _write_job(job_path, job)
        return 9

    if not short_mode:
        stf: set[str] = set()
        orf: set[str] = set()
        for x in result.get("selected_sources") or []:
            if not isinstance(x, dict):
                continue
            lp = x.get("long_source_policy")
            if isinstance(lp, dict):
                stf.add(str(lp.get("source_type") or "unknown"))
                orf.add(str(lp.get("orientation") or ""))
        result["source_type_set"] = sorted(stf)
        result["orientation_set"] = sorted(orf)

    # Metadata + publish gate + dedupe
    meta = {
        "title": f"NYC ambient {int(result['duration_sec'])}s",
        "description": f"StateVerge NYC job {job_id}; effective {result['effective_duration_sec']:.1f}s.",
        "tags": ["NYC", "New York", "ambient", "StateVerge"],
        "source_video": str(src) if src else "",
        "privacy_status": args.privacy_status,
        "channel": args.channel,
        "job_id": job_id,
        "duration_seconds": result["duration_sec"],
        "requested_duration_sec": float(requested_sec),
        "source_assets": [str(x.get("file_path") or x.get("path") or "") for x in result["selected_sources"]][:50],
        "video_type": "long" if result["duration_sec"] >= 600 else "medium",
        "music_channel": "nyc_long",
        "music_selection_mode": str(result.get("music_selection_mode") or "explicit_category"),
        "music_category": str(result.get("music_category") or ""),
        "music_track": str(result.get("music_track") or ""),
        "music_selection_reason": str(result.get("music_selection_reason") or ""),
    }
    try:
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "youtube_metadata.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        job["output"]["youtube_metadata"] = str(pack_dir / "youtube_metadata.json")
    except OSError:
        warnings.append("metadata_write_failed")

    job["output"]["encoded_video"] = str(enc_path)
    job["output"]["ready_to_upload_copy"] = str(out_final)
    result["output_path"] = str(out_final)

    pg = _run_publish_gate(out_final)
    result["publish_gate"] = pg
    result["ready_to_upload"] = bool(pg.get("passed"))

    src_paths_for_dedupe: list[str] = []
    for x in result["selected_sources"]:
        if isinstance(x, dict):
            p = str(x.get("file_path") or x.get("path") or "").strip()
            if p:
                src_paths_for_dedupe.append(p)
    ded = _run_dedupe_check(
        out_final,
        title=str(meta["title"]),
        eff_dur=float(result["duration_sec"] or 0),
        selected_paths=[p for p in src_paths_for_dedupe if p],
        requested_duration_sec=float(requested_sec),
    )
    result["dedupe"] = ded
    result["min_asset_count_required"] = False
    result["duration_based_validation"] = True

    log_lines.append(
        f"summary: requested={requested_sec:.1f}s effective={result['effective_duration_sec']:.1f}s "
        f"out={result['duration_sec']:.1f}s sources={len(result['selected_sources'])} "
        f"norm_segments={len(result['normalized_segments'])} publish_gate={pg.get('passed')} "
        f"dedupe_allowed={ded.get('allowed', ded.get('skipped'))}"
    )

    upload_attempted = bool(args.upload)
    result["upload_attempted"] = upload_attempted
    job["output"]["upload_requested"] = upload_attempted

    if not upload_attempted:
        job["status"] = "completed"
        job["finished_at"] = _utc()
        job["result"] = result
        job["output"]["upload"] = None
        _write_job(job_path, job)
        return 0

    if str(args.channel).strip().upper() != "NYC":
        job["status"] = "completed"
        warnings.append("upload_skipped_channel_not_nyc")
        job["finished_at"] = _utc()
        job["result"] = result
        _write_job(job_path, job)
        return 0

    if ded.get("skipped"):
        pass
    elif ded.get("allowed") is False:
        job["status"] = "completed"
        warnings.append(f"upload_blocked_dedupe:{ded.get('reason')}")
        job["finished_at"] = _utc()
        job["result"] = result
        _write_job(job_path, job)
        return 0

    ok_ch, chinfo = _verify_nyc_channel()
    job["output"]["channel_verification"] = chinfo
    if not ok_ch:
        job["status"] = "completed"
        warnings.append(f"nyc_channel_not_confirmed:{chinfo.get('reason')}")
        job["finished_at"] = _utc()
        job["result"] = result
        _write_job(job_path, job)
        return 0

    up = _upload_video(
        out_final,
        title=str(meta["title"]),
        description=str(meta["description"]),
        tags=list(meta.get("tags") or []),
        privacy=str(args.privacy_status),
        allow_public=bool(args.allow_public),
    )
    result["upload"] = up
    if up.get("ok") and up.get("video_id"):
        result["uploaded"] = True
        result["youtube_url"] = f"https://www.youtube.com/watch?v={up['video_id']}"
        job["status"] = "completed"
    else:
        job["status"] = "failed"
        errors.append(up.get("error") or "upload_failed")
    job["finished_at"] = _utc()
    job["result"] = result
    job["output"]["upload"] = up
    _write_job(job_path, job)
    return 0 if job["status"] == "completed" else 6


if __name__ == "__main__":
    raise SystemExit(main())
