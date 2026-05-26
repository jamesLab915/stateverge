"""Static channel / token guards for NYC long vs Shorts uploads (no YouTube API)."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from youtube_token_paths import OFFICIAL_LONG_TOKEN_PATH, OFFICIAL_SHORTS_TOKEN_PATH

UPLOADER_LONG = "NYC_LONG"
UPLOADER_SHORTS = "SHORTS"

SHORTS_PATH_MARKERS = ("shorts", "shorts_clips", "youtube_shorts", "shorts_uploads")
LONG_CLIP_MARKERS = ("nyc_long_clips", "publish_pack/nyc_long_uploads", "long_runtime/long_uploads")


def _resolve(p: Path) -> Path | None:
    try:
        return p.expanduser().resolve()
    except OSError:
        return None


def validate_long_channel_token(token_path: Path) -> tuple[bool, str]:
    """Long-form uploads must use the official ``~/StateVerge/data/youtube/token.json`` path."""
    got = _resolve(token_path)
    want = _resolve(OFFICIAL_LONG_TOKEN_PATH)
    shorts = _resolve(OFFICIAL_SHORTS_TOKEN_PATH)
    if got is None or want is None:
        return False, "token_path_unresolvable"
    if shorts is not None and got == shorts:
        return False, "long_queue_must_not_use_token_shorts"
    if got != want:
        return False, "long_token_not_official_path"
    return True, ""


def validate_shorts_channel_token(token_path: Path) -> tuple[bool, str]:
    """Shorts uploads must use ``~/StateVerge/data/youtube/token_shorts.json``."""
    got = _resolve(token_path)
    want = _resolve(OFFICIAL_SHORTS_TOKEN_PATH)
    longp = _resolve(OFFICIAL_LONG_TOKEN_PATH)
    if got is None or want is None:
        return False, "token_path_unresolvable"
    if longp is not None and got == longp:
        return False, "shorts_queue_must_not_use_long_token_json"
    if got != want:
        return False, "shorts_token_not_official_path"
    return True, ""


def _path_suggests_shorts_asset(video_path: Path) -> bool:
    s = str(video_path).replace("\\", "/").lower()
    return any(m in s for m in SHORTS_PATH_MARKERS)


def assert_long_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """Return a blocked envelope if context is invalid; otherwise None."""
    ok, reason = validate_long_channel_token(token_path)
    if not ok:
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": reason,
            "video_type": "long",
            "channel": UPLOADER_LONG,
        }
    if _path_suggests_shorts_asset(video_path):
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": "long_upload_path_contains_shorts_marker",
            "video_type": "long",
            "channel": UPLOADER_LONG,
        }
    # v1: resolved official long token implies NYC_LONG uploader mode.
    _ = os.environ.get("STATEVERGE_UPLOADER_MODE", "")
    return None


def assert_shorts_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """Return a blocked envelope if Shorts token path is wrong or asset is long-only path."""
    s = str(video_path).replace("\\", "/").lower()
    for m in LONG_CLIP_MARKERS:
        if m in s:
            return {
                "status": "blocked",
                "block_reason": "channel_guard_failed",
                "channel_guard_detail": "shorts_upload_path_reserved_for_long_channel",
                "video_type": "short",
                "channel": UPLOADER_SHORTS,
            }
    ok, reason = validate_shorts_channel_token(token_path)
    if not ok:
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": reason,
            "video_type": "short",
            "channel": UPLOADER_SHORTS,
        }
    return None


def blocked_envelope_to_json(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)
