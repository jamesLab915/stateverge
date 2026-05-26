"""Shared paths for Always Publish Scheduler v1."""

from __future__ import annotations

import os
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "scripts"

CONFIG_PATH = _REPO / "config" / "always_publish_schedule.json"
LEDGER_DIR = Path("/Volumes/SV_TRANSFER/publish_pack/always_publish")
DAILY_LEDGER_PATH = LEDGER_DIR / "daily_publish_ledger.json"
DAILY_DELIVERY_STATE_PATH = LEDGER_DIR / "daily_delivery_state.json"
BACKLOG_INDEX_PATH = LEDGER_DIR / "backlog_index.json"
FALLBACK_EVENTS_PATH = LEDGER_DIR / "fallback_events.json"
PUBLISH_CALENDAR_PATH = LEDGER_DIR / "publish_calendar.json"

STATUS_JSON_PATH = Path("/Volumes/SV_CACHE/logs/always_publish_status.json")
STATUS_MD_PATH = Path("/Volumes/SV_CACHE/logs/always_publish_status.md")

READY_ROOT = Path("/Volumes/SV_TRANSFER/ready_to_upload")
LONG_CLIPS_DIR = READY_ROOT / "nyc_long_clips"
SHORTS_CLIPS_DIR = READY_ROOT / "shorts_clips"

LONG_EMERGENCY_PATHS = (
    Path("/Volumes/SV_TRANSFER/publish_pack/nyc_long_uploads/long_autopublish_emergency.json"),
    _REPO / "data/long_runtime/long_uploads/long_autopublish_emergency.json",
)
LONG_REJECTED_PATH = Path(
    "/Volumes/SV_TRANSFER/publish_pack/nyc_long_uploads/long_rejected_outputs.json"
)
SHORTS_GLOBAL_LOCK = _REPO / "data/shorts_runtime/.shorts_cut_upload.global.lock"
UPLOAD_HISTORY_CSV = _REPO / "data/youtube/upload_history.csv"

DOCTOR_REPORT_PATHS = (
    Path("/Volumes/SV_CACHE/logs/stateverge_doctor_report.json"),
    _REPO / "logs/stateverge_doctor_report.json",
    Path.home() / "StateVerge_Control_Center/logs/stateverge_doctor_report.json",
)


def ensure_ledger_dir() -> Path:
    try:
        LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return LEDGER_DIR


def ensure_status_log_dir() -> None:
    for p in (STATUS_JSON_PATH.parent,):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def scripts_on_path() -> None:
    import sys

    sp = str(_SCRIPTS)
    if sp not in sys.path:
        sys.path.insert(0, sp)
