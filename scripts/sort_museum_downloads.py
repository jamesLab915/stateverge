#!/usr/bin/env python3
from pathlib import Path
import shutil
import re
from datetime import datetime

HOME = Path.home()

try:
    from src.utils.home_downloads import resolve_home_downloads
except ImportError:
    def resolve_home_downloads() -> Path:
        for name in ("Downloads", "downloads", "下载"):
            p = HOME / name
            if p.is_dir():
                return p.resolve()
        return (HOME / "Downloads").resolve()

DOWNLOADS = resolve_home_downloads()

PROJECT_ROOT = HOME / "StateVerge"
SERIES_DIR = PROJECT_ROOT / "topics" / "museum-series"
EPISODE_DIR = SERIES_DIR / "louvre_episode_01"

ASSETS_DIR = EPISODE_DIR / "assets"

DESTS = {
    "exterior": ASSETS_DIR / "footage" / "exterior",
    "interior": ASSETS_DIR / "footage" / "interior",
    "artwork": ASSETS_DIR / "footage" / "artwork",
    "paris": ASSETS_DIR / "footage" / "paris",
    "music": ASSETS_DIR / "music",
    "sfx": ASSETS_DIR / "sfx",
    "templates": ASSETS_DIR / "templates",
    "images": ASSETS_DIR / "images",
    "unsorted": ASSETS_DIR / "_raw_unsorted",
}

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}

KEYWORDS = {
    "exterior": [
        "louvre", "pyramid", "museum exterior", "palace", "architecture",
        "building", "facade", "outside"
    ],
    "interior": [
        "museum interior", "gallery", "hall", "hallway", "corridor",
        "exhibition", "interior", "visitor", "tourist", "art gallery"
    ],
    "artwork": [
        "painting", "renaissance", "sculpture", "statue", "artifact",
        "artwork", "canvas", "marble", "ancient", "egyptian"
    ],
    "paris": [
        "paris", "france", "seine", "eiffel", "city", "street",
        "aerial", "sunset", "night"
    ],
    "music": [
        "music", "piano", "cinematic", "documentary", "ambient",
        "score", "soundtrack"
    ],
    "sfx": [
        "sfx", "whoosh", "impact", "riser", "transition sound",
        "hit", "boom"
    ],
    "templates": [
        "template", "opener", "title", "lower third", "slideshow",
        "after effects", "premiere", "motion graphics"
    ],
}

def safe_name(path: Path) -> str:
    name = path.stem
    suffix = path.suffix.lower()
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
    return f"{cleaned}{suffix}"

def ensure_dirs():
    for d in DESTS.values():
        d.mkdir(parents=True, exist_ok=True)

def classify(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()

    if suffix in AUDIO_EXTS:
        if any(k in name for k in KEYWORDS["sfx"]):
            return "sfx"
        return "music"

    if suffix in IMAGE_EXTS:
        return "images"

    if suffix in ARCHIVE_EXTS:
        if any(k in name for k in KEYWORDS["templates"]):
            return "templates"
        return "unsorted"

    if suffix in VIDEO_EXTS:
        scores = {}
        for category, words in KEYWORDS.items():
            if category in {"music", "sfx", "templates"}:
                continue
            scores[category] = sum(1 for w in words if w in name)

        best = max(scores, key=scores.get)
        if scores[best] > 0:
            return best
        return "unsorted"

    return "unsorted"

def unique_dest(dest_dir: Path, filename: str) -> Path:
    target = dest_dir / filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    i = 2
    while True:
        candidate = dest_dir / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1

def main():
    ensure_dirs()

    moved = []
    skipped = []

    for path in DOWNLOADS.iterdir():
        if path.is_dir():
            continue

        suffix = path.suffix.lower()
        if suffix not in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS | ARCHIVE_EXTS:
            skipped.append(path.name)
            continue

        category = classify(path)
        dest_dir = DESTS[category]
        dest_path = unique_dest(dest_dir, safe_name(path))

        shutil.move(str(path), str(dest_path))
        moved.append((path.name, category, str(dest_path)))

    report_dir = EPISODE_DIR / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    report_path = report_dir / "sort_downloads_report.txt"
    with report_path.open("w", encoding="utf-8") as f:
        f.write(f"Sort report generated at {datetime.now().isoformat()}\n")
        f.write(f"Episode dir: {EPISODE_DIR}\n\n")

        f.write("Moved files:\n")
        for old, category, new in moved:
            f.write(f"- {old} -> {category} -> {new}\n")

        f.write("\nSkipped files:\n")
        for s in skipped:
            f.write(f"- {s}\n")

    print(f"[sort] moved={len(moved)} skipped={len(skipped)}")
    print(f"[sort] episode_dir={EPISODE_DIR}")
    print(f"[sort] report={report_path}")

if __name__ == "__main__":
    main()
