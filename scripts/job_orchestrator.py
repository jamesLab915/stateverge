#!/usr/bin/env python3
"""Unified Job Orchestrator v1 for StateVerge Media OS.

Queue semantics:
- ``pending`` in ``queue/queue.json`` lists job_ids awaiting a worker (``queued`` or ``retrying``).
- Dequeue order: higher ``priority`` first; ties broken by ``created_at`` ascending (FIFO).
- **Dependencies:** jobs with ``depends_on`` are skipped until every dependency is ``completed``.
  Such jobs stay ``status=queued`` with ``blocked_reason=null`` until runnable (documented: do not
  confuse with ``blocked``; only doctor / hard blocks use ``status=blocked``).
- **Doctor gate:** before spawning ``nyc_long_upload`` / ``shorts_cut_upload`` (or legacy alias
  ``shorts_upload``), the worker reads
  ``stateverge_doctor_report.json`` using the same candidate path order as ``doctor_gate_client.py``.
  If upload is blocked, the job becomes ``status=blocked``, ``blocked_reason=doctor_gate``, and is
  removed from ``pending`` (job file remains under ``jobs/`` for inspection).

Fail-open: library functions catch exceptions where used by API importers; CLI prints errors to stderr.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ORCH_VERSION = 1
_STATEVERGE_ROOT = Path.home() / "StateVerge"
_SCRIPTS_ROOT = Path(__file__).resolve().parent
_NYC_AUTO = _SCRIPTS_ROOT / "nyc_auto"

UPLOAD_JOB_TYPES = frozenset({"nyc_long_upload", "shorts_upload", "shorts_cut_upload"})

JOB_TYPE_DEFAULT_TIMEOUT: dict[str, int] = {
    "nyc_long_upload": 4 * 3600,
    "shorts_upload": 3 * 3600,
    "shorts_cut_upload": 3 * 3600,
    "semantic_music_refresh": 3600,
    "scene_signal_refresh": 3600,
    "media_index_refresh": 3 * 3600,
    "doctor_run": 3600,
    "lifecycle_refresh": 1800,
    "location_recovery": 7200,
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".orch_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _safe_read_json(path: Path) -> Any | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def resolve_orchestrator_base() -> Path:
    """Prefer ``/Volumes/SV_CACHE/orchestrator``; else ``~/StateVerge/data/orchestrator``."""
    primary_parent = Path("/Volumes/SV_CACHE")
    try:
        if primary_parent.is_dir():
            base = primary_parent / "orchestrator"
            base.mkdir(parents=True, exist_ok=True)
            if os.access(base, os.W_OK):
                return base
    except OSError:
        pass
    fb = Path.home() / "StateVerge" / "data" / "orchestrator"
    fb.mkdir(parents=True, exist_ok=True)
    return fb


def orchestrator_layout(base: Path | None = None) -> dict[str, Path]:
    b = base or resolve_orchestrator_base()
    return {
        "base": b,
        "jobs": b / "jobs",
        "queue": b / "queue",
        "workers": b / "workers",
        "logs": b / "logs",
        "history": b / "history",
        "locks": b / "locks",
        "queue_file": b / "queue" / "queue.json",
        "state_file": b / "orchestrator_state.json",
    }


def init_orchestrator_dirs(base: Path | None = None) -> dict[str, Any]:
    """Create layout and seed queue file. Returns state dict."""
    paths = orchestrator_layout(base)
    for key in ("jobs", "queue", "workers", "logs", "history", "locks"):
        paths[key].mkdir(parents=True, exist_ok=True)
    qf = paths["queue_file"]
    if not qf.is_file():
        seed = {
            "version": _ORCH_VERSION,
            "pending": [],
            "next_seq": 1,
            "output_dir": str(paths["base"]),
        }
        atomic_write_json(qf, seed)
    state = {
        "version": _ORCH_VERSION,
        "output_dir": str(paths["base"]),
        "initialized_at": _utc_iso(),
    }
    atomic_write_json(paths["state_file"], state)
    return state


def _load_doctor_client():
    if str(_NYC_AUTO) not in sys.path:
        sys.path.insert(0, str(_NYC_AUTO))
    try:
        from doctor_gate_client import (  # type: ignore
            is_upload_blocked_by_doctor_gate,
            load_doctor_report_json,
        )

        return load_doctor_report_json, is_upload_blocked_by_doctor_gate
    except Exception:

        def load_doctor_report_json():  # type: ignore[misc]
            return None, None, ["doctor_gate_client_import_failed"]

        def is_upload_blocked_by_doctor_gate(_rep):  # type: ignore[misc]
            return False, {"reason": "import_failed"}

        return load_doctor_report_json, is_upload_blocked_by_doctor_gate


def load_queue(paths: dict[str, Path] | None = None) -> dict[str, Any]:
    paths = paths or orchestrator_layout()
    raw = _safe_read_json(paths["queue_file"])
    if not isinstance(raw, dict):
        return {"version": _ORCH_VERSION, "pending": [], "next_seq": 1, "output_dir": str(paths["base"])}
    raw.setdefault("version", _ORCH_VERSION)
    raw.setdefault("pending", [])
    raw.setdefault("next_seq", 1)
    raw.setdefault("output_dir", str(paths["base"]))
    if not isinstance(raw["pending"], list):
        raw["pending"] = []
    return raw


def save_queue(queue: dict[str, Any], paths: dict[str, Path] | None = None) -> None:
    paths = paths or orchestrator_layout()
    atomic_write_json(paths["queue_file"], queue)


class QueueLock:
    """Exclusive lock for queue.json mutations (submit / claim / dequeue)."""

    def __init__(self, paths: dict[str, Path]):
        self.paths = paths
        self._fh: Any = None

    def __enter__(self) -> QueueLock:
        self.paths["queue"].mkdir(parents=True, exist_ok=True)
        p = self.paths["queue"] / ".orch_queue.lock"
        self._fh = open(p, "a+", encoding="utf-8")
        fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *_exc: Any) -> None:
        try:
            if self._fh:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
                self._fh.close()
        except OSError:
            pass
        self._fh = None


def _new_job_record(
    job_id: str,
    job_type: str,
    *,
    priority: int,
    metadata: dict[str, Any],
    depends_on: list[str],
    max_retries: int | None,
    timeout_seconds: int | None,
) -> dict[str, Any]:
    to = timeout_seconds if timeout_seconds is not None else JOB_TYPE_DEFAULT_TIMEOUT.get(job_type, 1800)
    mr = max_retries if max_retries is not None else 2
    return {
        "job_id": job_id,
        "job_type": job_type,
        "created_at": _utc_iso(),
        "started_at": None,
        "finished_at": None,
        "status": "queued",
        "priority": int(priority),
        "retry_count": 0,
        "max_retries": int(mr),
        "timeout_seconds": int(to),
        "worker_id": None,
        "depends_on": list(depends_on),
        "doctor_gate_checked": False,
        "upload_allowed": True,
        "result_path": None,
        "logs_path": None,
        "error": None,
        "warnings": [],
        "metadata": dict(metadata),
        "blocked_reason": None,
    }


def _job_path(paths: dict[str, Path], job_id: str) -> Path:
    return paths["jobs"] / f"{job_id}.json"


def get_job(job_id: str, paths: dict[str, Path] | None = None) -> dict[str, Any] | None:
    try:
        paths = paths or orchestrator_layout()
        data = _safe_read_json(_job_path(paths, job_id))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def update_job(job_id: str, patch: dict[str, Any], paths: dict[str, Path] | None = None) -> dict[str, Any]:
    paths = paths or orchestrator_layout()
    cur = get_job(job_id, paths) or {}
    cur.update(patch)
    atomic_write_json(_job_path(paths, job_id), cur)
    return cur


def _allocate_job_id(queue: dict[str, Any]) -> str:
    seq = int(queue.get("next_seq") or 1)
    job_id = f"job-{seq:06d}"
    queue["next_seq"] = seq + 1
    return job_id


def submit_job(
    job_type: str,
    priority: int = 50,
    metadata: dict[str, Any] | None = None,
    depends_on: list[str] | None = None,
    *,
    max_retries: int | None = None,
    timeout_seconds: int | None = None,
    paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Create job file and append to queue ``pending``. Returns job dict or error envelope."""
    try:
        paths = paths or orchestrator_layout()
        paths["jobs"].mkdir(parents=True, exist_ok=True)
        paths["queue"].mkdir(parents=True, exist_ok=True)
        with QueueLock(paths):
            queue = load_queue(paths)
            job_id = _allocate_job_id(queue)
            jt = str(job_type or "").strip()
            meta_use = dict(metadata or {})
            if jt == "shorts_upload":
                alias = meta_use.get("orchestrator_job_type_alias")
                if not isinstance(alias, dict):
                    meta_use["orchestrator_job_type_alias"] = {
                        "canonical_job_type": "shorts_cut_upload",
                        "submitted_as": "shorts_upload",
                        "note": "Legacy alias; argv matches shorts_cut_upload.",
                    }

            job = _new_job_record(
                job_id,
                jt,
                priority=priority,
                metadata=meta_use,
                depends_on=list(depends_on or []),
                max_retries=max_retries,
                timeout_seconds=timeout_seconds,
            )
            atomic_write_json(_job_path(paths, job_id), job)
            pending = queue.setdefault("pending", [])
            if isinstance(pending, list) and job_id not in pending:
                pending.append(job_id)
            save_queue(queue, paths)
        return job
    except Exception as exc:
        return {"ok": False, "error": repr(exc), "job_id": None}


