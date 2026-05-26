"""Unified review-queue JSON for long/short automation (unlisted → human review before public)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from publish_review_queue import resolve_review_queue_dir, utc_now_iso


def add_review_item(
    *,
    video_type: str,
    channel: str,
    local_video_path: str,
    youtube_video_id: str,
    youtube_url: str,
    privacy_status: str,
    title: str,
    job_id: str,
    result_path: str,
    warnings: list[str] | None = None,
) -> Path:
    """
    Write ``<job_id>.json`` under ``publish_pack/review_queue`` (or home fallback).

    ``video_type``: ``long`` | ``short``
    ``channel``: ``NYC_LONG`` | ``SHORTS``
    """
    root = resolve_review_queue_dir(warnings=warnings)
    jid = (job_id or "").strip() or "unknown_job"
    vid = (youtube_video_id or "").strip()
    url = (youtube_url or "").strip() or (f"https://www.youtube.com/watch?v={vid}" if vid else "")
    body: dict[str, Any] = {
        "job_id": jid,
        "video_type": str(video_type).lower(),
        "channel": str(channel).upper(),
        "local_video_path": str(local_video_path or "").strip(),
        "youtube_video_id": vid,
        "youtube_url": url,
        "privacy_status": str(privacy_status or "unlisted").lower(),
        "review_status": "pending",
        "requires_review_before_public": True,
        "title": str(title or "").strip(),
        "result_path": str(result_path or "").strip(),
        "created_at": utc_now_iso(),
    }
    out = root / f"{jid}.json"
    try:
        out.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        if warnings is not None:
            warnings.append(f"review_queue_job_json_write_failed:{out}:{exc!r}")
        raise
    return out
