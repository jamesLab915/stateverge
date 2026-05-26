"""Resolve the user's Downloads folder (Apple default, lowercase clone, zh-CN name)."""

from __future__ import annotations

from pathlib import Path

# Priority: typical macOS folder first, then lowercase custom dir, then Chinese UI name.
HOME_DOWNLOAD_DIR_NAMES: tuple[str, ...] = ("Downloads", "downloads", "下载")


def resolve_home_downloads() -> Path:
    home = Path.home()
    for name in HOME_DOWNLOAD_DIR_NAMES:
        p = home / name
        if p.is_dir():
            return p.resolve()
    return (home / "Downloads").resolve()
