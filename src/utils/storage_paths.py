"""Logical volume paths for StateVerge (SV_CACHE / SV_TRANSFER / SV_BACKUP / SV_ARCHIVE).

Reads ``$STATEVERGE_ROOT/config/storage_map.env`` (default ``~/StateVerge``).
Environment variables override file values when set (non-empty).

Fail-open: if a configured volume is missing, fall back to a repo-local directory
under ``_storage_fallback/`` so callers never crash on import.

Optional keys (same file or env):

- ``STATEVERGE_VOL`` — primary project SSD (default ``/Volumes/StateVerge``).
- ``STATEVERGE_TS_INPUT`` — dashcam / removable TS source (no default; unset means
  TS ingest tools skip until configured).

Do not hardcode vendor volume labels (e.g. ``/Volumes/T7``); use this module or env.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ENV_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$"
)


def code_root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", str(Path.home() / "StateVerge"))
    ).expanduser().resolve()


def storage_map_path() -> Path:
    return code_root() / "config" / "storage_map.env"


def _strip_quotes(raw: str) -> str:
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def load_storage_map_from_file(path: Path | None = None) -> dict[str, str]:
    """Parse KEY=value lines; ``#`` comments and blanks skipped."""
    p = path or storage_map_path()
    out: dict[str, str] = {}
    if not p.is_file():
        logger.debug("storage map file missing: %s", p)
        return out
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning("cannot read storage map %s: %s", p, exc)
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), _strip_quotes(m.group(2))
        if key:
            out[key] = val
    return out


def _effective(key: str, file_map: dict[str, str], default: str = "") -> str:
    env_val = os.environ.get(key)
    if env_val is not None and str(env_val).strip() != "":
        return str(env_val).strip()
    return file_map.get(key, default)


def _fallback_dir(role: str) -> Path:
    d = code_root() / "_storage_fallback" / role
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("fallback mkdir failed %s: %s", d, exc)
    return d


def _vlog(verbose: bool, fmt: str, *args: Any) -> None:
    if not verbose:
        return
    text = fmt % args if args else fmt
    logger.info(text)
    print(f"[storage_paths] {text}", flush=True)


def _resolve_role_path(
    env_key: str,
    *,
    configured: str,
    fallback_label: str,
    verbose: bool,
) -> Path:
    """Resolve SV_* root.

    Priority:
    1. Non-empty ``SV_*`` **environment** variable pointing at an existing directory (explicit operator override).
    2. Canonical ``/Volumes/SV_*`` when mounted — fixes stale ``storage_map.env`` entries that still
       pointed at repo fallback after the real volume was plugged in.
    3. Path from env/file/default if that path exists.
    4. Repo-local ``_storage_fallback``.
    """
    file_map = load_storage_map_from_file()
    canon = Path(configured).expanduser()

    env_raw = os.environ.get(env_key)
    env_explicit = env_raw is not None and str(env_raw).strip() != ""

    raw = _effective(env_key, file_map, configured)
    p = Path(raw).expanduser()

    if env_explicit and p.is_dir():
        _vlog(verbose, "%s -> %s (environment override)", env_key, str(p))
        return p

    if canon.is_dir():
        _vlog(verbose, "%s -> %s (canonical volume mounted)", env_key, str(canon))
        return canon

    if p.is_dir():
        _vlog(verbose, "%s -> %s (configured path mounted)", env_key, str(p))
        return p

    _vlog(
        verbose,
        "%s configured as %s but not mounted; using fallback under repo",
        env_key,
        str(p),
    )
    return _fallback_dir(fallback_label)


def get_sv_cache(*, verbose: bool = False) -> Path:
    return _resolve_role_path(
        "SV_CACHE",
        configured="/Volumes/SV_CACHE",
        fallback_label="sv_cache",
        verbose=verbose,
    )


def get_sv_transfer(*, verbose: bool = False) -> Path:
    return _resolve_role_path(
        "SV_TRANSFER",
        configured="/Volumes/SV_TRANSFER",
        fallback_label="sv_transfer",
        verbose=verbose,
    )


def get_sv_backup(*, verbose: bool = False) -> Path:
    return _resolve_role_path(
        "SV_BACKUP",
        configured="/Volumes/SV_BACKUP",
        fallback_label="sv_backup",
        verbose=verbose,
    )


def get_sv_archive(*, verbose: bool = False) -> Path:
    return _resolve_role_path(
        "SV_ARCHIVE",
        configured="/Volumes/SV_ARCHIVE",
        fallback_label="sv_archive",
        verbose=verbose,
    )


def get_sv_cache_renders(*, verbose: bool = False) -> Path:
    """SV_CACHE/renders — ffmpeg / automation renders feeding DaVinci inbox."""
    p = get_sv_cache(verbose=verbose) / "renders"
    _vlog(verbose, "SV_CACHE/renders -> %s", str(p))
    return p


