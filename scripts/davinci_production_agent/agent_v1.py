#!/usr/bin/env python3
"""Professional DaVinci Production Agent v1 — orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AGENT_DIR = Path(__file__).resolve().parent
_SCRIPTS = _AGENT_DIR.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from davinci_production_agent.config import load_config, save_config  # noqa: E402
from davinci_production_agent.cut_pipeline import run_auto_detect_cut_list  # noqa: E402
from davinci_production_agent.delivery_queue import (  # noqa: E402
    ensure_queue_dirs,
    list_upload_ready_manifests,
    mark_manifest_failed,
    mark_manifest_upload_ready,
    write_manifest,
)
from davinci_production_agent.idle_scheduler import can_run_future_inventory, idle_conditions  # noqa: E402
from davinci_production_agent.inventory_planner import inventory_snapshot, write_inventory_plan  # noqa: E402
from davinci_production_agent.paths import (  # noqa: E402
    AGENT_STATUS_JSON,
    CUTLISTS_ROOT,
    INBOX_VIDEO169_PRIMARY,
    INBOX_VIDEO169_SECONDARY,
    REPORTS_ROOT,
    STUDIO_ROOT,
    STUDIO_SUBDIRS,
    delivery_render_path,
)
from davinci_production_agent.presets import PRODUCTION_PRESET_IDS, canonical_production_preset, preset_rules  # noqa: E402
from davinci_production_agent.qc_gate import validate_render  # noqa: E402
from davinci_production_agent.resolve_api import (  # noqa: E402
    probe,
    probe_davinci_detailed,
    run_production_render,
    write_render_report,
    write_timeline_plan,
)
from davinci_production_agent.upload_from_queue import next_manifest_for_upload, preflight_upload, send_to_review  # noqa: E402


def ensure_studio_dirs() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for sub in STUDIO_SUBDIRS:
        p = STUDIO_ROOT / sub
        try:
            p.mkdir(parents=True, exist_ok=True)
            out[sub] = p.is_dir()
        except OSError:
            out[sub] = False
    return out


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _write_status(body: dict[str, Any]) -> None:
    AGENT_STATUS_JSON.parent.mkdir(parents=True, exist_ok=True)
    AGENT_STATUS_JSON.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def scan_inbox_sources() -> list[Path]:
    clips: list[Path] = []
    from davinci_production_agent.paths import VIDEO_EXTS

    for root in (INBOX_VIDEO169_PRIMARY, INBOX_VIDEO169_SECONDARY):
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                clips.append(p)
    return clips


def _load_cut_plan(job_id: str) -> dict[str, Any]:
    p = CUTLISTS_ROOT / job_id / "cut_list.json"
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def build_job(
    *,
    kind: str,
    preset: str | None = None,
    source: Path | None = None,
    count: int = 1,
) -> dict[str, Any]:
    """Build production jobs via Resolve render; never fake upload-ready from timeline_plan only."""
    cfg = load_config()
    ensure_studio_dirs()
    ensure_queue_dirs()

    preset_id = canonical_production_preset(
        preset or (cfg.get("default_shorts_preset") if kind == "shorts" else cfg.get("default_long_preset"))
    )
    rules = preset_rules(preset_id)
    resolve_status = probe_davinci_detailed(run_timeline_test=False)
    jobs: list[dict[str, Any]] = []
    queue_kind = "shorts" if kind == "shorts" else "long"

    sources = [source] if source and source.is_file() else scan_inbox_sources()[: max(1, count)]
    if not sources:
        return {
            "ok": False,
            "block_reason": "no_inbox_sources",
            "kind": kind,
            "preset": preset_id,
        }

    if not resolve_status.get("ready"):
        for src in sources[:count]:
            job_id = uuid.uuid4().hex[:12]
            cut_result = run_auto_detect_cut_list(src, job_id=job_id)
            plan = {
                "job_id": job_id,
                "kind": kind,
                "preset": preset_id,
                "preset_rules": rules,
                "source": str(src),
            }
            timeline_path = write_timeline_plan(plan, job_id=job_id)
            report_path = write_render_report(
                job_id,
                {
                    "job_id": job_id,
                    "kind": kind,
                    "preset": preset_id,
                    "source": str(src),
                    "resolve_status": resolve_status,
                    "cut_pipeline": cut_result,
                    "block_reason": "davinci_api_unavailable",
                    "render_success": False,
                    "timeline_plan": str(timeline_path),
                },
            )
            manifest_path = write_manifest(
                kind=queue_kind,
                render_path="",
                preset=preset_id,
                job_id=job_id,
                metadata={"source": str(src), "timeline_plan": str(timeline_path)},
                status="failed",
                upload_ready=False,
                qc_passed=False,
                render_report_path=str(report_path),
            )
            mark_manifest_failed(manifest_path, block_reason="davinci_api_unavailable", render_report_path=str(report_path))
            jobs.append(
                {
                    "job_id": job_id,
                    "source": str(src),
                    "preset": preset_id,
                    "timeline_plan": str(timeline_path),
                    "render_report": str(report_path),
                    "delivery_manifest": str(manifest_path),
                    "block_reason": "davinci_api_unavailable",
                    "upload_ready": False,
                    "cut_pipeline": cut_result,
                }
            )
        return {
            "ok": False,
            "block_reason": "davinci_api_unavailable",
            "kind": kind,
            "preset": preset_id,
            "jobs": jobs,
            "resolve_status": resolve_status,
        }

    for src in sources[:count]:
        job_id = uuid.uuid4().hex[:12]
        cut_result = run_auto_detect_cut_list(src, job_id=job_id)
        cut_plan = _load_cut_plan(job_id)
        plan = {
            "job_id": job_id,
            "kind": kind,
            "preset": preset_id,
            "preset_rules": rules,
            "source": str(src),
            "timeline_fps": rules.get("timeline_fps", 30),
            "resolution": rules.get("resolution"),
        }
        timeline_path = write_timeline_plan(plan, job_id=job_id)

        manifest_path = write_manifest(
            kind=queue_kind,
            render_path=str(delivery_render_path(kind=queue_kind, job_id=job_id)),
            preset=preset_id,
            job_id=job_id,
            metadata={"source": str(src), "timeline_plan": str(timeline_path)},
            status="rendering",
        )

        render_result: dict[str, Any] = {"ok": False, "block_reason": "resolve_render_disabled"}
        if cfg.get("enable_resolve_render"):
            render_result = run_production_render(
                job_id=job_id,
                kind=queue_kind,
                preset_id=preset_id,
                source_path=src,
                cut_plan=cut_plan,
                project_name=f"{queue_kind}_{job_id}",
            )

        output_path = Path(str(render_result.get("output_path") or delivery_render_path(kind=queue_kind, job_id=job_id)))
        report_path = write_render_report(
            job_id,
            {
                "job_id": job_id,
                "kind": kind,
                "preset": preset_id,
                "source": str(src),
                "resolve_status": resolve_status,
                "render_result": render_result,
                "cut_pipeline": cut_result,
                "block_reason": render_result.get("block_reason"),
                "render_success": bool(render_result.get("render_success")),
                "output_path": str(output_path),
                "timeline_plan": str(timeline_path),
            },
        )

        upload_ready = False
        block_reason = str(render_result.get("block_reason") or "")
        if render_result.get("render_success") and output_path.is_file():
            qc = validate_render(output_path, kind=queue_kind)
            if qc.get("ok"):
                mark_manifest_upload_ready(
                    manifest_path,
                    render_path=str(output_path),
                    qc_passed=True,
                    render_report_path=str(report_path),
                    metadata={"source": str(src), "qc": qc},
                )
                upload_ready = True
                block_reason = ""
            else:
                block_reason = "qc_failed"
                mark_manifest_failed(
                    manifest_path,
                    block_reason=block_reason,
                    render_report_path=str(report_path),
                )
                write_render_report(
                    job_id,
                    {
                        "job_id": job_id,
                        "block_reason": block_reason,
                        "qc": qc,
                        "render_success": False,
                    },
                )
        else:
            block_reason = block_reason or "resolve_render_failed"
            mark_manifest_failed(
                manifest_path,
                block_reason=block_reason,
                render_report_path=str(report_path),
            )

        jobs.append(
            {
                "job_id": job_id,
                "source": str(src),
                "preset": preset_id,
                "timeline_plan": str(timeline_path),
                "render_report": str(report_path),
                "render_path": str(output_path) if output_path.is_file() else None,
                "delivery_manifest": str(manifest_path),
                "block_reason": block_reason or None,
                "upload_ready": upload_ready,
                "cut_pipeline": cut_result,
            }
        )

    status = {
        "updated_at": _utc_iso(),
        "last_action": f"build_{kind}",
        "jobs": jobs,
        "resolve_status": resolve_status,
        "inventory": inventory_snapshot(),
        "upload_ready_count": len(list_upload_ready_manifests()),
    }
    _write_status(status)
    any_ok = any(j.get("upload_ready") for j in jobs)
    return {
        "ok": any_ok,
        "kind": kind,
        "preset": preset_id,
        "jobs": jobs,
        "resolve_status": resolve_status,
    }


def build_inventory_idle() -> dict[str, Any]:
    if not can_run_future_inventory():
        return {"ok": False, "idle": idle_conditions(), "block_reason": "idle_conditions_not_met"}
    snap = inventory_snapshot()
    plan_path = write_inventory_plan()
    built: list[dict[str, Any]] = []
    gaps = snap.get("gaps") or {}
    if int(gaps.get("long") or 0) > 0:
        built.append(build_job(kind="long", count=min(1, int(gaps["long"]))))
    if int(gaps.get("shorts") or 0) > 0:
        built.append(build_job(kind="shorts", count=min(4, int(gaps["shorts"]))))
    return {"ok": True, "inventory_plan": str(plan_path), "snapshot": snap, "built": built}


def status_snapshot() -> dict[str, Any]:
    cfg = load_config()
    resolve_status = probe_davinci_detailed(run_timeline_test=False)
    inv = inventory_snapshot()
    idle = idle_conditions()
    next_up = next_manifest_for_upload()
    from davinci_production_agent.delivery_queue import count_failed_manifests

    return {
        "version": "production_stabilization_v1",
        "updated_at": _utc_iso(),
        "config": cfg,
        "resolve_status": resolve_status,
        "resolve_connected": bool(resolve_status.get("ready")),
        "render_progress": resolve_status.get("render_progress") or {},
        "inventory": inv,
        "idle": idle,
        "future_inventory_can_run": can_run_future_inventory(),
        "next_upload_manifest": str(next_up) if next_up else None,
        "upload_ready_count": len(list_upload_ready_manifests()),
        "failed_render_count": count_failed_manifests(),
        "queue_health": {
            "long_ready": len(list_upload_ready_manifests(kind="long")),
            "shorts_ready": len(list_upload_ready_manifests(kind="shorts")),
            "failed": count_failed_manifests(),
        },
        "studio_dirs": ensure_studio_dirs(),
        "queue_dirs": ensure_queue_dirs(),
        "inbox_counts": {
            "primary": len(list(INBOX_VIDEO169_PRIMARY.rglob("*.mov"))) if INBOX_VIDEO169_PRIMARY.is_dir() else 0,
            "secondary": len(list(INBOX_VIDEO169_SECONDARY.rglob("*.mov"))) if INBOX_VIDEO169_SECONDARY.is_dir() else 0,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Professional DaVinci Production Agent v1")
    ap.add_argument("command", choices=("status", "build-long", "build-shorts", "build-inventory", "diagnose"))
    ap.add_argument("--preset", default="")
    ap.add_argument("--source", default="")
    ap.add_argument("--count", type=int, default=1)
    args = ap.parse_args()

    if args.command == "diagnose":
        from davinci_production_agent.diagnose_professional_davinci_agent import main as diag_main

        return diag_main()

    if args.command == "status":
        snap = status_snapshot()
        print(json.dumps(snap, indent=2, ensure_ascii=False))
        return 0

    src = Path(args.source).expanduser() if args.source.strip() else None
    preset = args.preset.strip() or None

    if args.command == "build-long":
        result = build_job(kind="long", preset=preset, source=src, count=args.count)
    elif args.command == "build-shorts":
        result = build_job(kind="shorts", preset=preset, source=src, count=args.count)
    else:
        result = build_inventory_idle()

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