def enqueue(job: dict[str, Any], paths: dict[str, Path] | None = None) -> None:
    """Append existing job_id to pending (job file must already exist)."""
    paths = paths or orchestrator_layout()
    job_id = str(job.get("job_id") or "").strip()
    if not job_id:
        return
    with QueueLock(paths):
        queue = load_queue(paths)
        pending = queue.setdefault("pending", [])
        if job_id not in pending:
            pending.append(job_id)
        save_queue(queue, paths)


def _script_path(rel_under_scripts: str) -> Path:
    return _SCRIPTS_ROOT / rel_under_scripts


def resolve_job_argv(job: dict[str, Any]) -> tuple[list[str] | None, str | None]:
    """Return (argv, error_message). cwd should be StateVerge repo root."""
    jt = str(job.get("job_type") or "")
    py = sys.executable
    job_id = str(job.get("job_id") or "")

    def must_exist(p: Path) -> str | None:
        if not p.is_file():
            return f"script_missing:{p}"
        return None

    meta = job.get("metadata") if isinstance(job.get("metadata"), dict) else {}
    extra: list[str] = []
    if isinstance(meta.get("argv_extra"), list):
        extra = [str(x) for x in meta["argv_extra"]]

    if jt == "nyc_long_upload":
        script = _script_path("jobs/nyc_cut_upload_job.py")
        err = must_exist(script)
        if err:
            return None, err
        argv = [
            py,
            str(script),
            "--upload",
            "--no-public",
            "--privacy-status",
            str(meta.get("privacy_status") or "unlisted"),
            "--job-id",
            job_id or uuid.uuid4().hex[:12],
        ]
        if meta.get("source_video"):
            argv.extend(["--source-video", str(meta["source_video"])])
        if meta.get("cut_start"):
            argv.extend(["--cut-start", str(meta["cut_start"])])
        if meta.get("duration_seconds") is not None:
            argv.extend(["--duration-seconds", str(meta["duration_seconds"])])
        if meta.get("audio_mode"):
            argv.extend(["--audio-mode", str(meta["audio_mode"])])
        if meta.get("music_category"):
            argv.extend(["--music-category", str(meta["music_category"])])
        if meta.get("music_volume") is not None:
            argv.extend(["--music-volume", str(meta["music_volume"])])
        if meta.get("original_volume") is not None:
            argv.extend(["--original-volume", str(meta["original_volume"])])
        if meta.get("channel"):
            argv.extend(["--channel", str(meta["channel"])])
        if meta.get("allow_public"):
            argv.append("--allow-public")
        argv.extend(extra)
        return argv, None

    if jt in {"shorts_upload", "shorts_cut_upload"}:
        script = _script_path("jobs/shorts_cut_upload_job.py")
        err = must_exist(script)
        if err:
            return None, err
        sid = job_id or uuid.uuid4().hex[:12]
        argv = [
            py,
            str(script),
            "--job-id",
            sid,
            "--upload",
            "--no-public",
            "--privacy-status",
            str(meta.get("privacy_status") or "unlisted"),
        ]
        if meta.get("duration_seconds") is not None:
            argv.extend(["--duration-seconds", str(meta["duration_seconds"])])
        if meta.get("audio_mode"):
            argv.extend(["--audio-mode", str(meta["audio_mode"])])
        if meta.get("music_category"):
            argv.extend(["--music-category", str(meta["music_category"])])
        if meta.get("music_volume") is not None:
            argv.extend(["--music-volume", str(meta["music_volume"])])
        if meta.get("dry_run"):
            argv.append("--dry-run")
        if meta.get("agent_private_upload"):
            argv.append("--agent-private-upload")
        argv.extend(extra)
        return argv, None

    if jt == "semantic_music_refresh":
        for rel in ("reclassify_music_library.py", "analyze_media_semantics.py"):
            script = _script_path(rel)
            if script.is_file():
                return [py, str(script), *extra], None
        return None, "semantic_music_refresh:no_script"

    if jt == "scene_signal_refresh":
        for rel in ("enrich_scene_signals.py", "diagnose_scene_signals.py"):
            script = _script_path(rel)
            if script.is_file():
                return [py, str(script), *extra], None
        return None, "scene_signal_refresh:no_script"

    if jt == "media_index_refresh":
        script = _script_path("index_media_library.py")
        err = must_exist(script)
        if err:
            return None, err
        return [py, str(script), *extra], None

    if jt == "doctor_run":
        script = _script_path("stateverge_doctor.py")
        err = must_exist(script)
        if err:
            return None, err
        return [py, str(script), *extra], None

    if jt == "lifecycle_refresh":
        script = _script_path("asset_lifecycle_intelligence.py")
        err = must_exist(script)
        if err:
            return None, err
        return [py, str(script), *extra], None

    if jt == "location_recovery":
        script = _script_path("location_signal_recovery_v2.py")
        err = must_exist(script)
        if err:
            return None, err
        return [py, str(script), *extra], None

    return None, f"unknown_job_type:{jt}"


