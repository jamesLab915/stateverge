"""Paths for DaVinci YouTube Audio Finishing Gate v1."""

from __future__ import annotations

from pathlib import Path

GATE_ROOT = Path("/Volumes/SV_CACHE/davinci_audio_finish")
INBOX_ROOT = GATE_ROOT / "inbox"
RENDERS_ROOT = GATE_ROOT / "renders"
REPORTS_ROOT = GATE_ROOT / "reports"

FINISHED_FOR_YOUTUBE_ROOT = Path("/Volumes/SV_TRANSFER/ready_to_upload/finished_for_youtube")

REPORT_BASENAME = "davinci_audio_finish_report.json"

VIDEO_EXTS = frozenset({".mp4", ".mov", ".m4v"})
MIN_OUTPUT_BYTES = 1024 * 1024
