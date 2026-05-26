#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_production_agent.config import DEFAULTS, load_config  # noqa: E402


def test_defaults_public_disabled():
    cfg = load_config()
    assert cfg.get("public_upload_disabled") is True
    assert cfg.get("review_before_public") is True
    assert cfg.get("future_inventory_enabled") is True


def test_defaults_version():
    cfg = load_config()
    assert "professional_davinci" in str(cfg.get("version", ""))