def evaluate_doctor_gate_for_job(job: dict[str, Any]) -> dict[str, Any]:
    """Return patch dict for job (may set blocked) or {}."""
    jt = str(job.get("job_type") or "")
    if jt not in UPLOAD_JOB_TYPES:
        return {}
    load_rep, is_blocked = _load_doctor_client()
    rep, _path_used, warns = load_rep()
    blocked, detail = is_blocked(rep)
    upload_allowed = bool(detail.get("upload_allowed", True)) if isinstance(detail, dict) else True
    patch: dict[str, Any] = {
        "doctor_gate_checked": True,
        "upload_allowed": upload_allowed,
    }
    if warns:
        w = list(job.get("warnings") or [])
        w.extend(str(x) for x in warns if x)
        patch["warnings"] = w
    if blocked:
        patch.update(
            {
                "status": "blocked",
                "blocked_reason": "doctor_gate",
                "error": json.dumps(detail, ensure_ascii=False)[:4000],
                "finished_at": _utc_iso(),
            }
        )
    return patch


def dependency_satisfied(job: dict[str, Any], paths: dict[str, Path]) -> bool:
    deps = job.get("depends_on") if isinstance(job.get("depends_on"), list) else []
    for dep_id in deps:
        dep = get_job(str(dep_id), paths)
        if not dep or str(dep.get("status")) != "completed":
            return False
    return True


