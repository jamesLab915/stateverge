#!/usr/bin/env python3
"""Load/save DaVinci audio finish gate settings (fail-open defaults)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from presets import PRESET_IDS, canonical_preset

CONFIG_REL = Path("config") / "davinci_audio_finish.json"

DEFAULTS: dict[str, Any] = {
    "enable_davinci_audio_finish": False,
    "require_davinci_audio_finish": False,
    "default_preset": "auto",
    "default_ambient_chain": "auto",
    "version": "davinci_youtube_audio_finishing_gate_v1",
}

_AMBIENT_CHAIN_IDS: tuple[str, ...] | None = None


def _ambient_chain_ids() -> tuple[str, ...]:
    global _AMBIENT_CHAIN_IDS  # noqa: PLW0603
    if _AMBIENT_CHAIN_IDS is not None:
        return _AMBIENT_CHAIN_IDS
    try:
        from ambient_chains.loader import AMBIENT_CHAIN_IDS  # noqa: WPS433

        _AMBIENT_CHAIN_IDS = AMBIENT_CHAIN_IDS
    except ImportError:
        _AMBIENT_CHAIN_IDS = ()
    return _AMBIENT_CHAIN_IDS or ()


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
    out["default_preset"] = canonical_preset(str(out.get("default_preset") or "auto"))
    if out["default_preset"] not in PRESET_IDS:
        out["default_preset"] = "auto"
    out["enable_davinci_audio_finish"] = bool(out.get("enable_davinci_audio_finish", False))
    out["require_davinci_audio_finish"] = bool(out.get("require_davinci_audio_finish", False))
    dac = str(out.get("default_ambient_chain") or "auto").strip()
    if dac != "auto" and dac not in _ambient_chain_ids():
        dac = "auto"
    out["default_ambient_chain"] = dac
    return out


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    if "enable_davinci_audio_finish" in updates:
        cfg["enable_davinci_audio_finish"] = bool(updates["enable_davinci_audio_finish"])
    if "require_davinci_audio_finish" in updates:
        cfg["require_davinci_audio_finish"] = bool(updates["require_davinci_audio_finish"])
    if "default_preset" in updates:
        cfg["default_preset"] = canonical_preset(str(updates["default_preset"]))
    if "default_ambient_chain" in updates:
        dac = str(updates["default_ambient_chain"]).strip()
        if dac == "auto" or dac in _ambient_chain_ids():
            cfg["default_ambient_chain"] = dac
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg
