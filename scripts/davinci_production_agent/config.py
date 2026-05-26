#!/usr/bin/env python3
"""Load/save Professional DaVinci Production Agent v1 settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from davinci_production_agent.presets import PRODUCTION_PRESET_IDS, canonical_production_preset

CONFIG_REL = Path("config") / "davinci_production_agent_v1.json"

DEFAULTS: dict[str, Any] = {
    "version": "professional_davinci_production_agent_v1",
    "future_inventory_enabled": True,
    "public_upload_disabled": True,
    "review_before_public": True,
    "upload_privacy": "unlisted",
    "prefer_delivery_queue": True,
    "long_inventory_days": 3,
    "shorts_inventory_days": 5,
    "idle_cpu_max_percent": 40,
    "default_long_preset": "long_driving",
    "default_shorts_preset": "shorts_cinematic",
    "enable_resolve_render": True,
    "enable_upload_from_queue": False,
    "delivery_queue_upload_only": True,
}


def config_path() -> Path:
    root = Path(os.environ.get("STATEVERGE_ROOT", str(Path.home() / "StateVerge"))).expanduser()
    return root / CONFIG_REL


def load_config() -> dict[str, Any]:
    out = dict(DEFAULTS)
    p = config_path()
    if not p.is_file():
        return out
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(data, dict):
        return out
    for k in DEFAULTS:
        if k in data:
            out[k] = data[k]
    out["future_inventory_enabled"] = bool(out.get("future_inventory_enabled", True))
    out["public_upload_disabled"] = bool(out.get("public_upload_disabled", True))
    out["review_before_public"] = bool(out.get("review_before_public", True))
    out["prefer_delivery_queue"] = bool(out.get("prefer_delivery_queue", True))
    out["enable_resolve_render"] = bool(out.get("enable_resolve_render", True))
    out["enable_upload_from_queue"] = bool(out.get("enable_upload_from_queue", False))
    out["delivery_queue_upload_only"] = bool(out.get("delivery_queue_upload_only", True))
    out["default_long_preset"] = canonical_production_preset(str(out.get("default_long_preset") or "long_driving"))
    out["default_shorts_preset"] = canonical_production_preset(
        str(out.get("default_shorts_preset") or "shorts_cinematic")
    )
    if out["default_long_preset"] not in PRODUCTION_PRESET_IDS:
        out["default_long_preset"] = "long_driving"
    if out["default_shorts_preset"] not in PRODUCTION_PRESET_IDS:
        out["default_shorts_preset"] = "shorts_cinematic"
    return out


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    bool_keys = (
        "future_inventory_enabled",
        "public_upload_disabled",
        "review_before_public",
        "prefer_delivery_queue",
        "enable_resolve_render",
        "enable_upload_from_queue",
        "delivery_queue_upload_only",
    )
    for k in bool_keys:
        if k in updates:
            cfg[k] = bool(updates[k])
    int_keys = ("long_inventory_days", "shorts_inventory_days", "idle_cpu_max_percent")
    for k in int_keys:
        if k in updates:
            try:
                cfg[k] = int(updates[k])
            except (TypeError, ValueError):
                pass
    str_keys = ("upload_privacy", "default_long_preset", "default_shorts_preset")
    for k in str_keys:
        if k in updates and updates[k] is not None:
            cfg[k] = str(updates[k])
    if "default_long_preset" in updates:
        cfg["default_long_preset"] = canonical_production_preset(str(updates["default_long_preset"]))
    if "default_shorts_preset" in updates:
        cfg["default_shorts_preset"] = canonical_production_preset(str(updates["default_shorts_preset"]))
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg
