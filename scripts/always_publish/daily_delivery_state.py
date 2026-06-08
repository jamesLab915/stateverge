"""Daily delivery state for Always Deliver Scheduler v2."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from always_publish.paths import DAILY_DELIVERY_STATE_PATH, ensure_ledger_dir
from always_publish.upload_counter import count_uploads_today, delivery_timezone, today_local_str

DAILY_LONG_REQUIRED = 1
DAILY_LONG_EXTRA_OPTIONAL = 1
DAILY_SHORTS_REQUIRED = 8

SOFT_COOLDOWN_MIN = 15
SOFT_COOLDOWN_MAX = 30


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state(day: str) -> dict[str, Any]:
    return {
        "version": "daily_delivery_state_v2",
        "date": day,
        "timezone": "America/New_York",
        "long_required": DAILY_LONG_REQUIRED,
        "long_extra_optional": DAILY_LONG_EXTRA_OPTIONAL,
        "long_uploaded": 0,
        "long_remaining": DAILY_LONG_REQUIRED,
        "long_extra_uploaded": 0,
        "shorts_required": DAILY_SHORTS_REQUIRED,
        "shorts_uploaded": 0,
        "shorts_remaining": DAILY_SHORTS_REQUIRED,
        "last_attempt_at": "",
        "last_attempt_kind": "",
        "last_failure_reason": "",
        "last_failure_class": "",
        "hard_block_reason": "",
        "cooldown_until": "",
        "delivery_complete": False,
        "next_retry_eta_minutes": 0,
    }


def _safe_read() -> dict[str, Any]:
    try:
        if not DAILY_DELIVERY_STATE_PATH.is_file():
            return {}
        data = json.loads(DAILY_DELIVERY_STATE_PATH.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_write(doc: dict[str, Any]) -> bool:
    try:
        ensure_ledger_dir()
        doc["updated_at"] = _utc_iso()
        DAILY_DELIVERY_STATE_PATH.write_text(
            json.dumps(doc, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


def _remaining(required: int, uploaded: int) -> int:
    return max(0, int(required) - int(uploaded))


def _delivery_complete(long_uploaded: int, shorts_uploaded: int) -> bool:
    return long_uploaded >= DAILY_LONG_REQUIRED and shorts_uploaded >= DAILY_SHORTS_REQUIRED


def _eta_minutes(cooldown_until: str) -> int:
    if not cooldown_until:
        return 0
    try:
        cu = datetime.fromisoformat(cooldown_until.replace("Z", "+00:00"))
        if cu.tzinfo is None:
            cu = cu.replace(tzinfo=timezone.utc)
        delta = cu - datetime.now(timezone.utc)
        return max(0, int(delta.total_seconds() // 60) + (1 if delta.total_seconds() % 60 else 0))
    except ValueError:
        return 0


def refresh_delivery_counts(*, persist: bool = True) -> dict[str, Any]:
    day = today_local_str()
    counts = count_uploads_today()
    long_uploaded = int(counts.get("long_uploaded") or 0)
    shorts_uploaded = int(counts.get("shorts_uploaded") or 0)
    long_extra_uploaded = max(0, long_uploaded - DAILY_LONG_REQUIRED)

    doc = _safe_read()
    if doc.get("date") != day:
        doc = _default_state(day)
    doc.update(
        {
            "date": day,
            "timezone": "America/New_York",
            "long_required": DAILY_LONG_REQUIRED,
            "long_extra_optional": DAILY_LONG_EXTRA_OPTIONAL,
            "long_uploaded": long_uploaded,
            "long_remaining": _remaining(DAILY_LONG_REQUIRED, long_uploaded),
            "long_extra_uploaded": long_extra_uploaded,
            "shorts_required": DAILY_SHORTS_REQUIRED,
            "shorts_uploaded": shorts_uploaded,
            "shorts_remaining": _remaining(DAILY_SHORTS_REQUIRED, shorts_uploaded),
            "delivery_complete": _delivery_complete(long_uploaded, shorts_uploaded),
            "next_retry_eta_minutes": _eta_minutes(str(doc.get("cooldown_until") or "")),
            "count_sources": counts.get("sources"),
        }
    )
    if persist:
        _safe_write(doc)
    return doc


def load_delivery_state() -> dict[str, Any]:
    day = today_local_str()
    doc = _safe_read()
    if doc.get("date") != day:
        return refresh_delivery_counts(persist=True)
    return refresh_delivery_counts(persist=True)


def record_attempt(
    *,
    kind: str,
    failure_reason: str = "",
    failure_class: str = "",
    hard_block_reason: str = "",
    cooldown_minutes: int = 0,
) -> dict[str, Any]:
    doc = load_delivery_state()
    doc["last_attempt_at"] = _utc_iso()
    doc["last_attempt_kind"] = kind
    if failure_reason:
        doc["last_failure_reason"] = failure_reason
    if failure_class:
        doc["last_failure_class"] = failure_class
    if hard_block_reason:
        doc["hard_block_reason"] = hard_block_reason
        doc["last_failure_class"] = "hard"
    elif failure_class == "soft" and cooldown_minutes > 0:
        doc["hard_block_reason"] = ""
        until = datetime.now(timezone.utc) + timedelta(minutes=cooldown_minutes)
        doc["cooldown_until"] = until.isoformat()
    doc["next_retry_eta_minutes"] = _eta_minutes(str(doc.get("cooldown_until") or ""))
    _safe_write(doc)
    return doc


def clear_hard_block() -> dict[str, Any]:
    doc = load_delivery_state()
    doc["hard_block_reason"] = ""
    if doc.get("last_failure_class") == "hard":
        doc["last_failure_class"] = ""
        doc["last_failure_reason"] = ""
    _safe_write(doc)
    return doc


def is_in_cooldown() -> tuple[bool, int]:
    doc = load_delivery_state()
    eta = _eta_minutes(str(doc.get("cooldown_until") or ""))
    if eta <= 0:
        return False, 0
    return True, eta


def has_hard_block() -> tuple[bool, str]:
    doc = load_delivery_state()
    reason = str(doc.get("hard_block_reason") or "").strip()
    return bool(reason), reason
