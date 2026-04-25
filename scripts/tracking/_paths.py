"""Shared path constants for ``scripts/tracking/`` (stdlib only).

All paths are anchored at the StateVerge repo root (``~/StateVerge``). Importing
this module also ensures the tracking script directory is on ``sys.path`` so
peer scripts can ``from _paths import ...``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
TRACK: Path = REPO_ROOT / "docs" / "tracking"
LOGS: Path = REPO_ROOT / "logs" / "tracking"
SCRIPTS: Path = REPO_ROOT / "scripts" / "tracking"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def ensure_dirs() -> None:
    """Create tracking directories if missing (safe to call multiple times)."""
    for d in (TRACK, LOGS, SCRIPTS, TRACK / "monthly_reports"):
        d.mkdir(parents=True, exist_ok=True)
