#!/usr/bin/env python3
"""Orchestrator worker: single-shot ``--once`` poll + job execution (subprocess)."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO_ROOT = Path.home() / "StateVerge"
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import job_orchestrator as orch  # noqa: E402


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _recover_stale(paths: dict[str, Path]) -> None:
    """Mark ``running`` jobs as failed when lock is missing/stale (fail-open)."""
    try:
        for jp in paths["jobs"].glob("*.json"):
            jid = jp.stem
            job = orch.get_job(jid, paths)
            if not isinstance(job, dict):
                continue
            if str(job.get("status")) != "running":
                continue
            lp = paths["locks"] / f"{jid}.lock"
            stale = True
            if lp.is_file():
                try:
                    raw = lp.read_text(encoding="utf-8", errors="replace")
                    d = json.loads(raw)
                    pid = int(d.get("pid") or 0)
                    if _pid_alive(pid):
                        stale = False
                except (OSError, json.JSONDecodeError, TypeError, ValueError):
                    stale = True
            if stale:
                orch.update_job(
                    jid,
                    {
                        "status": "failed",
                        "finished_at": _utc_iso(),
                        "error": "stale_running_recovered_by_worker",
                        "worker_id": job.get("worker_id"),
                    },
                    paths,
                )
                try:
                    lp.unlink(missing_ok=True)  # type: ignore[arg-type]
                except OSError:
                    pass
    except Exception:
        pass


def _write_heartbeat(
    paths: dict[str, Path],
    worker_id: str,
    *,
    status: str,
    active_job: str | None,
) -> None:
    paths["workers"].mkdir(parents=True, exist_ok=True)
    p = paths["workers"] / f"{worker_id}.json"
    doc = {
        "worker_id": worker_id,
        "hostname": socket.gethostname(),
        "started_at": _utc_iso(),
        "last_heartbeat": _utc_iso(),
        "active_job": active_job,
        "status": status,
    }
    try:
        orch.atomic_write_json(p, doc)
    except Exception:
        pass


def _try_acquire_job_lock(paths: dict[str, Path], job_id: str, worker_id: str) -> Any | None:
    paths["locks"].mkdir(parents=True, exist_ok=True)
    lp = paths["locks"] / f"{job_id}.lock"
    fh = open(lp, "a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    fh.seek(0)
    fh.truncate()
    fh.write(
        json.dumps(
            {"pid": os.getpid(), "worker_id": worker_id, "claimed_at": _utc_iso()},
            ensure_ascii=False,
        )
    )
    fh.flush()
    return fh


def _release_job_lock(fh: Any | None) -> None:
    if not fh:
        return
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
    except OSError:
        pass


def _run_subprocess_timed(
    argv: list[str],
    *,
    cwd: Path,
    timeout_sec: float,
    log_path: Path,
) -> tuple[int, str | None]:
    """Return (returncode, timeout_error_or_none)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log_path, "a", encoding="utf-8", errors="replace") as logf:
            logf.write(f"\n=== orchestrator spawn {_utc_iso()} ===\n")
            logf.write(json.dumps(argv, ensure_ascii=False) + "\n")
            logf.flush()
            proc = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdout=logf,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = time.monotonic() + float(timeout_sec)
            rc: int | None = None
            while time.monotonic() < deadline:
                rc = proc.poll()
                if rc is not None:
                    return int(rc), None
                time.sleep(0.4)
            try:
                proc.send_signal(signal.SIGTERM)
            except OSError:
                pass
            grace = time.monotonic() + 15.0
            while time.monotonic() < grace:
                rc = proc.poll()
                if rc is not None:
                    return int(rc), "timeout_sigterm"
                time.sleep(0.2)
            try:
                proc.kill()
            except OSError:
                pass
            try:
                rc = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                rc = -9
            return int(rc), "timeout_sigkill"
    except Exception as exc:
        return 1, repr(exc)


def _best_effort_lifecycle(paths: dict[str, Path], parent_job_id: str) -> None:
    script = _SCRIPTS / "asset_lifecycle_intelligence.py"
    if not script.is_file():
        return
    log_path = paths["logs"] / f"{parent_job_id}_lifecycle_tail.log"
    argv = [sys.executable, str(script)]
    _run_subprocess_timed(argv, cwd=_REPO_ROOT, timeout_sec=1200.0, log_path=log_path)


