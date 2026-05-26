"""Canonical paths: repository (CODE_ROOT) vs external SSD media (NYC_ROOT).

All NYC_AUTO ingest/output/packages/projects/library data must live under NYC_ROOT
on the mounted SSD. OAuth secrets and local log mirrors stay under CODE_ROOT.

Volume roots resolve via ``utils.storage_paths`` (``config/storage_map.env`` +
environment overrides). See ``STATEVERGE_VOL`` for the primary SSD mount.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Repository root: code, docs, ~/.secrets, data/youtube, logs/system mirrors.
CODE_ROOT = Path(os.environ.get("STATEVERGE_ROOT", str(Path.home() / "StateVerge"))).expanduser().resolve()

_SRC = CODE_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

try:
    from utils.storage_paths import get_stateverge_volume

    _verb = os.environ.get("STATEVERGE_STORAGE_VERBOSE", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    SSD_ROOT = get_stateverge_volume(verbose=_verb)
except Exception:
    SSD_ROOT = Path(os.environ.get("STATEVERGE_VOL", "/Volumes/StateVerge")).expanduser()

# External SSD project tree (volume name typically StateVerge).
NYC_ROOT = SSD_ROOT / "NYC_AUTO"
