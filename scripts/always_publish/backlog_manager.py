"""Ledger read/write for Always Publish Scheduler v1 (fail-open)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from always_publish.paths import (
    BACKLOG_INDEX_PATH,
    DAILY_LEDGER_PATH,
    FALLBACK_EVENTS_PATH,
    PUBLISH_CALENDAR_PATH,
    ensure_ledger_dir,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_read(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _safe_write(path: Path, data: dict[str, Any]) -> bool:
    try:
        ensure_ledger_dir()
        path.parent.mkdir(parents=True, exist_ok=True)
        data["updated_at"] = _utc_iso()
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def load_daily_ledger() -> dict[str, Any]:
    doc = _safe_read(DAILY_LEDGER_PATH)
    if not doc:
        return {"version": "daily_publish_ledger_v1", "days": {}}
    doc.setdefault("version", "daily_publish_ledger_v1")
    doc.setdefault("days", {})
    return doc


def save_daily_ledger(doc: dict[str, Any]) -> bool:
    return _safe_write(DAILY_LEDGER_PATH, doc)


def get_day_entry(day: str) -> dict[str, Any]:
    doc = load_daily_ledger()
    days = doc.get("days")
    if not isinstance(days, dict):
        days = {}
    ent = days.get(day)
    if not isinstance(ent, dict):
        ent = {"long_planned": 0, "shorts_planned": 0, "long_paths": [], "shorts_paths": []}
    ent.setdefault("long_planned", 0)
    ent.setdefault("shorts_planned", 0)
    ent.setdefault("long_paths", [])
    ent.setdefault("shorts_paths", [])
    return ent


def record_plan_only(day: str, *, long_paths: list[str], shorts_paths: list[str]) -> None:
    """Update ledger for planning (dry-run); does not mark uploaded."""
    doc = load_daily_ledger()
    days = doc.setdefault("days", {})
    ent = get_day_entry(day)
    if long_paths:
        ent["long_planned"] = min(1, int(ent.get("long_planned") or 0) + len(long_paths))
        lp = list(ent.get("long_paths") or [])
        lp.extend(long_paths)
        ent["long_paths"] = list(dict.fromkeys(lp))[:8]
    if shorts_paths:
        ent["shorts_planned"] = min(4, int(ent.get("shorts_planned") or 0) + len(shorts_paths))
        sp = list(ent.get("shorts_paths") or [])
        sp.extend(shorts_paths)
        ent["shorts_paths"] = list(dict.fromkeys(sp))[:16]
    ent["last_plan_at"] = _utc_iso()
    days[day] = ent
    save_daily_ledger(doc)


def load_backlog_index() -> dict[str, Any]:
    doc = _safe_read(BACKLOG_INDEX_PATH)
    if not doc:
        return {"version": "backlog_index_v1", "long": [], "shorts": []}
    doc.setdefault("version", "backlog_index_v1")
    doc.setdefault("long", [])
    doc.setdefault("shorts", [])
    return doc


def save_backlog_index(doc: dict[str, Any]) -> bool:
    return _safe_write(BACKLOG_INDEX_PATH, doc)


def refresh_backlog_index(*, long_candidates: list[dict[str, Any]], shorts_candidates: list[dict[str, Any]]) -> dict[str, Any]:
    doc = {
        "version": "backlog_index_v1",
        "long_count": len(long_candidates),
        "shorts_count": len(shorts_candidates),
        "long": long_candidates[:200],
        "shorts": shorts_candidates[:400],
        "refreshed_at": _utc_iso(),
    }
    save_backlog_index(doc)
    return doc


def append_fallback_event(event: dict[str, Any]) -> None:
    doc = _safe_read(FALLBACK_EVENTS_PATH)
    if not doc:
        doc = {"version": "fallback_events_v1", "events": []}
    events = doc.setdefault("events", [])
    if not isinstance(events, list):
        events = []
    event = dict(event)
    event["at"] = _utc_iso()
    events.append(event)
    doc["events"] = events[-500:]
    _safe_write(FALLBACK_EVENTS_PATH, doc)


def load_publish_calendar_snapshot() -> dict[str, Any]:
    return _safe_read(PUBLISH_CALENDAR_PATH)


def save_publish_calendar_snapshot(data: dict[str, Any]) -> bool:
    return _safe_write(PUBLISH_CALENDAR_PATH, data)


def path_in_ledger(path: str, *, kind: str = "long") -> bool:
    """Read-only dedupe: path appears in any day ledger."""
    key = "long_paths" if kind == "long" else "shorts_paths"
    doc = load_daily_ledger()
    days = doc.get("days")
    if not isinstance(days, dict):
        return False
    norm = path.strip()
    for ent in days.values():
        if not isinstance(ent, dict):
            continue
        for p in ent.get(key) or []:
            if str(p).strip() == norm:
                return True
    return False
