#!/usr/bin/env python3
"""Single-package YouTube upload entrypoint — Pro-only (``~/StateVerge``).

Delegates to ``nyc_auto/youtube_upload.py``. Secrets default under
``~/StateVerge/.secrets/youtube/`` (see that module for flags).
"""

from __future__ import annotations

import sys
from pathlib import Path

_NA = Path(__file__).resolve().parent / "nyc_auto"
if str(_NA) not in sys.path:
    sys.path.insert(0, str(_NA))

from youtube_upload import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
