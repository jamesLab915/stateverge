#!/usr/bin/env python3
"""Compute 3-day long / 5-day shorts delivery_queue inventory targets."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from davinci_production_agent.config import load_config
from davinci_production_agent.delivery_queue import count_queue_items, list_manifests
from davinci_production_agent.paths import DELIVERY_QUEUE_ROOT

LONG_INVENTORY_DAYS_DEFAULT = 3
SHORTS_INVENTORY_DAYS_DEFAULT = 5


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def inventory_targets(*, long_days: int | None = None, shorts_days: int | None = None) -> dict[str, Any]:
    cfg = load_config()
    ld = int(long_days if long_days is not None else cfg.get("long_inventory_days") or LONG_INVENTORY_DAYS_DEFAULT)
    sd = int(shorts_days if shorts_days is not None else cfg.get("shorts_inventory_days") or SHORTS_INVENTORY_DAYS_DEFAULT)
    return {
        "long_days": ld,
        "shorts_days": sd,
        "long_target_count": ld,
        "shorts_target_count": sd * 4,
        "shorts_per_day": 4,
    }


def inventory_snapshot() -> dict[str, Any]:
    """Current delivery_queue stock vs targets."""
    targets = inventory_targets()
    long_count = count_queue_items(kind="long", upload_ready_only=True)
    shorts_count = count_queue_items(kind="shorts", upload_ready_only=True)
    long_gap = max(0, targets["long_target_count"] - long_count)
    shorts_gap = max(0, targets["shorts_target_count"] - shorts_count)
    return {
        "generated_at": _utc_iso(),
        "delivery_queue_root": str(DELIVERY_QUEUE_ROOT),
        "targets": targets,
        "counts": {"long": long_count, "shorts": shorts_count},
        "gaps": {"long": long_gap, "shorts": shorts_gap},
        "inventory_days": {
            "long": long_count,
            "shorts": shorts_count / 4.0 if shorts_count else 0.0,
        },
        "ready_for_idle_build": long_gap > 0 or shorts_gap > 0,
        "manifests_sample": {
            "long": [m.name for m in list_manifests(kind="long")[:5]],
            "shorts": [m.name for m in list_manifests(kind="shorts")[:8]],
        },
    }


def write_inventory_plan(path: Path | None = None) -> Path:
    snap = inventory_snapshot()
    out = path or (DELIVERY_QUEUE_ROOT / "inventory_plan.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return out
