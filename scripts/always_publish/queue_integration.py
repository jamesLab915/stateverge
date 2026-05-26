"""Hooks for auto_publish_queue runners (Always Deliver v2)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]


def _ensure_scripts_path() -> None:
    sp = str(_SCRIPTS)
    if sp not in sys.path:
        sys.path.insert(0, sp)


def preflight_long_upload(*, force_schedule: bool = False) -> dict[str, Any]:
    _ensure_scripts_path()
    from always_publish.daily_delivery_state import load_delivery_state, record_attempt  # noqa: WPS433
    from always_publish.failure_recovery import should_retry_now  # noqa: WPS433
    from always_publish.quota_guard import long_upload_allowed  # noqa: WPS433

    state = load_delivery_state()
    retry_ok, retry_reason = should_retry_now()
    gate = long_upload_allowed(kind="1h", force=force_schedule)
    allowed = bool(gate.get("allowed")) and retry_ok
    reason = str(gate.get("reason") or "")
    if not retry_ok:
        reason = retry_reason
    if not allowed and not force_schedule and not str(reason).startswith("quota_gate_"):
        record_attempt(kind="long", failure_reason=reason, failure_class="hard" if "hard" in reason else "soft")
    return {
        "allowed": allowed or force_schedule,
        "reason": reason,
        "gate": gate,
        "delivery_state": state,
        "retry_ok": retry_ok,
    }


def preflight_shorts_upload(*, force: bool = False) -> dict[str, Any]:
    _ensure_scripts_path()
    from always_publish.daily_delivery_state import load_delivery_state, record_attempt  # noqa: WPS433
    from always_publish.failure_recovery import should_retry_now  # noqa: WPS433
    from always_publish.quota_guard import shorts_upload_allowed  # noqa: WPS433

    state = load_delivery_state()
    retry_ok, retry_reason = should_retry_now()
    gate = shorts_upload_allowed(force=force)
    allowed = bool(gate.get("allowed")) and retry_ok
    reason = str(gate.get("reason") or "")
    if not retry_ok:
        reason = retry_reason
    if not allowed and not force and not str(reason).startswith("quota_gate_"):
        record_attempt(kind="shorts", failure_reason=reason, failure_class="soft")
    return {
        "allowed": allowed or force,
        "reason": reason,
        "gate": gate,
        "delivery_state": state,
        "retry_ok": retry_ok,
    }


def record_queue_outcome(*, kind: str, payload: dict[str, Any]) -> None:
    _ensure_scripts_path()
    from always_publish.daily_delivery_state import refresh_delivery_counts, record_attempt  # noqa: WPS433
    from always_publish.failure_recovery import record_queue_failure  # noqa: WPS433

    status = str(payload.get("status") or "").lower()
    block = str(payload.get("block_reason") or "")
    if status in ("uploaded", "completed") or payload.get("uploaded") is True:
        refresh_delivery_counts(persist=True)
        record_attempt(kind=kind)
        return
    if block or status in ("blocked", "upload_failed", "schedule_gated"):
        record_queue_failure(
            kind=kind,
            reason=block or status,
            status=status,
            block_reason=block,
        )
        return
    record_attempt(kind=kind, failure_reason=status or "unknown")


def blocked_payload(kind: str, preflight: dict[str, Any]) -> dict[str, Any]:
    gate = preflight.get("gate") or {}
    return {
        "status": "blocked",
        "block_reason": str(preflight.get("reason") or "quota_blocked"),
        "video_type": "short" if kind == "shorts" else "long",
        "channel": "SHORTS" if kind == "shorts" else "NYC_LONG",
        "always_deliver_v2": True,
        "quota_gate": gate,
        "delivery_state": preflight.get("delivery_state"),
    }


def print_blocked(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def prefer_delivery_queue_source() -> bool:
    """Always Deliver v2: prefer publish_pack/delivery_queue when pro agent enabled."""
    try:
        _ensure_scripts_path()
        from davinci_production_agent.delivery_queue import prefer_delivery_queue_for_upload  # noqa: WPS433

        return prefer_delivery_queue_for_upload()
    except Exception:
        return False


def delivery_queue_upload_only() -> bool:
    """Production Stabilization v1: autopublish must not scan ready_to_upload."""
    try:
        _ensure_scripts_path()
        from davinci_production_agent.delivery_queue import delivery_queue_upload_only as _only  # noqa: WPS433

        return bool(_only())
    except Exception:
        return True


def next_delivery_manifest_for_kind(kind: str) -> Path | None:
    _ensure_scripts_path()
    from davinci_production_agent.delivery_queue import list_upload_ready_manifests, read_manifest  # noqa: WPS433
    from davinci_production_agent.upload_from_queue import preflight_upload  # noqa: WPS433

    qkind = "shorts" if kind == "shorts" else "long"
    for m in list_upload_ready_manifests(kind=qkind):  # type: ignore[arg-type]
        pf = preflight_upload(m, dry_run=True)
        if pf.get("allowed"):
            data = read_manifest(m)
            if data:
                return m
    return None


def delivery_manifest_to_video_meta(manifest_path: Path) -> dict[str, Any] | None:
    _ensure_scripts_path()
    from davinci_production_agent.delivery_queue import read_manifest  # noqa: WPS433

    data = read_manifest(manifest_path)
    if not data:
        return None
    rp = Path(str(data.get("render_path") or ""))
    if not rp.is_file():
        return None
    try:
        sz = int(rp.stat().st_size)
        mt = int(rp.stat().st_mtime)
    except OSError:
        return None
    meta = data.get("metadata") or {}
    return {
        "path": rp,
        "source_path": str(rp),
        "resolved_path": str(rp.resolve()),
        "basename": rp.name,
        "stem": rp.stem,
        "size_bytes": sz,
        "mtime": mt,
        "delivery_queue_manifest": str(manifest_path),
        "job_id": data.get("job_id"),
        "preset": data.get("preset"),
        "from_delivery_queue": True,
        **({} if not isinstance(meta, dict) else meta),
    }


def run_long_upload_from_delivery_queue(args: Any, *, warnings: list[str], errors: list[str]) -> int | None:
    """If delivery_queue_upload_only, handle long upload from manifest and return exit code; else None."""
    if not delivery_queue_upload_only():
        return None
    manifest = next_delivery_manifest_for_kind("long")
    if manifest is None:
        payload = {
            "status": "blocked",
            "block_reason": "no_delivery_queue_long_ready",
            "video_type": "long",
            "channel": "NYC_LONG",
            "delivery_queue_upload_only": True,
            "warnings": warnings,
        }
        print_blocked(payload)
        return 0
    _ensure_scripts_path()
    from davinci_production_agent.upload_from_queue import execute_upload  # noqa: WPS433

    dry_run = not bool(getattr(args, "upload", False))
    result = execute_upload(manifest, dry_run=dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    try:
        record_queue_outcome(kind="long", payload=result)
    except Exception:
        pass
    if result.get("status") == "uploaded":
        return 0
    if result.get("status") == "blocked":
        return 0
    return int(result.get("exit_code") or 1)


def run_shorts_upload_from_delivery_queue(args: Any) -> int | None:
    """Shorts runner: delivery_queue only when stabilization v1 active."""
    if not delivery_queue_upload_only():
        return None
    manifest = next_delivery_manifest_for_kind("shorts")
    if manifest is None:
        print_blocked(
            {
                "status": "blocked",
                "block_reason": "no_delivery_queue_shorts_ready",
                "video_type": "short",
                "channel": "SHORTS",
                "delivery_queue_upload_only": True,
            }
        )
        return 0
    _ensure_scripts_path()
    from davinci_production_agent.upload_from_queue import execute_upload  # noqa: WPS433

    dry_run = not bool(getattr(args, "upload", False))
    result = execute_upload(manifest, dry_run=dry_run)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    try:
        record_queue_outcome(kind="shorts", payload=result)
    except Exception:
        pass
    return int(result.get("exit_code") or 0)
