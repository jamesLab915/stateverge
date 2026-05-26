#!/usr/bin/env python3
"""Compatibility entrypoint — delegates to ``media_intel/query_media_library.py``."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from media_intel.query_media_library import main

if __name__ == "__main__":
    raise SystemExit(main())
