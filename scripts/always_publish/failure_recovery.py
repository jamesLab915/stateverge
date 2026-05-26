"""Soft vs hard failure classification for Always Deliver v2."""

from __future__ import annotations

import re
from typing import Any

from always_publish.daily_delivery_state import (
    SOFT_COOLDOWN_MAX,
    SOFT_COOLDOWN_MIN,
    record_attempt,
)

_HARD_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"duplicate|already_uploaded|content_key|quick_hash", re.I), "duplicate"),
    (re.compile(r"token.*invalid|invalid_grant|YOUTUBE_TOKEN", re.I), "token_invalid"),
    (re.compile(r"channel_guard", re.I), "channel_guard"),
    (re.compile(r"emergency|LONG_AUTOPUBLISH_DISABLED", re.I), "emergency"),
    (re.compile(r"doctor_gate_block_upload|BLOCK_UPLOAD", re.I), "doctor_block_upload"),
    (re.compile(r"wrong.?channel|shorts.*long|long.*shorts", re.I), "wrong_channel"),
    (re.compile(r"quota_exceeded|quota_gate|daily_quota_met", re.I), "quota_exceeded"),
    (re.compile(r"no_new_nyc_long|no_valid_candidates|no_material", re.I), "no_valid_candidates"),
)

_SOFT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"ffmpeg|ffprobe|encode", re.I), "ffmpeg"),
    (re.compile(r"upload.*fail|youtube.*error|network|timeout|connection", re.I), "upload_network"),
    (re.compile(r"stale.?lock|lock_busy|global_lock", re.I), "stale_lock"),
    (re.compile(r"doctor.*warn|doctor_gate_warning", re.I), "doctor_warning"),
    (re.compile(r"schedule_gated", re.I), "schedule_gated_soft"),
)


def classify_failure(reason: str, *, status: str = "", block_reason: str = "") -> dict[str, Any]:
    blob = " ".join(x for x in (reason, status, block_reason) if x).strip()
    for pat, label in _HARD_PATTERNS:
        if pat.search(blob):
            return {"class": "hard", "label": label, "retry": False, "cooldown_minutes": 0}
    for pat, label in _SOFT_PATTERNS:
        if pat.search(blob):
            return {
                "class": "soft",
                "label": label,
                "retry": True,
                "cooldown_minutes": SOFT_COOLDOWN_MIN,
            }
    if blob:
        return {"class": "soft", "label": "unknown_soft", "retry": True, "cooldown_minutes": SOFT_COOLDOWN_MAX}
    return {"class": "", "label": "", "retry": False, "cooldown_minutes": 0}


def record_queue_failure(
    *,
    kind: str,
    reason: str,
    status: str = "",
    block_reason: str = "",
) -> dict[str, Any]:
    info = classify_failure(reason, status=status, block_reason=block_reason)
    if info["class"] == "hard":
        return record_attempt(
            kind=kind,
            failure_reason=reason or block_reason,
            failure_class="hard",
            hard_block_reason=str(info.get("label") or reason),
        )
    if info["class"] == "soft":
        return record_attempt(
            kind=kind,
            failure_reason=reason or block_reason,
            failure_class="soft",
            cooldown_minutes=int(info.get("cooldown_minutes") or SOFT_COOLDOWN_MIN),
        )
    return record_attempt(kind=kind, failure_reason=reason)


def should_retry_now() -> tuple[bool, str]:
    from always_publish.daily_delivery_state import has_hard_block, is_in_cooldown

    blocked, reason = has_hard_block()
    if blocked:
        return False, reason
    cooling, eta = is_in_cooldown()
    if cooling:
        return False, f"cooldown_active:{eta}m"
    return True, "ok"
