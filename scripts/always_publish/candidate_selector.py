"""Long/shorts candidate selection with ffprobe checks (read-only, fail-open)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from always_publish.backlog_manager import load_backlog_index, path_in_ledger
from always_publish.paths import (
    LONG_CLIPS_DIR,
    LONG_REJECTED_PATH,
    LONG_EMERGENCY_PATHS,
    READY_ROOT,
    SHORTS_CLIPS_DIR,
    UPLOAD_HISTORY_CSV,
    scripts_on_path,
)

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv"}
_MIN_LONG_SEC = 2700.0
_SHORTS_MIN_SEC = 15.0
_SHORTS_MAX_SEC = 60.0
_FFPROBE = os.environ.get("FFPROBE_BIN") or shutil.which("ffprobe") or "ffprobe"


def _ffprobe_json(path: Path) -> dict[str, Any] | None:
    try:
        r = subprocess.run(
            [
                _FFPROBE,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}")
        return data if isinstance(data, dict) else None
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def probe_media(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "duration_sec": 0.0, "width": 0, "height": 0}
    prob = _ffprobe_json(path)
    if not prob:
        return out
    fmt = prob.get("format") if isinstance(prob.get("format"), dict) else {}
    try:
        out["duration_sec"] = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        pass
    for st in prob.get("streams") or []:
        if not isinstance(st, dict) or st.get("codec_type") != "video":
            continue
        try:
            out["width"] = int(st.get("width") or 0)
            out["height"] = int(st.get("height") or 0)
        except (TypeError, ValueError):
            pass
        break
    return out


def _is_vertical(w: int, h: int) -> bool:
    return h > w and w > 0


def _is_landscape(w: int, h: int) -> bool:
    return w >= h and w > 0


def _load_rejected_keys() -> set[str]:
    keys: set[str] = set()
    try:
        if not LONG_REJECTED_PATH.is_file():
            return keys
        data = json.loads(LONG_REJECTED_PATH.read_text(encoding="utf-8", errors="replace"))
        ent = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(ent, list):
            return keys
        for e in ent:
            if not isinstance(e, dict):
                continue
            for k in ("quarantine_path", "resolved_path", "path"):
                raw = str(e.get(k) or "").strip()
                if raw:
                    keys.add(raw.lower())
    except (OSError, json.JSONDecodeError):
        pass
    return keys


def _upload_history_paths() -> set[str]:
    out: set[str] = set()
    try:
        if not UPLOAD_HISTORY_CSV.is_file():
            return out
        for line in UPLOAD_HISTORY_CSV.read_text(encoding="utf-8", errors="replace").splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 4:
                out.add(parts[3].strip().lower())
    except OSError:
        pass
    return out


def _forbidden_long_path(p: Path) -> str | None:
    s = str(p).replace("\\", "/").lower()
    if any(m in s for m in ("/shorts/", "shorts_clips", "video916", "picture916")):
        return "shorts_or_vertical_tree"
    if "_rejected_long_outputs" in {x.lower() for x in p.parts}:
        return "rejected_quarantine"
    return None


def _scan_videos(root: Path, *, max_files: int = 400, probe_limit: int = 24) -> list[Path]:
    if not root.is_dir():
        return []
    found: list[Path] = []
    try:
        for p in root.rglob("*"):
            if len(found) >= max_files:
                break
            if not p.is_file() or p.name.startswith("._"):
                continue
            if p.suffix.lower() in _VIDEO_EXTS:
                found.append(p)
    except OSError:
        pass
    try:
        found.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    except OSError:
        pass
    return found[:probe_limit]


def _long_row(p: Path, *, level: str, rejected: set[str], uploaded: set[str]) -> dict[str, Any] | None:
    forb = _forbidden_long_path(p)
    if forb:
        return None
    sp = str(p)
    if sp.lower() in rejected or sp.lower() in uploaded:
        return None
    if path_in_ledger(sp, kind="long"):
        return None
    meta = probe_media(p)
    dur = float(meta.get("duration_sec") or 0)
    w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
    if dur < _MIN_LONG_SEC:
        return None
    if _is_vertical(w, h):
        return None
    if not _is_landscape(w, h) and w > 0:
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    content_type = "ferry_1h_realsound" if "ferry" in p.name.lower() else "driving_1h_music"
    if "3h" in p.name.lower() or dur >= 10000:
        content_type = "long_3h_special_ferry" if "ferry" in p.name.lower() else "long_3h_special_driving"
    return {
        "path": sp,
        "level": level,
        "duration_sec": dur,
        "width": w,
        "height": h,
        "mtime": mtime,
        "content_type": content_type,
    }


def _shorts_row(
    p: Path,
    *,
    level: str,
    uploaded: set[str],
    min_sec: float = _SHORTS_MIN_SEC,
    max_sec: float = _SHORTS_MAX_SEC,
    require_vertical: bool = False,
) -> dict[str, Any] | None:
    sp = str(p)
    if sp.lower() in uploaded:
        return None
    if path_in_ledger(sp, kind="shorts"):
        return None
    meta = probe_media(p)
    dur = float(meta.get("duration_sec") or 0)
    w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
    if dur < min_sec or dur > max_sec:
        return None
    vertical = _is_vertical(w, h)
    if require_vertical and not vertical:
        return None
    try:
        mtime = p.stat().st_mtime
    except OSError:
        mtime = 0.0
    return {
        "path": sp,
        "level": level,
        "duration_sec": dur,
        "width": w,
        "height": h,
        "vertical": vertical,
        "mtime": mtime,
    }


def collect_long_candidates_by_level() -> dict[str, list[dict[str, Any]]]:
    rejected = _load_rejected_keys()
    uploaded = _upload_history_paths()
    out: dict[str, list[dict[str, Any]]] = {lvl: [] for lvl in (
        "L1_ready_nyc_long_clips",
        "L2_ready_other_long",
        "L3_publish_pack_staged",
        "L4_backlog_index",
        "L5_older_ready_pool",
        "L6_no_material",
    )}

    for p in _scan_videos(LONG_CLIPS_DIR, max_files=80, probe_limit=24):
        row = _long_row(p, level="L1_ready_nyc_long_clips", rejected=rejected, uploaded=uploaded)
        if row:
            out["L1_ready_nyc_long_clips"].append(row)

    if READY_ROOT.is_dir():
        for p in _scan_videos(READY_ROOT, max_files=120, probe_limit=20):
            if "nyc_long_clips" in str(p):
                continue
            if "shorts" in str(p).lower():
                continue
            row = _long_row(p, level="L2_ready_other_long", rejected=rejected, uploaded=uploaded)
            if row:
                out["L2_ready_other_long"].append(row)

    pack = Path("/Volumes/SV_TRANSFER/publish_pack")
    for sub in ("nyc_long_uploads", "always_publish"):
        staged = pack / sub
        for p in _scan_videos(staged, max_files=60, probe_limit=12):
            row = _long_row(p, level="L3_publish_pack_staged", rejected=rejected, uploaded=uploaded)
            if row:
                out["L3_publish_pack_staged"].append(row)

    backlog = load_backlog_index()
    for item in backlog.get("long") or []:
        if not isinstance(item, dict):
            continue
        pp = Path(str(item.get("path") or ""))
        if pp.is_file():
            row = _long_row(pp, level="L4_backlog_index", rejected=rejected, uploaded=uploaded)
            if row:
                out["L4_backlog_index"].append(row)

    for p in _scan_videos(READY_ROOT, max_files=80, probe_limit=12):
        row = _long_row(p, level="L5_older_ready_pool", rejected=rejected, uploaded=uploaded)
        if row:
            out["L5_older_ready_pool"].append(row)

    for k in out:
        out[k].sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
    return out


def collect_shorts_candidates_by_level() -> dict[str, list[dict[str, Any]]]:
    uploaded = _upload_history_paths()
    out: dict[str, list[dict[str, Any]]] = {
        "L1_ready_shorts_clips": [],
        "L2_publish_pack_shorts": [],
        "L3_backlog_shorts": [],
        "L4_relaxed_duration": [],
        "L5_any_vertical_pool": [],
        "L6_no_material": [],
    }
    for p in _scan_videos(SHORTS_CLIPS_DIR, max_files=80, probe_limit=32):
        row = _shorts_row(p, level="L1_ready_shorts_clips", uploaded=uploaded, require_vertical=True)
        if row:
            out["L1_ready_shorts_clips"].append(row)

    pack_shorts = Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads")
    for p in _scan_videos(pack_shorts, max_files=60, probe_limit=16):
        row = _shorts_row(p, level="L2_publish_pack_shorts", uploaded=uploaded, require_vertical=True)
        if row:
            out["L2_publish_pack_shorts"].append(row)

    backlog = load_backlog_index()
    for item in backlog.get("shorts") or []:
        if not isinstance(item, dict):
            continue
        pp = Path(str(item.get("path") or ""))
        if pp.is_file():
            row = _shorts_row(pp, level="L3_backlog_shorts", uploaded=uploaded, require_vertical=True)
            if row:
                out["L3_backlog_shorts"].append(row)

    if READY_ROOT.is_dir():
        for p in _scan_videos(READY_ROOT, max_files=80, probe_limit=16):
            if "shorts" not in str(p).lower() and "916" not in str(p):
                continue
            row = _shorts_row(
                p,
                level="L4_relaxed_duration",
                uploaded=uploaded,
                min_sec=10.0,
                max_sec=90.0,
                require_vertical=True,
            )
            if row:
                out["L4_relaxed_duration"].append(row)
            row2 = _shorts_row(
                p,
                level="L5_any_vertical_pool",
                uploaded=uploaded,
                min_sec=8.0,
                max_sec=120.0,
                require_vertical=False,
            )
            if row2 and _is_vertical(int(row2.get("width") or 0), int(row2.get("height") or 0)):
                out["L5_any_vertical_pool"].append(row2)

    for k in out:
        out[k].sort(key=lambda r: float(r.get("mtime") or 0), reverse=True)
    return out


def emergency_active() -> tuple[bool, dict[str, Any]]:
    for ep in LONG_EMERGENCY_PATHS:
        try:
            if not ep.is_file():
                continue
            data = json.loads(ep.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict) and data.get("LONG_AUTOPUBLISH_DISABLED"):
                return True, {"path": str(ep), **{k: data.get(k) for k in ("block_reason", "updated_at")}}
        except (OSError, json.JSONDecodeError):
            continue
    return False, {}


def load_guards_plan() -> dict[str, Any]:
    """Doctor, channel, emergency, shorts lock — checked in plan, never bypassed."""
    import sys

    scripts_on_path()
    nyc_auto_dir = Path(__file__).resolve().parents[1] / "nyc_auto"
    nap = str(nyc_auto_dir)
    if nyc_auto_dir.is_dir() and nap not in sys.path:
        sys.path.insert(0, nap)
    guards: dict[str, Any] = {
        "doctor_blocked": False,
        "doctor_detail": {},
        "channel_guard_ok": True,
        "channel_guard_detail": {},
        "long_emergency_active": False,
        "long_emergency_detail": {},
        "shorts_global_lock": False,
        "duplicate_guard_warnings": [],
    }
    try:
        from nyc_auto.doctor_gate_client import (  # noqa: WPS433
            is_upload_blocked_by_doctor_gate,
            load_doctor_report_json,
        )

        report, _, _ = load_doctor_report_json()
        blocked, detail = is_upload_blocked_by_doctor_gate(report)
        guards["doctor_blocked"] = blocked
        guards["doctor_detail"] = detail
    except Exception as exc:  # noqa: BLE001
        guards["doctor_detail"] = {"fail_open": True, "error": repr(exc)}

    try:
        from channel_guard import (  # noqa: WPS433
            validate_long_channel_token,
            validate_shorts_channel_token,
        )
        from youtube_token_paths import (  # noqa: WPS433
            OFFICIAL_LONG_TOKEN_PATH,
            OFFICIAL_SHORTS_TOKEN_PATH,
        )

        ok_l, r_l = validate_long_channel_token(OFFICIAL_LONG_TOKEN_PATH)
        ok_s, r_s = validate_shorts_channel_token(OFFICIAL_SHORTS_TOKEN_PATH)
        guards["channel_guard_ok"] = ok_l and ok_s
        guards["channel_guard_detail"] = {"long": r_l, "shorts": r_s}
    except Exception as exc:  # noqa: BLE001
        guards["channel_guard_detail"] = {"fail_open": True, "error": repr(exc)}

    em, emd = emergency_active()
    guards["long_emergency_active"] = em
    guards["long_emergency_detail"] = emd

    from always_publish.paths import SHORTS_GLOBAL_LOCK

    guards["shorts_global_lock"] = SHORTS_GLOBAL_LOCK.is_file()

    uploaded = _upload_history_paths()
    if len(uploaded) > 5000:
        guards["duplicate_guard_warnings"].append("upload_history_large_read_only_ok")
    return guards
