"""Write JSON + Markdown status reports (fail-open)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from always_publish.paths import STATUS_JSON_PATH, STATUS_MD_PATH, ensure_status_log_dir


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_status(payload: dict[str, Any]) -> dict[str, str]:
    ensure_status_log_dir()
    payload = dict(payload)
    payload["written_at"] = _utc_iso()
    paths = {"json": str(STATUS_JSON_PATH), "md": str(STATUS_MD_PATH)}
    try:
        STATUS_JSON_PATH.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        paths["json_error"] = "write_failed"
    try:
        STATUS_MD_PATH.write_text(_render_md(payload), encoding="utf-8")
    except OSError:
        paths["md_error"] = "write_failed"
    return paths


def _render_md(p: dict[str, Any]) -> str:
    lines = [
        "# Always Publish Status",
        "",
        f"- written_at: `{p.get('written_at', '')}`",
        f"- mode: `{p.get('mode', '')}`",
        f"- date: `{p.get('date', '')}`",
        "",
        "## Today",
        f"- long_ready: **{p.get('today_long_ready', False)}**",
        f"- shorts_count: **{p.get('today_shorts_count', 0)}** / 4",
        f"- backlog_count: **{p.get('backlog_count', 0)}**",
        "",
        "## Guards",
    ]
    g = p.get("guards") if isinstance(p.get("guards"), dict) else {}
    lines.append(f"- doctor_blocked: `{g.get('doctor_blocked')}`")
    lines.append(f"- channel_guard_ok: `{g.get('channel_guard_ok')}`")
    lines.append(f"- long_emergency_active: `{g.get('long_emergency_active')}`")
    lines.append(f"- shorts_global_lock: `{g.get('shorts_global_lock')}`")
    lines.append("")
    lines.append("## Next times")
    cal = p.get("calendar") if isinstance(p.get("calendar"), dict) else {}
    lines.append(f"- next_long_daily: `{cal.get('next_long_daily', '')}`")
    lines.append(f"- next_long_special: `{cal.get('next_long_special', '')}`")
    for i, st in enumerate(cal.get("next_shorts_slots") or []):
        lines.append(f"- shorts_slot_{i + 1}: `{st}`")
    lines.append("")
    if p.get("long_plan"):
        lp = p["long_plan"]
        lines.append("## Long plan")
        lines.append(f"- fallback_level: `{lp.get('fallback_level')}`")
        lines.append(f"- path: `{lp.get('path', '')}`")
        lines.append("")
    if p.get("shorts_plans"):
        lines.append("## Shorts plans")
        for i, sp in enumerate(p.get("shorts_plans") or []):
            lines.append(f"### Slot {i + 1}")
            lines.append(f"- time: `{sp.get('slot_time', '')}`")
            lines.append(f"- fallback_level: `{sp.get('fallback_level')}`")
            lines.append(f"- path: `{sp.get('path', '')}`")
    if p.get("blockers"):
        lines.append("")
        lines.append("## Blockers")
        for b in p.get("blockers") or []:
            lines.append(f"- {b}")
    if p.get("warnings"):
        lines.append("")
        lines.append("## Warnings")
        for w in p.get("warnings") or []:
            lines.append(f"- {w}")
    return "\n".join(lines) + "\n"