def _remove_from_pending_unlocked(job_id: str, queue: dict[str, Any], paths: dict[str, Path]) -> None:
    pend = queue.get("pending")
    if isinstance(pend, list) and job_id in pend:
        queue["pending"] = [x for x in pend if str(x) != job_id]
        save_queue(queue, paths)


def claim_next_runnable_job(paths: dict[str, Path] | None = None) -> dict[str, Any] | None:
    """Under queue lock: resolve doctor blocks, dequeue next job_id from pending, return job dict."""
    paths = paths or orchestrator_layout()
    with QueueLock(paths):
        for _ in range(500):
            queue = load_queue(paths)
            pending = [str(x) for x in queue.get("pending") or [] if str(x)]
            candidates: list[dict[str, Any]] = []
            for jid in pending:
                job = get_job(jid, paths)
                if not job:
                    continue
                st = str(job.get("status") or "")
                if st not in {"queued", "retrying"}:
                    continue
                if not dependency_satisfied(job, paths):
                    continue
                candidates.append(job)

            if not candidates:
                return None

            def sort_key(j: dict[str, Any]) -> tuple[int, str]:
                pr = -int(j.get("priority") or 0)
                created = str(j.get("created_at") or "")
                return (pr, created)

            candidates.sort(key=sort_key)
            job = candidates[0]
            jid = str(job.get("job_id"))
            jt = str(job.get("job_type") or "")
            if jt in UPLOAD_JOB_TYPES:
                patch = evaluate_doctor_gate_for_job(job)
                if patch.get("status") == "blocked":
                    job.update(patch)
                    atomic_write_json(_job_path(paths, jid), job)
                    _remove_from_pending_unlocked(jid, queue, paths)
                    continue
            _remove_from_pending_unlocked(jid, queue, paths)
            return dict(job)
    return None


