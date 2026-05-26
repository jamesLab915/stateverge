#!/usr/bin/env python3
"""Shorts pool supply — counts ready clips, inbox media, mount + fallback flags."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path.home() / "StateVerge"
_SCRIPTS = _REPO / "scripts"
_NYC = _SCRIPTS / "nyc_auto"
for _p in (_SCRIPTS, _NYC):
    if _p.is_dir() and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_publish_queue as apq  # noqa: E402

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "shorts_pool_supply_diagnose.json"
OUT_MD = CONTROL_LOGS / "shorts_pool_supply_diagnose.md"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ffprobe_has_video(path: Path) -> bool:
    probe = apq._ffprobe_json(path)  # noqa: SLF001
    if not probe:
        return False
    _, hv = apq._duration_and_has_video(probe)  # noqa: SLF001
    return bool(hv)


def _list_files(root: Path, exts: set[str]) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    try:
        for p in root.rglob("*"):
            if not p.is_file() or p.name.startswith("._"):
                continue
            if p.suffix.lower() in exts:
                out.append(p)
    except OSError:
        pass
    return out


def main() -> int:
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)
    shorts_primary = ready / "shorts_clips"
    shorts_fb = Path.home() / "StateVerge" / "data" / "shorts_runtime" / "shorts_ready_clips"
    iphone = xfer / "00_INBOX" / "iphone"
    cache_inbox = cache / "inbox"

    sv_transfer_mounted = xfer.is_dir()
    sv_cache_mounted = cache.is_dir()

    ready_primary_files = _list_files(shorts_primary, VIDEO_EXTS)
    ready_fb_files = _list_files(shorts_fb, VIDEO_EXTS)
    fallback_used = bool(ready_fb_files) and (not sv_transfer_mounted or not shorts_primary.is_dir())

    ready_all = ready_primary_files + ready_fb_files
    ready_shorts_count = len(ready_all)
    ready_has_video = 0
    for p in ready_all:
        if _ffprobe_has_video(p):
            ready_has_video += 1

    inbox_videos: list[Path] = []
    inbox_images: list[Path] = []
    for root in (iphone, cache_inbox):
        inbox_videos.extend(_list_files(root, VIDEO_EXTS))
        inbox_images.extend(_list_files(root, IMAGE_EXTS))

    src_paths = [str(p) for p in ready_all]
    candidate_nonempty = bool(src_paths)

    next_action = (
        "Add vertical Shorts-ready .mp4 files under SV_TRANSFER/ready_to_upload/shorts_clips "
        "(or home shorts_ready_clips fallback when volume offline), then run dry_run_shorts_safe."
    )
    if not sv_transfer_mounted:
        next_action = "Mount SV_TRANSFER first. " + next_action
    elif ready_has_video > 0:
        next_action = (
            f"Ready pool has {ready_has_video} Shorts video file(s) with ffprobe video stream; "
            "if auto_publish_shorts is still blocked, run diagnose_auto_publish_v2 and check worker logs "
            "(channel_guard, ledger, or diagnose-only snapshot modes)."
        )
    elif ready_has_video == 0 and inbox_videos:
        next_action = (
            "Inbox has video files; use your Shorts ingest or place compliant vertical clips into shorts_clips."
        )

    summary: dict[str, Any] = {
        "generated_at": _utc(),
        "ready_shorts_count": ready_shorts_count,
        "ready_shorts_has_video_count": ready_has_video,
        "inbox_video_count": len(inbox_videos),
        "inbox_image_count": len(inbox_images),
        "candidate_source_paths_nonempty": candidate_nonempty,
        "sample_ready_paths": src_paths[:12],
        "sv_transfer_mounted": sv_transfer_mounted,
        "sv_cache_mounted": sv_cache_mounted,
        "fallback_used": fallback_used,
        "next_action": next_action,
    }

    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    md = [
        f"# Shorts pool supply ({summary['generated_at']})",
        "",
        f"- ready_shorts_count: **{summary['ready_shorts_count']}**",
        f"- ready_shorts_has_video_count: **{summary['ready_shorts_has_video_count']}**",
        f"- inbox_video_count: **{summary['inbox_video_count']}**",
        f"- inbox_image_count: **{summary['inbox_image_count']}**",
        f"- candidate_source_paths_nonempty: **{summary['candidate_source_paths_nonempty']}**",
        f"- sv_transfer_mounted: **{summary['sv_transfer_mounted']}**",
        f"- sv_cache_mounted: **{summary['sv_cache_mounted']}**",
        f"- fallback_used: **{summary['fallback_used']}**",
        "",
        "## Next action",
        "",
        next_action,
        "",
    ]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print(json.dumps({"ok": True, "json": str(OUT_JSON), "md": str(OUT_MD), **summary}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
