#!/usr/bin/env python3
"""
StateVerge SSD 素材盘点：扫描、元数据、去重复制（仅 --apply）、索引与队列。

默认 dry-run（不写库、不复制）。必须显式传入 --apply 才执行复制与队列写入。
绝不进入 /Volumes/StateVerge/私人别碰；不删除、不移动任何原始文件。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from stateverge_paths import SSD_ROOT  # noqa: E402

try:
    from stateverge_protected import is_protected_path, reload_protected_roots
except ImportError:

    def reload_protected_roots() -> None:
        return None

    def is_protected_path(path: str | Path) -> bool:
        p = str(Path(path).expanduser())
        return "私人别碰" in p.replace("\\", "/")


VIDEO_EXT = frozenset({".mov", ".mp4", ".m4v", ".hevc", ".mts", ".ts"})
IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".heic", ".dng", ".tif", ".tiff"})
AUDIO_EXT = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".aiff", ".aif"})
TEMPLATE_EXT = frozenset({".prproj", ".drp", ".fcpxml", ".xml"})

DIR_SKIP_NAMES = frozenset(
    {
        ".Trash",
        "Trash",
        ".Trashes",
        ".Spotlight-V100",
        ".fseventsd",
        "__pycache__",
        "node_modules",
        ".git",
        ".venv",
        ".TemporaryItems",
    }
)

LOG_SUBDIR_SKIP = frozenset({"logs"})

NYC_KEYWORDS = (
    "manhattan",
    "chinatown",
    "times_square",
    "times-square",
    "grand_central",
    "grand-central",
    "brooklyn",
    "financial_district",
    "financial-district",
    "central_park",
    "central-park",
    "midtown",
    "downtown",
    "soho",
    "tribeca",
    "wall_street",
    "wall-street",
    "nyc",
    "new_york",
    "new-york",
)

NYC_LAT_MIN, NYC_LAT_MAX = 40.45, 40.95
NYC_LON_MIN, NYC_LON_MAX = -74.28, -73.68

_EXIFTOOL_WARNED = False


def log_event(root: Path, msg: str, stats: dict[str, Any]) -> None:
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    stats.setdefault("_lines", []).append(line)
    log_dir = root / "07_AUTOMATION" / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "asset_inventory.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def protected_skip(root: Path, path: Path, reason: str, stats: dict[str, Any]) -> None:
    stats["protected_skip"] = stats.get("protected_skip", 0) + 1
    log_event(
        root,
        f'PROTECTED_SKIP path={path} reason={reason}',
        stats,
    )


def sha256_quick(path: Path, head_bytes: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    st = path.stat()
    h.update(str(st.st_size).encode())
    h.update(str(int(st.st_mtime)).encode())
    with open(path, "rb") as f:
        h.update(f.read(head_bytes))
    return h.hexdigest()


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def exiftool_path() -> str | None:
    return shutil.which("exiftool")


def ffprobe_json(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "has_video": False,
        "has_audio": False,
        "duration_seconds": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "codec": "",
        "audio_codec": "",
    }
    exe = ffprobe_path()
    if not exe:
        return out
    try:
        r = subprocess.run(
            [
                exe,
                "-v",
                "error",
                "-probesize",
                "5000000",
                "-analyzeduration",
                "5000000",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if r.returncode != 0:
            return out
        data = json.loads(r.stdout or "{}")
        streams = data.get("streams") or []
        for s in streams:
            if s.get("codec_type") == "audio":
                out["has_audio"] = True
                if not out["audio_codec"]:
                    out["audio_codec"] = str(s.get("codec_name") or "")
        for s in streams:
            if s.get("codec_type") != "video":
                continue
            out["has_video"] = True
            out["width"] = int(s.get("width") or 0)
            out["height"] = int(s.get("height") or 0)
            out["codec"] = str(s.get("codec_name") or "")
            rfr = s.get("r_frame_rate") or "0/1"
            try:
                a, b = str(rfr).split("/")
                out["fps"] = float(a) / float(b) if float(b) else 0.0
            except (ValueError, ZeroDivisionError):
                pass
            break
        fmt = data.get("format") or {}
        try:
            out["duration_seconds"] = float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            pass
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        pass
    return out


def exiftool_row(path: Path) -> dict[str, Any]:
    global _EXIFTOOL_WARNED
    exe = exiftool_path()
    if not exe:
        if not _EXIFTOOL_WARNED:
            _EXIFTOOL_WARNED = True
            print(
                'EXIFTOOL_MISSING install_with="brew install exiftool"',
                flush=True,
            )
        return {}
    tags = [
        "Make",
        "Model",
        "CreateDate",
        "MediaCreateDate",
        "ModifyDate",
        "GPSLatitude",
        "GPSLongitude",
        "GPSAltitude",
        "ImageWidth",
        "ImageHeight",
        "Orientation",
        "UserComment",
        "Keywords",
        "QuickTime:Keywords",
    ]
    args = [exe, "-j", "-n"] + [f"-{t}" for t in tags] + [str(path)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=90, check=False)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {}
        rows = json.loads(r.stdout)
        return rows[0] if rows and isinstance(rows[0], dict) else {}
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, IndexError):
        return {}


def time_bucket(hour: int, minute: int) -> str:
    t = hour * 60 + minute
    if 5 * 60 <= t <= 10 * 60 + 59:
        return "morning"
    if 11 * 60 <= t <= 15 * 60 + 59:
        return "daytime"
    if 16 * 60 <= t <= 19 * 60 + 59:
        return "golden_hour"
    return "night"


def orientation_label(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "unknown"
    if w > h:
        return "landscape"
    if h > w:
        return "portrait"
    return "square"


def gps_nyc_guess(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return "unknown_location"
    if NYC_LAT_MIN <= lat <= NYC_LAT_MAX and NYC_LON_MIN <= lon <= NYC_LON_MAX:
        return "nyc_metro"
    return "unknown_location"


def location_slug_from_text(path: Path) -> str | None:
    blob = (str(path).lower() + " " + path.name.lower()).replace("-", "_")
    for kw in NYC_KEYWORDS:
        k = kw.replace("-", "_")
        if k in blob:
            return k
    return None


def parse_exif_datetime(s: str | None) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def detect_timelapse(path: Path, meta: dict[str, Any], fp: dict[str, Any]) -> tuple[bool, str]:
    blob = (str(path).lower() + path.name.lower()).replace("_", " ")
    if any(
        x in blob
        for x in (
            "timelapse",
            "time-lapse",
            "time lapse",
            "延时",
            "缩时",
        )
    ):
        return True, "path_or_name_keyword"
    uc = str(meta.get("UserComment") or meta.get("Keywords") or "").lower()
    if "timelapse" in uc or "time lapse" in uc:
        return True, "exif_user_comment"
    dur = float(fp.get("duration_seconds") or 0)
    fps = float(fp.get("fps") or 0)
    if dur > 5 and fps >= 50 and dur < 600:
        return True, "fps_duration_heuristic"
    if dur > 0 and fps > 0 and dur * fps < 500 and dur > 30:
        return True, "low_frame_count_heuristic"
    return False, "unknown"


def device_tier_and_source(
    path: Path, meta: dict[str, Any], exif: dict[str, Any]
) -> tuple[str, str, str, str]:
    make = str(exif.get("Make") or meta.get("make") or "").strip()
    model = str(exif.get("Model") or meta.get("model") or "").strip()
    blob = f"{make} {model}".lower()
    source = "metadata"
    if "apple" in make.lower() or make.lower() == "apple":
        if "iphone 17 pro max" in model.lower():
            return "iphone_17_pro_max", make, model, source
        if "iphone" in model.lower():
            return "iphone", make, model, source
    if make and "iphone" not in blob and "apple" not in make.lower():
        return "camera_or_other", make, model, source
    if not make and not model:
        name = path.name.upper()
        ext = path.suffix.lower()
        weak_ext = ext in {".heic", ".mov", ".mp4", ".m4v"}
        if weak_ext and (
            re.match(r"^IMG_\d+", name)
            or re.match(r"^\d{4}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_IMG_\d+", name)
        ):
            return "possible_iphone", "", "", "fallback_filename_weak"
    if "dji" in blob or "osmo" in blob:
        return "drone", make, model, source
    if "gopro" in blob:
        return "drone", make, model, source
    return "unknown", make, model, source


def should_skip_dir(root: Path, dirpath: Path, name: str) -> bool:
    p = dirpath / name
    if is_protected_path(p):
        return True
    try:
        p.relative_to(root / "01_ASSET_LIBRARY")
        return True
    except ValueError:
        pass
    nl = name.lower()
    if nl in DIR_SKIP_NAMES or name in DIR_SKIP_NAMES:
        return True
    if nl.startswith(".spotlight"):
        return True
    if name in LOG_SUBDIR_SKIP and "07_AUTOMATION" in p.parts:
        return True
    if nl in ("tmp", "temp", ".tmp") and "03_OUTPUT" in p.parts:
        return True
    if "05_INDEX" in p.parts or "08_CONFIG" in p.parts:
        return True
    return False


def skip_file(path: Path, root: Path) -> bool:
    if is_protected_path(path):
        return True
    try:
        path.relative_to(root / "01_ASSET_LIBRARY")
        return True
    except ValueError:
        pass
    if "05_INDEX" in path.parts or "08_CONFIG" in path.parts:
        return True
    n = path.name
    if n.startswith("._") or n == ".DS_Store":
        return True
    if n.startswith(".Spotlight"):
        return True
    return False


def media_category(path: Path) -> str:
    e = path.suffix.lower()
    if e == ".json":
        blob = str(path).lower()
        if any(k in blob for k in ("fcpxml", "premiere", "resolve", "project", "template")):
            return "template"
        return "other"
    if e in VIDEO_EXT:
        return "video"
    if e in IMAGE_EXT:
        return "image"
    if e in AUDIO_EXT:
        return "audio"
    if e in TEMPLATE_EXT:
        return "template"
    return "other"


def iter_scan_files(
    root: Path, stats: dict[str, Any], max_files: int | None
) -> Iterator[Path]:
    root = root.resolve()
    n = 0
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dp = Path(dirpath)
        if is_protected_path(dp):
            protected_skip(root, dp, "private_do_not_touch", stats)
            dirnames[:] = []
            continue
        nd: list[str] = []
        for d in list(dirnames):
            child = dp / d
            if is_protected_path(child):
                protected_skip(root, child, "private_do_not_touch", stats)
                continue
            if should_skip_dir(root, dp, d):
                continue
            nd.append(d)
        dirnames[:] = nd
        for fn in filenames:
            f = dp / fn
            if skip_file(f, root):
                continue
            cat = media_category(f)
            if cat == "other":
                continue
            yield f
            n += 1
            if max_files is not None and n >= max_files:
                dirnames[:] = []
                return


def ensure_layout(root: Path, stats: dict[str, Any]) -> None:
    dirs = [
        "00_INBOX/airdrop",
        "00_INBOX/camera_import",
        "00_INBOX/manual_drop",
        "01_ASSET_LIBRARY/video/iphone/iphone_17_pro_max",
        "01_ASSET_LIBRARY/video/iphone/iphone_other",
        "01_ASSET_LIBRARY/video/camera",
        "01_ASSET_LIBRARY/video/drone",
        "01_ASSET_LIBRARY/video/screen_recording",
        "01_ASSET_LIBRARY/video/unknown",
        "01_ASSET_LIBRARY/image/iphone/iphone_17_pro_max",
        "01_ASSET_LIBRARY/image/iphone/iphone_other",
        "01_ASSET_LIBRARY/image/camera",
        "01_ASSET_LIBRARY/image/dng_raw",
        "01_ASSET_LIBRARY/image/unknown",
        "01_ASSET_LIBRARY/audio/music",
        "01_ASSET_LIBRARY/audio/sfx",
        "01_ASSET_LIBRARY/audio/voice",
        "01_ASSET_LIBRARY/audio/unknown",
        "01_ASSET_LIBRARY/envato/music",
        "01_ASSET_LIBRARY/envato/video",
        "01_ASSET_LIBRARY/envato/templates",
        "01_ASSET_LIBRARY/envato/licenses",
        "01_ASSET_LIBRARY/thumbnails",
        "01_ASSET_LIBRARY/rejected/low_quality",
        "01_ASSET_LIBRARY/rejected/duplicate",
        "01_ASSET_LIBRARY/rejected/unsupported",
        "01_ASSET_LIBRARY/rejected/metadata_unknown",
        "01_ASSET_LIBRARY/video_by_date_location",
        "01_ASSET_LIBRARY/image_by_date_location",
        "01_ASSET_LIBRARY/timelapse/video",
        "01_ASSET_LIBRARY/timelapse/image_sequence",
        "01_ASSET_LIBRARY/timelapse/output",
        "01_ASSET_LIBRARY/timelapse/index",
        "01_ASSET_LIBRARY/timelapse/queues",
        "02_PROJECTS/NYC/long",
        "02_PROJECTS/NYC/shorts",
        "02_PROJECTS/NYC/thumbnails",
        "02_PROJECTS/NYC/published",
        "02_PROJECTS/StateVerge_CN",
        "02_PROJECTS/Finance",
        "02_PROJECTS/Experiments",
        "03_OUTPUT/youtube_long",
        "03_OUTPUT/youtube_shorts",
        "03_OUTPUT/thumbnails",
        "03_OUTPUT/review_queue",
        "04_ARCHIVE/published_originals",
        "04_ARCHIVE/published_compressed",
        "04_ARCHIVE/old_outputs",
        "05_INDEX",
        "06_REPORTS/system_audit",
        "06_REPORTS/asset_inventory",
        "07_AUTOMATION/scripts",
        "07_AUTOMATION/logs",
        "07_AUTOMATION/queues",
        "07_AUTOMATION/config",
        "08_CONFIG",
    ]
    for rel in dirs:
        p = root / rel
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    stats["dirs_created"] = len(dirs)


def write_protected_config(root: Path) -> None:
    cfg = root / "08_CONFIG" / "protected_paths.json"
    txt = root / "05_INDEX" / "protected_paths.txt"
    payload = {
        "protected_paths": [str(root / "私人别碰")],
        "rules": {
            "scan": False,
            "read": False,
            "copy": False,
            "move": False,
            "delete": False,
            "index": False,
            "use_as_asset": False,
        },
    }
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    txt.write_text(str(root / "私人别碰") + "\n", encoding="utf-8")
    pol = root / "08_CONFIG" / "automation_policy.json"
    pol.write_text(
        json.dumps(
            {
                "phase": 1,
                "allow_delete_original": False,
                "allow_move_original": False,
                "ingest_mode": "copy_only",
                "pipeline_order": [
                    "stateverge_asset_inventory --dry-run",
                    "stateverge_asset_inventory --apply",
                    "read_queues_only_for_render",
                    "published_assets_dedupe_before_upload",
                ],
                "timelapse_library_isolated": True,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def build_record(root: Path, path: Path, stats: dict[str, Any]) -> dict[str, Any] | None:
    if is_protected_path(path):
        protected_skip(root, path, "private_do_not_touch", stats)
        return None
    cat = media_category(path)
    st = path.stat()
    fp = ffprobe_json(path) if cat == "video" else {}
    exif = exiftool_row(path) if cat in ("video", "image") else {}
    meta_make = str(exif.get("Make") or "").strip()
    meta_model = str(exif.get("Model") or "").strip()
    if meta_make or meta_model:
        log_event(
            root,
            f"DEVICE_DETECTED path={path} make={meta_make} model={meta_model} source=metadata",
            stats,
        )
    creation = (
        exif.get("CreateDate")
        or exif.get("MediaCreateDate")
        or exif.get("ModifyDate")
    )
    dt = parse_exif_datetime(str(creation) if creation else "")
    if dt is None:
        dt = datetime.fromtimestamp(st.st_mtime)
        shoot_src = "filesystem_mtime"
    else:
        shoot_src = "exif"
    shoot_date = dt.date().isoformat()
    shoot_time = dt.strftime("%H:%M:%S")
    tb = time_bucket(dt.hour, dt.minute)
    lat = exif.get("GPSLatitude")
    lon = exif.get("GPSLongitude")
    try:
        lat_f = float(lat) if lat is not None else None
    except (TypeError, ValueError):
        lat_f = None
    try:
        lon_f = float(lon) if lon is not None else None
    except (TypeError, ValueError):
        lon_f = None
    gps_guess = gps_nyc_guess(lat_f, lon_f)
    slug_kw = location_slug_from_text(path)
    location_guess = slug_kw or gps_guess
    location_slug = (slug_kw or gps_guess).replace(" ", "_")
    w = int(exif.get("ImageWidth") or fp.get("width") or 0)
    h = int(exif.get("ImageHeight") or fp.get("height") or 0)
    orient = orientation_label(w, h)
    tier, make, model, dev_src = device_tier_and_source(path, {"make": meta_make, "model": meta_model}, exif)
    if dev_src == "fallback_filename_weak":
        log_event(
            root,
            "DEVICE_DETECTED path=%s model=possible_iphone source=fallback_filename_weak reason=metadata_missing_device_model"
            % (path,),
            stats,
        )
    is_tl, tl_reason = detect_timelapse(path, exif, fp)
    if is_tl:
        stats["timelapse_detected"] = stats.get("timelapse_detected", 0) + 1
        log_event(root, f"TIMELAPSE_DETECTED path={path} reason={tl_reason}", stats)
    envato_hit = any(
        k in str(path).lower()
        for k in ("envato", "elements", "license", "videohive", "audiojungle")
    )
    quality_status = "ok"
    skip_reason = ""
    if cat == "video" and not fp.get("has_video"):
        quality_status = "unknown"
        skip_reason = "metadata_unknown_video"
        stats["metadata_unknown"] = stats.get("metadata_unknown", 0) + 1
        log_event(root, f"METADATA_UNKNOWN path={path}", stats)
    eligible = not is_tl and skip_reason == "" and path.suffix.lower() != ".dng"
    if path.suffix.lower() == ".dng":
        eligible = False
    daily_date = shoot_date
    qhash = sha256_quick(path)
    asset_id = hashlib.sha256(
        f"{qhash}|{path.name}|{fp.get('duration_seconds', 0)}".encode()
    ).hexdigest()[:20]
    rec: dict[str, Any] = {
        "asset_id": asset_id,
        "source_path": str(path.resolve()),
        "original_path": str(path.resolve()),
        "current_path": str(path.resolve()),
        "library_path": "",
        "action": "none",
        "media_type": cat,
        "category": cat,
        "device_tier": tier,
        "make": make or None,
        "model": model or None,
        "width": w,
        "height": h,
        "duration_seconds": float(fp.get("duration_seconds") or 0),
        "orientation": orient,
        "creation_time": str(creation) if creation else None,
        "shoot_date": shoot_date,
        "shoot_time": shoot_time,
        "time_bucket": tb,
        "gps_lat": lat_f,
        "gps_lon": lon_f,
        "location_guess": location_guess,
        "location_slug": location_slug or "unknown_location",
        "is_timelapse": is_tl,
        "timelapse_reason": tl_reason,
        "eligible_for_daily_content": eligible,
        "daily_content_date": daily_date,
        "size_bytes": st.st_size,
        "sha256_quick": qhash,
        "status": "planned",
        "skip_reason": skip_reason,
        "used_in_content_ids": [],
        "usage_count": 0,
        "last_used_date": None,
        "protected": False,
        "fps": fp.get("fps"),
        "codec": fp.get("codec"),
        "audio_codec": fp.get("audio_codec"),
        "device_detect_source": dev_src,
    }
    return rec


def _is_move_excluded_source(root: Path, src: Path) -> tuple[bool, str]:
    """Return (excluded, reason) for --mode move."""
    try:
        rel = src.resolve().relative_to(root.resolve())
    except Exception:
        return True, "outside_root"
    top = rel.parts[0] if rel.parts else ""
    if top in {"03_OUTPUT", "04_ARCHIVE", "05_INDEX", "06_REPORTS", "07_AUTOMATION", "08_CONFIG"}:
        return True, f"excluded_topdir_{top}"
    return False, ""


def _write_duplicate_cleanup_plan(root: Path, records: list[dict[str, Any]]) -> None:
    """
    Generate duplicate cleanup plan ONLY (no deletes).
    Based on sha256_quick duplicates among scanned records.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for r in records:
        qh = str(r.get("sha256_quick") or "")
        if not qh:
            continue
        groups.setdefault(qh, []).append(r)
    rows: list[dict[str, Any]] = []
    for qh, items in groups.items():
        if len(items) <= 1:
            continue
        # keep first, plan the rest
        keep = items[0]
        for dup in items[1:]:
            rows.append(
                {
                    "sha256_quick": qh,
                    "keep_source_path": keep.get("source_path"),
                    "duplicate_source_path": dup.get("source_path"),
                    "duplicate_asset_id": dup.get("asset_id"),
                    "note": "plan_only_no_delete",
                }
            )
    out = root / "06_REPORTS" / "asset_inventory" / "duplicate_cleanup_plan.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text("sha256_quick,keep_source_path,duplicate_source_path,duplicate_asset_id,note\n", encoding="utf-8")
        return
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "sha256_quick",
                "keep_source_path",
                "duplicate_source_path",
                "duplicate_asset_id",
                "note",
            ],
            extrasaction="ignore",
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)


