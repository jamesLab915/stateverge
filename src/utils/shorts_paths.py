"""Canonical Shorts automation paths (Pro-only, SV_TRANSFER / SV_CACHE).

Do not default writes to legacy roots (/Volumes/StateVerge, /Volumes/SV_WORK, other users).
"""

from __future__ import annotations

from pathlib import Path

from utils.storage_paths import code_root, get_sv_cache, get_sv_transfer


def token_shorts_path() -> Path:
    return code_root() / "data" / "youtube" / "token_shorts.json"


def youtube_client_secrets_path() -> Path:
    return code_root() / ".secrets" / "youtube" / "client_secrets.json"


def long_channel_token_path() -> Path:
    """Long-form NYC channel token — must never be used for Shorts upload."""
    return code_root() / "data" / "youtube" / "token.json"


def shorts_ready_clips_dir(*, verbose: bool = False) -> Path:
    return get_sv_transfer(verbose=verbose) / "ready_to_upload" / "shorts_clips"


def shorts_publish_pack_root(*, verbose: bool = False) -> Path:
    return get_sv_transfer(verbose=verbose) / "publish_pack" / "shorts_uploads"


def shorts_used_assets_json(*, verbose: bool = False) -> Path:
    """Append-only ledger of rendered Shorts inputs (dedupe / anti-repeat)."""
    return shorts_publish_pack_root(verbose=verbose) / "shorts_used_assets.json"


def shorts_image_cache_dir(*, verbose: bool = False) -> Path:
    """Converted HEIC/DNG/TIFF intermediates (originals never deleted)."""
    return shorts_renders_root(verbose=verbose) / "image_cache"


def shorts_jobs_root(*, verbose: bool = False) -> Path:
    return get_sv_cache(verbose=verbose) / "jobs" / "shorts"


def shorts_logs_root(*, verbose: bool = False) -> Path:
    return get_sv_cache(verbose=verbose) / "logs" / "shorts"


def shorts_renders_root(*, verbose: bool = False) -> Path:
    return get_sv_cache(verbose=verbose) / "renders" / "shorts"


def ensure_shorts_dirs(*, verbose: bool = False) -> dict[str, Path]:
    """Create expected directories (fail-open per path)."""
    paths = {
        "ready_clips": shorts_ready_clips_dir(verbose=verbose),
        "publish_pack": shorts_publish_pack_root(verbose=verbose),
        "used_assets": shorts_used_assets_json(verbose=verbose),
        "jobs": shorts_jobs_root(verbose=verbose),
        "logs": shorts_logs_root(verbose=verbose),
        "renders": shorts_renders_root(verbose=verbose),
        "image_cache": shorts_image_cache_dir(verbose=verbose),
    }
    for p in paths.values():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return paths
