"""Private upload review queue — JSON + Markdown artifacts (Pro-only)."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def resolve_review_queue_dir(*, warnings: list[str] | None = None) -> Path:
    primary = Path("/Volumes/SV_TRANSFER/publish_pack/review_queue")
    fallback = Path.home() / "StateVerge/data/review_queue"
    for p in (primary, fallback):
        try:
            p.mkdir(parents=True, exist_ok=True)
            if os.access(p, os.W_OK):
                return p
        except OSError as exc:
            if warnings is not None:
                warnings.append(f"review_queue_dir:{p}:{exc!r}")
            continue
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback


def _md_escape(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def write_private_upload_review(
    video_id: str,
    payload: dict[str, Any],
    *,
    warnings: list[str] | None = None,
) -> tuple[Path, Path]:
    """Write ``<video_id>.json`` and ``<video_id>.md`` under review queue root."""
    root = resolve_review_queue_dir(warnings=warnings)
    vid = (video_id or "").strip()
    if not vid:
        vid = "unknown"
    jpath = root / f"{vid}.json"
    mpath = root / f"{vid}.md"
    body = dict(payload)
    body.setdefault("review_status", "needs_human_review")
    body.setdefault("privacy", "private")
    body.setdefault("youtube_video_id", vid)
    body.setdefault("human_review_required", True)
    body.setdefault("agent_uploaded", True)
    jpath.write_text(json.dumps(body, indent=2, ensure_ascii=False), encoding="utf-8")
    title = _md_escape(str(body.get("title") or body.get("title_used") or ""))
    url = str(body.get("youtube_url") or f"https://www.youtube.com/watch?v={vid}")
    md_lines = [
        "# Private upload — human review required",
        "",
        "## Status",
        "",
        "- **Privacy**: **Private** (uploaded to YouTube as private).",
        "- **Human review**: **Required** before any public or scheduled-public release.",
        "- **Automation**: This upload was **not** made public. The system will **not** auto-publish as Public.",
        "",
        "## YouTube Studio",
        "",
        "Open [YouTube Studio](https://studio.youtube.com/) for this channel. To go live:",
        "",
        "1. Review title, description, thumbnail, and ads suitability.",
        "2. When ready, change visibility to **Public** or **Schedule** manually.",
        "3. Until then, keep **Private** or **Unlisted** as you prefer.",
        "",
        "## Summary",
        "",
        f"- **video_id**: `{vid}`",
        f"- **title**: {title or '(none)'}",
        f"- **url**: {url}",
        f"- **channel_type**: `{body.get('channel_type', '')}`",
        f"- **uploaded_at**: `{body.get('uploaded_at', '')}`",
        f"- **package_dir**: `{body.get('package_dir', '')}`",
        f"- **source_video**: `{body.get('source_video', '')}`",
        "",
        "## Review JSON",
        "",
        f"See: `{jpath}`",
        "",
    ]
    mpath.write_text("\n".join(md_lines), encoding="utf-8")
    return jpath, mpath


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
