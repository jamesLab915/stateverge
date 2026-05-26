"""Resolve scan directories under the external StateVerge volume only."""

from __future__ import annotations

from pathlib import Path

from utils.storage_paths import get_stateverge_volume

STATEVERGE_VOLUME = get_stateverge_volume()


def volume_mounted() -> bool:
    return STATEVERGE_VOLUME.is_dir()


def normalized_scan_relative(raw: str | None, *, default_rel: str) -> str:
    """POSIX relative path without leading slashes; rejects ``..``."""
    base_default = default_rel.strip().replace("\\", "/").strip("/")
    s = (raw or "").strip().replace("\\", "/").strip("/")
    if not s:
        s = base_default
    parts = Path(s).parts
    if any(p == ".." for p in parts):
        raise ValueError("路径不能包含 ..")
    return Path(*parts).as_posix() if parts else base_default


def resolve_scan_directory(root_rel: str | None, *, default_rel: str) -> Path:
    """Resolved directory path; must stay under ``STATEVERGE_VOLUME``."""
    if not volume_mounted():
        raise RuntimeError("StateVerge 未挂载")
    rel = normalized_scan_relative(root_rel, default_rel=default_rel)
    vol = STATEVERGE_VOLUME.resolve()
    cand = (vol / rel).resolve()
    try:
        cand.relative_to(vol)
    except ValueError as e:
        raise ValueError("路径须在 StateVerge 卷内") from e
    return cand


def resolve_file_under_scan(scan_root: Path, rel: str) -> Path:
    """Resolve ``rel`` (posix under scan_root); raise ValueError if escapes."""
    if not rel or rel.startswith("/"):
        raise ValueError("invalid rel")
    raw = Path(rel)
    if raw.is_absolute():
        raise ValueError("absolute path forbidden")
    cand = (scan_root / raw).resolve()
    scan_r = scan_root.resolve()
    try:
        cand.relative_to(scan_r)
    except ValueError as e:
        raise ValueError("路径超出当前扫描目录") from e
    return cand


def is_under_or_equal(scan_root: Path, ancestor: Path) -> bool:
    """True if scan_root is ancestor or same path."""
    try:
        scan_root.resolve().relative_to(ancestor.resolve())
        return True
    except ValueError:
        return scan_root.resolve() == ancestor.resolve()
