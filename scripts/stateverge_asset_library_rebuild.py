#!/usr/bin/env python3
"""
扫描固定入口，用 ffprobe / exiftool / PIL 识别视频与图片（不用扩展名或文件名语义），
复制到 01_ASSET_LIBRARY 并维护 version=2 索引。

规则：不进入「私人别碰」；默认 copy2，不删 00_INBOX 原文件。
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
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any, Iterator

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import SSD_ROOT as VOL  # noqa: E402
LIB = VOL / "01_ASSET_LIBRARY"
INDEX_DIR = LIB / "_index"
LOG_DIR = LIB / "_logs"
REBUILD_MASTER_LOG = VOL / "07_AUTOMATION" / "logs" / "asset_rebuild.log"
HOME_FALLBACK_LOG = Path.home() / "Library/Logs/StateVerge" / "asset_rebuild.log"

SCAN_ROOTS = [
    VOL / "00_INBOX",
    VOL / "02_PROJECTS",
    VOL / "04_ASSETS",
]

PRIVATE_SEGMENT = "私人别碰"
DIR_SKIP_PARTS = frozenset(
    {
        "node_modules",
        ".git",
        ".venv",
        "__pycache__",
        ".Trash",
        "Trashes",
        ".TemporaryItems",
        ".fseventsd",
        ".Spotlight-V100",
    }
)
DIR_SKIP_PREFIX = (".Spotlight",)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def log_rebuild(msg: str) -> None:
    line = f"{_iso_now()} {msg}"
    print(line, flush=True)
    for log_path in (REBUILD_MASTER_LOG, HOME_FALLBACK_LOG):
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            continue


def path_has_private(p: Path) -> bool:
    return PRIVATE_SEGMENT in p.parts or PRIVATE_SEGMENT in str(p)


def skip_macos_junk_file(p: Path) -> bool:
    n = p.name
    if n == ".DS_Store":
        return True
    if n.startswith("._"):
        return True
    if n.startswith(".Spotlight"):
        return True
    if "Trashes" in p.parts:
        return True
    return False


def should_skip_dir(p: Path) -> bool:
    if path_has_private(p):
        return True
    for part in p.parts:
        if part in DIR_SKIP_PARTS:
            return True
        if part.startswith(DIR_SKIP_PREFIX):
            return True
    return False


def ffprobe_path() -> str | None:
    return shutil.which("ffprobe")


def exiftool_path() -> str | None:
    return shutil.which("exiftool")


_tools_warned: set[str] = set()


def warn_missing_tool(name: str) -> None:
    if name in _tools_warned:
        return
    _tools_warned.add(name)
    log_rebuild(f"WARN_MISSING_TOOL {name}")


def sha1_file(path: Path, chunk: int = 1024 * 1024) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest().lower()


def ffprobe_streams(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "has_video": False,
        "has_audio": False,
        "duration": 0.0,
        "width": 0,
        "height": 0,
        "fps": 0.0,
        "make": None,
        "model": None,
        "creation_time": None,
        "video_codec": None,
    }
    exe = ffprobe_path()
    if not exe:
        warn_missing_tool("ffprobe")
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
            timeout=180,
            check=False,
        )
        if r.returncode != 0:
            return out
        data = json.loads(r.stdout or "{}")
        fmt = data.get("format") or {}
        tags = fmt.get("tags") or {}
        if isinstance(tags, dict):
            for k, v in tags.items():
                if v is None:
                    continue
                kl = str(k).lower()
                if kl in ("make", "com.apple.quicktime.make"):
                    out["make"] = str(v).strip()
                if kl in ("model", "com.apple.quicktime.model", "device_model_name"):
                    out["model"] = str(v).strip()
                if "creation_time" in kl or "creationdate" in kl:
                    out["creation_time"] = str(v).strip()
        try:
            out["duration"] = float(fmt.get("duration") or 0)
        except (TypeError, ValueError):
            pass
        streams = data.get("streams") or []
        for s in streams:
            if s.get("codec_type") == "audio":
                out["has_audio"] = True
        for s in streams:
            if s.get("codec_type") != "video":
                continue
            out["has_video"] = True
            out["video_codec"] = str(s.get("codec_name") or "").lower()
            out["width"] = int(s.get("width") or 0)
            out["height"] = int(s.get("height") or 0)
            rfr = s.get("r_frame_rate") or "0/1"
            try:
                a, b = str(rfr).split("/")
                out["fps"] = float(a) / float(b) if float(b) else 0.0
            except (ValueError, ZeroDivisionError):
                pass
            st = s.get("tags") or {}
            if isinstance(st, dict):
                for k, v in st.items():
                    if v is None:
                        continue
                    kl = str(k).lower()
                    if kl in ("make", "com.apple.quicktime.make") and not out["make"]:
                        out["make"] = str(v).strip()
                    if kl in ("model", "com.apple.quicktime.model") and not out["model"]:
                        out["model"] = str(v).strip()
            break
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, TypeError, ValueError):
        pass
    return out


def exiftool_json(path: Path, tags: list[str]) -> dict[str, Any]:
    exe = exiftool_path()
    if not exe:
        warn_missing_tool("exiftool")
        return {}
    args = [exe, "-j", "-n", *[f"-{t}" for t in tags], str(path)]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120, check=False)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {}
        rows = json.loads(r.stdout)
        if not rows or not isinstance(rows[0], dict):
            return {}
        return rows[0]
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError, IndexError):
        return {}


def pil_image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image  # type: ignore

        with Image.open(path) as im:
            w, h = im.size
            if w > 0 and h > 0:
                return int(w), int(h)
    except Exception:
        pass
    return None


def probe_image_dimensions(path: Path) -> tuple[int, int] | None:
    wh = pil_image_dimensions(path)
    if wh:
        return wh
    row = exiftool_json(
        path,
        [
            "ImageWidth",
            "ImageHeight",
            "Orientation",
            "Make",
            "Model",
            "CreateDate",
            "MediaCreateDate",
            "ModifyDate",
            "GPSLatitude",
            "GPSLongitude",
            "GPSAltitude",
        ],
    )
    try:
        w = int(float(row.get("ImageWidth") or 0))
        h = int(float(row.get("ImageHeight") or 0))
        if w > 0 and h > 0:
            return w, h
    except (TypeError, ValueError):
        pass
    return None


def detect_media_type(path: Path) -> tuple[str, dict[str, Any]]:
    """
    Returns (media_type, probe_dict).
    media_type: video | photo | unknown
    """
    fv = ffprobe_streams(path)
    if fv.get("has_video"):
        codec = str(fv.get("video_codec") or "").lower()
        dur = float(fv.get("duration") or 0)
        still_codecs = ("mjpeg", "png", "gif", "webp", "bmp", "ppm", "pgm", "pbm", "pam", "tiff", "ljpeg")
        if codec in still_codecs and dur < 2.0 and int(fv.get("width") or 0) > 0:
            return "photo", fv
        return "video", fv
    wh = probe_image_dimensions(path)
    if wh:
        fv["width"], fv["height"] = wh[0], wh[1]
        return "photo", fv
    return "unknown", fv


def time_bucket_from_ts(ts: float | None) -> str:
    if ts is None or ts <= 0:
        return "unknown_time"
    try:
        h = datetime.fromtimestamp(ts).hour
    except (OSError, ValueError):
        return "unknown_time"
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 16:
        return "day"
    if 16 <= h < 20:
        return "golden_hour"
    return "night"


def _safe_stem(stem: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", stem).strip() or "file"
    return s[:120]


def _parse_ts_for_folder(s: str | None, mtime: float) -> float:
    if not s:
        return mtime
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
    ):
        try:
            return datetime.strptime(s.replace("Z", "+00:00")[:26], fmt).timestamp()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return mtime


def build_library_rel(
    media_type: str,
    anchor_ts: float,
    city_folder: str,
    time_bucket: str,
) -> Path:
    dt = datetime.fromtimestamp(anchor_ts)
    y = dt.strftime("%Y")
    ym = dt.strftime("%Y-%m")
    ymd = dt.strftime("%Y-%m-%d")
    city_safe = re.sub(r"[^\w\-]", "_", city_folder)[:64] or "unknown"
    tb = time_bucket if time_bucket in (
        "morning",
        "day",
        "golden_hour",
        "night",
        "unknown_time",
    ) else "unknown_time"
    base = "01_VIDEO" if media_type == "video" else "02_PHOTO"
    return Path(base) / "raw" / y / ym / ymd / city_safe / tb


def enrich_metadata(
    path: Path,
    media_type: str,
    fv: dict[str, Any],
    st: os.stat_result,
) -> dict[str, Any]:
    row = exiftool_json(
        path,
        [
            "Orientation",
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
        ],
    )
    gps_lat = row.get("GPSLatitude")
    gps_lon = row.get("GPSLongitude")
    gps_alt = row.get("GPSAltitude")
    try:
        gps_lat_f = float(gps_lat) if gps_lat is not None else None
    except (TypeError, ValueError):
        gps_lat_f = None
    try:
        gps_lon_f = float(gps_lon) if gps_lon is not None else None
    except (TypeError, ValueError):
        gps_lon_f = None
    try:
        gps_alt_f = float(gps_alt) if gps_alt is not None else None
    except (TypeError, ValueError):
        gps_alt_f = None

    created_s = (
        row.get("CreateDate")
        or row.get("MediaCreateDate")
        or fv.get("creation_time")
    )
    if isinstance(created_s, str):
        created_s = created_s.strip() or None
    modified_s = row.get("ModifyDate")
    if isinstance(modified_s, str):
        modified_s = modified_s.strip() or None

    mtime = float(st.st_mtime)
    c_ts = _parse_ts_for_folder(str(created_s) if created_s else "", mtime)
    anchor = c_ts if created_s else mtime
    tb = time_bucket_from_ts(anchor)

    make = (row.get("Make") or fv.get("make") or "") or ""
    model = (row.get("Model") or fv.get("model") or "") or ""
    blob = f"{make} {model}".lower()
    is_iphone = "iphone" in blob
    is_17pm = bool(re.search(r"iphone\s*17\s*pro\s*max", blob, re.I))

    orient = row.get("Orientation")
    orientation = str(int(orient)) if orient is not None else "1"

    w = int(fv.get("width") or row.get("ImageWidth") or 0)
    h = int(fv.get("height") or row.get("ImageHeight") or 0)
    try:
        w = int(float(w))
        h = int(float(h))
    except (TypeError, ValueError):
        w, h = 0, 0

    has_video = bool(fv.get("has_video"))
    has_audio = bool(fv.get("has_audio"))

    return {
        "created_at": created_s,
        "modified_at": modified_s or datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "gps_lat": gps_lat_f,
        "gps_lon": gps_lon_f,
        "gps_altitude": gps_alt_f,
        "city": "unknown",
        "neighborhood": "unknown",
        "address_guess": "unknown",
        "time_bucket": tb,
        "device_make": str(make).strip() or None,
        "device_model": str(model).strip() or None,
        "is_iphone": is_iphone,
        "is_iphone_17_pro_max": is_17pm,
        "orientation": orientation,
        "width": w,
        "height": h,
        "duration": float(fv.get("duration") or 0),
        "has_video": has_video,
        "has_audio": has_audio,
        "_anchor_ts": anchor,
    }


def make_dest_filename(original_stem: str, sha1_hex: str, ext: str, anchor_ts: float) -> str:
    dt = datetime.fromtimestamp(anchor_ts)
    prefix = dt.strftime("%Y%m%d_%H%M%S")
    stem = _safe_stem(original_stem)
    h12 = sha1_hex[:12]
    e = ext.lower() if ext else ""
    if e and not e.startswith("."):
        e = "." + e
    return f"{prefix}_{stem}_{h12}{e}"


def iter_candidate_files(root: Path) -> Iterator[Path]:
    if not root.is_dir():
        return
    if path_has_private(root):
        return
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dp = Path(dirpath)
        if path_has_private(dp):
            dirnames[:] = []
            continue
        new_dirs: list[str] = []
        for d in dirnames:
            p = dp / d
            if should_skip_dir(p) or path_has_private(p):
                continue
            new_dirs.append(d)
        dirnames[:] = new_dirs
        for fn in filenames:
            f = dp / fn
            if path_has_private(f):
                continue
            if skip_macos_junk_file(f):
                continue
            if not f.is_file():
                continue
            yield f


def _hex40(s: str) -> bool:
    s = s.lower()
    return len(s) == 40 and all(c in "0123456789abcdef" for c in s)


def load_existing_index(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if isinstance(data, dict) and data.get("version") == 2 and isinstance(data.get("entries"), list):
        entries = data["entries"]
    else:
        entries = data.get("entries")
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        sha = e.get("sha1")
        if isinstance(sha, str) and _hex40(sha):
            out[sha.lower()] = dict(e)
            continue
        lp = e.get("library_path")
        if not lp:
            continue
        p = Path(lp)
        if not p.is_file():
            continue
        try:
            digest = sha1_file(p).lower()
        except OSError:
            continue
        if digest in out:
            continue
        cat = str(e.get("category") or "")
        if "VIDEO" in cat.upper() or cat.startswith("01_VIDEO"):
            mt = "video"
        elif "PHOTO" in cat.upper() or cat.startswith("02_PHOTO"):
            mt = "photo"
        else:
            mt = "unknown"
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        out[digest] = {
            "media_type": mt,
            "file_path": str(e.get("original_path") or lp),
            "library_path": str(lp),
            "original_name": Path(str(e.get("original_path") or lp)).name,
            "sha1": digest,
            "size": sz,
            "created_at": e.get("created_at"),
            "modified_at": None,
            "duration": float(e.get("duration") or 0),
            "width": int(e.get("width") or 0),
            "height": int(e.get("height") or 0),
            "orientation": "1",
            "device_make": None,
            "device_model": None,
            "gps_lat": e.get("gps_latitude"),
            "gps_lon": e.get("gps_longitude"),
            "gps_altitude": None,
            "city": "unknown",
            "neighborhood": "unknown",
            "address_guess": "unknown",
            "time_bucket": str(e.get("time_bucket") or "unknown_time"),
            "is_iphone": False,
            "is_iphone_17_pro_max": False,
            "has_audio": False,
            "has_video": mt == "video",
        }
    return out


def entry_v2(
    *,
    media_type: str,
    file_path: str,
    library_path: str,
    original_name: str,
    sha1: str,
    size: int,
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "media_type": media_type,
        "file_path": file_path,
        "library_path": library_path,
        "original_name": original_name,
        "sha1": sha1,
        "size": size,
        "created_at": meta.get("created_at"),
        "modified_at": meta.get("modified_at"),
        "duration": float(meta.get("duration") or 0),
        "width": int(meta.get("width") or 0),
        "height": int(meta.get("height") or 0),
        "orientation": str(meta.get("orientation") or "1"),
        "device_make": meta.get("device_make"),
        "device_model": meta.get("device_model"),
        "gps_lat": meta.get("gps_lat"),
        "gps_lon": meta.get("gps_lon"),
        "gps_altitude": meta.get("gps_altitude"),
        "city": meta.get("city") or "unknown",
        "neighborhood": meta.get("neighborhood") or "unknown",
        "address_guess": meta.get("address_guess") or "unknown",
        "time_bucket": meta.get("time_bucket") or "unknown_time",
        "is_iphone": bool(meta.get("is_iphone")),
        "is_iphone_17_pro_max": bool(meta.get("is_iphone_17_pro_max")),
        "has_audio": bool(meta.get("has_audio")),
        "has_video": (media_type == "video") and bool(meta.get("has_video")),
    }


def process_one_file(
    src: Path,
    move: bool,
    by_sha1: dict[str, dict[str, Any]],
    log_lines: list[str],
) -> dict[str, Any] | None:
    if path_has_private(src):
        log_lines.append(f"SKIP_PROTECTED_PRIVATE {src}")
        log_rebuild(f"SKIP_PROTECTED_PRIVATE {src}")
        print(f"SKIP_PROTECTED_PRIVATE {src}", flush=True)
        return None
    if skip_macos_junk_file(src):
        log_lines.append(f"SKIP_MACOS_SIDECAR {src}")
        log_rebuild(f"SKIP_MACOS_SIDECAR {src}")
        print(f"SKIP_MACOS_SIDECAR {src}", flush=True)
        return None

    try:
        st = src.stat()
    except OSError as e:
        log_rebuild(f"ERROR_METADATA stat {src} {e}")
        log_lines.append(f"ERROR_METADATA {src} {e}")
        print(f"ERROR_METADATA {src} {e}", flush=True)
        return None

    media_type, fv = detect_media_type(src)
    if media_type == "unknown":
        log_lines.append(f"SKIP_UNKNOWN_TYPE {src}")
        return None

    try:
        sha1 = sha1_file(src)
    except OSError as e:
        log_rebuild(f"ERROR_METADATA sha1 {src} {e}")
        log_lines.append(f"ERROR_METADATA {src} {e}")
        print(f"ERROR_METADATA {src} {e}", flush=True)
        return None

    meta = enrich_metadata(src, media_type, fv, st)
    anchor_ts = float(meta.pop("_anchor_ts"))
    city_folder = str(meta.get("city") or "unknown")
    rel = build_library_rel(media_type, anchor_ts, city_folder, str(meta.get("time_bucket")))
    ext = src.suffix
    dest_name = make_dest_filename(src.stem, sha1, ext, anchor_ts)
    dest = LIB / rel / dest_name

    if sha1 in by_sha1:
        prev = by_sha1[sha1]
        prev.update(
            entry_v2(
                media_type=media_type,
                file_path=str(src.resolve()),
                library_path=str(prev.get("library_path") or dest),
                original_name=src.name,
                sha1=sha1,
                size=st.st_size,
                meta=meta,
            )
        )
        log_lines.append(f"DEDUP_SHA1_UPDATE_INDEX {sha1[:12]} {src}")
        log_rebuild(f"DEDUP_SHA1 {sha1[:12]} {src}")
        return prev

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if move:
            shutil.move(str(src), str(dest))
            log_lines.append(f"MOVE {src} -> {dest}")
        else:
            shutil.copy2(str(src), str(dest))
            log_lines.append(f"COPY {src} -> {dest}")
    except OSError as e:
        log_rebuild(f"ERROR_METADATA copy {src} {e}")
        log_lines.append(f"ERROR_METADATA {src} {e}")
        print(f"ERROR_METADATA {src} {e}", flush=True)
        return None

    ent = entry_v2(
        media_type=media_type,
        file_path=str(src.resolve()),
        library_path=str(dest.resolve()),
        original_name=src.name,
        sha1=sha1,
        size=st.st_size,
        meta=meta,
    )
    by_sha1[sha1] = ent
    log_rebuild(f"INGEST_{media_type.upper()} {dest}")
    log_lines.append(f"INGEST_{media_type.upper()} {dest}")
    print(f"INGEST_{media_type.upper()} {dest}", flush=True)
    return ent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--move", action="store_true", help="移动而非复制（默认 copy2）")
    ap.add_argument(
        "--inbox-only",
        action="store_true",
        help="仅扫描 00_INBOX 整棵目录树",
    )
    ap.add_argument(
        "--airdrop-only",
        action="store_true",
        help="仅扫描 00_INBOX/airdrop 单层目录（watcher 默认）",
    )
    args = ap.parse_args()

    if not VOL.is_dir():
        print(f"ERROR: {VOL} not mounted")
        return 1

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    json_path = INDEX_DIR / "asset_index.json"
    by_sha1 = load_existing_index(json_path)

    if args.airdrop_only:
        roots = [VOL / "00_INBOX" / "airdrop"]
    elif args.inbox_only:
        roots = [VOL / "00_INBOX"]
    else:
        roots = list(SCAN_ROOTS)

    log_lines: list[str] = []
    log_rebuild("REBUILD_START roots=" + ",".join(str(r) for r in roots))

    processed_this_run: set[str] = set()

    for root in roots:
        if path_has_private(root):
            log_rebuild(f"SKIP_PROTECTED_PRIVATE root={root}")
            continue
        if not root.is_dir():
            log_lines.append(f"MISSING_SCAN_ROOT: {root}")
            continue
        log_lines.append(f"SCAN: {root}")
        for f in iter_candidate_files(root):
            key = str(f.resolve())
            if key in processed_this_run:
                continue
            processed_this_run.add(key)
            process_one_file(f, move=args.move, by_sha1=by_sha1, log_lines=log_lines)

    entries = list(by_sha1.values())
    entries.sort(key=lambda e: (e.get("library_path") or ""))

    payload: dict[str, Any] = {"version": 2, "entries": entries}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    detail_log = LOG_DIR / f"rebuild_{ts}.log"
    detail_log.write_text("\n".join(log_lines) + "\n", encoding="utf-8")

    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    csv_path = INDEX_DIR / "asset_index.csv"
    if entries:
        keys: list[str] = []
        for e in entries:
            for k in e:
                if k not in keys:
                    keys.append(k)
        with open(csv_path, "w", newline="", encoding="utf-8") as cf:
            w = csv.DictWriter(cf, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            for e in entries:
                w.writerow(e)
    else:
        hdr = list(
            entry_v2(
                media_type="video",
                file_path="",
                library_path="",
                original_name="",
                sha1="0" * 40,
                size=0,
                meta={
                    "created_at": None,
                    "modified_at": None,
                    "duration": 0.0,
                    "width": 0,
                    "height": 0,
                    "orientation": "1",
                    "device_make": None,
                    "device_model": None,
                    "gps_lat": None,
                    "gps_lon": None,
                    "gps_altitude": None,
                    "city": "unknown",
                    "neighborhood": "unknown",
                    "address_guess": "unknown",
                    "time_bucket": "unknown_time",
                    "is_iphone": False,
                    "is_iphone_17_pro_max": False,
                    "has_audio": False,
                    "has_video": False,
                },
            ).keys()
        )
        csv_path.write_text(",".join(hdr) + "\n", encoding="utf-8")

    log_rebuild(f"REBUILD_DONE entries={len(entries)}")
    print(f"INDEX: {json_path}")
    print(f"CSV:   {csv_path}")
    print(f"LOG:   {detail_log}")
    print(f"ENTRIES: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