def iter_recent_jobs(
    *,
    limit: int = 100,
    paths: dict[str, Path] | None = None,
) -> list[dict[str, Any]]:
    paths = paths or orchestrator_layout()
    root = paths["jobs"]
    try:
        files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for p in files[:limit]:
        data = _safe_read_json(p)
        if isinstance(data, dict):
            out.append(data)
    return out


def count_orchestrator_snapshot(paths: dict[str, Path] | None = None) -> dict[str, int]:
    paths = paths or orchestrator_layout()
    jobs = iter_recent_jobs(limit=5000, paths=paths)
    q = load_queue(paths)
    pending_list = [str(x) for x in (q.get("pending") or []) if str(x)]
    pending_set = set(pending_list)
    running = 0
    failed = 0
    blocked = 0
    retrying = 0
    for j in jobs:
        st = str(j.get("status") or "")
        if st == "running":
            running += 1
        elif st == "failed":
            failed += 1
        elif st == "blocked":
            blocked += 1
        elif st == "retrying":
            retrying += 1
    queued_in_pending = 0
    for jid in pending_list:
        job = get_job(jid, paths)
        if not job:
            continue
        st = str(job.get("status") or "")
        if st in {"queued", "retrying"}:
            queued_in_pending += 1
    try:
        worker_count = len(list(paths["workers"].glob("*.json")))
    except OSError:
        worker_count = 0
    return {
        "QUEUE_COUNT": queued_in_pending,
        "RUNNING_COUNT": running,
        "FAILED_COUNT": failed,
        "BLOCKED_COUNT": blocked,
        "RETRYING_COUNT": retrying,
        "WORKER_COUNT": worker_count,
        "ERROR_COUNT": failed,
    }


