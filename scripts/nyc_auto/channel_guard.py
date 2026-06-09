"""Static channel / token guards for NYC long vs Shorts uploads (no YouTube API)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from youtube_token_paths import (
    OFFICIAL_LONG_TOKEN_PATH,
    OFFICIAL_MUSIC_TOKEN_PATH,
    OFFICIAL_SHORTS_TOKEN_PATH,
    OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH,
)

UPLOADER_LONG = "NYC_LONG"
UPLOADER_SHORTS = "SHORTS"  # legacy label — slot repurposed for 张子维
UPLOADER_MUSIC = "STATEVERGE_MUSIC"
UPLOADER_ZHANG_ZIWEI = "ZHANG_ZIWEI"

SHORTS_PATH_MARKERS = ("shorts", "shorts_clips", "youtube_shorts", "shorts_uploads")
LONG_CLIP_MARKERS = ("nyc_long_clips", "publish_pack/nyc_long_uploads", "long_runtime/long_uploads")

SHORTS_MAX_DURATION_SEC = 60.0
SHORTS_MIN_DURATION_SEC = 5.0
SHORTS_MIN_HEIGHT = 720
SHORTS_TARGET_ASPECT_MIN = 1.05  # display height > width


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


def validate_music_channel_token(token_path: Path) -> tuple[bool, str]:
    """StateVerge Music MV uploads must use ``token_music.json`` only."""
    got = _resolve(token_path)
    want = _resolve(OFFICIAL_MUSIC_TOKEN_PATH)
    longp = _resolve(OFFICIAL_LONG_TOKEN_PATH)
    ziwei = _resolve(OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH)
    if got is None or want is None:
        return False, "token_path_unresolvable"
    if longp is not None and got == longp:
        return False, "music_queue_must_not_use_long_token_json"
    if ziwei is not None and got == ziwei:
        return False, "music_queue_must_not_use_zhang_ziwei_token"
    if got != want:
        return False, "music_token_not_official_path"
    return True, ""


def validate_zhang_ziwei_channel_token(token_path: Path) -> tuple[bool, str]:
    """张子维 MV uploads must use repurposed ``token_shorts.json`` only."""
    got = _resolve(token_path)
    want = _resolve(OFFICIAL_ZHANG_ZIWEI_TOKEN_PATH)
    longp = _resolve(OFFICIAL_LONG_TOKEN_PATH)
    music = _resolve(OFFICIAL_MUSIC_TOKEN_PATH)
    if got is None or want is None:
        return False, "token_path_unresolvable"
    if longp is not None and got == longp:
        return False, "zhang_ziwei_queue_must_not_use_long_token_json"
    if music is not None and got == music:
        return False, "zhang_ziwei_queue_must_not_use_token_music"
    if got != want:
        return False, "zhang_ziwei_token_not_official_path"
    return True, ""


def validate_shorts_channel_token(token_path: Path) -> tuple[bool, str]:
    """Legacy Shorts slot — repurposed for 张子维; Real NYC Shorts uploads disabled."""
    return False, "shorts_channel_repurposed_for_zhang_ziwei"


def _video_rotation_deg(st: dict[str, Any]) -> int:
    rot = 0
    tags = st.get("tags") or {}
    try:
        rot = int(tags.get("rotate") or 0)
    except (TypeError, ValueError):
        rot = 0
    for sd in st.get("side_data_list") or []:
        if "rotation" not in sd:
            continue
        try:
            rot = int(sd["rotation"]) % 360
        except (TypeError, ValueError):
            pass
    return int(rot) % 360


def probe_youtube_shorts_format(video_path: Path) -> dict[str, Any]:
    """ffprobe summary for YouTube Shorts eligibility (vertical, ≤60s)."""
    out: dict[str, Any] = {"path": str(video_path)}
    ffprobe = shutil.which("ffprobe") or "ffprobe"
    try:
        r = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(video_path.expanduser().resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        out["error"] = f"ffprobe_failed:{exc!r}"
        return out
    if r.returncode != 0:
        out["error"] = "ffprobe_nonzero"
        return out
    try:
        data = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        out["error"] = "ffprobe_json_invalid"
        return out
    dur = 0.0
    try:
        dur = float((data.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    out["duration_sec"] = round(dur, 3)
    w = h = 0
    rot = 0
    for st in data.get("streams") or []:
        if st.get("codec_type") != "video":
            continue
        try:
            w, h = int(st.get("width") or 0), int(st.get("height") or 0)
        except (TypeError, ValueError):
            w, h = 0, 0
        rot = _video_rotation_deg(st)
        break
    if abs(rot) in (90, 270):
        w, h = h, w
    out["width"] = w
    out["height"] = h
    out["rotation_deg"] = rot
    out["portrait"] = h > w * SHORTS_TARGET_ASPECT_MIN
    out["shorts_duration_ok"] = SHORTS_MIN_DURATION_SEC <= dur <= SHORTS_MAX_DURATION_SEC
    out["shorts_format_ok"] = bool(
        out["portrait"] and out["shorts_duration_ok"] and h >= SHORTS_MIN_HEIGHT and w > 0
    )
    return out


def validate_youtube_shorts_file(video_path: Path) -> tuple[bool, str, dict[str, Any]]:
    """Return (ok, detail_reason, probe_dict)."""
    if not video_path.is_file():
        return False, "video_missing", {"path": str(video_path)}
    probe = probe_youtube_shorts_format(video_path)
    if probe.get("error"):
        return False, str(probe["error"]), probe
    if not probe.get("portrait"):
        return False, "not_vertical_portrait", probe
    if not probe.get("shorts_duration_ok"):
        dur = float(probe.get("duration_sec") or 0)
        if dur > SHORTS_MAX_DURATION_SEC:
            return False, "shorts_duration_over_60s", probe
        return False, "shorts_duration_too_short", probe
    if int(probe.get("height") or 0) < SHORTS_MIN_HEIGHT:
        return False, "shorts_height_too_low", probe
    return True, "", probe


def _shorts_format_block(video_path: Path, *, channel: str, upload_surface: str = "") -> dict[str, Any] | None:
    ok, reason, probe = validate_youtube_shorts_file(video_path)
    if ok:
        return None
    body: dict[str, Any] = {
        "status": "blocked",
        "block_reason": "shorts_format_guard_failed",
        "channel_guard_detail": reason,
        "video_type": "short",
        "channel": channel,
        "shorts_format_probe": probe,
    }
    if upload_surface:
        body["upload_surface"] = upload_surface
    return body


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


def assert_long_channel_short_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """Upload a vertical Short to **StateVerge NYC** (long OAuth token).

    Unlike ``assert_long_upload_context``, paths under ``shorts_clips`` / ``shorts_uploads`` are allowed.
    Do not use ``token_shorts.json`` (that is for Real NYC Shorts only).
    """
    ok, reason = validate_long_channel_token(token_path)
    if not ok:
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": reason,
            "video_type": "short",
            "channel": UPLOADER_LONG,
            "upload_surface": "long_channel_short",
        }
    fmt_block = _shorts_format_block(video_path, channel=UPLOADER_LONG, upload_surface="long_channel_short")
    if fmt_block:
        return fmt_block
    _ = os.environ.get("STATEVERGE_UPLOADER_MODE", "")
    return None


def assert_music_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """StateVerge Music MV uploads — dedicated ``token_music.json`` only."""
    ok, reason = validate_music_channel_token(token_path)
    if not ok:
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": reason,
            "video_type": "music_mv",
            "channel": UPLOADER_MUSIC,
        }
    return None


def assert_zhang_ziwei_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """张子维 国语 MV — ``token_shorts.json`` repurposed slot; horizontal cinematic only."""
    ok, reason = validate_zhang_ziwei_channel_token(token_path)
    if not ok:
        return {
            "status": "blocked",
            "block_reason": "channel_guard_failed",
            "channel_guard_detail": reason,
            "video_type": "zhang_ziwei_mv",
            "channel": UPLOADER_ZHANG_ZIWEI,
        }
    fmt_block = _shorts_format_block(
        video_path, channel=UPLOADER_ZHANG_ZIWEI, upload_surface="zhang_ziwei_mv"
    )
    if fmt_block:
        fmt_block["block_reason"] = "zhang_ziwei_mv_must_be_landscape"
        fmt_block["channel_guard_detail"] = (
            "张子维频道为 16:9 电影感 MV，禁止竖屏 Shorts 格式"
        )
        return fmt_block
    return None


def assert_shorts_upload_context(video_path: Path, token_path: Path) -> dict[str, Any] | None:
    """Real NYC Shorts 已停用 — ``token_shorts.json`` 划归张子维国语 MV 频道。"""
    return {
        "status": "blocked",
        "block_reason": "shorts_channel_repurposed",
        "channel_guard_detail": (
            "token_shorts.json 已用于国语音乐频道（张子维 MV）；"
            "请使用 youtube_upload_direct_zhang_ziwei.py"
        ),
        "video_type": "short",
        "channel": UPLOADER_SHORTS,
        "replacement_channel": UPLOADER_ZHANG_ZIWEI,
    }


def blocked_envelope_to_json(obj: dict[str, Any]) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)
