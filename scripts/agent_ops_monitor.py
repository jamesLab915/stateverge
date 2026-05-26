#!/usr/bin/env python3
"""
StateVerge Agent Operations Monitor v1 — Pro-only ops + limited safe repair.
Does not delete raw media, format disks, edit tokens, real upload, or SSH Air.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL_CENTER = Path.home() / "StateVerge_Control_Center"
LOGS_CC = CONTROL_CENTER / "logs"
LATEST_JSON = LOGS_CC / "agent_ops_monitor_latest.json"
LATEST_MD = LOGS_CC / "agent_ops_monitor_latest.md"
HISTORY = LOGS_CC / "agent_ops_history"
SAFE_MODE_FLAG = LOGS_CC / "STATEVERGE_SAFE_MODE.flag"
SHORTS_GLOBAL_LOCK = STATEVERGE / "data" / "shorts_runtime" / ".shorts_cut_upload.global.lock"
STALE_LOCK_DIR = STATEVERGE / "data" / "shorts_runtime" / "stale_locks"
JOB_REPAIR_BACKUP = LOGS_CC / "agent_job_repair_backups"

FORBIDDEN_SUBSTRINGS = (
    "hennyhowie",
    "192.168.12.90",
    "ssh hennyhowie@",
)

PERMISSIONS: dict[str, bool] = {
    "delete_raw_media": False,
    "format_disks": False,
    "edit_tokens": False,
    "real_upload": False,
    "delete_youtube_video": False,
    "ssh_air": False,
    "restart_server_mode": True,
    "cleanup_stale_locks": True,
    "run_diagnostics": True,
    "run_dry_runs": True,
    "reload_launchagents": False,
    "write_logs": True,
}

STATUS_LOG_NAMES = (
    "pro_only_rebuild_v1_status.md",
    "nyc_chronological_long_assembly_v1_status.md",
    "auto_metadata_generation_v1_status.md",
    "long_video_audio_policy_v1_status.md",
    "real_sound_gate_vfr_safety_patch_v1_status.md",
    "automation_system_final_health_check.md",
    "shorts_duplicate_upload_guard_v1_status.md",
    "shorts_backend_queue_guard_v1_status.md",
)


def utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _run(argv: list[str], *, cwd: Path | None = None, timeout: float = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return int(r.returncode), r.stdout or "", r.stderr or ""
    except Exception as exc:  # noqa: BLE001
        return 1, "", repr(exc)


def _http_get(url: str, *, timeout: float = 6.0) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "StateVerge-AgentOpsMonitor/1"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8", errors="replace")[:100_000]
            return int(resp.status), body
    except urllib.error.HTTPError as e:
        try:
            b = e.read().decode("utf-8", errors="replace")[:20_000]
        except OSError:
            b = ""
        return int(e.code), b
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def _disk_pct_free(path: Path) -> tuple[bool, float | None]:
    try:
        if not path.is_dir():
            return False, None
        u = shutil.disk_usage(path)
        pct = 100.0 * float(u.free) / float(u.total) if u.total else 0.0
        return True, pct
    except OSError:
        return False, None


def _grep_forbidden_in_file(p: Path) -> list[str]:
    hits: list[str] = []
    if not p.is_file():
        return hits
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits
    low = text.lower()
    for s in FORBIDDEN_SUBSTRINGS:
        if s.lower() in low:
            hits.append(f"{p.name}:contains:{s}")
    return hits


def _scan_tree_forbidden(root: Path, *, max_files: int = 400) -> list[str]:
    """Light scan: plist + sh under project roots only."""
    hits: list[str] = []
    n = 0
    for pat in ("*.plist", "*.sh", "*.command"):
        for p in root.rglob(pat):
            if n >= max_files:
                return hits
            if any(x in p.parts for x in ("node_modules", ".git", ".venv")):
                continue
            hits.extend(_grep_forbidden_in_file(p))
            n += 1
    return hits


def _plist_launchctl(label: str) -> dict[str, Any]:
    rc, out, err = _run(["launchctl", "print", f"gui/{os.getuid()}/{label}"], timeout=15)
    loaded = rc == 0 and "program =" in out
    plist_path = ""
    m = re.search(r"path = ([^;]+);", out)
    if m:
        plist_path = m.group(1).strip()
    bad = [x for x in FORBIDDEN_SUBSTRINGS if x.lower() in out.lower()]
    upload_true = "upload=true" in out.lower() or "--upload" in out.lower()
    return {
        "label": label,
        "launchctl_rc": rc,
        "loaded_guess": loaded,
        "plist_path": plist_path,
        "forbidden_substrings_in_print": bad,
        "upload_flag_suspicious_in_print": upload_true,
        "stderr_tail": err[-2000:],
    }


def _count_process(pattern: str) -> int:
    rc, out, _ = _run(["/bin/ps", "-ax", "-o", "pid=,command="], timeout=30)
    if rc != 0:
        return 0
    n = 0
    for line in out.splitlines():
        if pattern in line and "grep" not in line:
            n += 1
    return n


def _pgrep_count(pattern: str) -> int:
    rc, out, _ = _run(["/usr/bin/pgrep", "-fl", pattern], timeout=15)
    if rc not in (0, 1):
        return 0
    return len([ln for ln in out.splitlines() if ln.strip()])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _maybe_restart_server_mode(*, do_it: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"attempted": False, "steps": []}
    if not do_it:
        return out
    out["attempted"] = True
    stop_sh = CONTROL_CENTER / "stop_server_mode.sh"
    start_sh = CONTROL_CENTER / "start_server_mode.sh"
    if stop_sh.is_file():
        rc, so, se = _run(["/bin/bash", str(stop_sh)], cwd=CONTROL_CENTER, timeout=60)
        out["steps"].append({"cmd": "stop_server_mode", "rc": rc, "stderr": se[-4000:]})
    time.sleep(2)
    if start_sh.is_file():
        rc, so, se = _run(["/bin/bash", str(start_sh)], cwd=CONTROL_CENTER, timeout=60)
        out["steps"].append({"cmd": "start_server_mode", "rc": rc, "stderr": se[-4000:]})
    return out


def _maybe_move_stale_shorts_lock(*, do_it: bool) -> dict[str, Any]:
    meta: dict[str, Any] = {"attempted": False, "moved": False, "reason": ""}
    if not SHORTS_GLOBAL_LOCK.is_file():
        meta["reason"] = "no_lock_file"
        return meta
    shorts_workers = _count_process("shorts_cut_upload_job.py") + _count_process("auto_publish_queue_shorts.py")
    if shorts_workers > 0:
        meta["reason"] = "workers_running"
        return meta
    # Heuristic: if lock file non-empty try parse pid
    try:
        raw = SHORTS_GLOBAL_LOCK.read_text(encoding="utf-8", errors="replace").strip()
        pid = None
        if raw.startswith("{"):
            data = json.loads(raw)
            if isinstance(data, dict) and data.get("pid"):
                pid = int(data["pid"])
        if pid is not None and _pid_alive(pid):
            meta["reason"] = "pid_alive"
            return meta
    except Exception:
        pass
    if not do_it:
        meta["reason"] = "dry_run_would_move"
        return meta
    STALE_LOCK_DIR.mkdir(parents=True, exist_ok=True)
    dest = STALE_LOCK_DIR / f".shorts_cut_upload.global.lock.{utc_ts()}"
    try:
        shutil.move(str(SHORTS_GLOBAL_LOCK), str(dest))
        meta["attempted"] = True
        meta["moved"] = True
        meta["dest"] = str(dest)
    except OSError as exc:
        meta["reason"] = repr(exc)
    return meta


def _maybe_launchagent_reload(labels: list[str], *, do_it: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"attempted": False, "steps": []}
    if not do_it:
        return out
    out["attempted"] = True
    for lb in labels:
        rc, so, se = _run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{lb}"], timeout=30)
        out["steps"].append({"label": lb, "rc": rc, "stderr": se[-2000:]})
    return out


def _tail_file(path: Path, *, n: int = 40) -> str:
    if not path.is_file():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except OSError:
        return ""


def _run_agent_remediation_scan(*, timeout: float = 180) -> dict[str, Any]:
    """Agent Remediation Bridge v1 — scan-only; never upload or bypass guards."""
    script = STATEVERGE / "scripts" / "always_publish" / "agent_remediation_v1.py"
    if not script.is_file():
        return {"ok": False, "skipped": True, "reason": "agent_remediation_v1_missing"}
    py = STATEVERGE / ".venv_audio" / "bin" / "python3"
    exe = str(py) if py.is_file() else sys.executable
    rc, so, se = _run([exe, str(script), "--scan-only"], cwd=STATEVERGE, timeout=timeout)
    if rc != 0:
        return {"ok": False, "returncode": rc, "stderr_tail": se[-4000:]}
    try:
        data = json.loads(so.strip() or "{}")
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {"ok": False, "parse_error": True, "stdout_tail": so[-4000:]}


def _run_diagnose_script(rel: str, *, timeout: float = 300) -> dict[str, Any]:
    script = STATEVERGE / rel
    if not script.is_file():
        return {"skipped": True, "path": str(script)}
    py = STATEVERGE / ".venv_audio" / "bin" / "python3"
    exe = str(py) if py.is_file() else sys.executable
    rc, so, se = _run([exe, str(script)], cwd=STATEVERGE, timeout=timeout)
    return {"skipped": False, "returncode": rc, "stdout_tail": so[-8000:], "stderr_tail": se[-8000:]}


def _jobs_root() -> Path:
    try:
        import sys as _sys

        src = STATEVERGE / "src"
        if src.is_dir() and str(src) not in _sys.path:
            _sys.path.insert(0, str(src))
        from utils.storage_paths import get_sv_cache  # type: ignore

        return get_sv_cache(verbose=False) / "jobs"
    except Exception:
        return Path("/Volumes/SV_CACHE/jobs")


def _maybe_repair_stale_jobs(*, do_it: bool) -> dict[str, Any]:
    """Backup job.json and patch status if stale heuristics match."""
    out: dict[str, Any] = {"attempted": False, "patched": []}
    root = _jobs_root()
    if not root.is_dir():
        out["reason"] = "jobs_root_missing"
        return out
    ts = utc_ts()
    backup_dir = JOB_REPAIR_BACKUP / ts
    now = time.time()
    for d in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime if p.is_dir() else 0, reverse=True)[:80]:
        if not d.is_dir():
            continue
        jp = d / "job.json"
        if not jp.is_file():
            continue
        try:
            data = json.loads(jp.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        st = str(data.get("status") or "")
        if st != "running":
            continue
        worker_exit = d / "worker_exit.json"
        should_patch = False
        reason = ""
        if worker_exit.is_file():
            try:
                ex = json.loads(worker_exit.read_text(encoding="utf-8", errors="replace"))
                rc = int(ex.get("returncode") or -1)
                if rc in (14, 15, 16, 17, 18) or ex.get("finished_at"):
                    should_patch = True
                    reason = f"worker_exit_rc={rc}"
            except Exception:
                pass
        try:
            age_h = (now - jp.stat().st_mtime) / 3600.0
        except OSError:
            age_h = 0
        if age_h > 6.0 and not _count_process("nyc_cut_upload_job.py") and not _count_process("shorts_cut_upload_job.py"):
            should_patch = True
            reason = reason or "running_stale_6h_no_worker"
        if not should_patch:
            continue
        if not do_it:
            out.setdefault("would_patch", []).append({"job_id": d.name, "reason": reason})
            continue
        backup_dir.mkdir(parents=True, exist_ok=True)
        bpath = backup_dir / f"{d.name}_job.json.bak"
        shutil.copy2(jp, bpath)
        note = d / "repair_note.json"
        note.write_text(
            json.dumps(
                {"at": datetime.now(timezone.utc).isoformat(), "reason": reason, "backup": str(bpath)},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        data["status"] = "stale_repaired"
        data["stale_repair_reason"] = reason
        jp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        out["attempted"] = True
        out["patched"].append({"job_id": d.name, "reason": reason, "backup": str(bpath)})
    return out


def run_cycle(args: argparse.Namespace) -> dict[str, Any]:
    operation_blocked: list[str] = []
    checks: dict[str, Any] = {}
    repairs: dict[str, Any] = {}
    critical_reasons: list[str] = []

    user = os.environ.get("USER", "")
    checks["pro_only"] = {
        "user": user,
        "stateverge_root": str(STATEVERGE),
        "control_center_root": str(CONTROL_CENTER),
        "forbidden_in_plists": _scan_tree_forbidden(CONTROL_CENTER / "launchagents")
        + _scan_tree_forbidden(CONTROL_CENTER / "scripts"),
        "forbidden_in_stateverge_launchagents": _scan_tree_forbidden(STATEVERGE / "scripts", max_files=200),
    }
    if any("hennyhowie" in x.lower() for x in checks["pro_only"]["forbidden_in_plists"]):
        critical_reasons.append("forbidden_air_path_in_launchagents")
    if any("192.168.12.90" in x for x in checks["pro_only"]["forbidden_in_plists"]):
        critical_reasons.append("forbidden_lan_ip_in_launchagents")

    vols: dict[str, Any] = {}
    for name, mount in (
        ("SV_CACHE", Path("/Volumes/SV_CACHE")),
        ("SV_TRANSFER", Path("/Volumes/SV_TRANSFER")),
        ("SV_BACKUP", Path("/Volumes/SV_BACKUP")),
    ):
        ok, pct = _disk_pct_free(mount)
        w = False
        try:
            w = ok and os.access(mount, os.W_OK)
        except OSError:
            w = False
        sev = "ok"
        if ok and pct is not None:
            if pct < 5.0:
                sev = "critical"
                critical_reasons.append(f"disk_low:{name}")
            elif pct < 10.0:
                sev = "warning"
        elif not ok:
            sev = "warning"
        vols[name] = {"mounted": ok, "writable": w, "free_pct": pct, "status": sev}
    checks["volumes"] = vols

    be = _http_get("http://127.0.0.1:8765/api/automation/status")
    root_be = _http_get("http://127.0.0.1:8765/")
    sh = _http_get("http://127.0.0.1:8765/api/shorts/status")
    fe = _http_get("http://127.0.0.1:3000/server-mode")
    checks["http"] = {
        "backend_root_http": root_be[0],
        "backend_automation_http": be[0],
        "shorts_status_http": sh[0],
        "frontend_server_mode_http": fe[0],
    }

    ffmpeg_n = _count_process("ffmpeg")
    uvicorn_n = _count_process("uvicorn")
    next_n = _pgrep_count("next-server") + _pgrep_count("next dev")
    shorts_w = _count_process("shorts_cut_upload_job.py")
    shorts_q = _count_process("auto_publish_queue_shorts.py")
    long_q = _count_process("auto_publish_queue.py")
    yt_up = _count_process("youtube_upload.py")
    checks["processes"] = {
        "ffmpeg": ffmpeg_n,
        "uvicorn": uvicorn_n,
        "next_dev_guess": next_n,
        "shorts_worker": shorts_w,
        "shorts_queue": shorts_q,
        "long_queue": long_q,
        "youtube_upload_py": yt_up,
        "duplicate_shorts_workers": shorts_w > 1,
    }
    if shorts_w > 1:
        critical_reasons.append("duplicate_shorts_workers")

    checks["launchagents"] = {
        "nyc": _plist_launchctl("com.stateverge.nyc.autopublish"),
        "shorts": _plist_launchctl("com.stateverge.shorts.autopublish"),
    }

    tok_long = STATEVERGE / "data" / "youtube" / "token.json"
    tok_shorts = STATEVERGE / "data" / "youtube" / "token_shorts.json"
    secrets = STATEVERGE / ".secrets" / "youtube" / "client_secrets.json"
    cg = STATEVERGE / "scripts" / "nyc_auto" / "channel_guard.py"
    checks["tokens_presence"] = {
        "token_json_exists": tok_long.is_file(),
        "token_shorts_json_exists": tok_shorts.is_file(),
        "client_secrets_exists": secrets.is_file(),
        "channel_guard_exists": cg.is_file(),
    }
    if not cg.is_file():
        critical_reasons.append("channel_guard_missing")

    checks["diagnostics"] = {
        "auto_publish_v2": _run_diagnose_script("scripts/diagnose_auto_publish_v2.py"),
        "shorts_autopublish": _run_diagnose_script("scripts/diagnose_shorts_autopublish.py"),
    }

    log_tails = {}
    for name in STATUS_LOG_NAMES:
        p = LOGS_CC / name
        if p.is_file():
            log_tails[name] = _tail_file(p, n=25)
    checks["log_tails"] = log_tails

    mobile_tasks = CONTROL_CENTER / "remote_agent" / "allowed_tasks.json"
    mobile_agent = CONTROL_CENTER / "remote_agent" / "mobile_command_agent.py"
    checks["mobile_command_bridge"] = {
        "allowed_tasks_json": mobile_tasks.is_file(),
        "mobile_command_agent_py": mobile_agent.is_file(),
    }
    if be[0] == 200:
        mc = _http_get("http://127.0.0.1:8765/api/mobile-command/tasks")
        checks["mobile_command_bridge"]["tasks_http"] = mc[0]

    safe_mode = bool(critical_reasons)
    if safe_mode and args.mode in ("monitor", "repair") and args.mode != "dry-run":
        SAFE_MODE_FLAG.parent.mkdir(parents=True, exist_ok=True)
        if args.safe_repair:
            SAFE_MODE_FLAG.write_text(
                "\n".join(
                    [
                        f"created_at={datetime.now(timezone.utc).isoformat()}",
                        "reasons=" + json.dumps(critical_reasons, ensure_ascii=False),
                    ]
                ),
                encoding="utf-8",
            )

    # Repairs
    if args.allow_server_restart and be[0] != 200 and args.safe_repair:
        repairs["server_mode"] = _maybe_restart_server_mode(do_it=(args.mode not in ("dry-run", "report")))
        time.sleep(4)
        be = _http_get("http://127.0.0.1:8765/api/automation/status")
        checks["http"]["backend_automation_http_after_repair"] = be[0]

    if args.allow_stale_lock_cleanup and args.safe_repair:
        repairs["shorts_lock"] = _maybe_move_stale_shorts_lock(do_it=(args.mode not in ("dry-run", "report")))

    if args.allow_launchagent_reload and args.mode == "repair" and args.safe_repair:
        repairs["launchagent_kickstart"] = _maybe_launchagent_reload(
            ["com.stateverge.nyc.autopublish", "com.stateverge.shorts.autopublish"],
            do_it=True,
        )

    if args.safe_repair and args.mode not in ("dry-run", "report"):
        repairs["stale_jobs"] = _maybe_repair_stale_jobs(do_it=True)
    elif args.safe_repair:
        repairs["stale_jobs"] = _maybe_repair_stale_jobs(do_it=False)

    overall = "ok"
    if any(v.get("status") == "critical" for v in vols.values()) or safe_mode:
        overall = "critical"
    elif any(v.get("status") == "warning" for v in vols.values()) or checks["http"]["backend_automation_http"] != 200:
        overall = "warning"

    safe_upload = (
        not safe_mode
        and checks["tokens_presence"]["channel_guard_exists"]
        and checks["tokens_presence"]["token_json_exists"]
        and checks["tokens_presence"]["token_shorts_json_exists"]
        and not checks["processes"]["duplicate_shorts_workers"]
        and yt_up == 0
    )

    idle_work: dict[str, Any] = {
        "idle_work_started": False,
        "idle_work_result": None,
        "idle_work_report_path": "",
        "idle_work_skipped_reason": "",
    }
    idle_policy: dict[str, Any] = {}
    try:
        apj = json.loads((STATEVERGE / "config" / "agent_permissions.json").read_text(encoding="utf-8"))
        idle_policy = apj.get("idle_work_policy") if isinstance(apj.get("idle_work_policy"), dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        idle_policy = {}
    idle_enabled = bool(idle_policy.get("enabled"))
    dup_diag_bad = False
    try:
        d1 = checks.get("diagnostics", {}).get("auto_publish_v2") or {}
        d2 = checks.get("diagnostics", {}).get("shorts_autopublish") or {}
        if isinstance(d1, dict) and int(d1.get("returncode") or 0) != 0:
            dup_diag_bad = True
        if isinstance(d2, dict) and int(d2.get("returncode") or 0) != 0:
            dup_diag_bad = True
    except (TypeError, ValueError):
        dup_diag_bad = False

    if (
        idle_enabled
        and not critical_reasons
        and overall == "ok"
        and yt_up == 0
        and shorts_w < 2
        and ffmpeg_n < 2
        and not dup_diag_bad
        and args.mode in ("monitor", "repair")
        and args.safe_repair
        and args.mode not in ("dry-run", "report")
    ):
        py = (
            str(STATEVERGE / ".venv_audio" / "bin" / "python3")
            if (STATEVERGE / ".venv_audio" / "bin" / "python3").is_file()
            else sys.executable
        )
        script = STATEVERGE / "scripts" / "agent_idle_asset_organizer.py"
        if script.is_file():
            idle_work["idle_work_started"] = True
            try:
                r = subprocess.run(
                    [
                        py,
                        str(script),
                        "--mode",
                        "run-once",
                        "--safe-only",
                        "--max-runtime-minutes",
                        str(int(idle_policy.get("max_runtime_minutes_per_cycle") or 60)),
                    ],
                    cwd=str(STATEVERGE),
                    capture_output=True,
                    text=True,
                    timeout=int(idle_policy.get("max_runtime_minutes_per_cycle") or 60) * 60 + 120,
                    check=False,
                )
                idle_work["idle_work_result"] = {
                    "returncode": int(r.returncode),
                    "stderr_tail": (r.stderr or "")[-2000:],
                }
                idle_work["idle_work_report_path"] = str(CONTROL_CENTER / "logs" / "idle_asset_organizer_latest.md")
            except Exception as exc:  # noqa: BLE001
                idle_work["idle_work_skipped_reason"] = repr(exc)
        else:
            idle_work["idle_work_skipped_reason"] = "idle_organizer_script_missing"
    else:
        idle_work["idle_work_skipped_reason"] = (
            "conditions_not_met"
            if idle_enabled
            else "idle_work_disabled_or_permissions_missing"
        )

    payload: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": args.mode,
        "permissions": PERMISSIONS,
        "operation_blocked_by_policy": operation_blocked,
        "checks": checks,
        "repairs": repairs,
        "overall_status": overall,
        "safe_to_run_dry_run": overall != "critical" or not safe_mode,
        "safe_to_enable_schedule": overall == "ok" and be[0] == 200,
        "safe_to_enable_upload": bool(safe_upload),
        "safe_mode_flag": safe_mode,
        "safe_mode_reasons": critical_reasons,
        "latest_json": str(LATEST_JSON),
        "latest_md": str(LATEST_MD),
        "idle_work": idle_work,
    }
    try:
        scripts_dir = STATEVERGE / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from ai.ai_client import ai_request

        brief = json.dumps(
            {"overall_status": overall, "processes": checks.get("processes"), "http": checks.get("http")},
            default=str,
        )[:8000]
        payload["ai_ops_summary"] = ai_request(
            "ops_report_summary",
            brief,
            system_hint="Return JSON with keys overall_summary, top_issues, recommended_next_actions, risk_level.",
        )
    except Exception:
        payload["ai_ops_summary"] = {"ok": False, "fallback_required": True, "error_type": "ai_import_or_call_failed"}
    payload["remediation_plan"] = _run_agent_remediation_scan()
    return payload


def write_outputs(payload: dict[str, Any], *, json_only: bool) -> None:
    LOGS_CC.mkdir(parents=True, exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)
    ts = utc_ts()
    hist_j = HISTORY / f"agent_ops_{ts}.json"
    hist_m = HISTORY / f"agent_ops_{ts}.md"
    LATEST_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    hist_j.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if json_only:
        return
    lines = [
        "# Agent Operations Monitor",
        "",
        f"- **generated_at**: `{payload.get('generated_at')}`",
        f"- **overall_status**: **{payload.get('overall_status')}**",
        f"- **safe_mode**: {payload.get('safe_mode_flag')}",
        f"- **safe_to_enable_upload**: {payload.get('safe_to_enable_upload')}",
        "",
        "## Volumes",
        "",
        "```json",
        json.dumps(payload.get("checks", {}).get("volumes"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## HTTP",
        "",
        "```json",
        json.dumps(payload.get("checks", {}).get("http"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Processes",
        "",
        "```json",
        json.dumps(payload.get("checks", {}).get("processes"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Repairs",
        "",
        "```json",
        json.dumps(payload.get("repairs"), indent=2, ensure_ascii=False),
        "```",
        "",
        "## Safe mode reasons",
        "",
        "```json",
        json.dumps(payload.get("safe_mode_reasons"), indent=2, ensure_ascii=False),
        "```",
        "",
    ]
    text = "\n".join(lines)
    LATEST_MD.write_text(text, encoding="utf-8")
    hist_m.write_text(text, encoding="utf-8")
    nf = os.environ.get("AGENT_NOTIFY_FILE") or getattr(sys.modules[__name__], "_notify_file", None)
    if nf:
        try:
            Path(nf).write_text(text[-8000:], encoding="utf-8")
        except OSError:
            pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("monitor", "repair", "dry-run", "report"), default="monitor")
    ap.add_argument("--interval-sec", type=int, default=0)
    ap.add_argument("--safe-repair", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-server-restart", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-stale-lock-cleanup", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-launchagent-reload", action=argparse.BooleanOptionalAction, default=False)
    ap.add_argument("--allow-upload", action="store_true", default=False)
    ap.add_argument("--allow-token-edit", action="store_true", default=False)
    ap.add_argument("--allow-delete", action="store_true", default=False)
    ap.add_argument("--json-only", action="store_true", default=False)
    ap.add_argument("--notify-file", default="")
    args = ap.parse_args()
    if args.allow_upload or args.allow_token_edit or args.allow_delete:
        print(
            json.dumps(
                {
                    "error": "forbidden_flags",
                    "operation_blocked_by_policy": ["upload/token_edit/delete disabled at CLI"],
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    if args.notify_file:
        os.environ["AGENT_NOTIFY_FILE"] = args.notify_file

    def once() -> dict[str, Any]:
        if args.mode == "report":
            if LATEST_JSON.is_file():
                return json.loads(LATEST_JSON.read_text(encoding="utf-8"))
            return {"error": "no_latest_report"}
        return run_cycle(args)

    payload = once()
    write_outputs(payload if isinstance(payload, dict) else {"error": "bad_payload"}, json_only=args.json_only)

    if args.interval_sec and args.interval_sec > 0:
        while True:
            time.sleep(float(args.interval_sec))
            payload = run_cycle(args)
            write_outputs(payload, json_only=args.json_only)

    print(json.dumps({"ok": True, "overall_status": payload.get("overall_status")}, indent=2))
    return 0 if payload.get("overall_status") != "critical" else 0


if __name__ == "__main__":
    raise SystemExit(main())
