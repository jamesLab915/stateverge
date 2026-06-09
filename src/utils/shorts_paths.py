"""Canonical Shorts automation paths (Pro-only, SV_TRANSFER / SV_CACHE).

Do not default writes to legacy roots (/Volumes/StateVerge, /Volumes/SV_WORK, other users).
"""

from __future__ import annotations

import os
from pathlib import Path

from utils.storage_paths import code_root, get_sv_cache, get_sv_transfer


def token_shorts_path() -> Path:
    """Repurposed for 张子维 Ziwei Zhang 国语 MV — Real NYC Shorts 已停用."""
    return code_root() / "data" / "youtube" / "token_shorts.json"


def token_zhang_ziwei_path() -> Path:
    """Alias: 张子维国语频道 OAuth（与 token_shorts_path 相同文件）."""
    return token_shorts_path()


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


def shorts_police_clips_dir(*, verbose: bool = False) -> Path:
    """Pre-cut police / siren clips for Shorts (default ``素材/police_clips``).

    Override with ``STATEVERGE_SHORTS_POLICE_CLIPS_DIR`` or ``storage_map.env``.
    """
    override = os.environ.get("STATEVERGE_SHORTS_POLICE_CLIPS_DIR", "").strip()
    if not override:
        try:
            from utils.storage_paths import load_storage_map_from_file

            override = (load_storage_map_from_file().get("STATEVERGE_SHORTS_POLICE_CLIPS_DIR") or "").strip()
        except Exception:
            pass
    if override:
        return Path(override).expanduser().resolve()
    return shorts_materials_dir(verbose=verbose) / "police_clips"


def shorts_materials_dir(*, verbose: bool = False) -> Path:
    """Shorts candidate pool (default ``/Volumes/SV_CACHE/air``).

    Override with ``STATEVERGE_SHORTS_MATERIALS_DIR`` (env or ``config/storage_map.env``).
    """
    override = os.environ.get("STATEVERGE_SHORTS_MATERIALS_DIR", "").strip()
    if not override:
        try:
            from utils.storage_paths import load_storage_map_from_file

            override = (load_storage_map_from_file().get("STATEVERGE_SHORTS_MATERIALS_DIR") or "").strip()
        except Exception:
            pass
    if override:
        return Path(override).expanduser().resolve()
    return get_sv_cache(verbose=verbose) / "air"


def shorts_portrait_only(*, verbose: bool = False) -> bool:
    """When true, Shorts worker only picks portrait (h > w) videos from ``shorts_materials_dir``."""
    _ = verbose
    raw = os.environ.get("STATEVERGE_SHORTS_PORTRAIT_ONLY", "").strip().lower()
    if not raw:
        try:
            from utils.storage_paths import load_storage_map_from_file

            raw = (load_storage_map_from_file().get("STATEVERGE_SHORTS_PORTRAIT_ONLY") or "").strip().lower()
        except Exception:
            raw = ""
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return False


def ensure_shorts_dirs(*, verbose: bool = False) -> dict[str, Path]:
    """Create expected directories (fail-open per path)."""
    paths = {
        "police_clips": shorts_police_clips_dir(verbose=verbose),
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
