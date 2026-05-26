"""StateVerge path resolver — Pro-only defaults (MacBook Pro, user ``ziweizhang``).

Official volumes: ``/Volumes/SV_CACHE``, ``/Volumes/SV_TRANSFER``, ``/Volumes/SV_BACKUP``.
Environment overrides: ``STATEVERGE_HOME``, ``STATEVERGE_CONTROL_CENTER_HOME``,
``SV_CACHE``, ``SV_TRANSFER``, ``SV_BACKUP``.

Scripts should prefer this module or ``src.utils.storage_paths`` for SV_* roots; this file adds
Control Center home and convenience paths for jobs / media_index / publish queues.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.storage_paths import (  # noqa: E402
    get_sv_backup as _get_sv_backup_core,
    get_sv_cache as _get_sv_cache_core,
    get_sv_transfer as _get_sv_transfer_core,
)


def get_stateverge_home() -> Path:
    raw = (os.environ.get("STATEVERGE_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "StateVerge").resolve()


def get_control_center_home() -> Path:
    raw = (os.environ.get("STATEVERGE_CONTROL_CENTER_HOME") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / "StateVerge_Control_Center").resolve()


def get_sv_cache(*, verbose: bool = False) -> Path:
    return _get_sv_cache_core(verbose=verbose)


def get_sv_transfer(*, verbose: bool = False) -> Path:
    return _get_sv_transfer_core(verbose=verbose)


def get_sv_backup(*, verbose: bool = False) -> Path:
    return _get_sv_backup_core(verbose=verbose)


def get_media_index_dir() -> Path:
    return get_sv_transfer() / "media_index"


def get_ready_to_upload_dir() -> Path:
    return get_sv_transfer() / "ready_to_upload"


def get_publish_pack_dir() -> Path:
    return get_sv_transfer() / "publish_pack"


def get_jobs_dir() -> Path:
    return get_sv_cache() / "jobs"


def get_logs_dir() -> Path:
    return get_sv_cache() / "logs"


def get_renders_dir() -> Path:
    return get_sv_cache() / "renders"


def get_review_reports_dir() -> Path:
    return get_sv_cache() / "review_reports"


def get_iphone_inbox_dir() -> Path:
    return get_sv_transfer() / "00_INBOX" / "iphone"


def get_cache_inbox_dir() -> Path:
    return get_sv_cache() / "inbox"


def default_youtube_secrets_dir() -> Path:
    """Token/client paths stay under repo ``.secrets`` / ``data`` — do not move secrets blindly."""
    return get_stateverge_home() / ".secrets" / "youtube"

