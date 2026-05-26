#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path

_AGENT = Path(__file__).resolve().parents[1]
_SCRIPTS = _AGENT.parent
for p in (_SCRIPTS, _AGENT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from davinci_production_agent.inventory_planner import inventory_snapshot, inventory_targets  # noqa: E402


def test_inventory_targets_defaults():
    t = inventory_targets()
    assert t["long_days"] == 3
    assert t["shorts_days"] == 5
    assert t["shorts_target_count"] == 20


def test_inventory_snapshot_shape():
    snap = inventory_snapshot()
    assert "counts" in snap
    assert "gaps" in snap
    assert "targets" in snap
