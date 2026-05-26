#!/usr/bin/env python3
"""Diagnose Professional DaVinci Production Agent + Production Stabilization Phase v1."""

from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parent
_SCRIPTS = _AGENT.parent
_REPO = _SCRIPTS.parent
for p in (_SCRIPTS, _AGENT):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_production_agent.config import load_config  # noqa: E402
from davinci_production_agent.delivery_queue import (  # noqa: E402
    delivery_queue_upload_only,
    ensure_queue_dirs,
)
from davinci_production_agent.idle_scheduler import idle_conditions  # noqa: E402
from davinci_production_agent.inventory_planner import inventory_snapshot  # noqa: E402
from davinci_production_agent.paths import (  # noqa: E402
    AGENT_REPORT_MD,
    DELIVERY_QUEUE_LONG,
    DELIVERY_QUEUE_ROOT,
    DELIVERY_QUEUE_SHORTS,
    FINISHED_FOR_YOUTUBE_ROOT,
    INBOX_VIDEO169_PRIMARY,
    INBOX_VIDEO169_SECONDARY,
    MARKER_AGENT_READY,
    MARKER_API_READY,
    MARKER_DELIVERY_QUEUE_UPLOAD_ONLY,
    MARKER_FUTURE_INVENTORY_AUTOBUILD,
    MARKER_FUTURE_INVENTORY,
    MARKER_PRODUCTION_STABILIZATION_READY,
    MARKER_PUBLIC_DISABLED,
    MARKER_RESOLVE_RENDER_ACTIVE,
    MARKER_REVIEW_BEFORE_PUBLIC,
    MARKER_UPLOAD_FROM_QUEUE,
    STABILIZATION_REPORT_MD,
    STUDIO_ROOT,
    STUDIO_SUBDIRS,
)
from davinci_production_agent.presets import PRODUCTION_PRESET_IDS  # noqa: E402
from davinci_production_agent.resolve_api import probe_davinci_detailed  # noqa: E402

_REQUIRED_MODULES = (
    "agent_v1.py",
    "inventory_planner.py",
    "idle_scheduler.py",
    "resolve_api.py",
    "cut_pipeline.py",
    "delivery_queue.py",
    "upload_from_queue.py",
    "qc_gate.py",
    "config.py",
    "presets.py",
    "paths.py",
)


def _ensure_dirs() -> dict[str, bool]:
    out: dict[str, bool] = {}
    for sub in STUDIO_SUBDIRS:
        p = STUDIO_ROOT / sub
        try:
            p.mkdir(parents=True, exist_ok=True)
            out[sub] = p.is_dir()
        except OSError:
            out[sub] = False
    try:
        DELIVERY_QUEUE_ROOT.mkdir(parents=True, exist_ok=True)
        DELIVERY_QUEUE_LONG.mkdir(parents=True, exist_ok=True)
        DELIVERY_QUEUE_SHORTS.mkdir(parents=True, exist_ok=True)
        out["delivery_queue"] = DELIVERY_QUEUE_ROOT.is_dir()
    except OSError:
        out["delivery_queue"] = False
    return out