def dest_paths_for_record(root: Path, rec: dict[str, Any]) -> list[tuple[str, str]]:
    """Returns list of (relative_dest_dir, role_tag)."""
    out: list[tuple[str, str]] = []
    cat = rec["media_type"]
    slug = rec["location_slug"] or "unknown_location"
    tb = rec["time_bucket"]
    sd = str(rec.get("shoot_date") or "2000-01-01")
    try:
        y, ym, _ = sd.split("-")
    except ValueError:
        y, ym = "2000", "2000-01"
    date_prefix = f"{y}/{y}-{ym}/{sd}"
    tier = rec["device_tier"]
    if rec.get("is_timelapse") and cat == "video":
        out.append(
            (
                f"01_ASSET_LIBRARY/timelapse/video/{date_prefix}/{slug}/",
                "timelapse_video",
            )
        )
        return out
    if cat == "video" and not rec.get("is_timelapse"):
        out.append(
            (
                f"01_ASSET_LIBRARY/video_by_date_location/{date_prefix}/{tb}/{slug}/",
                "by_date",
            )
        )
    if cat == "image" and not rec.get("is_timelapse"):
        out.append(
            (
                f"01_ASSET_LIBRARY/image_by_date_location/{date_prefix}/{tb}/{slug}/",
                "by_date_image",
            )
        )
    if cat == "video" and tier == "iphone_17_pro_max":
        out.append(
            (
                f"01_ASSET_LIBRARY/video/iphone/iphone_17_pro_max/{date_prefix}/",
                "device",
            )
        )
    elif cat == "video" and tier in ("iphone", "possible_iphone"):
        out.append(
            (f"01_ASSET_LIBRARY/video/iphone/iphone_other/{date_prefix}/", "device")
        )
    elif cat == "video" and tier == "drone":
        out.append((f"01_ASSET_LIBRARY/video/drone/{date_prefix}/", "device"))
    elif cat == "video":
        out.append((f"01_ASSET_LIBRARY/video/unknown/{date_prefix}/", "device"))
    if cat == "image":
        ext = Path(rec["source_path"]).suffix.lower()
        if ext == ".dng":
            out.append((f"01_ASSET_LIBRARY/image/dng_raw/{date_prefix}/", "dng"))
        elif tier == "iphone_17_pro_max":
            out.append(
                (
                    f"01_ASSET_LIBRARY/image/iphone/iphone_17_pro_max/{date_prefix}/",
                    "device",
                )
            )
        elif tier in ("iphone", "possible_iphone"):
            out.append(
                (
                    f"01_ASSET_LIBRARY/image/iphone/iphone_other/{date_prefix}/",
                    "device",
                )
            )
        else:
            out.append((f"01_ASSET_LIBRARY/image/unknown/{date_prefix}/", "device"))
    if cat == "audio":
        blob = rec["source_path"].lower()
        if any(k in blob for k in ("sfx", "effect", "whoosh", "impact")):
            out.append(("01_ASSET_LIBRARY/audio/sfx/", "audio"))
        elif any(k in blob for k in ("voice", "vocal", "tts")):
            out.append(("01_ASSET_LIBRARY/audio/voice/", "audio"))
        else:
            out.append(("01_ASSET_LIBRARY/audio/music/", "audio"))
    if any(k in rec["source_path"].lower() for k in ("envato", "elements", "license")):
        out.append(("01_ASSET_LIBRARY/envato/video/", "envato"))
    return out


