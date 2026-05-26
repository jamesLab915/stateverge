"""Count successful long/short uploads for today (America/New_York)."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from always_publish.paths import UPLOAD_HISTORY_CSV

_REPO = Path(__file__).resolve().parents[2]
_SHORTS_UPLOAD_ROOTS = (
    _REPO / "data" / "shorts_runtime" / "shorts_uploads",
    Path("/Volumes/SV_TRANSFER/publish_pack/shorts_uploads"),
)
_LONG_UPLOAD_ROOTS = (
    _REPO / "data" / "long_runtime" / "long_uploads",
    Path("/Volumes/SV_TRANSFER/publish_pack/nyc_long_uploads"),
)

_SHORTS_MARKERS = ("shorts_clips", "shorts_uploads", "/shorts/", "youtube_shorts")
_LONG_MARKERS = ("nyc_long_clips", "nyc_long_uploads", "nyc_long", "long_uploads", "manual_review_pack")


def delivery_timezone() -> Any:
    try:
        return ZoneInfo("America/New_York")
    except Exception:
        return datetime.now().astimezone().tzinfo or timezone.utc


def today_local_date() -> date:
    return datetime.now(delivery_timezone()).date()


def today_local_str() -> str:
    return today_local_date().isoformat()


def _parse_dt(value: str, tz: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(tz)
    except ValueError:
        return None


def _path_kind(path: str) -> str | None:
    s = path.replace("\\", "/").lower()
    if any(m in s for m in _SHORTS_MARKERS):
        return "shorts"
    if any(m in s for m in _LONG_MARKERS):
        return "long"
    if "shorts" in s and "nyc_long" not in s:
        return "shorts"
    if re.search(r"nyc_long|_long_|long_clips", s):
        return "long"
    return None


def _is_success_row(*, status: str, privacy: str, video_id: str) -> bool:
    if str(status or "").lower() != "success":
        return False
    if str(privacy or "").lower() == "public":
        return False
    return bool(str(video_id or "").strip())


def _safe_read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _shorts_job_success(doc: dict[str, Any], up: dict[str, Any] | None) -> bool:
    if up:
        if up.get("ok") is True and str(up.get("status") or "").lower() in ("success", "uploaded", "ok"):
            priv = str(up.get("privacy_used") or up.get("privacy") or "").lower()
            if priv == "public":
                return False
            return priv in ("", "unlisted", "private") and bool(up.get("video_id"))
    if doc.get("uploaded") is True:
        priv = str(doc.get("privacy_status") or doc.get("privacy") or "unlisted").lower()
        return priv != "public"
    st = str(doc.get("status") or "").lower()
    return st == "uploaded" and bool(doc.get("youtube_video_id"))


def _long_job_success(doc: dict[str, Any], up: dict[str, Any] | None) -> bool:
    if up:
        if up.get("ok") is True and bool(up.get("video_id")):
            priv = str(up.get("privacy_used") or up.get("privacy") or "").lower()
            return priv != "public"
    if doc.get("uploaded") is True and doc.get("youtube_video_id"):
        priv = str(doc.get("privacy_status") or doc.get("privacy") or "unlisted").lower()
        return priv != "public"
    uploads = doc.get("uploads")
    if isinstance(uploads, list):
        for u in uploads:
            if isinstance(u, dict) and u.get("ok") and u.get("video_id"):
                return True
    st = str(doc.get("status") or "").lower()
    return st in ("uploaded", "completed") and bool(doc.get("youtube_video_id"))


def _count_from_upload_history(today: date, tz: Any) -> tuple[int, int, list[str]]:
    long_ids: set[str] = set()
    shorts_ids: set[str] = set()
    if not UPLOAD_HISTORY_CSV.is_file():
        return 0, 0, []
    try:
        lines = UPLOAD_HISTORY_CSV.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0, 0, []
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) < 8:
            continue
        uploaded_at, _pid, _pkg, video_path, video_id, privacy, _title, status = parts[:8]
        if not _is_success_row(status=status, privacy=privacy, video_id=video_id):
            continue
        dt = _parse_dt(uploaded_at, tz)
        if not dt or dt.date() != today:
            continue
        kind = _path_kind(video_path)
        vid = video_id.strip()
        if kind == "long" and vid and vid not in long_ids:
            long_ids.add(vid)
        elif kind == "shorts" and vid and vid not in shorts_ids:
            shorts_ids.add(vid)
    return len(long_ids), len(shorts_ids), ["upload_history_csv"]


def _count_shorts_jobs(today: date, tz: Any) -> tuple[int, list[str]]:
    seen: set[str] = set()
    sources: list[str] = []
    for root in _SHORTS_UPLOAD_ROOTS:
        if not root.is_dir():
            continue
        sources.append(str(root))
        for result_path in root.glob("*/shorts_job_result.json"):
            doc = _safe_read_json(result_path)
            up_path = result_path.parent / "upload_result.json"
            up = _safe_read_json(up_path) if up_path.is_file() else None
            created = _parse_dt(str(doc.get("created_at") or ""), tz)
            if created and created.date() != today:
                continue
            if not _shorts_job_success(doc, up if up else None):
                continue
            vid = str((up or {}).get("video_id") or doc.get("youtube_video_id") or "").strip()
            if vid:
                seen.add(vid)
    return len(seen), sources


def _count_long_jobs(today: date, tz: Any) -> tuple[int, list[str]]:
    seen: set[str] = set()
    sources: list[str] = []
    for root in _LONG_UPLOAD_ROOTS:
        if not root.is_dir():
            continue
        sources.append(str(root))
        for result_path in root.glob("*/long_job_result.json"):
            doc = _safe_read_json(result_path)
            up_path = result_path.parent / "upload_result.json"
            up = _safe_read_json(up_path) if up_path.is_file() else None
            finished = _parse_dt(str(doc.get("finished_at") or doc.get("created_at") or ""), tz)
            if finished and finished.date() != today:
                continue
            if not _long_job_success(doc, up if up else None):
                continue
            vid = str(
                (up or {}).get("video_id")
                or doc.get("youtube_video_id")
                or ""
            ).strip()
            if not vid and isinstance(doc.get("uploads"), list):
                for u in doc["uploads"]:
                    if isinstance(u, dict) and u.get("ok"):
                        vid = str(u.get("video_id") or "").strip()
                        if vid:
                            break
            if vid:
                seen.add(vid)
    return len(seen), sources


def count_uploads_today(*, on_date: date | None = None) -> dict[str, Any]:
    tz = delivery_timezone()
    today = on_date or today_local_date()
    long_hist, shorts_hist, hist_src = _count_from_upload_history(today, tz)
    long_jobs, long_src = _count_long_jobs(today, tz)
    shorts_jobs, shorts_src = _count_shorts_jobs(today, tz)
    long_uploaded = max(long_hist, long_jobs)
    shorts_uploaded = max(shorts_hist, shorts_jobs)
    return {
        "date": today.isoformat(),
        "timezone": "America/New_York",
        "long_uploaded": long_uploaded,
        "shorts_uploaded": shorts_uploaded,
        "sources": {
            "long": list(dict.fromkeys(hist_src + long_src)),
            "shorts": list(dict.fromkeys(hist_src + shorts_src)),
        },
    }
