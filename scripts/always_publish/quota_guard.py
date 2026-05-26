"""Daily quota guard — quota-guaranteed (upload counts, not schedule triggers)."""

from __future__ import annotations

from typing import Any

from always_publish.daily_delivery_state import (
    DAILY_LONG_EXTRA_OPTIONAL,
    DAILY_LONG_REQUIRED,
    DAILY_SHORTS_REQUIRED,
    load_delivery_state,
    refresh_delivery_counts,
)
from always_publish.upload_counter import today_local_str


def get_delivery_quota_snapshot(*, refresh: bool = True) -> dict[str, Any]:
    if refresh:
        return refresh_delivery_counts(persist=True)
    return load_delivery_state()


def check_quota(day: str, cfg: dict[str, Any], *, planned_long: int = 0, planned_shorts: int = 0) -> dict[str, Any]:
    snap = get_delivery_quota_snapshot(refresh=True)
    max_long = int(cfg.get("max_daily_long") or DAILY_LONG_REQUIRED)
    max_shorts = int(cfg.get("max_daily_shorts") or DAILY_SHORTS_REQUIRED)
    long_cap = DAILY_LONG_REQUIRED + DAILY_LONG_EXTRA_OPTIONAL
    long_uploaded = int(snap.get("long_uploaded") or 0)
    shorts_uploaded = int(snap.get("shorts_uploaded") or 0)
    allow_long = max(0, min(max_long, long_cap) - long_uploaded - planned_long)
    allow_shorts = max(0, max_shorts - shorts_uploaded - planned_shorts)
    long_required_met = long_uploaded >= DAILY_LONG_REQUIRED
    return {
        "day": day or today_local_str(),
        "timezone": snap.get("timezone") or "America/New_York",
        "mode": "quota_guaranteed",
        "max_daily_long": max_long,
        "max_daily_shorts": max_shorts,
        "long_required": DAILY_LONG_REQUIRED,
        "long_extra_optional": DAILY_LONG_EXTRA_OPTIONAL,
        "long_uploaded": long_uploaded,
        "long_remaining": int(snap.get("long_remaining") or 0),
        "long_required_met": long_required_met,
        "shorts_required": DAILY_SHORTS_REQUIRED,
        "shorts_uploaded": shorts_uploaded,
        "shorts_remaining": int(snap.get("shorts_remaining") or 0),
        "used_long": long_uploaded,
        "used_shorts": shorts_uploaded,
        "allow_long": allow_long,
        "allow_shorts": allow_shorts,
        "long_quota_ok": allow_long > 0,
        "shorts_quota_ok": allow_shorts > 0,
        "delivery_complete": bool(snap.get("delivery_complete")),
        "hard_block_reason": snap.get("hard_block_reason") or "",
        "cooldown_until": snap.get("cooldown_until") or "",
        "next_retry_eta_minutes": int(snap.get("next_retry_eta_minutes") or 0),
        "last_attempt_at": snap.get("last_attempt_at") or "",
    }


def long_upload_allowed(*, kind: str = "1h", force: bool = False) -> dict[str, Any]:
    snap = get_delivery_quota_snapshot()
    long_uploaded = int(snap.get("long_uploaded") or 0)
    long_required = DAILY_LONG_REQUIRED
    long_cap = long_required + DAILY_LONG_EXTRA_OPTIONAL
    k = "1h" if kind in ("1h", "standard", "") else "3h" if kind in ("3h", "extended") else kind
    allowed = True
    reason = "ok"
    if not force:
        if k == "1h" and long_uploaded >= long_required:
            allowed = False
            reason = "quota_gate_long_required_met"
        elif k in ("3h", "extra") and long_uploaded >= long_cap:
            allowed = False
            reason = "quota_gate_long_daily_cap_met"
    return {
        "allowed": allowed,
        "reason": reason,
        "schedule_mode": "quota_guaranteed",
        "long_uploaded": long_uploaded,
        "long_required": long_required,
        "long_cap": long_cap,
        "long_remaining": max(0, long_required - long_uploaded),
    }


def shorts_upload_allowed(*, force: bool = False) -> dict[str, Any]:
    snap = get_delivery_quota_snapshot()
    shorts_uploaded = int(snap.get("shorts_uploaded") or 0)
    allowed = True
    reason = "ok"
    if not force and shorts_uploaded >= DAILY_SHORTS_REQUIRED:
        allowed = False
        reason = "quota_gate_shorts_required_met"
    return {
        "allowed": allowed,
        "reason": reason,
        "schedule_mode": "quota_guaranteed",
        "shorts_uploaded": shorts_uploaded,
        "shorts_required": DAILY_SHORTS_REQUIRED,
        "shorts_remaining": max(0, DAILY_SHORTS_REQUIRED - shorts_uploaded),
    }