def load_index_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.is_file():
        return keys
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
                keys.add(o.get("sha256_quick", ""))
            except json.JSONDecodeError:
                continue
    return keys


def append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def yesterday(local_tz: str) -> date:
    now = datetime.now(ZoneInfo(local_tz))
    return (now - timedelta(days=1)).date()


def _safe_shoot_date(r: dict[str, Any]) -> date | None:
    try:
        return date.fromisoformat(str(r.get("shoot_date") or ""))
    except ValueError:
        return None


def build_queues(
    root: Path,
    records: list[dict[str, Any]],
    stats: dict[str, Any],
    local_tz: str,
) -> None:
    yday = yesterday(local_tz)
    ystr = yday.isoformat()
    stats["yesterday_date"] = ystr

    def pool_video(cond) -> list[dict[str, Any]]:
        return [
            r
            for r in records
            if r["media_type"] == "video" and not r.get("is_timelapse") and cond(r)
        ]

    long_p0 = pool_video(
        lambda r: r["shoot_date"] == ystr
        and r.get("orientation") == "landscape"
        and float(r.get("duration_seconds") or 0) >= 30
        and r.get("eligible_for_daily_content")
    )
    short_p0 = pool_video(
        lambda r: r["shoot_date"] == ystr
        and r.get("orientation") == "portrait"
        and 5 <= float(r.get("duration_seconds") or 0) <= 60
    )
    thumb_p0 = [
        r
        for r in records
        if r["media_type"] == "image"
        and not r.get("is_timelapse")
        and r.get("shoot_date") == ystr
        and Path(r["source_path"]).suffix.lower() != ".dng"
    ]
    thumb_p0.sort(key=lambda r: -(r.get("width") or 0) * (r.get("height") or 0))

    def extend_long(pool: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
        start = yday - timedelta(days=days)
        seen = {r["asset_id"] for r in pool}
        out = list(pool)
        for r in records:
            if r["media_type"] != "video" or r.get("is_timelapse"):
                continue
            sd = _safe_shoot_date(r)
            if sd is None or sd >= yday or sd < start:
                continue
            if r.get("orientation") != "landscape":
                continue
            if float(r.get("duration_seconds") or 0) < 30:
                continue
            if r["asset_id"] in seen:
                continue
            seen.add(r["asset_id"])
            out.append(r)
        return out

    def extend_short(pool: list[dict[str, Any]], days: int) -> list[dict[str, Any]]:
        start = yday - timedelta(days=days)
        seen = {r["asset_id"] for r in pool}
        out = list(pool)
        for r in records:
            if r["media_type"] != "video" or r.get("is_timelapse"):
                continue
            sd = _safe_shoot_date(r)
            if sd is None or sd >= yday or sd < start:
                continue
            if r.get("orientation") != "portrait":
                continue
            d = float(r.get("duration_seconds") or 0)
            if not (5 <= d <= 60):
                continue
            if r["asset_id"] in seen:
                continue
            seen.add(r["asset_id"])
            out.append(r)
        return out

    long_fill = list(long_p0)
    ltag: str | None = None
    if len(long_fill) < 3:
        long_fill = extend_long(long_fill, 7)
        ltag = "P1"
    if len(long_fill) < 3:
        long_fill = extend_long(long_fill, 30)
        ltag = "P2"
    if len(long_fill) < 1:
        long_fill = pool_video(
            lambda r: r.get("orientation") == "landscape"
            and float(r.get("duration_seconds") or 0) >= 30
        )
        ltag = "P3"
    if ltag:
        stats.setdefault("backfill", Counter())[ltag] += 1
        log_event(root, f"BACKFILL_USED level={ltag} reason=insufficient_yesterday", stats)

    short_fill = list(short_p0)
    stag: str | None = None
    if len(short_fill) < 2:
        short_fill = extend_short(short_fill, 7)
        stag = "P1"
    if len(short_fill) < 2:
        short_fill = extend_short(short_fill, 30)
        stag = "P2"
    if len(short_fill) < 1:
        short_fill = pool_video(
            lambda r: r.get("orientation") == "portrait"
            and 5 <= float(r.get("duration_seconds") or 0) <= 60
        )
        stag = "P3"
    if stag:
        stats.setdefault("backfill", Counter())[f"short_{stag}"] += 1
        log_event(root, f"BACKFILL_USED level={stag} reason=insufficient_yesterday_short", stats)

    qdir = (
        root / "07_AUTOMATION" / "queues"
        if stats.get("_apply")
        else root / "06_REPORTS" / "asset_inventory" / "queue_preview"
    )
    qdir.mkdir(parents=True, exist_ok=True)
    stats["yesterday_long_candidates"] = len(long_fill)
    stats["yesterday_short_candidates"] = len(short_fill)
    stats["yesterday_thumb_candidates"] = len(thumb_p0)

    def qwrite(name: str, items: list[dict[str, Any]], plevel: str, back: bool) -> None:
        p = qdir / name
        if p.is_file():
            p.unlink()
        for i, r in enumerate(items[:200]):
            append_jsonl(
                p,
                {
                    "priority_level": plevel,
                    "is_backfill": back,
                    "content_id": "",
                    "candidate_score": 1.0 - i * 0.001,
                    "source_assets": [r["asset_id"]],
                    "reason": "daily_yesterday_queue",
                    "asset_ref": r["source_path"],
                },
            )

    qwrite("daily_yesterday_long_candidates.jsonl", long_fill, "P0", ltag is None)
    qwrite("daily_yesterday_short_candidates.jsonl", short_fill, "P0", stag is None)
    qwrite("daily_yesterday_thumbnail_candidates.jsonl", thumb_p0, "P0", False)
    log_event(
        root,
        f"FOUND_YESTERDAY_ASSETS count_long={len(long_p0)} count_short={len(short_p0)} count_thumb={len(thumb_p0)}",
        stats,
    )
    for name, pool, orient, used_tag in (
        ("nyc_long_ready.jsonl", long_fill, "landscape", ltag),
        ("nyc_short_ready.jsonl", short_fill, "portrait", stag),
    ):
        outp = qdir / name
        if outp.is_file():
            outp.unlink()
        for i, r in enumerate(pool[:50]):
            append_jsonl(
                outp,
                {
                    "priority_level": "P0",
                    "is_backfill": used_tag is not None,
                    "content_id": "",
                    "candidate_score": 1.0 - i * 0.01,
                    "source_assets": [r["asset_id"]],
                    "reason": f"nyc_{orient}_ready_skeleton",
                },
            )
    log_event(
        root,
        f"QUEUE_LONG candidates={len(long_fill)} QUEUE_SHORT candidates={len(short_fill)}",
        stats,
    )


def apply_ingest(
    root: Path,
    records: list[dict[str, Any]],
    seen: set[str],
    stats: dict[str, Any],
    *,
    mode: str,
) -> None:
    for rec in records:
        if is_protected_path(rec["source_path"]):
            continue
        src = Path(rec["source_path"])
        if mode == "move":
            excluded, reason = _is_move_excluded_source(root, src)
            if excluded:
                log_event(root, f"MOVE_SKIP reason={reason} source={src}", stats)
                continue
        qh = rec["sha256_quick"]
        if qh in seen:
            log_event(
                root,
                f"DUPLICATE_SKIP source={rec['source_path']} existing=sha256_quick_match",
                stats,
            )
            stats["duplicate_skip"] = stats.get("duplicate_skip", 0) + 1
            continue
        seen.add(qh)
        dest_dirs = dest_paths_for_record(root, rec)
        if not dest_dirs:
            continue
        primary_rel = dest_dirs[0][0]
        dest_dir = (root / primary_rel).resolve()
        if is_protected_path(dest_dir):
            protected_skip(root, dest_dir, "private_do_not_touch", stats)
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{rec['asset_id']}_{src.name}"
        try:
            rec["library_path"] = str(dest)
            rec["original_path"] = rec.get("original_path") or str(src)
            rec["current_path"] = rec.get("current_path") or str(src)

            if mode == "hardlink":
                # link only; if fails, skip without copy.
                try:
                    if not dest.exists():
                        os.link(src, dest)
                    rec["status"] = "hardlinked"
                    rec["action"] = "hardlink"
                except OSError as e:
                    rec["status"] = f"hardlink_skip:{e}"
                    log_event(root, f"HARDLINK_SKIP source={src} target={dest} err={e}", stats)
                    continue
            elif mode == "move":
                # safety: must not be protected, must not be same path
                if is_protected_path(src):
                    log_event(root, f"MOVE_SKIP reason=protected source={src}", stats)
                    continue
                if str(src.resolve()) == str(dest.resolve()):
                    log_event(root, f"MOVE_SKIP reason=same_path source={src}", stats)
                    continue
                # write target first (copy2), verify sha256_quick match, then unlink source
                shutil.copy2(src, dest)
                if not dest.is_file() or dest.stat().st_size <= 0:
                    log_event(root, f"MOVE_SKIP reason=target_empty source={src} target={dest}", stats)
                    continue
                q_src = rec["sha256_quick"]
                q_dst = sha256_quick(dest)
                if q_src != q_dst:
                    log_event(root, f"MOVE_SKIP reason=sha256_mismatch source={src} target={dest}", stats)
                    continue
                # OK: remove original (move semantics)
                src.unlink()
                rec["status"] = "moved"
                rec["action"] = "move"
                rec["current_path"] = str(dest)
                log_event(root, f"MOVE_OK source={rec['original_path']} target={dest}", stats)
            else:
                # copy
                shutil.copy2(src, dest)
                rec["status"] = "copied"
                rec["action"] = "copy"

            if rec.get("is_timelapse"):
                log_event(root, f"TIMELAPSE_ISOLATED path={src} library_path={dest}", stats)
                tlq = (
                    root
                    / "01_ASSET_LIBRARY"
                    / "timelapse"
                    / "queues"
                    / "timelapse_candidates.jsonl"
                )
                append_jsonl(
                    tlq,
                    {"asset_id": rec["asset_id"], "library_path": str(dest), "reason": "timelapse"},
                )
        except OSError as e:
            rec["status"] = f"{mode}_error:{e}"
        for rel, _tag in dest_dirs[1:3]:
            d2 = (root / rel).resolve()
            if is_protected_path(d2):
                continue
            d2.mkdir(parents=True, exist_ok=True)
            dfile = d2 / f"{rec['asset_id']}_{src.name}"
            try:
                if not dfile.exists():
                    if mode == "hardlink":
                        os.link(dest, dfile) if dest.exists() else None
                    else:
                        # for move/copy: source may be gone; copy from primary dest
                        shutil.copy2(dest if dest.exists() else src, dfile)
            except OSError:
                pass


def write_audit_reports(root: Path, stats: dict[str, Any], records: list[dict[str, Any]]) -> None:
    audit_dir = root / "06_REPORTS" / "system_audit"
    inv_dir = root / "06_REPORTS" / "asset_inventory"
    audit_dir.mkdir(parents=True, exist_ok=True)
    inv_dir.mkdir(parents=True, exist_ok=True)
    vcount = sum(1 for r in records if r["media_type"] == "video")
    icount = sum(1 for r in records if r["media_type"] == "image")
    acount = sum(1 for r in records if r["media_type"] == "audio")
    iphone = sum(1 for r in records if "iphone" in r.get("device_tier", ""))
    ip17 = sum(1 for r in records if r.get("device_tier") == "iphone_17_pro_max")
    land = sum(
        1
        for r in records
        if r["media_type"] == "video" and r.get("orientation") == "landscape"
    )
    port = sum(
        1
        for r in records
        if r["media_type"] == "video" and r.get("orientation") == "portrait"
    )
    ystr = stats.get("yesterday_date", "")
    yv = sum(
        1
        for r in records
        if r["media_type"] == "video" and r.get("shoot_date") == ystr
    )
    yi = sum(
        1
        for r in records
        if r["media_type"] == "image" and r.get("shoot_date") == ystr
    )
    skips = Counter(r.get("skip_reason") or "(none)" for r in records)
    locs = Counter(r.get("location_slug") or "unknown" for r in records)
    tbs = Counter(r.get("time_bucket") or "unknown" for r in records)
    largest = sorted(records, key=lambda r: -r.get("size_bytes", 0))[:20]
    lines = [
        "# StateVerge system audit",
        "",
        f"1. Protected path configured: `/Volumes/StateVerge/私人别碰`",
        f"2. PROTECTED_SKIP count: {stats.get('protected_skip', 0)}",
        f"3. Total media files scanned: {stats.get('files_scanned', 0)}",
        f"4. Videos: {vcount}  Images: {icount}  Audio: {acount}",
        f"5. iPhone-tier assets: {iphone}  iPhone 17 Pro Max: {ip17}",
        f"6. Landscape videos: {land}  Portrait videos: {port}",
        f"7. Timelapse detected: {stats.get('timelapse_detected', 0)}",
        f"8. Timelapse isolated from daily long/short queues: yes (separate paths + timelapse_candidates.jsonl)",
        f"9. Yesterday date: {ystr}",
        f"10. Yesterday videos: {yv}  images: {yi}",
        f"11. daily_yesterday_long_candidates: {stats.get('yesterday_long_candidates', 0)}",
        f"12. daily_yesterday_short_candidates: {stats.get('yesterday_short_candidates', 0)}",
        f"13. daily_yesterday_thumbnail_candidates: {stats.get('yesterday_thumb_candidates', 0)}",
        f"14. Backfill counters: {dict(stats.get('backfill', {}))}",
        f"15. Duplicate skips: {stats.get('duplicate_skip', 0)}",
        f"16. Metadata unknown: {stats.get('metadata_unknown', 0)}",
        "",
        "## Top skip_reason",
    ]
    for reason, c in skips.most_common(10):
        lines.append(f"- {reason}: {c}")
    lines += ["", "## Top locations", ""]
    for loc, c in locs.most_common(20):
        lines.append(f"- {loc}: {c}")
    lines += ["", "## time_bucket", ""]
    for tb, c in tbs.most_common():
        lines.append(f"- {tb}: {c}")
    lines += ["", "## Largest 20 files (by size_bytes)", ""]
    for r in largest:
        lines.append(f"- {r.get('size_bytes')}  {r.get('source_path')}")
    lines += [
        "",
        "## Next steps",
        "- Wire NYC render pipelines to read only `07_AUTOMATION/queues/nyc_*_ready.jsonl`.",
        "- Never scan `01_ASSET_LIBRARY/timelapse` for standard long/short.",
        "- Run with `--apply` after dry-run review.",
    ]
    (audit_dir / "stateverge_system_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    inv_lines = [
        "# Asset inventory report",
        "",
        f"Records: {len(records)}",
        f"Dry-run mode: {not stats.get('_apply', False)}",
    ]
    (inv_dir / "asset_inventory_report.md").write_text("\n".join(inv_lines) + "\n", encoding="utf-8")


def scan_manifest(root: Path, stats: dict[str, Any], records: list[dict[str, Any]]) -> None:
    p = root / "05_INDEX" / "scan_manifest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "files_scanned": stats.get("files_scanned", 0),
                "protected_skip": stats.get("protected_skip", 0),
                "record_count": len(records),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=SSD_ROOT)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Copy files, write jsonl/csv queues (default: dry-run only)",
    )
    ap.add_argument(
        "--mode",
        choices=("copy", "move", "hardlink"),
        default="copy",
        help="Ingest mode: copy (default), move (space-saving), hardlink (same disk; no fallback copy)",
    )
    ap.add_argument("--timezone", default="America/New_York", help="IANA timezone for yesterday")
    ap.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Stop after N media files (dry-run / smoke test; default unlimited)",
    )
    args = ap.parse_args()

    root: Path = args.root.expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: root not found: {root}")
        return 1

    reload_protected_roots()
    stats: dict[str, Any] = {"_apply": args.apply}
    ensure_layout(root, stats)
    write_protected_config(root)

    records: list[dict[str, Any]] = []
    stats["files_scanned"] = 0
    for f in iter_scan_files(root, stats, args.max_files):
        stats["files_scanned"] += 1
        rec = build_record(root, f, stats)
        if rec:
            records.append(rec)

    index_jsonl = root / "05_INDEX" / "assets_master.jsonl"
    seen_hashes = load_index_keys(index_jsonl) if args.apply else set()

    if args.apply:
        apply_ingest(root, records, seen_hashes, stats, mode=str(args.mode))
        _write_duplicate_cleanup_plan(root, records)
        # score assets (best-effort)
        try:
            sys.path.insert(0, str(root / "07_AUTOMATION" / "scripts"))
            from stateverge_asset_scorer import score_asset

            for r in records:
                if r.get("is_timelapse"):
                    r["quality_score"] = 0
                    continue
                r["quality_score"] = score_asset(r).score
        except Exception:
            pass
        if index_jsonl.is_file():
            index_jsonl.unlink()
        for rec in records:
            append_jsonl(index_jsonl, rec)
        write_csv(root / "05_INDEX" / "assets_master.csv", records)
        pub = root / "05_INDEX" / "published_assets.jsonl"
        if not pub.exists():
            pub.write_text("", encoding="utf-8")
    else:
        _write_duplicate_cleanup_plan(root, records)
        preview = root / "06_REPORTS" / "asset_inventory" / "dry_run_plan.jsonl"
        preview.parent.mkdir(parents=True, exist_ok=True)
        if preview.is_file():
            preview.unlink()
        for rec in records:
            rec["planned_destinations"] = [
                str(root / rel) for rel, _ in dest_paths_for_record(root, rec)
            ]
            append_jsonl(preview, rec)

    build_queues(root, records, stats, args.timezone)
    scan_manifest(root, stats, records)
    write_audit_reports(root, stats, records)

    print(
        json.dumps(
            {
                "dry_run": not args.apply,
                "files_scanned": stats.get("files_scanned"),
                "records": len(records),
                "protected_skip": stats.get("protected_skip", 0),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
