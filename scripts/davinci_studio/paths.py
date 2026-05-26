"""Shared paths for StateVerge DaVinci Folder Studio v1 (independent module)."""

from __future__ import annotations

from pathlib import Path

STUDIO_ROOT = Path("/Volumes/SV_CACHE/davinci_studio")
INBOX_ROOT = STUDIO_ROOT / "inbox"

# Default visual-picker sources (same pair as shorts/long inbox scans and Control Center inbox UI).
AUTOMATION_MATERIAL_FOLDER_PRIMARY = Path("/Volumes/SV_TRANSFER/00_INBOX/iphone")
AUTOMATION_MATERIAL_FOLDER_SECONDARY = Path("/Volumes/SV_CACHE/inbox")
PROJECTS_ROOT = STUDIO_ROOT / "projects"
RENDERS_ROOT = STUDIO_ROOT / "renders"
REPORTS_ROOT = STUDIO_ROOT / "reports"
THUMBNAILS_ROOT = STUDIO_ROOT / "thumbnails"
# Per-source normalized segment cache (key = path + mtime + audio filter); never touches sources.
NORMALIZE_CACHE_ROOT = STUDIO_ROOT / "normalize_cache"

NYC_LONG_MUSIC_ROOT = Path("/Volumes/SV_TRANSFER/04_AUDIO/music/nyc_long")
ENVATO_MUSIC_ROOT = Path("/Volumes/SV_TRANSFER/04_AUDIO/music")
DRIVING_MUSIC_SUBDIRS = ("ambient", "calm_piano", "cinematic", "night_drive")

VIDEO_EXTS = frozenset({".mov", ".mp4", ".m4v"})

OUTPUT_FPS = 30
OUTPUT_VIDEO_CODEC = "libx264"
OUTPUT_PIX_FMT = "yuv420p"
OUTPUT_AUDIO_CODEC = "aac"
OUTPUT_AUDIO_BITRATE = "192k"
OUTPUT_AUDIO_RATE = "48000"

MARKER_READY = "DAVINCI_FOLDER_STUDIO_V1_READY=true"
RUNNER_MARKER_READY = "DAVINCI_FOLDER_RUNNER_READY=true"

FINISHED_FOR_YOUTUBE_ROOT = Path(
    "/Volumes/SV_TRANSFER/ready_to_upload/finished_for_youtube"
)

FOLDER_RUNNER_MODES = frozenset({"ferry_real", "driving_music", "shorts_cinematic"})
