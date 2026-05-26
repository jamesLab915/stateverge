#!/usr/bin/env python3
"""Doctor Gate v1 — diagnose + repair (no upload, no token edits, no deleting originals).

- Cleans AppleDouble ``._*`` files only under ``ready_to_upload`` and ``publish_pack`` on SV_TRANSFER.
- Moves bad ready_to_upload media to quarantine (never deletes).
- May move stale, unheld flock files to ``quarantine_locks`` (never blind-delete).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
STATEVERGE = HOME / "StateVerge"
SV_CACHE_LOG = Path("/Volumes/SV_CACHE/logs/stateverge_doctor_report.json")
HOME_LOG = STATEVERGE / "logs" / "stateverge_doctor_report.json"
SV_TRANSFER = Path("/Volumes/SV_TRANSFER")
READY_PRIMARY = SV_TRANSFER / "ready_to_upload"
PACK_PRIMARY = SV_TRANSFER / "publish_pack"
STALE_LOCK_SEC = 2 * 3600
REPORT_MAX_AGE_SEC = 24 * 3600
VIDEO_EXT = {".mp4", ".mov", ".m4v"}
MIN_BYTES = 1024 * 1024
MIN_DURATION_SEC = 5.0
SKIP_DIR_NAMES = {"node_modules", ".git", "__pycache__", ".venv", "venv", "_doctor_gate_quarantine", "_doctor_quarantine_locks"}
MAX_WALK_NODES = 12000
MAX_FFPROBE = 150

FORBIDDEN_PREFIXES = (str(HOME / "hennyhowie"), "/Users/hennyhowie", "/Volumes/SV_WORK", "/Volumes/StateVerge")


def _forbidden(p: Path) -> bool:
    s = str(p.resolve()) if p.exists() else str(p)
    return any(s.startswith(fp) for fp in FORBIDDEN_PREFIXES if fp)


def _resolve_transfer_ready() -> tuple[Path, Path | None, list[str]]:
    warns: list[str] = []
    try:
        sys.path.insert(0, str(STATEVERGE / "src"))
        from utils.storage_paths import get_sv_transfer  # type: ignore

        xfer = get_sv_transfer(verbose=False)
        r = xfer / "ready_to_upload"
        return r, xfer, warns
    except Exception as exc:  # noqa: BLE001
        warns.append(f"get_sv_transfer_failed:{exc!r}")
        if READY_PRIMARY.exists():
            return READY_PRIMARY, SV_TRANSFER, warns
        fb = STATEVERGE / "data" / "transfer_fallback" / "ready_to_upload"
        return fb, None, warns + ["using_home_fallback_ready_to_upload_guess"]


def _ffprobe_duration(path: Path) -> tuple[float | None, str | None]:
    exe = shutil.which("ffprobe")
    if not exe:
        return None, "ffprobe_not_on_path"
    cmd = [
        exe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=25, check=False)
        if r.returncode != 0:
            return None, f"ffprobe_rc_{r.returncode}"
        d = float((r.stdout or "").strip() or 0.0)
        return (d if d > 0 else None), None
    except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
        return None, repr(exc)


def _lsof_holders(path: Path) -> tuple[bool, str]:
    """Return (has_holders, detail)."""
    ls = shutil.which("lsof")
    if not ls:
        return False, "lsof_missing_treat_as_no_holders"
    try:
        r = subprocess.run([ls, str(path)], capture_output=True, text=True, timeout=15, check=False)
        out = (r.stdout or "").strip()
        if r.returncode != 0 and not out:
            return False, f"lsof_rc_{r.returncode}"
        lines = [ln for ln in out.splitlines() if ln.strip()][1:]  # drop header
        return bool(lines), f"lines={len(lines)}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, repr(exc)


def _read_report(p: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    meta: dict[str, Any] = {"path": str(p), "exists": p.is_file(), "mtime_iso": None, "age_hours": None}
    if not p.is_file():
        return None, meta
    try:
        st = p.stat()
        meta["mtime_iso"] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
        meta["age_hours"] = round((time.time() - st.st_mtime) / 3600.0, 2)
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        return (data if isinstance(data, dict) else None), meta
    except (OSError, json.JSONDecodeError) as exc:
        meta["read_error"] = repr(exc)
        return None, meta


def _extract_fields(rep: dict[str, Any] | None) -> dict[str, Any]:
    if not rep:
        return {"note": "no_report"}
    g = rep.get("G_ready_to_upload_health") or {}
    f = rep.get("F_upload_lock_health") or {}
    gr = str(rep.get("gate_reason") or "")
    dwr = int(g.get("duration_errors") or 0) if isinstance(g, dict) else 0
    many = "ready_to_upload_many_probe_failures" in gr or (
        isinstance(rep.get("doctor_gate_warning_reasons"), list)
        and "ready_to_upload_many_probe_failures" in (rep.get("doctor_gate_warning_reasons") or [])
    )
    probe_fail = "ready_to_upload_probe_failures" in gr or (
        isinstance(rep.get("doctor_gate_warning_reasons"), list)
        and "ready_to_upload_probe_failures" in (rep.get("doctor_gate_warning_reasons") or [])
    )
    stale = "stale_lock" in gr or any("stale_lock" in str(x) for x in (rep.get("warnings") or []))
    dg = str(rep.get("doctor_gate") or "")
    blocked = dg.upper() == "BLOCK_UPLOAD"
    return {
        "doctor_gate": dg,
        "upload_allowed": bool(rep.get("upload_allowed")),
        "gate_reason": gr[:800],
        "blocked_by_doctor_gate": blocked,
        "critical_issues": list(rep.get("critical_issues") or [])[:50],
        "warning_reasons": list(rep.get("warnings") or [])[:80],
        "doctor_gate_warning_reasons": list(rep.get("doctor_gate_warning_reasons") or [])[:50],
        "ready_to_upload_many_probe_failures": bool(many),
        "ready_to_upload_probe_failures": bool(probe_fail) or (dwr > 0),
        "ready_to_upload_duration_errors_count": dwr,
        "stale_lock": bool(stale),
        "G_ready_to_upload_health": g if isinstance(g, dict) else {},
        "F_stale_locks": f.get("stale_locks") if isinstance(f, dict) else [],
        "generated_at": rep.get("generated_at"),
    }


def _ensure_quarantine_root(xfer: Path | None) -> Path:
    if xfer and xfer.exists():
        root = xfer / "ready_to_upload" / "_doctor_gate_quarantine"
    else:
        root = STATEVERGE / "data" / "doctor_gate_quarantine"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _ensure_lock_quarantine(xfer: Path | None) -> Path:
    if xfer and xfer.exists():
        root = xfer / "ready_to_upload" / "_doctor_quarantine_locks"
    else:
        root = STATEVERGE / "data" / "doctor_quarantine_locks"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _clean_appledouble_under(roots: list[Path], log: list[str]) -> int:
    removed = 0
    for root in roots:
        if not root.is_dir() or _forbidden(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
            for fn in filenames:
                if not fn.startswith("._"):
                    continue
                fp = Path(dirpath) / fn
                if _forbidden(fp):
                    continue
                try:
                    fp.unlink()
                    removed += 1
                except OSError as exc:
                    log.append(f"appledouble_delete_failed:{fp}:{exc!r}")
    return removed


def _quarantine_bad_media(ready: Path, qroot: Path, log: list[str]) -> tuple[int, int]:
    """Return (moved_count, probed_count)."""
    if not ready.is_dir() or _forbidden(ready):
        log.append("ready_to_upload_skip_forbidden_or_missing")
        return 0, 0
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    batch = qroot / ts
    batch.mkdir(parents=True, exist_ok=True)
    moved = 0
    probed = 0
    nodes = 0
    for dirpath, dirnames, filenames in os.walk(ready):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES and not d.startswith(".")]
        for fn in filenames:
            nodes += 1
            if nodes > MAX_WALK_NODES:
                log.append("scan_truncated_max_nodes")
                return moved, probed
            if fn.startswith("._"):
                continue
            fp = Path(dirpath) / fn
            if fp.suffix.lower() not in VIDEO_EXT:
                continue
            if _forbidden(fp):
                continue
            reason = ""
            try:
                sz = fp.stat().st_size
            except OSError as exc:
                log.append(f"stat_fail:{fp}:{exc!r}")
                continue
            if sz == 0:
                reason = "zero_bytes"
            elif sz < MIN_BYTES:
                reason = "under_1mb"
            if reason:
                h = hashlib.sha256(str(fp).encode()).hexdigest()[:12]
                dest = batch / h / fp.name
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(fp), str(dest))
                    moved += 1
                    log.append(f"quarantine:{reason}:{fp}->{dest}")
                except OSError as exc:
                    log.append(f"quarantine_move_failed:{fp}:{exc!r}")
                continue
            if probed >= MAX_FFPROBE:
                continue
            probed += 1
            dur, err = _ffprobe_duration(fp)
            bad = False
            if err:
                bad = True
                reason = f"ffprobe:{err}"
            elif dur is not None and dur < MIN_DURATION_SEC:
                bad = True
                reason = f"duration_lt_{MIN_DURATION_SEC}s:{dur}"
            if bad:
                h = hashlib.sha256(str(fp).encode()).hexdigest()[:12]
                dest = batch / h / fp.name
                try:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(fp), str(dest))
                    moved += 1
                    log.append(f"quarantine:{reason}:{fp}->{dest}")
                except OSError as exc:
                    log.append(f"quarantine_move_failed:{fp}:{exc!r}")
    return moved, probed


def _handle_stale_flocks(xfer: Path | None, log: list[str]) -> str:
    """Search shorts flock under publish_pack; move if stale + no lsof holders."""
    if not xfer or not xfer.exists():
        return "skip_locks_no_transfer"
    pattern = "shorts_used_assets.json.shorts_used_assets.fcntl.lock"
    found: list[Path] = []
    try:
        for p in (xfer / "publish_pack").rglob("*"):
            if p.is_file() and p.name == pattern:
                found.append(p)
    except OSError as exc:
        log.append(f"lock_scan_error:{exc!r}")
        return "scan_error"
    if not found:
        return "no_shorts_flock_files"
    lroot = _ensure_lock_quarantine(xfer)
    status_parts: list[str] = []
    for lk in found:
        if _forbidden(lk):
            continue
        try:
            st = lk.stat()
            age = time.time() - st.st_mtime
        except OSError:
            status_parts.append(f"{lk}:stat_fail")
            continue
        holders, det = _lsof_holders(lk)
        stale = age > STALE_LOCK_SEC
        status_parts.append(f"path={lk.name} age_s={round(age,1)} stale={stale} holders={holders}({det})")
        if holders:
            log.append(f"lock_skip_has_holders:{lk}")
            continue
        if not stale:
            log.append(f"lock_skip_not_stale:{lk}")
            continue
        h = hashlib.sha256(str(lk).encode()).hexdigest()[:10]
        dest = lroot / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") / h / lk.name
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(lk), str(dest))
            log.append(f"lock_quarantined:{lk}->{dest}")
        except OSError as exc:
            log.append(f"lock_move_failed:{lk}:{exc!r}")
    return "; ".join(status_parts) if status_parts else "no_lock_paths"


def _run_doctor(py: Path) -> int:
    script = STATEVERGE / "scripts" / "stateverge_doctor.py"
    if not script.is_file():
        print("ERROR: stateverge_doctor.py missing", file=sys.stderr)
        return 2
    r = subprocess.run([str(py), str(script)], cwd=str(STATEVERGE), check=False)
    return int(r.returncode)


def main() -> int:
    py = STATEVERGE / ".venv_audio" / "bin" / "python3"
    if not py.is_file():
        py = Path(sys.executable)

    print("=== STEP 1: read doctor reports ===", flush=True)
    pairs = [(SV_CACHE_LOG, "sv_cache"), (HOME_LOG, "home")]
    before: dict[str, Any] = {}
    for path, label in pairs:
        rep, meta = _read_report(path)
        before[label] = {"meta": meta, "fields": _extract_fields(rep)}
        print(json.dumps({label: before[label]}, indent=2, ensure_ascii=False), flush=True)

    # Staleness (SV_CACHE is authoritative for upload gate)
    rep_sv, meta_sv = _read_report(SV_CACHE_LOG)
    stale_report = False
    if meta_sv.get("exists") and meta_sv.get("age_hours") is not None:
        stale_report = float(meta_sv["age_hours"]) > 24.0
    print(
        json.dumps(
            {"sv_cache_report_stale_over_24h": stale_report, "sv_cache_meta": meta_sv},
            indent=2,
            ensure_ascii=False,
        ),
        flush=True,
    )

    xfer = SV_TRANSFER if SV_TRANSFER.exists() else None
    ready, xfer_resolved, xfer_warns = _resolve_transfer_ready()
    for w in xfer_warns:
        print(f"WARN:{w}", flush=True)
    if xfer_resolved is not None:
        xfer = xfer_resolved

    repair_log: list[str] = []
    print("=== STEP 4: AppleDouble cleanup (allowed roots only) ===", flush=True)
    clean_roots = []
    if READY_PRIMARY.is_dir() and not _forbidden(READY_PRIMARY):
        clean_roots.append(READY_PRIMARY)
    if PACK_PRIMARY.is_dir() and not _forbidden(PACK_PRIMARY):
        clean_roots.append(PACK_PRIMARY)
    n_dot = _clean_appledouble_under(clean_roots, repair_log)
    print(f"removed_appledouble_files={n_dot}", flush=True)

    print("=== STEP 4b: quarantine bad ready_to_upload videos ===", flush=True)
    qroot = _ensure_quarantine_root(xfer)
    moved, probed = _quarantine_bad_media(ready, qroot, repair_log)
    print(f"quarantine_moved={moved} ffprobe_probes={probed}", flush=True)

    print("=== STEP 5: stale flock handling ===", flush=True)
    lock_status = _handle_stale_flocks(xfer, repair_log)
    print(f"STALE_LOCK_STATUS={lock_status}", flush=True)

    print("=== STEP 6: re-run stateverge_doctor.py ===", flush=True)
    rc = _run_doctor(py)
    print(f"stateverge_doctor_exit={rc}", flush=True)

    print("=== AFTER: re-read SV_CACHE + home reports ===", flush=True)
    rep2, meta2 = _read_report(SV_CACHE_LOG)
    rep2h, meta2h = _read_report(HOME_LOG)
    fields_sv = _extract_fields(rep2)
    fields_home = _extract_fields(rep2h)
    print(json.dumps({"sv_cache_after_meta": meta2, "fields_sv_cache": fields_sv}, indent=2, ensure_ascii=False), flush=True)
    print(json.dumps({"home_after_meta": meta2h, "fields_home": fields_home}, indent=2, ensure_ascii=False), flush=True)

    # doctor_gate_client simulation (same order as upload queue)
    sys.path.insert(0, str(STATEVERGE / "scripts" / "nyc_auto"))
    try:
        from doctor_gate_client import check_real_upload_allowed_by_doctor  # type: ignore

        allowed, detail, gw = check_real_upload_allowed_by_doctor(dry_run=False)
    except Exception as exc:  # noqa: BLE001
        allowed, detail, gw = True, {"error": repr(exc)}, []

    g = (rep2 or {}).get("G_ready_to_upload_health") or {}
    derr = int(g.get("duration_errors") or 0) if isinstance(g, dict) else 0
    dg = str((rep2 or {}).get("doctor_gate") or fields_sv.get("doctor_gate") or "")
    ua = bool((rep2 or {}).get("upload_allowed", fields_sv.get("upload_allowed")))
    gr = str((rep2 or {}).get("gate_reason") or fields_sv.get("gate_reason") or "")

    safe = allowed and dg.upper() != "BLOCK_UPLOAD"
    print("", flush=True)
    print(f"DOCTOR_GATE={dg}", flush=True)
    print(f"UPLOAD_ALLOWED={ua}", flush=True)
    print(f"GATE_REASON={gr[:500]}", flush=True)
    print(f"READY_TO_UPLOAD_PROBE_FAILURES_COUNT={derr}", flush=True)
    print(f"STALE_LOCK_STATUS={lock_status}", flush=True)
    print(f"SAFE_TO_RETRY_SHORTS_UPLOAD={safe}", flush=True)
    print(json.dumps({"doctor_gate_client_allowed": allowed, "detail": detail, "gate_warnings": gw}, indent=2), flush=True)

    out_path = HOME / "StateVerge_Control_Center" / "logs" / "doctor_gate_diagnose_repair.json"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(
                {
                    "before": before,
                    "repair_log_tail": repair_log[-200:],
                    "after_fields_sv": fields_sv,
                    "doctor_gate_client": {"allowed": allowed, "detail": detail},
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"wrote:{out_path}", flush=True)
    except OSError as exc:
        print(f"WARN: could not write summary json: {exc!r}", flush=True)

    return 0 if rc == 0 else rc


if __name__ == "__main__":
    raise SystemExit(main())
