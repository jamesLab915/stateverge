"""
Envato download sorter: scan the resolved home Downloads folder (``~/Downloads``,
``~/downloads``, or ``~/下载`` — whichever exists first), move into ``assets/envato/``.

Run from repo root::

    export PYTHONPATH="$PWD"
    python -m src.utils.sort_envato --once
    python -m src.utils.sort_envato --watch
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
import zipfile
from pathlib import Path

from src.utils.home_downloads import resolve_home_downloads

# Repository root: .../StateVerge
REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
DOWNLOADS: Path = resolve_home_downloads()
ENVATO_BASE: Path = REPO_ROOT / "assets" / "envato"

INCOMPLETE_SUFFIXES: tuple[str, ...] = (
    ".crdownload",
    ".tmp",
    ".part",
    ".download",
)

# Target buckets under assets/envato/
BUCKET_NAMES: tuple[str, ...] = (
    "music",
    "sfx",
    "transitions",
    "lower_thirds",
    "title_openers",
    "overlays",
    "_licenses",
    "_raw_backup",
)

# Optional project route: when an Envato file's name/parent/metadata matches
# trigger_keywords, the file is parked under base_dir/_raw_unsorted instead of
# going through the regular Envato classifier. Sub-categorization is intentionally
# deferred — this is just a holding bay for now.
PROJECT_ROUTE = {
    "name": "museum_series_louvre",
    "base_dir": "~/StateVerge/assets/envato/museum_series/louvre_episode_01",
    "trigger_keywords": [
        "louvre",
        "museum",
        "art gallery",
        "gallery",
        "renaissance",
        "sculpture",
        "statue",
        "paris",
        "france",
        "pyramid",
    ],
}

AUDIO_EXTS: set[str] = {".mp3", ".wav", ".m4a", ".aiff", ".aif"}
VIDEO_EXTS: set[str] = {".mp4", ".mov"}
TEMPLATE_EXTS: set[str] = {".aep", ".prproj", ".mogrt"}
LICENSE_EXTS: set[str] = {".txt", ".pdf"}

SFX_KEYWORDS: tuple[str, ...] = (
    "hit",
    "swoosh",
    "whoosh",
    "transition",
    "logo",
    "impact",
    "boom",
    "riser",
)
VIDEO_TRANSITION: tuple[str, ...] = ("transition", "stinger")
VIDEO_OVERLAYS: tuple[str, ...] = (
    "overlay",
    "grain",
    "light",
    "dust",
    "hud",
    "particle",
    "leak",
    "flare",
)
LOWER_THIRDS_KEYWORDS: tuple[str, ...] = ("lower", "third", "subtitle")


def log(msg: str) -> None:
    print(f"[envato_sort] {msg}")


def _is_incomplete(path: Path) -> bool:
    name = path.name.lower()
    return any(name.endswith(s) for s in INCOMPLETE_SUFFIXES)


def _wait_stable(path: Path, *, checks: int = 3, interval: float = 0.35) -> bool:
    if not path.is_file():
        return False
    last: int = -1
    stable = 0
    for _ in range(120):
        try:
            sz = path.stat().st_size
        except OSError:
            return False
        if sz == last and sz > 0:
            stable += 1
            if stable >= checks:
                return True
        else:
            stable = 0
        last = sz
        time.sleep(interval)
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _unique_path(dest: Path) -> Path:
    if not dest.exists():
        return dest
    base = dest.stem
    suf = dest.suffix
    n = 2
    while True:
        c = dest.parent / f"{base}__v{n}{suf}"
        if not c.exists():
            return c
        n += 1
        if n > 9999:
            raise OSError("could not find unique name for: %s" % dest)


def _zip_path_safe(root: Path, member: str) -> bool:
    if member.startswith("/") or ".." in Path(member).parts:
        return False
    dest = (root / member).resolve()
    try:
        dest.relative_to(root.resolve())
    except ValueError:
        return False
    return True


def ensure_envato_tree() -> None:
    for name in BUCKET_NAMES:
        (ENVATO_BASE / name).mkdir(parents=True, exist_ok=True)


def _title_openers_match(name: str) -> bool:
    n = name.lower()
    if "opener" in n or "intro" in n:
        return True
    if "subtitle" in n:
        return False
    return "title" in n


def _lower_thirds_match(name: str) -> bool:
    n = name.lower()
    return any(k in n for k in LOWER_THIRDS_KEYWORDS)


def match_project_route(path: Path, metadata_text: str = "") -> bool:
    text = f"{path.name} {path.parent.name} {metadata_text}".lower()
    return any(k in text for k in PROJECT_ROUTE["trigger_keywords"])


def classify_file(path: Path) -> str:
    """
    Return one of BUCKET_NAMES for a regular file.
    """
    if not path.is_file():
        return "_raw_backup"

    name = path.name
    n = name.lower()
    ext = path.suffix.lower()

    if ext in LICENSE_EXTS or "license" in n or "licence" in n:
        return "_licenses"

    if ext in AUDIO_EXTS:
        for kw in SFX_KEYWORDS:
            if kw in n:
                return "sfx"
        return "music"

    if ext in VIDEO_EXTS:
        for kw in VIDEO_TRANSITION:
            if kw in n:
                return "transitions"
        for kw in VIDEO_OVERLAYS:
            if kw in n:
                return "overlays"
        if _title_openers_match(n):
            return "title_openers"
        if _lower_thirds_match(n):
            return "lower_thirds"
        return "_raw_backup"

    if ext in TEMPLATE_EXTS:
        if _lower_thirds_match(n):
            return "lower_thirds"
        if _title_openers_match(n):
            return "title_openers"
        return "_raw_backup"

    return "_raw_backup"


def _move_preserve_name(src: Path, dest_dir: Path) -> bool:
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = src.name
    target = _unique_path(dest_dir / name)
    try:
        shutil.move(str(src), str(target))
    except OSError as e:
        log(f"action=error op=move file={src!s} err={e!s}")
        return False
    log(f"action=move file={src!s} target={target!s}")
    return True


def _extract_zip_safe(zip_path: Path, out_dir: Path) -> bool:
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if member.endswith("/") or not member:
                    continue
                if not _zip_path_safe(out_dir, member):
                    log(
                        f"action=skip file={zip_path!s} reason=zip_unsafe_path member={member!r}"
                    )
                    continue
                dest = out_dir / member
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, "r") as fsrc, open(dest, "wb") as fdst:
                    shutil.copyfileobj(fsrc, fdst)
        return True
    except zipfile.BadZipFile as e:
        log(
            f"action=error op=unzip file={zip_path!s} err=bad_zip:{e!s} "
        )
        return False
    except OSError as e:
        log(f"action=error op=unzip file={zip_path!s} err={e!s}")
        return False


def process_zip(zip_path: Path) -> int:
    """
    Unzip, sort every file, move original .zip to _raw_backup.
    Returns 1 on success, 0 on skip (corrupt / unstable / move error).
    """
    if not zip_path.is_file() or not zip_path.name.lower().endswith(".zip"):
        return 0
    if _is_incomplete(zip_path):
        log(f"action=skip file={zip_path!s} reason=downloading")
        return 0
    if not _wait_stable(zip_path):
        log(f"action=skip file={zip_path!s} reason=unstable")
        return 0

    if match_project_route(zip_path):
        project_unsorted_dir = (
            Path(PROJECT_ROUTE["base_dir"]).expanduser() / "_raw_unsorted"
        )
        print(f"[envato_sorter] route={PROJECT_ROUTE['name']} file={zip_path.name}")
        return 1 if _move_preserve_name(zip_path, project_unsorted_dir) else 0

    log(f"action=unzip file={zip_path!s}")
    work = (
        DOWNLOADS
        / f".envato_unzip_{zip_path.stem}_{int(time.time() * 1000)}"
    )
    work.mkdir(parents=True, exist_ok=True)
    try:
        if not _extract_zip_safe(zip_path, work):
            return 0

        for f in work.rglob("*"):
            if not f.is_file() or f.name.startswith("."):
                continue
            b = classify_file(f)
            dest_dir = ENVATO_BASE / b
            _move_preserve_name(f, dest_dir)

        bdir = ENVATO_BASE / "_raw_backup"
        bdir.mkdir(parents=True, exist_ok=True)
        dest_zip = _unique_path(bdir / zip_path.name)
        try:
            shutil.move(str(zip_path), str(dest_zip))
        except OSError as e:
            log(f"action=error op=move_zip file={zip_path!s} err={e!s}")
            return 0
        log(f"action=move file={zip_path!s} target={dest_zip!s}")
    finally:
        if work.is_dir():
            try:
                shutil.rmtree(str(work), ignore_errors=True)
            except OSError:
                pass
    return 1


def process_loose_file(path: Path) -> int:
    """Classify and move a single file from Downloads. Returns 1 if moved, 0 if skipped."""
    if _is_incomplete(path):
        log(f"action=skip file={path!s} reason=downloading")
        return 0
    if not path.is_file():
        return 0
    if not _wait_stable(path):
        log(f"action=skip file={path!s} reason=unstable")
        return 0

    if match_project_route(path):
        project_unsorted_dir = (
            Path(PROJECT_ROUTE["base_dir"]).expanduser() / "_raw_unsorted"
        )
        print(f"[envato_sorter] route={PROJECT_ROUTE['name']} file={path.name}")
        return 1 if _move_preserve_name(path, project_unsorted_dir) else 0

    if path.suffix.lower() == ".zip":
        return process_zip(path)

    dest_bucket = classify_file(path)
    dest_dir = ENVATO_BASE / dest_bucket
    if _move_preserve_name(path, dest_dir):
        return 1
    return 0


def run_once() -> int:
    if not DOWNLOADS.is_dir():
        log(f"action=error op=scan reason=downloads_missing path={DOWNLOADS!s}")
        return 1
    ensure_envato_tree()
    total = 0
    for p in sorted(DOWNLOADS.iterdir()):
        if p.is_dir() or not p.is_file():
            continue
        if p.name.startswith("."):
            continue
        if p.suffix.lower() == ".zip":
            total += process_zip(p)
        else:
            total += process_loose_file(p)
    log(f"action=summary moved_batches={total}")
    return 0


def _try_watch() -> int:
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer

    if not DOWNLOADS.is_dir():
        log(f"action=error op=watch reason=downloads_missing path={DOWNLOADS!s}")
        return 1

    ensure_envato_tree()

    class _H(FileSystemEventHandler):
        def __init__(self) -> None:
            self._t = 0.0

        def _go(self, path: Path) -> None:
            now = time.time()
            if now - self._t < 0.15:
                time.sleep(0.15)
            self._t = time.time()
            p = path.resolve()
            if p.parent != DOWNLOADS:
                return
            if not p.is_file():
                return
            if p.name.startswith(".envato_unzip") or p.name.startswith("."):
                return
            try:
                if p.suffix.lower() == ".zip":
                    process_zip(p)
                else:
                    process_loose_file(p)
            except Exception as e:  # noqa: BLE001 — keep watch process alive
                log(f"action=error op=handler file={p!s} err={e!s}")

        def on_created(self, event) -> None:  # type: ignore[no-untyped-def]
            if getattr(event, "is_directory", False):
                return
            self._go(Path(event.src_path))

        def on_moved(self, event) -> None:  # type: ignore[no-untyped-def]
            if getattr(event, "is_directory", False):
                return
            dest = getattr(event, "dest_path", None)
            if dest:
                self._go(Path(dest))

    log(f"action=watch path={DOWNLOADS!s} -> {ENVATO_BASE!s}")
    observer = Observer()
    observer.schedule(_H(), str(DOWNLOADS), recursive=False)
    observer.start()
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        observer.stop()
        observer.join(timeout=4)
    return 0


def run_watch() -> int:
    try:
        return _try_watch()
    except ImportError:
        log(
            "action=error op=watch reason=watchdog_missing install=pip install watchdog"
        )
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Envato: sort files from ~/Downloads into assets/envato"
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--once",
        action="store_true",
        help="Scan Downloads once and exit",
    )
    g.add_argument(
        "--watch",
        action="store_true",
        help="Watch Downloads (needs: pip install watchdog)",
    )
    args = ap.parse_args()
    if args.once:
        return run_once()
    return run_watch()


if __name__ == "__main__":
    sys.exit(main())
