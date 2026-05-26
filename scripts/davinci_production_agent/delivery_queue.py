#!/usr/bin/env python3
"""Write/read delivery_queue manifests — sole autopublish source for Production Stabilization v1."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from davinci_production_agent.paths import DELIVERY_QUEUE_LONG, DELIVERY_QUEUE_ROOT, DELIVERY_QUEUE_SHORTS

QueueKind = Literal["long", "shorts"]


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _kind_dir(kind: QueueKind) -> Path:
    return DELIVERY_QUEUE_LONG if kind == "long" else DELIVERY_QUEUE_SHORTS


def ensure_queue_dirs() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for kind in ("long", "shorts"):
        p = _kind_dir(kind)  # type: ignore[arg-type]
        try:
            p.mkdir(parents=True, exist_ok=True)
            out[kind] = p.is_dir()
        except OSError:
            out[kind] = False
    try:
        DELIVERY_QUEUE_ROOT.mkdir(parents=True, exist_ok=True)
        out["root"] = DELIVERY_QUEUE_ROOT.is_dir()
    except OSError:
        out["root"] = False
    return out


def list_manifests(*, kind: QueueKind | None = None) -> list[Path]:
    ensure_queue_dirs()
    paths: list[Path] = []
    kinds: tuple[QueueKind, ...] = (kind,) if kind else ("long", "shorts")
    for k in kinds:
        root = _kind_dir(k)
        if not root.is_dir():
            continue
        pattern = f"{k}_*.json"
        paths.extend(
            sorted(
                (p for p in root.glob(pattern) if p.is_file() and not p.name.startswith("._")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        )
    return paths


def count_queue_items(*, kind: QueueKind | None = None, upload_ready_only: bool = False) -> int:
    if not upload_ready_only:
        return len(list_manifests(kind=kind))
    return len(list_upload_ready_manifests(kind=kind))


def read_manifest(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_manifest_file(path: Path, body: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_manifest(
    *,
    kind: QueueKind,
    render_path: str,
    preset: str,
    metadata: dict[str, Any] | None = None,
    job_id: str | None = None,
    qc_passed: bool = False,
    render_report_path: str | None = None,
    upload_ready: bool = False,
    status: str = "pending",
) -> Path:
    ensure_queue_dirs()
    jid = job_id or uuid.uuid4().hex[:12]
    stamp = _utc_stamp()
    name = f"{kind}_{stamp}_{jid}.json"
    out = _kind_dir(kind) / name
    body: dict[str, Any] = {
        "version": "delivery_queue_manifest_v1",
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "kind": kind,
        "job_id": jid,
        "preset": preset,
        "render_path": render_path,
        "render_report": render_report_path or "",
        "qc_passed": bool(qc_passed),
        "upload_ready": bool(upload_ready),
        "status": status,
        "upload_privacy": "unlisted",
        "public_upload_allowed": False,
        "metadata": metadata or {},
    }
    return write_manifest_file(out, body)


def update_manifest(path: Path, updates: dict[str, Any]) -> dict[str, Any] | None:
    data = read_manifest(path)
    if not data:
        return None
    data.update(updates)
    data["updated_at"] = _utc_iso()
    write_manifest_file(path, data)
    return data


def mark_manifest_failed(
    path: Path,
    *,
    block_reason: str,
    render_report_path: str | None = None,
    retry: bool | None = None,
) -> dict[str, Any] | None:
    """Mark manifest failed; never leaves upload_ready true."""
    updates: dict[str, Any] = {
        "status": "failed",
        "upload_ready": False,
        "qc_passed": False,
        "block_reason": block_reason,
    }
    if render_report_path:
        updates["render_report"] = render_report_path
    data = update_manifest(path, updates)
    if data is None:
        return None
    try:
        from always_publish.failure_recovery import record_queue_failure  # noqa: WPS433

        record_queue_failure(
            kind=str(data.get("kind") or "long"),
            reason=block_reason,
            status="failed",
            block_reason=block_reason,
        )
    except Exception:
        pass
    if retry is not None:
        meta = data.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        meta["retry_allowed"] = bool(retry)
        update_manifest(path, {"metadata": meta})
    return read_manifest(path)


def mark_manifest_upload_ready(
    path: Path,
    *,
    render_path: str,
    qc_passed: bool,
    render_report_path: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not qc_passed:
        return mark_manifest_failed(path, block_reason="qc_failed")
    rp = Path(render_path)
    if not rp.is_file() or rp.stat().st_size < 4096:
        return mark_manifest_failed(path, block_reason="render_missing_or_corrupt")
    extra = metadata or {}
    prior = read_manifest(path) or {}
    prior_meta = prior.get("metadata") if isinstance(prior.get("metadata"), dict) else {}
    merged_meta = {**prior_meta, **extra}
    return update_manifest(
        path,
        {
            "render_path": str(rp),
            "render_report": render_report_path,
            "qc_passed": True,
            "upload_ready": True,
            "status": "upload_ready",
            "block_reason": "",
            "metadata": merged_meta,
        },
    )


def list_upload_ready_manifests(*, kind: QueueKind | None = None) -> list[Path]:
    ready: list[Path] = []
    for m in list_manifests(kind=kind):
        data = read_manifest(m)
        if not data:
            continue
        if data.get("status") == "failed":
            continue
        if not data.get("upload_ready"):
            continue
        if not data.get("qc_passed"):
            continue
        rp = Path(str(data.get("render_path") or ""))
        if not rp.is_file():
            continue
        ready.append(m)
    return ready


def count_failed_manifests(*, kind: QueueKind | None = None) -> int:
    n = 0
    for m in list_manifests(kind=kind):
        data = read_manifest(m)
        if data and str(data.get("status") or "") == "failed":
            n += 1
    return n


def prefer_delivery_queue_for_upload() -> bool:
    from davinci_production_agent.config import load_config

    return bool(load_config().get("prefer_delivery_queue", True))


def delivery_queue_upload_only() -> bool:
    """Autopublish must not scan ready_to_upload when stabilization v1 is active."""
    from davinci_production_agent.config import load_config

    cfg = load_config()
    return bool(cfg.get("delivery_queue_upload_only", True))
