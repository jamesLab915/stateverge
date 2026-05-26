#!/usr/bin/env python3
import os, json, shutil, hashlib
from pathlib import Path
from datetime import datetime

ROOTS = [
    Path("/Volumes/SV_TRANSFER/04_AUDIO"),
    Path("/Volumes/SV_CACHE/inbox/envato"),
    Path.home() / "StateVerge",
    Path.home() / "StateVerge_Control_Center",
]

DELETE_ROOTS = [
    Path("/Volumes/SV_TRANSFER/04_AUDIO/music"),
    Path("/Volumes/SV_CACHE/inbox/envato"),
]

AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a", ".flac", ".aiff", ".ogg"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z"}
LICENSE_HINTS = {"license", "licence", "envato", "audiojungle", "preview", "demo"}

REPORT_DIR = Path("/Volumes/SV_CACHE/logs")
if not REPORT_DIR.exists():
    REPORT_DIR = Path.home() / "StateVerge" / "logs"
REPORT_DIR.mkdir(parents=True, exist_ok=True)

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
report_json = REPORT_DIR / f"music_reset_audit_{ts}.json"
delete_txt = REPORT_DIR / f"music_deleted_files_{ts}.txt"

def should_skip(p: Path):
    s = str(p)
    skip_parts = [
        "node_modules", ".git", "__pycache__",
        "ready_to_upload", "renders", "publish_pack/shorts_uploads",
        "review_queue", "audio_clean", "normalized"
    ]
    return any(x in s for x in skip_parts)

def is_music_related(p: Path):
    name = p.name.lower()
    ext = p.suffix.lower()
    return (
        ext in AUDIO_EXTS
        or ext in ARCHIVE_EXTS
        or any(h in name for h in LICENSE_HINTS)
    )

found = []
for root in ROOTS:
    if not root.exists():
        continue
    for dirpath, dirnames, filenames in os.walk(root):
        dp = Path(dirpath)
        dirnames[:] = [d for d in dirnames if d not in {"node_modules", ".git", "__pycache__"}]
        if should_skip(dp):
            continue
        for fn in filenames:
            p = dp / fn
            if p.name.startswith("._"):
                continue
            if not is_music_related(p):
                continue
            try:
                st = p.stat()
                found.append({
                    "path": str(p),
                    "size_bytes": st.st_size,
                    "ext": p.suffix.lower(),
                    "delete_candidate": any(str(p).startswith(str(dr)) for dr in DELETE_ROOTS),
                })
            except Exception as e:
                found.append({"path": str(p), "error": repr(e), "delete_candidate": False})

deleted = []
errors = []

for dr in DELETE_ROOTS:
    if not dr.exists():
        continue
    for item in dr.iterdir():
        try:
            if item.name.startswith("."):
                continue
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            deleted.append(str(item))
        except Exception as e:
            errors.append({"path": str(item), "error": repr(e)})

# Rebuild clean music structure
music_root = Path("/Volumes/SV_TRANSFER/04_AUDIO/music")
for d in [
    "ambient", "calm_piano", "cinematic", "dark_documentary",
    "night_drive", "cyberpunk", "luxury", "tension", "uplifting",
    "licenses", "metadata", "archive"
]:
    (music_root / d).mkdir(parents=True, exist_ok=True)

# Recreate Envato inbox
Path("/Volumes/SV_CACHE/inbox/envato").mkdir(parents=True, exist_ok=True)

report = {
    "status": "music_system_reset_done",
    "created_at": datetime.now().isoformat(),
    "scanned_roots": [str(x) for x in ROOTS],
    "delete_roots": [str(x) for x in DELETE_ROOTS],
    "found_count": len(found),
    "deleted_count": len(deleted),
    "error_count": len(errors),
    "found": found,
    "deleted": deleted,
    "errors": errors,
    "new_music_root": str(music_root),
    "envato_inbox": "/Volumes/SV_CACHE/inbox/envato",
    "rule_from_now_on": "Only Envato Project Use downloads with license should enter music library."
}

report_json.write_text(json.dumps(report, indent=2, ensure_ascii=False))
delete_txt.write_text("\n".join(deleted))

print("MUSIC_SYSTEM_RESET_DONE")
print("REPORT_JSON=", report_json)
print("DELETED_LIST=", delete_txt)
print("FOUND_COUNT=", len(found))
print("DELETED_COUNT=", len(deleted))
print("ERROR_COUNT=", len(errors))
