"""
Path safety helpers for the dashboard's write endpoints.

Every write performed by /api/create/* MUST go through `safe_topic_dir()` so
that no request can escape the StateVerge repo via a crafted slug
(`../`, absolute paths, symlinks, etc.).

Also provides `archive_existing()` so that no write silently overwrites an
existing artifact: the old file(s) are first moved to
    <archive_root>/<YYYYMMDD_HHMMSS>/<filename>
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

# Allow letters, digits, underscore, hyphen, dot. Must start with letter/digit.
# Length 1..64. No path separators, no leading dot ⇒ no `..`, `./`, `/etc`, etc.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class UnsafePathError(ValueError):
    """Raised when a slug or path would escape the topics root."""


def validate_slug(slug: str) -> str:
    s = (slug or "").strip()
    if not s:
        raise UnsafePathError("topic slug required")
    if not _SLUG_RE.match(s):
        raise UnsafePathError(
            f"invalid topic slug: {s!r} "
            f"(allowed: letters, digits, _ - . — 1..64 chars, must start "
            f"with letter or digit)"
        )
    return s


def safe_topic_dir(slug: str, repo_root: Path, *, create: bool = True) -> Path:
    """
    Resolve `topics/<slug>/` under `repo_root` and verify the resolved path is
    actually inside `repo_root/topics`. Raises UnsafePathError otherwise.
    """
    slug = validate_slug(slug)
    topics_root = (repo_root / "topics").resolve()
    target = (topics_root / slug).resolve()
    # Path.is_relative_to (3.9+) returns False for `target == topics_root`
    # which is fine for us — we never want the bare topics root.
    if target == topics_root or not target.is_relative_to(topics_root):
        raise UnsafePathError(
            f"resolved path escapes topics root: {target}"
        )
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def safe_subpath(topic_dir: Path, *parts: str) -> Path:
    """
    Build a path inside `topic_dir`, ensuring every component is a simple file
    or directory name (no `/`, `\\`, `..`, no leading dot beyond the first
    component). Returns the resolved absolute Path; does NOT create the file.
    """
    for part in parts:
        if not part or "/" in part or "\\" in part or part in (".", ".."):
            raise UnsafePathError(f"invalid path component: {part!r}")
    target = topic_dir.joinpath(*parts).resolve()
    topic_dir_resolved = topic_dir.resolve()
    if not target.is_relative_to(topic_dir_resolved):
        raise UnsafePathError(
            f"resolved path escapes topic dir: {target}"
        )
    return target


def archive_existing(paths: list[Path], archive_root: Path) -> Path | None:
    """
    For any path in `paths` that exists, move it to
    `archive_root/<YYYYMMDD_HHMMSS>/<filename>`.

    Returns the archive directory path if anything was moved, else None.
    """
    found = [p for p in paths if p.exists()]
    if not found:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_dir = archive_root / ts
    dst_dir.mkdir(parents=True, exist_ok=True)
    for p in found:
        target = dst_dir / p.name
        # if a file with that name was somehow already archived this second,
        # append a counter
        if target.exists():
            i = 1
            while True:
                candidate = dst_dir / f"{p.stem}.{i}{p.suffix}"
                if not candidate.exists():
                    target = candidate
                    break
                i += 1
        shutil.move(str(p), str(target))
    return dst_dir
