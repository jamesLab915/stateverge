"""Shared paths for Professional DaVinci Production Agent v1."""

from __future__ import annotations

import sys
from pathlib import Path

# Resolve legacy STATEVERGE_VOL via canonical storage_paths (respects storage_map.env + env override).
# Fallback: /Volumes/StateVerge for backwards compatibility only.
try:
    _SRC = Path(__file__).resolve().parent.parent.parent / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from utils.storage_paths import get_stateverge_volume as _get_sv_vol  # noqa: E402
    _STATEVERGE_VOL: Path = _get_sv_vol()
except Exception:
    _STATEVERGE_VOL = Path("/Volumes/StateVerge")

# Input sources (iPhone video169)
INBOX_VIDEO169_PRIMARY = Path("/Volumes/SV_TRANSFER/00_INBOX/iphone/video169")
INBOX_VIDEO169_SECONDARY = Path("/Volumes/SV_CACHE/inbox/video169")

# Output inventory (renders + manifests — never ready_to_upload for DaVinci renders)
DELIVERY_QUEUE_ROOT = Path("/Volumes/SV_TRANSFER/publish_pack/delivery_queue")
DELIVERY_QUEUE_LONG = DELIVERY_QUEUE_ROOT / "long"
DELIVERY_QUEUE_SHORTS = DELIVERY_QUEUE_ROOT / "shorts"
FINISHED_FOR_YOUTUBE_ROOT = Path("/Volumes/SV_TRANSFER/ready_to_upload/finished_for_youtube")

# DaVinci workspace (extends davinci_studio layout)
STUDIO_ROOT = Path("/Volumes/SV_CACHE/davinci_studio")
PROJECTS_ROOT = STUDIO_ROOT / "projects"
TIMELINES_ROOT = STUDIO_ROOT / "timelines"
RENDERS_ROOT = STUDIO_ROOT / "renders"
REPORTS_ROOT = STUDIO_ROOT / "reports"
CACHE_ROOT = STUDIO_ROOT / "cache"
CUTLISTS_ROOT = STUDIO_ROOT / "cutlists"
QC_ROOT = STUDIO_ROOT / "qc"
MUSIC_CACHE_ROOT = STUDIO_ROOT / "music_cache"

# Agent status / reports
AGENT_STATUS_JSON = REPORTS_ROOT / "professional_davinci_agent_status.json"
AGENT_REPORT_MD = Path.home() / "StateVerge_Control_Center/logs/professional_davinci_production_agent_v1.md"
STABILIZATION_REPORT_MD = Path.home() / "StateVerge_Control_Center/logs/production_stabilization_v1.md"

# Required volumes for idle inventory mode.
# _STATEVERGE_VOL is resolved above via storage_paths (respects STATEVERGE_VOL env / storage_map.env).
REQUIRED_VOLUMES = (
    Path("/Volumes/SV_TRANSFER"),
    Path("/Volumes/SV_CACHE"),
    _STATEVERGE_VOL,
)

STUDIO_SUBDIRS = (
    "projects",
    "timelines",
    "renders",
    "reports",
    "cache",
    "cutlists",
    "qc",
    "music_cache",
)

VIDEO_EXTS = frozenset({".mov", ".mp4", ".m4v"})
IPHONE_VFR_MARKERS = ("/video169/", "/00_INBOX/iphone/", "/iphone/video169")

MARKER_AGENT_READY = "PROFESSIONAL_DAVINCI_AGENT_READY=true"
MARKER_API_READY = "DAVINCI_API_READY=true"
MARKER_FUTURE_INVENTORY = "FUTURE_INVENTORY_MODE=true"
MARKER_UPLOAD_FROM_QUEUE = "UPLOAD_FROM_DELIVERY_QUEUE=true"
MARKER_PUBLIC_DISABLED = "PUBLIC_UPLOAD_DISABLED=true"
MARKER_REVIEW_BEFORE_PUBLIC = "REVIEW_BEFORE_PUBLIC=true"

# Production Stabilization Phase v1
MARKER_PRODUCTION_STABILIZATION_READY = "PRODUCTION_STABILIZATION_V1_READY=true"
MARKER_DELIVERY_QUEUE_UPLOAD_ONLY = "DELIVERY_QUEUE_UPLOAD_ONLY=true"
MARKER_RESOLVE_RENDER_ACTIVE = "RESOLVE_RENDER_ACTIVE=true"
MARKER_FUTURE_INVENTORY_AUTOBUILD = "FUTURE_INVENTORY_AUTOBUILD=true"


def delivery_render_path(*, kind: str, job_id: str) -> Path:
    """Canonical Resolve render output under delivery_queue (not ready_to_upload)."""
    root = DELIVERY_QUEUE_SHORTS if kind == "shorts" else DELIVERY_QUEUE_LONG
    return root / f"{job_id}.mp4"