def get_sv_cache_uploads(*, verbose: bool = False) -> Path:
    """SV_CACHE/uploads — alternate queue source for DaVinci prep."""
    p = get_sv_cache(verbose=verbose) / "uploads"
    _vlog(verbose, "SV_CACHE/uploads -> %s", str(p))
    return p


def get_davinci_inbox(*, verbose: bool = False) -> Path:
    p = get_sv_cache(verbose=verbose) / "davinci_inbox"
    _vlog(verbose, "davinci_inbox -> %s", str(p))
    return p


def get_davinci_projects(*, verbose: bool = False) -> Path:
    p = get_sv_cache(verbose=verbose) / "davinci_projects"
    _vlog(verbose, "davinci_projects -> %s", str(p))
    return p


def get_davinci_exports(*, verbose: bool = False) -> Path:
    p = get_sv_cache(verbose=verbose) / "davinci_exports"
    _vlog(verbose, "davinci_exports -> %s", str(p))
    return p


def get_davinci_done(*, verbose: bool = False) -> Path:
    p = get_sv_cache(verbose=verbose) / "davinci_done"
    _vlog(verbose, "davinci_done -> %s", str(p))
    return p


def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:
    """SV_TRANSFER/ready_to_upload — DaVinci-approved masters for publish gate / upload."""
    p = get_sv_transfer(verbose=verbose) / "ready_to_upload"
    _vlog(verbose, "ready_to_upload -> %s", str(p))
    return p


def get_davinci_logs_dir() -> Path:
    """Repo-local logs for DaVinci manifests (under ``STATEVERGE_ROOT`` / ``~/StateVerge``)."""
    return code_root() / "logs" / "davinci"


def get_stateverge_volume(*, verbose: bool = False) -> Path:
    """Primary StateVerge workflow SSD (NYC_AUTO, 07_AUTOMATION, etc.)."""
    file_map = load_storage_map_from_file()
    raw = _effective("STATEVERGE_VOL", file_map, "/Volumes/StateVerge")
    p = Path(raw).expanduser()
    _vlog(verbose, "STATEVERGE_VOL -> %s (exists=%s)", str(p), p.is_dir())
    return p


def get_nyc_video_root(*, verbose: bool = False) -> Path:
    """``<StateVerge>/NYC/video`` (classic long-form video library path)."""
    root = get_stateverge_volume(verbose=verbose) / "NYC" / "video"
    _vlog(verbose, "NYC video root -> %s", root)
    return root


def get_nyc_music_root(*, verbose: bool = False) -> Path:
    """``<StateVerge>/NYC/music``."""
    root = get_stateverge_volume(verbose=verbose) / "NYC" / "music"
    _vlog(verbose, "NYC music root -> %s", root)
    return root


def resolve_ts_input_root(*, verbose: bool = False) -> Path | None:
    """TS / dashcam card root from ``STATEVERGE_TS_INPUT`` (env or storage_map)."""
    file_map = load_storage_map_from_file()
    raw = _effective("STATEVERGE_TS_INPUT", file_map, "")
    if not raw:
        _vlog(verbose, "STATEVERGE_TS_INPUT unset; TS ingest disabled until configured")
        return None
    p = Path(raw).expanduser()
    _vlog(verbose, "STATEVERGE_TS_INPUT -> %s", p)
    return p


def summarize_targets(*, verbose: bool = False) -> dict[str, str]:
    """Print-friendly snapshot of resolved logical paths."""
    ts = resolve_ts_input_root(verbose=verbose)
    lines = {
        "STATEVERGE_VOL": str(get_stateverge_volume(verbose=verbose)),
        "SV_CACHE": str(get_sv_cache(verbose=verbose)),
        "SV_TRANSFER": str(get_sv_transfer(verbose=verbose)),
        "SV_BACKUP": str(get_sv_backup(verbose=verbose)),
        "SV_ARCHIVE": str(get_sv_archive(verbose=verbose)),
        "STATEVERGE_TS_INPUT": str(ts) if ts else "(unset)",
        "NYC_VIDEO": str(get_nyc_video_root(verbose=verbose)),
        "NYC_MUSIC": str(get_nyc_music_root(verbose=verbose)),
    }
    return lines


def write_probe_file(root: Path, *, dry_run: bool = False) -> bool:
    """Best-effort writable probe under ``root`` (fail-open)."""
    if dry_run or not root.is_dir():
        return False
    try:
        probe = root / ".stateverge_write_probe"
        probe.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=probe, prefix="w_", suffix=".tmp")
        os.close(fd)
        Path(tmp).unlink(missing_ok=True)
        return True
    except OSError as exc:
        logger.warning("write probe failed under %s: %s", root, exc)
        return False