def run_once(worker_id: str | None = None) -> int:
    wid = (worker_id or "").strip() or uuid.uuid4().hex[:12]
    paths = orch.orchestrator_layout()
    try:
        orch.init_orchestrator_dirs(paths["base"])
    except Exception:
        pass
    _recover_stale(paths)
    _write_heartbeat(paths, wid, status="idle", active_job=None)

    job = orch.claim_next_runnable_job(paths)
    if not job:
        _write_heartbeat(paths, wid, status="idle", active_job=None)
        return 0

    jid = str(job.get("job_id"))
    lock_fh = _try_acquire_job_lock(paths, jid, wid)
    if lock_fh is None:
        orch.enqueue(job, paths)
        orch.update_job(jid, {"status": "queued", "worker_id": None}, paths)
        _write_heartbeat(paths, wid, status="idle", active_job=None)
        return 0

    log_path = paths["logs"] / f"{jid}.log"
    orch.update_job(
        jid,
        {
            "status": "running",
            "started_at": _utc_iso(),
            "worker_id": wid,
            "logs_path": str(log_path),
            "error": None,
        },
        paths,
    )
    _write_heartbeat(paths, wid, status="busy", active_job=jid)

    argv, err = orch.resolve_job_argv(job)
    warnings = list(job.get("warnings") or [])
    if err and str(job.get("job_type")) in {"semantic_music_refresh", "scene_signal_refresh"}:
        warnings.append(f"orchestrator_soft_skip:{err}")
        orch.update_job(
            jid,
            {
                "status": "completed",
                "finished_at": _utc_iso(),
                "warnings": warnings,
                "error": None,
                "result_path": None,
            },
            paths,
        )
        try:
            hist = paths["history"] / f"{jid}.json"
            orch.atomic_write_json(hist, orch.get_job(jid, paths) or {})
        except Exception:
            pass
        _release_job_lock(lock_fh)
        try:
            (paths["locks"] / f"{jid}.lock").unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        _write_heartbeat(paths, wid, status="idle", active_job=None)
        return 0

    if err or not argv:
        orch.update_job(
            jid,
            {
                "status": "failed",
                "finished_at": _utc_iso(),
                "error": err or "no_argv",
                "warnings": warnings,
            },
            paths,
        )
        _release_job_lock(lock_fh)
        try:
            (paths["locks"] / f"{jid}.lock").unlink(missing_ok=True)  # type: ignore[arg-type]
        except OSError:
            pass
        _write_heartbeat(paths, wid, status="idle", active_job=None)
        return 0

    to = float(job.get("timeout_seconds") or 1800)
    rc, terr = _run_subprocess_timed(argv, cwd=_REPO_ROOT, timeout_sec=to, log_path=log_path)

    jt = str(job.get("job_type") or "")
    cur = orch.get_job(jid, paths) or job
    w = list(cur.get("warnings") or [])
    if terr:
        w.append(f"subprocess_timeout:{terr}")

    if terr:
        new_rc = int(cur.get("retry_count") or 0) + 1
        max_r = int(cur.get("max_retries") or 2)
        if new_rc > max_r:
            orch.update_job(
                jid,
                {
                    "status": "failed",
                    "finished_at": _utc_iso(),
                    "retry_count": new_rc,
                    "error": f"timeout_after_retries:{terr}",
                    "warnings": w,
                    "worker_id": wid,
                },
                paths,
            )
        else:
            orch.update_job(
                jid,
                {
                    "status": "retrying",
                    "retry_count": new_rc,
                    "started_at": None,
                    "worker_id": None,
                    "warnings": w,
                    "error": terr,
                },
                paths,
            )
            orch.enqueue({"job_id": jid}, paths)
    elif rc == 0:
        orch.update_job(
            jid,
            {
                "status": "completed",
                "finished_at": _utc_iso(),
                "error": None,
                "warnings": w,
                "result_path": None,
                "worker_id": wid,
            },
            paths,
        )
        if jt in {"nyc_long_upload", "shorts_upload", "shorts_cut_upload"}:
            try:
                _best_effort_lifecycle(paths, jid)
            except Exception as exc:
                w2 = list((orch.get_job(jid, paths) or {}).get("warnings") or [])
                w2.append(f"lifecycle_hook_failed:{exc!r}")
                orch.update_job(jid, {"warnings": w2}, paths)
    else:
        orch.update_job(
            jid,
            {
                "status": "failed",
                "finished_at": _utc_iso(),
                "error": f"nonzero_exit:{rc}",
                "warnings": w,
                "worker_id": wid,
            },
            paths,
        )

    try:
        snap = orch.get_job(jid, paths) or {}
        orch.atomic_write_json(paths["history"] / f"{jid}.json", snap)
    except Exception:
        pass

    _release_job_lock(lock_fh)
    try:
        (paths["locks"] / f"{jid}.lock").unlink(missing_ok=True)  # type: ignore[arg-type]
    except OSError:
        pass
    _write_heartbeat(paths, wid, status="idle", active_job=None)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="Poll and run at most one job")
    ap.add_argument("--worker-id", default=None)
    ns = ap.parse_args(argv)
    if not ns.once:
        print("orchestrator_worker: use --once", file=sys.stderr)
        return 2
    try:
        return run_once(ns.worker_id)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": repr(exc)}, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