def _write_report_md(path: Path, title: str, flags: dict[str, bool], report: dict[str, object]) -> None:
    lines = [
        f"# {title}",
        "",
        f"Generated at `{report.get('generated_at', '')}`.",
        "",
        "## Flags",
        "",
    ]
    for k, v in flags.items():
        lines.append(f"- `{k}={'true' if v else 'false'}`")
    lines.extend(["", "## Resolve checks", ""])
    checks = ((report.get("resolve_status") or {}).get("checks") or {})
    for name, detail in checks.items():
        ok = bool((detail or {}).get("ok")) if isinstance(detail, dict) else False
        lines.append(f"- `{name}`: {'OK' if ok else 'FAIL'}")
    lines.extend(["", "## JSON", "", "```json", json.dumps(report, indent=2), "```", ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    cfg = load_config()
    resolve_status = probe_davinci_detailed(run_timeline_test=True)
    dirs = _ensure_dirs()
    queue_dirs = ensure_queue_dirs()
    modules = {m: (_AGENT / m).is_file() for m in _REQUIRED_MODULES}
    cc_page = Path.home() / "StateVerge_Control_Center/frontend/src/app/davinci-autonomous-studio/page.tsx"

    modules_ok = all(modules.values())
    dirs_ok = all(dirs.values()) and all(queue_dirs.values())
    checks = resolve_status.get("checks") or {}
    api_ready = bool(resolve_status.get("ready")) and all(
        bool((checks.get(k) or {}).get("ok")) for k in checks
    ) if checks else bool(resolve_status.get("ready"))

    inv = inventory_snapshot()
    idle = idle_conditions()
    delivery_only = delivery_queue_upload_only()
    resolve_render_active = bool(cfg.get("enable_resolve_render")) and api_ready
    future_autobuild = bool(cfg.get("future_inventory_enabled")) and bool(idle.get("idle_ok"))

    report: dict[str, object] = {
        "version": "production_stabilization_v1",
        "generated_at": inv.get("generated_at"),
        "config": cfg,
        "config_path": str(Path.home() / "StateVerge/config/davinci_production_agent_v1.json"),
        "resolve_status": resolve_status,
        "inventory": inv,
        "idle": idle,
        "paths": {
            "inbox_primary": str(INBOX_VIDEO169_PRIMARY),
            "inbox_secondary": str(INBOX_VIDEO169_SECONDARY),
            "delivery_queue_long": str(DELIVERY_QUEUE_LONG),
            "delivery_queue_shorts": str(DELIVERY_QUEUE_SHORTS),
            "finished_for_youtube": str(FINISHED_FOR_YOUTUBE_ROOT),
            "studio_root": str(STUDIO_ROOT),
        },
        "dirs": dirs,
        "queue_dirs": queue_dirs,
        "modules": modules,
        "presets": list(PRODUCTION_PRESET_IDS),
        "control_center_page": cc_page.is_file(),
    }

    agent_ready = modules_ok and dirs_ok and bool(cfg)
    stabilization_ready = agent_ready and delivery_only and resolve_render_active
    upload_queue = bool(cfg.get("prefer_delivery_queue")) and delivery_only
    public_disabled = bool(cfg.get("public_upload_disabled", True))
    review_before = bool(cfg.get("review_before_public", True))
    future_inv = bool(cfg.get("future_inventory_enabled"))

    flags = {
        "PROFESSIONAL_DAVINCI_AGENT_READY": agent_ready,
        "DAVINCI_API_READY": api_ready,
        "PRODUCTION_STABILIZATION_V1_READY": stabilization_ready,
        "DELIVERY_QUEUE_UPLOAD_ONLY": delivery_only,
        "RESOLVE_RENDER_ACTIVE": resolve_render_active,
        "FUTURE_INVENTORY_AUTOBUILD": future_autobuild,
        "FUTURE_INVENTORY_MODE": future_inv,
        "UPLOAD_FROM_DELIVERY_QUEUE": upload_queue,
        "PUBLIC_UPLOAD_DISABLED": public_disabled,
        "REVIEW_BEFORE_PUBLIC": review_before,
    }
    report["flags"] = flags
    report["ok"] = stabilization_ready

    print(json.dumps(report, indent=2, ensure_ascii=False))
    for key, val in flags.items():
        print(f"{key}={'true' if val else 'false'}")
    print(MARKER_AGENT_READY if agent_ready else "PROFESSIONAL_DAVINCI_AGENT_READY=false")
    print(MARKER_API_READY if api_ready else "DAVINCI_API_READY=false")
    print(MARKER_PRODUCTION_STABILIZATION_READY if stabilization_ready else "PRODUCTION_STABILIZATION_V1_READY=false")
    print(MARKER_DELIVERY_QUEUE_UPLOAD_ONLY if delivery_only else "DELIVERY_QUEUE_UPLOAD_ONLY=false")
    print(MARKER_RESOLVE_RENDER_ACTIVE if resolve_render_active else "RESOLVE_RENDER_ACTIVE=false")
    print(MARKER_FUTURE_INVENTORY_AUTOBUILD if future_autobuild else "FUTURE_INVENTORY_AUTOBUILD=false")
    print(MARKER_FUTURE_INVENTORY if future_inv else "FUTURE_INVENTORY_MODE=false")
    print(MARKER_UPLOAD_FROM_QUEUE if upload_queue else "UPLOAD_FROM_DELIVERY_QUEUE=false")
    print(MARKER_PUBLIC_DISABLED if public_disabled else "PUBLIC_UPLOAD_DISABLED=false")
    print(MARKER_REVIEW_BEFORE_PUBLIC if review_before else "REVIEW_BEFORE_PUBLIC=false")

    _write_report_md(AGENT_REPORT_MD, "Professional DaVinci Production Agent v1", flags, report)
    _write_report_md(STABILIZATION_REPORT_MD, "Production Stabilization Phase v1", flags, report)
    return 0 if stabilization_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
