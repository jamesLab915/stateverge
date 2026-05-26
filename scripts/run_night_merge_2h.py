#!/usr/bin/env python3
"""One-off: concat NYC/video/night clips and loop to 2 hours into night/merged/."""

from __future__ import annotations

import json
import sys
import traceback

sys.path.insert(0, "/Users/ziweizhang/stateverge/src")

from studio_dashboard.services.stateverge_scan import STATEVERGE_VOLUME  # noqa: E402
from studio_dashboard.services.nyc_video_merge import (  # noqa: E402
    list_video_files,
    merge_video_paths_ordered,
)

TARGET = 7200.0


def main() -> None:
    night = STATEVERGE_VOLUME / "NYC" / "video" / "night"
    entries = list_video_files(
        scan_root=night, recursive=False, exclude_merged_outputs=True
    )
    resolved = [(night / e["rel"]).resolve() for e in entries]
    print(f"merge {len(resolved)} clips → loop {TARGET} s", flush=True)
    try:
        out = merge_video_paths_ordered(
            resolved,
            scan_root=night,
            loop_enabled=True,
            loop_target_seconds=TARGET,
            filename_slug="night",
        )
        print(json.dumps(out, ensure_ascii=False, indent=2), flush=True)
    except Exception:
        traceback.print_exc()
        raise


if __name__ == "__main__":
    main()