def print_unified_status_footer(paths: dict[str, Path] | None = None) -> None:
    c = count_orchestrator_snapshot(paths)
    print("UNIFIED_JOB_ORCHESTRATOR_V1_DONE")
    print(f"QUEUE_COUNT={c['QUEUE_COUNT']}")
    print(f"RUNNING_COUNT={c['RUNNING_COUNT']}")
    print(f"FAILED_COUNT={c['FAILED_COUNT']}")
    print(f"BLOCKED_COUNT={c['BLOCKED_COUNT']}")
    print(f"WORKER_COUNT={c['WORKER_COUNT']}")
    print(f"ERROR_COUNT={c['ERROR_COUNT']}")


def api_safe_submit(body: dict[str, Any]) -> dict[str, Any]:
    """Fail-open envelope for FastAPI."""
    try:
        paths = orchestrator_layout()
        if not paths["base"].exists():
            init_orchestrator_dirs(paths["base"])
        jt = str(body.get("job_type") or "").strip()
        if not jt:
            return {"ok": False, "error": "missing_job_type"}
        pr = body.get("priority")
        priority = int(pr) if pr is not None else 50
        meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
        job = submit_job(jt, priority=priority, metadata=meta)
        if isinstance(job, dict) and job.get("ok") is False:
            return {"ok": False, "error": job.get("error"), "detail": job}
        if not isinstance(job, dict) or not job.get("job_id"):
            return {"ok": False, "error": "submit_unexpected_result", "detail": job}
        return {"ok": True, "job": job}
    except Exception as exc:
        return {"ok": False, "error": repr(exc)}


def api_safe_jobs(limit: int = 80) -> dict[str, Any]:
    try:
        paths = orchestrator_layout()
        if not paths["jobs"].is_dir():
            return {"ok": False, "message": "orchestrator_jobs_dir_missing", "jobs": []}
        return {"ok": True, "jobs": iter_recent_jobs(limit=limit, paths=paths)}
    except Exception as exc:
        return {"ok": False, "message": repr(exc), "jobs": []}


def api_safe_workers() -> dict[str, Any]:
    try:
        paths = orchestrator_layout()
        wd = paths["workers"]
        if not wd.is_dir():
            return {"ok": False, "message": "orchestrator_workers_dir_missing", "workers": []}
        workers: list[dict[str, Any]] = []
        for p in sorted(wd.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
            data = _safe_read_json(p)
            if isinstance(data, dict):
                workers.append(data)
        return {"ok": True, "workers": workers}
    except Exception as exc:
        return {"ok": False, "message": repr(exc), "workers": []}


def _cli_submit(raw_json: str) -> int:
    try:
        body = json.loads(raw_json)
        if not isinstance(body, dict):
            print(json.dumps({"ok": False, "error": "json_not_object"}, indent=2))
            return 1
        out = api_safe_submit(body)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("ok") else 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": repr(exc)}, indent=2))
        return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="StateVerge Unified Job Orchestrator v1")
    ap.add_argument("--init", action="store_true", help="Create dirs + seed queue.json")
    ap.add_argument("--demo-submit", action="store_true", help="Enqueue doctor_run + semantic_music_refresh")
    ap.add_argument("--status", action="store_true", help="Print orchestrator counts footer")
    ap.add_argument("--submit", type=str, default=None, help="JSON body for submit_job (inline)")
    ns = ap.parse_args(argv)
    rc = 0
    try:
        if ns.init:
            init_orchestrator_dirs()
        if ns.submit:
            rc = max(rc, _cli_submit(ns.submit))
        if ns.demo_submit:
            init_orchestrator_dirs()
            r1 = submit_job("doctor_run", priority=60, metadata={"source": "demo-submit"})
            if not isinstance(r1, dict) or not r1.get("job_id"):
                rc = 1
            r2 = submit_job("semantic_music_refresh", priority=40, metadata={"source": "demo-submit"})
            if not isinstance(r2, dict) or not r2.get("job_id"):
                rc = 1
        if ns.status or ns.demo_submit:
            print_unified_status_footer()
        if ns.demo_submit:
            return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": repr(exc)}, indent=2), file=sys.stderr)
        return 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
