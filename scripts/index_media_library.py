#!/usr/bin/env python3
"""Build a semantic media index for StateVerge dual-source library (fail-open).

Official sources (priority):
  1) Premium: /Volumes/SV_TRANSFER/00_INBOX/iphone
  2) Raw:     /Volumes/SV_CACHE/inbox

Outputs (preferred):
  /Volumes/SV_TRANSFER/media_index/media_index.json
  /Volumes/SV_TRANSFER/media_index/media_index.csv
  /Volumes/SV_TRANSFER/media_index/media_index_v3.json
  /Volumes/SV_TRANSFER/media_index/media_index_v3.csv

If SV_TRANSFER is not writable, falls back to:
  ~/StateVerge/logs/media_index/

Never deletes/moves/overwrites media. Index generation is read-only.

Media Intelligence v3 is additive: it is projected from the existing v2 row
plus lightweight ffprobe/file-name/GPS heuristics. Incremental semantic
enrichment is merged fail-open by absolute media path from
media_semantics_sidecars.json and the previous media_index_v3.json, so a scan
can refresh physical metadata without losing AI/semantic fields.
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
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Literal

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

try:
    from media_intel.highlight_heuristics import compute_highlight_segments  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def compute_highlight_segments(_path: Path, **_kwargs: Any) -> list[dict[str, Any]]:  # type: ignore[misc]
        return []


_NYC_AUTO = _SCRIPTS_DIR / "nyc_auto"
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

try:
    from nyc_long_source_policy import channel_allowed_flags, is_valid_nyc_long_source  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def is_valid_nyc_long_source(_p: Any, ffprobe_meta: Any = None, source_info: Any = None) -> dict[str, Any]:  # type: ignore[misc]
        return {
            "long_allowed": True,
            "shorts_allowed": True,
            "reject_reasons": [],
            "source_type": "unknown",
            "orientation": "landscape",
        }

    def channel_allowed_flags(long_a: bool, short_a: bool) -> list[str]:  # type: ignore[misc]
        o = []
        if long_a:
            o.append("long_nyc")
        if short_a:
            o.append("shorts")
        return o

try:
    from storage_paths import get_sv_transfer  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

MediaType = Literal["video", "photo"]
Orientation = Literal["landscape", "portrait", "square", "unknown"]
AspectBucket = Literal["16:9", "9:16", "4:3", "1:1", "other", "unknown"]
SourceType = Literal["driving_fixed", "walking_handheld", "timelapse", "photo", "unknown"]
MotionHint = Literal["fixed", "handheld", "unknown"]
QualityHint = Literal["premium", "raw"]

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".hevc"}
PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".dng"}
MEDIA_INDEX_V3_JSON_NAME = "media_index_v3.json"
MEDIA_INDEX_V3_CSV_NAME = "media_index_v3.csv"
SEMANTIC_SIDECAR_NAME = "media_semantics_sidecars.json"
EMBEDDINGS_DB_NAME = "media_embeddings.sqlite"
V3_SEMANTIC_FIELDS = {
    "scene_type",
    "camera_motion",
    "audio_type",
    "mood",
    "time_of_day",
    "season",
    "quality_score",
    "stability_score",
    "audio_score",
    "usable_for",
    "highlight_segments",
    "embedding_text",
    "ai_summary",
    "search_keywords",
    "weather_hint",
}
V3_FIELDS = [
    "id",
    "path",
    "filename",
    "created_at",
    "duration_sec",
    "fps",
    "resolution",
    "orientation",
    "device",
    "codec",
    "gps_lat",
    "gps_lon",
    "gps_altitude",
    "gps_datetime",
    "borough",
    "neighborhood",
    "landmark",
    "time_of_day",
    "weather_hint",
    "season",
    "scene_type",
    "camera_motion",
    "audio_type",
    "mood",
    "quality_score",
    "stability_score",
    "audio_score",
    "usable_for",
    "highlight_segments",
    "embedding_text",
    "ai_summary",
    "search_keywords",
]


def log(msg: str) -> None:
    print(f"[index_media_library] {msg}", flush=True)


def is_sidecar(p: Path) -> bool:
    return p.name.startswith("._") or p.name == ".DS_Store"


def safe_stat(p: Path) -> tuple[int, float, float] | None:
    try:
        st = p.stat()
        return int(st.st_size), float(getattr(st, "st_ctime", 0.0)), float(st.st_mtime)
    except OSError:
        return None


def ffmpeg_mean_luma_at_seek(path: Path, seek_sec: float, *, timeout_sec: float = 14.0) -> float | None:
    """Single-frame mean luma (0–255) for night/day hint; ``None`` on failure."""
    if shutil.which("ffmpeg") is None:
        return None
    try:
        if not path.is_file():
            return None
    except OSError:
        return None
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        str(max(0.0, float(seek_sec))),
        "-i",
        str(path),
        "-frames:v",
        "1",
        "-vf",
        "scale=48:27,format=gray",
        "-f",
        "rawvideo",
        "-",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout_sec, check=False)
        if r.returncode != 0 or not r.stdout:
            return None
        data = r.stdout
        return float(sum(data)) / float(len(data))
    except Exception:
        return None


def ffprobe_json(path: Path, *, timeout_sec: float = 90.0) -> dict[str, Any] | None:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout_sec, check=False)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def parse_fps(rate: Any) -> float:
    s = str(rate or "").strip()
    if not s or s == "0/0":
        return 0.0
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            den = float(b)
            if den == 0:
                return 0.0
            return round(float(a) / den, 3)
        except (TypeError, ValueError):
            return 0.0
    try:
        return round(float(s), 3)
    except (TypeError, ValueError):
        return 0.0


def sips_dims(path: Path) -> tuple[int, int] | None:
    cmd = ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=20, check=False)
        if r.returncode != 0:
            return None
        w = h = 0
        for line in (r.stdout or "").splitlines():
            if "pixelWidth" in line:
                try:
                    w = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
            if "pixelHeight" in line:
                try:
                    h = int(line.split(":")[-1].strip())
                except ValueError:
                    pass
        if w > 0 and h > 0:
            return w, h
        return None
    except Exception:
        return None


def classify_orientation(w: int, h: int) -> Orientation:
    if w <= 0 or h <= 0:
        return "unknown"
    if w > h:
        return "landscape"
    if h > w:
        return "portrait"
    return "square"


def aspect_ratio_bucket(w: int, h: int) -> tuple[float, AspectBucket]:
    if w <= 0 or h <= 0:
        return 0.0, "unknown"
    r = w / h
    # nearest buckets with tolerance
    def near(val: float, target: float, tol: float = 0.06) -> bool:
        return abs(val - target) <= tol

    if near(r, 16 / 9):
        return r, "16:9"
    if near(r, 9 / 16):
        return r, "9:16"
    if near(r, 4 / 3):
        return r, "4:3"
    if near(r, 1.0, tol=0.04):
        return r, "1:1"
    return r, "other"


_KW_DRIVING = re.compile(
    r"(drive|driving|dashboard|dashcam|dash_cam|car_cam|pov_drive|fixed_cam|road_cam|fdr|bqe|highway|tunnel_entrance)",
    re.I,
)
_KW_WALKING = re.compile(r"(walk|walking|handheld|street_walk|on_foot|tourist|pov_walk|gimbal_walk)", re.I)
# Do not treat generic ``skyline`` filenames as timelapse; that belongs in scene inference.
_KW_TIMELAPSE = re.compile(r"(timelapse|time[\s_-]?lapse|hyperlapse|intervalometer|\btlapse\b|timestack)", re.I)


def likely_source_type(path: Path, *, is_photo: bool) -> SourceType:
    if is_photo:
        return "photo"
    s = str(path).lower()
    if _KW_DRIVING.search(s):
        return "driving_fixed"
    if _KW_WALKING.search(s):
        return "walking_handheld"
    if _KW_TIMELAPSE.search(s):
        return "timelapse"
    return "unknown"


def camera_motion_hint(st: SourceType) -> MotionHint:
    if st == "driving_fixed":
        return "fixed"
    if st == "walking_handheld":
        return "handheld"
    return "unknown"


def _path_lower(path: Path) -> str:
    try:
        return f"{path.name} {path.as_posix()}".lower()
    except Exception:
        return path.name.lower()


def usable_for(path: Path, st: SourceType, ori: Orientation, *, is_photo: bool) -> list[str]:
    """Heuristic roles from source type, orientation, and path tokens (fail-open)."""
    if is_photo:
        return ["thumbnail", "cover", "still_broll"]
    pl = _path_lower(path)
    out: list[str] = []
    if st == "driving_fixed" and ori == "landscape":
        out.extend(["longform", "ambient", "driving", "documentary_broll", "documentary", "transition"])
    elif st == "walking_handheld" and ori == "portrait":
        out.extend(["shorts", "reels", "vertical_broll"])
    elif st == "walking_handheld" and ori == "landscape":
        out.extend(["documentary_broll", "documentary", "travel", "city_walk", "ambient"])
    elif st == "timelapse" and ori == "landscape":
        out.extend(["cinematic", "intro", "transition", "skyline", "timelapse_slice", "establishing_shot", "ambient"])
    elif st == "unknown" and ori == "landscape":
        out.append("archive_broll")

    if any(k in pl for k in ("ferry", "staten_island", "si_ferry", "whitehall", "st_george")):
        out.extend(["ferry_broll", "waterfront", "transit_broll", "ambient", "documentary"])
        if any(k in pl for k in ("skyline", "manhattan", "dumbo", "esb", "empire", "lower_manhattan")):
            out.append("ferry_skyline")
    if any(k in pl for k in ("bridge", "brooklyn_bridge", "manhattan_bridge", "williamsburg")):
        out.extend(["bridge_broll", "skyline_view", "establishing_shot"])
    if "subway" in pl or "metro" in pl or "mta" in pl:
        out.append("transit_broll")
    if any(k in pl for k in ("skyline", "manhattan", "dumbo", "esb", "empire_state")):
        out.extend(["skyline", "establishing_shot", "intro"])
    if "rain" in pl or "wet" in pl or "storm" in pl:
        out.append("weather_broll")
    if "sunset" in pl or "sunrise" in pl or "golden" in pl:
        out.append("golden_hour")
    if st == "driving_fixed" and ("night" in pl or "nocturnal" in pl or "afterdark" in pl):
        out.extend(["night_drive", "ambient"])
    if st == "driving_fixed" and ori == "portrait":
        out.extend(["shorts", "night_drive"])

    dedup: list[str] = []
    seen: set[str] = set()
    for x in out:
        if x and x not in seen:
            dedup.append(x)
            seen.add(x)
    if not dedup and not is_photo:
        dedup = ["archive_broll"]
    return dedup


_TIMELAPSE_MANUAL_TAGS = [
    "cinematic",
    "intro",
    "transition",
    "skyline",
    "documentary_broll",
    "timelapse_slice",
    "establishing_shot",
    "ambient",
]


def load_timelapse_manifest_paths() -> set[str]:
    """Resolved absolute paths listed in timelapse_slice_manifest.json (fail-open)."""
    mp = Path.home() / "StateVerge" / "config" / "timelapse_slice_manifest.json"
    out: set[str] = set()
    if not mp.is_file():
        return out
    try:
        data = json.loads(mp.read_text(encoding="utf-8", errors="replace"))
        for ent in data.get("files") or []:
            if not isinstance(ent, dict):
                continue
            raw = (ent.get("path") or "").strip()
            if not raw:
                continue
            try:
                out.add(str(Path(raw).expanduser().resolve()))
            except OSError:
                out.add(str(Path(raw).expanduser()))
    except OSError:
        pass
    except json.JSONDecodeError:
        pass
    return out


def merge_usable_pipe(existing: str, extra: list[str]) -> str:
    cur = [x for x in (existing or "").split("|") if x]
    seen = set(cur)
    for e in extra:
        if e not in seen:
            cur.append(e)
            seen.add(e)
    return "|".join(cur)


@dataclass(frozen=True)
class LandBBox:
    location_name: str
    borough: str
    neighborhood: str
    nearby_landmark: str
    lat_min: float
    lat_max: float
    lon_min: float
    lon_max: float


# Order: specific regions before broad Midtown (last).
NYC_LANDMARK_BBOXES: tuple[LandBBox, ...] = (
    LandBBox("Times Square", "Manhattan", "Times Square", "Times Square", 40.7550, 40.7615, -73.9900, -73.9820),
    LandBBox("Chinatown Manhattan", "Manhattan", "Chinatown", "Chinatown Manhattan", 40.7130, 40.7225, -74.0050, -73.9920),
    LandBBox("Financial District", "Manhattan", "Financial District", "Financial District", 40.7000, 40.7135, -74.0150, -73.9980),
    LandBBox("Hudson Yards", "Manhattan", "Hudson Yards", "Hudson Yards", 40.7500, 40.7585, -74.0060, -73.9950),
    LandBBox("Central Park", "Manhattan", "Central Park", "Central Park", 40.7640, 40.8000, -73.9810, -73.9490),
    LandBBox("Brooklyn Bridge / DUMBO", "Brooklyn", "DUMBO", "Brooklyn Bridge / DUMBO", 40.7000, 40.7085, -73.9980, -73.9850),
    LandBBox("Midtown Manhattan", "Manhattan", "Midtown", "Midtown Manhattan", 40.7480, 40.7650, -73.9950, -73.9700),
)


def has_exiftool() -> bool:
    return shutil.which("exiftool") is not None


def _float_or_none(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_exiftool_tags(path: Path) -> dict[str, Any]:
    exe = shutil.which("exiftool")
    if not exe:
        return {}
    cmd = [
        exe,
        "-j",
        "-n",
        "-GPSLatitude",
        "-GPSLongitude",
        "-GPSAltitude",
        "-GPSDateTime",
        "-GPSCoordinates",
        "-GPSPosition",
        "-Location",
        "-Composite:GPSLatitude",
        "-Composite:GPSLongitude",
        "-Composite:GPSAltitude",
        "-Keys:GPSCoordinates",
        "-UserData:GPSCoordinates",
        "-QuickTime:GPSCoordinates",
        "-CreateDate",
        "-CreationDate",
        "-Make",
        "-Model",
        "-DeviceModelName",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, text=True, capture_output=True, timeout=45, check=False)
        if r.returncode != 0 or not (r.stdout or "").strip():
            return {}
        arr = json.loads(r.stdout)
        if not arr or not isinstance(arr, list):
            return {}
        row = arr[0]
        return row if isinstance(row, dict) else {}
    except Exception:
        return {}


_ISO6709_HEAD = re.compile(r"^([+-]?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)")
# ISO 6709 Annex H compressed form: sign-delimited lat, lon, optional alt, optional trailing slash.
_QT_ISO6709_FULL = re.compile(
    r"^([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?/?$"
)
# exiftool -n style "lat lon [alt]" (three floats) or "lat lon"
_SPACE_COORDS = re.compile(
    r"^\s*([-+]?\d+(?:\.\d+)?)\s+([-+]?\d+(?:\.\d+)?)(?:\s+([-+]?\d+(?:\.\d+)?))?\s*$"
)
_GPS_HINT = re.compile(
    r"(?:^|[_:./])(?:gps|location|iso6709|coordinates|position)(?:$|[_:./])",
    re.I,
)


def parse_quicktime_gps(s: str) -> tuple[float | None, float | None]:
    """Parse QuickTime / Apple ISO6709 location strings (fail-open).

    Examples: ``+40.7247-073.9876+000.000/``, ``+40.7989-073.9601+022.711/``.
    """
    if not isinstance(s, str):
        return None, None
    t = s.strip().replace(" ", "")
    if not t:
        return None, None
    try:
        m = _QT_ISO6709_FULL.match(t)
        if m:
            return float(m.group(1)), float(m.group(2))
        m2 = _ISO6709_HEAD.match(t)
        if m2:
            return float(m2.group(1)), float(m2.group(2))
    except (ValueError, TypeError):
        return None, None
    return None, None


def parse_iso6709_location(s: str) -> tuple[float | None, float | None]:
    return _parse_exiftool_coord_strings(s)


def _exiftool_key_basename(key: str) -> str:
    s = str(key)
    return s.split(":", 1)[-1] if ":" in s else s


def _exiftool_numeric_lat_lon(row: dict[str, Any]) -> tuple[float | None, float | None]:
    lat = lon = None
    for k, v in row.items():
        if str(k) == "SourceFile":
            continue
        base = _exiftool_key_basename(k)
        lb = base.lower()
        if lb == "gpslatitude":
            lat = lat if lat is not None else _float_or_none(v)
        elif lb == "gpslongitude":
            lon = lon if lon is not None else _float_or_none(v)
    return lat, lon


def _parse_exiftool_coord_strings(val: Any) -> tuple[float | None, float | None]:
    if val is None:
        return None, None
    s = str(val).strip()
    if not s:
        return None, None
    m = _SPACE_COORDS.match(s)
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            return None, None
    compact = s.replace(" ", "")
    la, lo = parse_quicktime_gps(compact)
    if la is not None and lo is not None:
        return la, lo
    m2 = _ISO6709_HEAD.match(compact)
    if not m2:
        return None, None
    try:
        return float(m2.group(1)), float(m2.group(2))
    except ValueError:
        return None, None


def _gps_from_exiftool_row(row: dict[str, Any]) -> tuple[float | None, float | None, float | None]:
    """Prefer numeric GPSLatitude/GPSLongitude; else parse ISO6709 / space-separated strings from any tag."""
    lat, lon = _exiftool_numeric_lat_lon(row)
    alt: float | None = None
    for k, v in row.items():
        if str(k) == "SourceFile":
            continue
        base = _exiftool_key_basename(k)
        lb = base.lower()
        if lb == "gpsaltitude":
            alt = alt if alt is not None else _float_or_none(v)
    if lat is not None and lon is not None:
        return lat, lon, alt
    for k, v in row.items():
        if str(k) == "SourceFile":
            continue
        base = _exiftool_key_basename(k)
        lk = f"{k} {base}".lower()
        if not (
            "gpscoordinates" in lk
            or "gpsposition" in lk
            or "iso6709" in lk
            or lk == "location"
            or ("location" in lk and "coordinates" in lk)
        ):
            continue
        la, lo = _parse_exiftool_coord_strings(v)
        if la is not None and lo is not None:
            return la, lo, alt
    return lat, lon, alt


def _ffprobe_tag_value_iter(data: dict[str, Any]) -> Iterable[tuple[str, str]]:
    fmt = data.get("format") or {}
    tags = fmt.get("tags") or {}
    if isinstance(tags, dict):
        for k, v in tags.items():
            yield str(k), str(v) if v is not None else ""
    for s in data.get("streams") or []:
        st = s.get("tags") or {}
        if isinstance(st, dict):
            for k, v in st.items():
                yield str(k), str(v) if v is not None else ""


def _merge_ffprobe_tags(data: dict[str, Any]) -> dict[str, Any]:
    """Format tags first; fill missing keys from stream tag dicts (read-only helper)."""
    fmt = data.get("format") or {}
    fmt_tags = fmt.get("tags") or {}
    if not isinstance(fmt_tags, dict):
        fmt_tags = {}
    merged: dict[str, Any] = dict(fmt_tags)
    for s in data.get("streams") or []:
        st = s.get("tags") or {}
        if not isinstance(st, dict):
            continue
        for k, v in st.items():
            merged.setdefault(k, v)
    return merged


def ffprobe_gps_and_tags(path: Path) -> tuple[float | None, float | None, dict[str, Any]]:
    data = ffprobe_json(path)
    if not data:
        return None, None, {}
    merged = _merge_ffprobe_tags(data)
    lat = lon = None
    for k, v in _ffprobe_tag_value_iter(data):
        lk = k.lower()
        val = str(v) if v is not None else ""
        if not val.strip():
            continue
        hit = (
            "iso6709" in lk
            or "gpscoordinates" in lk
            or lk.endswith(".location")
            or "location" in lk
            or "coordinates" in lk
            or "gpsposition" in lk
            or lk == "location"
        )
        if hit or _GPS_HINT.search(lk):
            la, lo = _parse_exiftool_coord_strings(val)
            if la is not None and lo is not None:
                lat, lon = la, lo
                break
    return lat, lon, merged


def extract_media_metadata(path: Path) -> dict[str, Any]:
    lat = lon = alt = None
    gps_dt = ""
    make = model = device = created_orig = ""
    meta_src = "none"

    if has_exiftool():
        ex = read_exiftool_tags(path)
        if ex:
            meta_src = "exiftool"
            elat, elon, ealt = _gps_from_exiftool_row(ex)
            lat, lon, alt = elat, elon, ealt
            if lat is None:
                lat = _float_or_none(ex.get("GPSLatitude"))
            if lon is None:
                lon = _float_or_none(ex.get("GPSLongitude"))
            if alt is None:
                alt = _float_or_none(ex.get("GPSAltitude"))
            gdt = ex.get("GPSDateTime")
            if gdt is not None:
                gps_dt = str(gdt)
            make = str(ex.get("Make") or "").strip()
            model = str(ex.get("Model") or "").strip()
            device = str(ex.get("DeviceModelName") or "").strip()
            cd = ex.get("CreateDate") or ex.get("CreationDate")
            if cd is not None:
                created_orig = str(cd).strip()

    fla, flo, fptags = ffprobe_gps_and_tags(path)
    if lat is None:
        lat = fla
    if lon is None:
        lon = flo
    if lat is not None and lon is not None and meta_src == "none":
        meta_src = "ffprobe"
    if not created_orig and fptags:
        ct = fptags.get("creation_time") or fptags.get("com.apple.quicktime.creationdate")
        if ct:
            created_orig = str(ct).strip()

    return {
        "lat": lat,
        "lon": lon,
        "alt": alt,
        "gps_datetime": gps_dt,
        "camera_make": make,
        "camera_model": model,
        "device_model": device,
        "created_time_original": created_orig,
        "meta_source": meta_src,
    }


def classify_gps_bbox(lat: float, lon: float) -> tuple[str, str, str, str]:
    """location_name, borough, neighborhood, nearby_landmark."""
    for box in NYC_LANDMARK_BBOXES:
        if box.lat_min <= lat <= box.lat_max and box.lon_min <= lon <= box.lon_max:
            return box.location_name, box.borough, box.neighborhood, box.nearby_landmark
    return "", "", "", ""


_FILENAME_LOC_RULES: tuple[tuple[re.Pattern[str], str, str, str, str], ...] = (
    (
        re.compile(r"times square|timessquare|时代广场|\b42nd\b|broadway", re.I),
        "Times Square",
        "Manhattan",
        "Times Square",
        "Times Square",
    ),
    (re.compile(r"chinatown|\bcanal\b|\bmott\b|\bbowery\b", re.I), "Chinatown Manhattan", "Manhattan", "Chinatown", "Chinatown Manhattan"),
    (re.compile(r"wall street|\bfidi\b|financial|nyse", re.I), "Financial District", "Manhattan", "Financial District", "Financial District"),
    (re.compile(r"central park", re.I), "Central Park", "Manhattan", "Central Park", "Central Park"),
    (re.compile(r"hudson yards|\bvessel\b", re.I), "Hudson Yards", "Manhattan", "Hudson Yards", "Hudson Yards"),
)


def location_from_filename(path: Path) -> tuple[str, str, str, str]:
    """location_name, borough, neighborhood, nearby_landmark."""
    s = f"{path.name} {path.as_posix()}"
    for pat, ln, bo, nh, lm in _FILENAME_LOC_RULES:
        if pat.search(s):
            return ln, bo, nh, lm
    return "", "", "", ""


def resolve_gps_location_fields(path: Path, meta: dict[str, Any]) -> dict[str, Any]:
    lat: float | None = meta.get("lat")  # type: ignore[assignment]
    lon: float | None = meta.get("lon")  # type: ignore[assignment]
    alt = meta.get("alt")
    gps_dt = str(meta.get("gps_datetime") or "")
    make = str(meta.get("camera_make") or "")
    model = str(meta.get("camera_model") or "")
    device = str(meta.get("device_model") or "")
    created_orig = str(meta.get("created_time_original") or "")
    meta_src = str(meta.get("meta_source") or "none")

    loc_name = bor = neigh = near_lm = ""
    conf = ""
    loc_src = "none"

    if lat is not None and lon is not None:
        loc_name, bor, neigh, near_lm = classify_gps_bbox(float(lat), float(lon))
        if loc_name:
            conf = "bbox"
            loc_src = "gps_bbox"
        else:
            loc_src = meta_src if meta_src in ("exiftool", "ffprobe") else "exiftool"
            conf = ""
    else:
        loc_name, bor, neigh, near_lm = location_from_filename(path)
        if loc_name:
            conf = "filename_hint"
            loc_src = "filename"
        else:
            loc_src = "none"

    return {
        "gps_lat": lat,
        "gps_lon": lon,
        "gps_altitude": alt if alt is not None else None,
        "gps_datetime": gps_dt,
        "camera_make": make,
        "camera_model": model,
        "device_model": device,
        "created_time_original": created_orig,
        "location_name": loc_name,
        "borough": bor,
        "neighborhood": neigh,
        "nearby_landmark": near_lm,
        "location_confidence": conf,
        "location_source": loc_src,
    }


_LANDMARK_NAMES: tuple[str, ...] = (
    "Times Square",
    "Midtown Manhattan",
    "Chinatown Manhattan",
    "Financial District",
    "Central Park",
    "Hudson Yards",
    "Brooklyn Bridge / DUMBO",
)


def compute_location_stats(rows: list["IndexRow"]) -> dict[str, Any]:
    st: dict[str, Any] = {
        "with_gps": 0,
        "without_gps": 0,
        "times_square": 0,
        "midtown_manhattan": 0,
        "chinatown_manhattan": 0,
        "financial_district": 0,
        "central_park": 0,
        "hudson_yards": 0,
        "brooklyn_bridge_dumbo": 0,
        "orientation_by_location": {lm: {"landscape": 0, "portrait": 0, "square": 0} for lm in _LANDMARK_NAMES},
    }
    ori_map: dict[str, dict[str, int]] = st["orientation_by_location"]
    for r in rows:
        if r.gps_lat is not None and r.gps_lon is not None:
            st["with_gps"] += 1
        else:
            st["without_gps"] += 1
        key = (r.location_name or "").strip()
        if key == "Times Square":
            st["times_square"] += 1
        elif key == "Midtown Manhattan":
            st["midtown_manhattan"] += 1
        elif key == "Chinatown Manhattan":
            st["chinatown_manhattan"] += 1
        elif key == "Financial District":
            st["financial_district"] += 1
        elif key == "Central Park":
            st["central_park"] += 1
        elif key == "Hudson Yards":
            st["hudson_yards"] += 1
        elif key == "Brooklyn Bridge / DUMBO":
            st["brooklyn_bridge_dumbo"] += 1
        if key in ori_map:
            o = (r.orientation or "").strip()
            if o in ("landscape", "portrait", "square"):
                ori_map[key][o] += 1
    return st


def csv_row_dict(r: IndexRow) -> dict[str, Any]:
    d = asdict(r)
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None:
            out[k] = ""
        else:
            out[k] = v
    return out


def _path_id(path: Path) -> str:
    try:
        raw = str(path.resolve())
    except OSError:
        raw = str(path)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()[:20]


def _iso_from_ts(ts: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
    except Exception:
        return ""


def _created_at(row: "IndexRow") -> str:
    return (row.created_time_original or row.gps_datetime or _iso_from_ts(row.created_time)).strip()


def _parse_created_datetime(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    iso = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(iso)
    except Exception:
        pass
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def _hour_from_created(row: "IndexRow") -> int | None:
    dt = _parse_created_datetime(_created_at(row))
    if dt is not None:
        return dt.hour
    try:
        return datetime.fromtimestamp(float(row.created_time)).hour
    except Exception:
        return None


def _hour_from_mtime(row: "IndexRow") -> int | None:
    try:
        return datetime.fromtimestamp(float(row.modified_time)).hour
    except Exception:
        return None


def _month_from_created(row: "IndexRow") -> int | None:
    dt = _parse_created_datetime(_created_at(row))
    if dt is not None:
        return dt.month
    try:
        return datetime.fromtimestamp(float(row.created_time)).month
    except Exception:
        return None


def classify_time_of_day(row: "IndexRow") -> str:
    h = _hour_from_created(row)
    if h is None:
        h = _hour_from_mtime(row)
    if h is None:
        return "day"
    if 5 <= h < 10:
        return "morning"
    if 10 <= h < 17:
        return "day"
    if 17 <= h < 21:
        return "evening"
    return "night"


def classify_season(row: "IndexRow") -> str:
    m = _month_from_created(row)
    if m in (12, 1, 2):
        return "winter"
    if m in (3, 4, 5):
        return "spring"
    if m in (6, 7, 8):
        return "summer"
    if m in (9, 10, 11):
        return "fall"
    return ""


def weather_hint_from_name(path: Path) -> str:
    s = path.as_posix().lower()
    for key, label in (
        ("rain", "rain"),
        ("snow", "snow"),
        ("fog", "fog"),
        ("mist", "fog"),
        ("cloud", "cloudy"),
        ("sunny", "sunny"),
        ("sunset", "sunset"),
        ("night", "night"),
    ):
        if key in s:
            return label
    return ""


def split_pipe(s: str) -> list[str]:
    return [x for x in (s or "").split("|") if x]


def _tokens_from_path(path: Path) -> list[str]:
    raw = re.sub(r"[^A-Za-z0-9]+", " ", path.stem).lower()
    return [x for x in raw.split() if len(x) >= 3][:16]


_LOCATION_SCENE_TAGS = frozenset({"landmark", "manhattan"})


def infer_scene_type(row: "IndexRow", path: Path) -> list[str]:
    """Lightweight scene labels from source type, GPS fields, and path tokens."""
    out: list[str] = []
    st = row.likely_source_type
    pl = _path_lower(path)
    bor = (row.borough or "").lower()
    if row.media_type == "photo":
        out.append("still_photo")
    if st == "driving_fixed":
        out.extend(["city_street", "driving", "traffic"])
    elif st == "walking_handheld":
        out.extend(["city_street", "walking", "street"])
    elif st == "timelapse" or row.is_manual_timelapse:
        out.append("timelapse")
        if any(x in pl for x in ("skyline", "dumbo", "esb", "empire", "manhattan", "sunset", "sunrise")):
            out.append("skyline")
    elif st == "unknown" and row.media_type == "video":
        if any(x in pl for x in ("traffic", "highway", "fdr", "bqe", "gridlock", "congestion", "tunnel")):
            out.extend(["city_street", "traffic"])

    if row.nearby_landmark or row.location_name:
        out.append("landmark")
    if "manhattan" in bor or "manhattan" in pl:
        out.append("manhattan")
    if any(x in pl for x in ("ferry", "staten_island", "si_ferry", "whitehall", "st_george", "staten")):
        out.extend(["ferry", "waterfront"])
    if any(x in pl for x in ("bridge", "brooklyn_bridge", "manhattan_bridge", "williamsburg", "verrazano", "verrazzano", "rfk", "triboro", "queensboro")):
        out.extend(["bridge_traffic", "skyline_view"])
    if "subway" in pl or "metro" in pl or "mta" in pl:
        out.append("subway")
    if any(x in pl for x in ("skyline", "dumbo", "brooklyn_bridge", "manhattan_bridge", "esb", "empire")):
        out.extend(["skyline_view", "skyline"])
    if "central_park" in pl or "central park" in pl:
        out.append("park")

    night_token = any(x in pl for x in ("night", "nocturnal", "afterdark", "latenight", "midnight"))
    if night_token:
        if st == "driving_fixed":
            out.append("night_driving")
        else:
            out.append("night_city")

    if row.media_type == "video":
        meaningful = [x for x in out if x not in _LOCATION_SCENE_TAGS]
        if not meaningful:
            out.append("city_broll")
    return list(dict.fromkeys(out))


_MOOD_NON_GENERIC = frozenset(
    {
        "calm",
        "water",
        "chaotic",
        "moody",
        "nocturnal",
        "low_key",
        "bright_day",
        "golden_hour",
        "street_level",
        "cinematic",
        "night_drive",
    }
)


def infer_mood(row: "IndexRow", time_of_day: str, path: Path, mean_luma: float | None) -> list[str]:
    out: list[str] = []
    pl = _path_lower(path)
    st = row.likely_source_type
    if time_of_day in ("night", "evening", "morning"):
        out.append(time_of_day)
    if any(x in pl for x in ("ferry", "staten_island", "si_ferry", "whitehall", "st_george")):
        out.extend(["calm", "water"])
    if st == "driving_fixed" and time_of_day == "night":
        out.append("night_drive")
    if (
        row.orientation == "landscape"
        and row.quality_hint == "premium"
        and (
            st in ("driving_fixed", "timelapse")
            or row.is_manual_timelapse
            or any(k in pl for k in ("skyline", "cinematic", "aerial", "golden", "sunset", "sunrise"))
        )
    ):
        out.append("cinematic")
    if st == "walking_handheld":
        out.append("street_level")
    if any(x in pl for x in ("calm", "quiet", "empty")):
        out.append("calm")
    if any(x in pl for x in ("chaos", "crowd", "busy", "rush")):
        out.append("chaotic")
    if "rain" in pl or "wet" in pl:
        out.append("moody")
    if mean_luma is not None:
        if mean_luma < 48.0:
            out.extend(["nocturnal", "low_key"])
        elif mean_luma > 135.0 and time_of_day in ("evening", "day"):
            out.append("bright_day")
        if 90.0 < mean_luma < 165.0 and time_of_day == "evening":
            out.append("golden_hour")
    if not _MOOD_NON_GENERIC.intersection(out):
        out.append("urban")
    return list(dict.fromkeys(out))


def infer_audio_type(row: "IndexRow", path: Path, has_audio: bool, time_of_day: str) -> list[str]:
    pl = _path_lower(path)
    if row.media_type == "photo":
        return []
    if not has_audio:
        return ["silent"]
    tags: list[str] = ["original_sound"]
    if row.likely_source_type == "driving_fixed" or "traffic" in pl or "highway" in pl or "fdr" in pl:
        tags.append("traffic_noise")
    if row.likely_source_type == "walking_handheld" and row.orientation == "portrait":
        tags.append("windy")
    if time_of_day == "night" and row.likely_source_type == "driving_fixed":
        tags.append("quiet_ambient")
    if "music" in pl or "dj" in pl:
        tags.append("music")
    return list(dict.fromkeys(tags))


def refine_camera_motion(row: "IndexRow", path: Path, fps: float) -> str:
    st = row.likely_source_type
    pl = _path_lower(path)
    if st == "timelapse" or row.is_manual_timelapse:
        return "tripod_timelapse"
    if st == "driving_fixed":
        return "dash_fixed"
    if st == "walking_handheld":
        if "gimbal" in pl or "smooth" in pl:
            return "gimbal_smooth"
        return "handheld_walk"
    if fps >= 50.0 and row.duration < 90:
        return "high_fps_broll"
    hint = row.camera_motion_hint
    if hint == "fixed":
        return "locked_off"
    if hint == "handheld":
        return "handheld"
    return "unknown"


def numeric_score(v: float) -> float:
    return round(max(0.0, min(1.0, float(v))), 3)


def quality_score_for(row: "IndexRow") -> float:
    score = 0.35
    if row.quality_hint == "premium":
        score += 0.25
    if row.width >= 3840 or row.height >= 2160:
        score += 0.2
    elif row.width >= 1920 or row.height >= 1080:
        score += 0.12
    if row.duration >= 10 or row.is_photo:
        score += 0.08
    if row.gps_lat is not None and row.gps_lon is not None:
        score += 0.05
    else:
        score -= 0.04
    fp = (row.file_path or "").replace("\\", "/")
    if "._" in fp or "/._" in fp:
        score -= 0.12
    if row.is_video:
        if row.duration <= 0:
            score -= 0.15
        elif row.duration < 1.5:
            score -= 0.08
        elif row.duration < 3.0:
            score -= 0.03
    if row.likely_source_type == "driving_fixed" and row.orientation == "portrait":
        score -= 0.06
    return numeric_score(score)


def stability_score_for(row: "IndexRow") -> float:
    if row.camera_motion_hint == "fixed":
        return 0.82
    if row.camera_motion_hint == "handheld":
        return 0.45
    if row.likely_source_type == "timelapse":
        return 0.78
    return 0.5


def audio_score_for(has_audio: bool, row: "IndexRow") -> float:
    if not row.is_video:
        return 0.0
    return 0.62 if has_audio else 0.15


def heuristic_highlight_segments(path: Path, row: "IndexRow", *, fps: float) -> list[dict[str, Any]]:
    """Real timestamps via sparse ffmpeg decode; empty on failure (no fake mid-clip windows).

    Set ``STATEVERGE_SKIP_HIGHLIGHT_SCAN=1`` to skip decoding during very large bulk indexes
    (highlights stay ``[]`` until a run without that env).
    """
    if (os.environ.get("STATEVERGE_SKIP_HIGHLIGHT_SCAN") or "").strip() == "1":
        return []
    if not row.is_video or row.duration <= 0:
        return []
    try:
        return compute_highlight_segments(
            path,
            duration=float(row.duration),
            width=int(row.width),
            height=int(row.height),
            fps=float(fps or 0.0),
            orientation=str(row.orientation or "unknown"),
            likely_source_type=str(row.likely_source_type or "unknown"),
        )
    except Exception:
        return []


def build_embedding_text(row: "IndexRow", path: Path, scene_type: list[str], mood: list[str], time_of_day: str) -> str:
    bits = [
        path.stem.replace("_", " "),
        row.media_type,
        row.orientation,
        row.likely_source_type,
        row.borough,
        row.neighborhood,
        row.nearby_landmark,
        time_of_day,
        " ".join(scene_type),
        " ".join(mood),
    ]
    return " ".join(x for x in bits if x).strip()


def search_keywords_for(row: "IndexRow", path: Path, scene_type: list[str], mood: list[str], time_of_day: str) -> list[str]:
    vals = _tokens_from_path(path) + [
        row.media_type,
        row.orientation,
        row.likely_source_type,
        row.borough,
        row.neighborhood,
        row.nearby_landmark,
        row.location_name,
        time_of_day,
    ]
    vals.extend(scene_type)
    vals.extend(mood)
    out: list[str] = []
    for v in vals:
        s = str(v or "").strip().lower()
        if s and s not in out:
            out.append(s)
    return out


def load_v3_semantic_overrides(outdir: Path) -> dict[str, dict[str, Any]]:
    """Load prior/sidecar semantic fields keyed by media path; sidecars win over prior v3."""
    merged: dict[str, dict[str, Any]] = {}
    prior = outdir / MEDIA_INDEX_V3_JSON_NAME
    if prior.is_file():
        try:
            data = json.loads(prior.read_text(encoding="utf-8", errors="replace"))
            for it in data.get("items") or []:
                if not isinstance(it, dict):
                    continue
                p = str(it.get("path") or "")
                if not p:
                    continue
                merged[p] = {k: it[k] for k in V3_SEMANTIC_FIELDS if k in it}
        except Exception:
            pass
    sidecar = outdir / SEMANTIC_SIDECAR_NAME
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8", errors="replace"))
            raw_items = data.get("items") if isinstance(data, dict) else None
            if isinstance(raw_items, dict):
                iterable = raw_items.items()
            elif isinstance(raw_items, list):
                iterable = ((str(x.get("path") or ""), x) for x in raw_items if isinstance(x, dict))
            else:
                iterable = []
            for p, vals in iterable:
                if not p or not isinstance(vals, dict):
                    continue
                cur = dict(merged.get(p, {}))
                cur.update({k: vals[k] for k in V3_SEMANTIC_FIELDS if k in vals})
                merged[p] = cur
        except Exception:
            pass
    return merged


def build_v3_entry(row: "IndexRow", path: Path, *, fps: float, codec: str, has_audio: bool, overrides: dict[str, Any]) -> dict[str, Any]:
    mean_luma: float | None = None
    if row.is_video and row.duration > 0.2:
        try:
            seek = min(max(row.duration * 0.08, 0.5), max(row.duration - 0.5, 0.0))
            mean_luma = ffmpeg_mean_luma_at_seek(path, seek)
        except Exception:
            mean_luma = None
    tod = classify_time_of_day(row)
    if mean_luma is not None and mean_luma < 42.0 and tod in ("day", "evening", "morning"):
        tod = "night"
    season = classify_season(row)
    weather = weather_hint_from_name(path)
    scene_type = infer_scene_type(row, path)
    mood = infer_mood(row, tod, path, mean_luma)
    usable = split_pipe(row.usable_for)
    audio_type = infer_audio_type(row, path, has_audio, tod)
    highlights = heuristic_highlight_segments(path, row, fps=fps)
    cam_motion = refine_camera_motion(row, path, fps)
    embedding_text = build_embedding_text(row, path, scene_type, mood, tod)
    keywords = search_keywords_for(row, path, scene_type, mood, tod)
    ai_summary = " ".join(
        x
        for x in [
            row.orientation,
            row.likely_source_type.replace("_", " "),
            row.nearby_landmark or row.neighborhood or row.borough,
            tod,
            cam_motion,
        ]
        if x
    ).strip()
    base: dict[str, Any] = {
        "id": _path_id(path),
        "path": row.file_path,
        "filename": row.file_name,
        "created_at": _created_at(row),
        "duration_sec": round(float(row.duration), 3),
        "fps": float(fps or 0.0),
        "resolution": f"{row.width}x{row.height}" if row.width and row.height else "",
        "orientation": row.orientation,
        "device": row.device_model or row.camera_model or row.camera_make,
        "codec": codec,
        "gps_lat": row.gps_lat,
        "gps_lon": row.gps_lon,
        "gps_altitude": row.gps_altitude,
        "gps_datetime": row.gps_datetime,
        "borough": row.borough,
        "neighborhood": row.neighborhood,
        "landmark": row.nearby_landmark or row.location_name,
        "time_of_day": tod,
        "weather_hint": weather,
        "season": season,
        "scene_type": scene_type,
        "camera_motion": cam_motion,
        "audio_type": audio_type,
        "mood": mood,
        "quality_score": quality_score_for(row),
        "stability_score": stability_score_for(row),
        "audio_score": audio_score_for(has_audio, row),
        "usable_for": usable,
        "highlight_segments": highlights,
        "embedding_text": embedding_text,
        "ai_summary": ai_summary,
        "search_keywords": keywords,
    }
    for k, v in overrides.items():
        if k in V3_SEMANTIC_FIELDS:
            base[k] = v
    return base


def v3_csv_row_dict(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in V3_FIELDS:
        v = row.get(k)
        if v is None:
            out[k] = ""
        elif isinstance(v, (list, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        else:
            out[k] = v
    return out


@dataclass
class IndexRow:
    source_root: str
    file_path: str
    file_name: str
    media_type: str
    extension: str
    width: int
    height: int
    duration: float
    orientation: str
    aspect_ratio: float
    aspect_ratio_bucket: str
    is_landscape: bool
    is_portrait: bool
    is_square: bool
    is_photo: bool
    is_video: bool
    file_size: int
    created_time: float
    modified_time: float
    likely_source_type: str
    camera_motion_hint: str
    quality_hint: str
    usable_for: str  # pipe-separated
    allow_full_duration_single_use: bool
    require_slice_segments: bool
    max_segment_seconds: int
    is_manual_timelapse: bool
    long_allowed: bool
    shorts_allowed: bool
    long_reject_reasons: str
    channel_allowed: str
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_altitude: float | None = None
    gps_datetime: str = ""
    camera_make: str = ""
    camera_model: str = ""
    device_model: str = ""
    created_time_original: str = ""
    location_name: str = ""
    borough: str = ""
    neighborhood: str = ""
    nearby_landmark: str = ""
    location_confidence: str = ""
    location_source: str = ""


def index_row_from_dict(d: dict[str, Any]) -> IndexRow:
    """Rehydrate ``IndexRow`` from a prior ``media_index.json`` item (same field names)."""
    fn = {f.name for f in fields(IndexRow)}
    return IndexRow(**{k: d[k] for k in fn if k in d})


def iter_media_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return []
    try:
        for p in root.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if is_sidecar(p):
                continue
            suf = p.suffix.lower()
            if suf in VIDEO_EXTS or suf in PHOTO_EXTS:
                yield p
    except OSError:
        return []


def probe_video_details(path: Path) -> dict[str, Any]:
    data = ffprobe_json(path)
    if not data:
        return {"width": 0, "height": 0, "duration": 0.0, "has_video": False, "has_audio": False, "fps": 0.0, "codec": "", "display_aspect_ratio": "", "sample_aspect_ratio": ""}
    fmt = data.get("format") or {}
    try:
        dur = float(fmt.get("duration") or 0.0) or 0.0
    except (TypeError, ValueError):
        dur = 0.0
    hv = ha = False
    w = h = 0
    fps = 0.0
    codec = ""
    dar = sar = ""
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video" and not hv:
            hv = True
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
            fps = parse_fps(s.get("avg_frame_rate") or s.get("r_frame_rate"))
            codec = str(s.get("codec_name") or "")
            dar = str(s.get("display_aspect_ratio") or "").strip()
            sar = str(s.get("sample_aspect_ratio") or "").strip()
        if s.get("codec_type") == "audio":
            ha = True
    return {
        "width": w,
        "height": h,
        "duration": float(dur),
        "has_video": hv,
        "has_audio": ha,
        "fps": fps,
        "codec": codec,
        "display_aspect_ratio": dar,
        "sample_aspect_ratio": sar,
    }


def probe_video(path: Path) -> tuple[int, int, float, bool, bool]:
    d = probe_video_details(path)
    return int(d["width"]), int(d["height"]), float(d["duration"]), bool(d["has_video"]), bool(d["has_audio"])


def probe_photo(path: Path) -> tuple[int, int]:
    dims = sips_dims(path)
    if dims:
        return dims
    data = ffprobe_json(path)
    if data:
        for s in data.get("streams") or []:
            if s.get("codec_type") == "video":
                w = int(s.get("width") or 0)
                h = int(s.get("height") or 0)
                if w > 0 and h > 0:
                    return w, h
    return 0, 0


def resolve_output_dir() -> tuple[Path, bool]:
    preferred = get_sv_transfer(verbose=False) / "media_index"
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        if os.access(preferred, os.W_OK):
            return preferred, False
    except OSError:
        pass
    fallback = Path.home() / "StateVerge" / "logs" / "media_index"
    try:
        fallback.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return fallback, True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--incremental",
        action="store_true",
        help="Reuse GPS/device metadata from prior media_index.json when size+mtime unchanged (use --refresh-gps to re-run exiftool/ffprobe once after GPS parser changes)",
    )
    ap.add_argument(
        "--refresh-gps",
        action="store_true",
        help="Always run exiftool/ffprobe metadata extraction for GPS even when --incremental would otherwise skip it",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        log("verbose enabled")
    log(f"exiftool_available={has_exiftool()}")

    premium_root = get_sv_transfer(verbose=False) / "00_INBOX" / "iphone"
    raw_root = Path("/Volumes/SV_CACHE/inbox")
    roots: list[tuple[str, Path, QualityHint]] = [
        ("premium", premium_root, "premium"),
        ("raw", raw_root, "raw"),
    ]

    outdir, used_fallback = resolve_output_dir()
    out_json = outdir / "media_index.json"
    out_csv = outdir / "media_index.csv"
    out_json_v3 = outdir / MEDIA_INDEX_V3_JSON_NAME
    out_csv_v3 = outdir / MEDIA_INDEX_V3_CSV_NAME

    log(f"premium_source={premium_root} online={premium_root.is_dir()}")
    log(f"raw_source={raw_root} online={raw_root.is_dir()}")
    log(f"outdir={outdir} fallback={used_fallback}")
    log(f"dry_run={args.dry_run} incremental={args.incremental} refresh_gps={args.refresh_gps}")

    prior_by_path: dict[str, dict[str, Any]] = {}
    if args.incremental and out_json.is_file():
        try:
            prev_pl = json.loads(out_json.read_text(encoding="utf-8", errors="replace"))
            for it in prev_pl.get("items") or []:
                if isinstance(it, dict) and it.get("file_path"):
                    prior_by_path[str(it["file_path"])] = it
        except Exception:
            pass
        if args.verbose:
            log(f"incremental prior index rows={len(prior_by_path)}")

    manifest_paths = load_timelapse_manifest_paths()
    if manifest_paths and args.verbose:
        log(f"timelapse_manifest entries={len(manifest_paths)}")

    semantic_overrides = load_v3_semantic_overrides(outdir)
    rows: list[IndexRow] = []
    v3_rows: list[dict[str, Any]] = []
    stats = {"total": 0, "video": 0, "photo": 0, "landscape": 0, "portrait": 0, "square": 0, "unknown": 0}

    for _label, root, root_quality in roots:
        for p in iter_media_files(root):
            st = safe_stat(p)
            if st is None:
                continue
            size, ctime, mtime = st
            suf = p.suffix.lower()
            is_video = suf in VIDEO_EXTS
            is_photo = suf in PHOTO_EXTS and not is_video
            mtype: MediaType = "video" if is_video else "photo"

            prev_item = prior_by_path.get(str(p))
            skip_meta = (
                args.incremental
                and isinstance(prev_item, dict)
                and int(prev_item.get("file_size") or -1) == size
                and abs(float(prev_item.get("modified_time") or -1.0) - float(mtime)) < 0.01
            )
            cached: IndexRow | None = None
            if skip_meta:
                try:
                    cached = index_row_from_dict(prev_item)
                except Exception:
                    skip_meta = False
                    cached = None

            w = h = 0
            dur = 0.0
            hv = ha = False
            fps = 0.0
            codec = ""
            if is_video:
                vd = probe_video_details(p)
                w = int(vd.get("width") or 0)
                h = int(vd.get("height") or 0)
                dur = float(vd.get("duration") or 0.0)
                hv = bool(vd.get("has_video"))
                ha = bool(vd.get("has_audio"))
                fps = float(vd.get("fps") or 0.0)
                codec = str(vd.get("codec") or "")
            else:
                w, h = probe_photo(p)
                codec = suf.lstrip(".")

            ori = classify_orientation(w, h)
            ar, bucket = aspect_ratio_bucket(w, h)
            stype = likely_source_type(p, is_photo=is_photo)
            motion = camera_motion_hint(stype)
            use_list = usable_for(p, stype, ori, is_photo=is_photo)
            quality_hint = root_quality

            allow_full = True
            require_slice = False
            max_seg = 0
            is_manual_tl = False
            try:
                resolved = str(p.resolve())
            except OSError:
                resolved = str(p)
            if resolved in manifest_paths and is_video and hv:
                is_manual_tl = True
                stype = "timelapse"
                motion = "fixed"
                quality_hint = "premium"
                use_list = merge_usable_pipe("|".join(use_list), _TIMELAPSE_MANUAL_TAGS).split("|")
                use_list = [x for x in use_list if x]
                allow_full = False
                require_slice = True
                max_seg = 8

            reuse_meta_cache = skip_meta and cached is not None and not args.refresh_gps
            if not reuse_meta_cache:
                meta = {}
                loc_fields: dict[str, Any] = {}
                try:
                    meta = extract_media_metadata(p)
                    loc_fields = resolve_gps_location_fields(p, meta)
                except Exception:
                    loc_fields = resolve_gps_location_fields(
                        p,
                        {
                            "lat": None,
                            "lon": None,
                            "alt": None,
                            "gps_datetime": "",
                            "camera_make": "",
                            "camera_model": "",
                            "device_model": "",
                            "created_time_original": "",
                            "meta_source": "none",
                        },
                    )
            else:
                meta = {
                    "lat": cached.gps_lat,
                    "lon": cached.gps_lon,
                    "alt": cached.gps_altitude,
                    "gps_datetime": cached.gps_datetime,
                    "camera_make": cached.camera_make,
                    "camera_model": cached.camera_model,
                    "device_model": cached.device_model,
                    "created_time_original": cached.created_time_original,
                    "meta_source": "incremental_cache",
                }
                loc_fields = resolve_gps_location_fields(p, meta)

            stats["total"] += 1
            stats[mtype] += 1
            stats[ori] += 1 if ori in stats else 0

            mini_ff: dict[str, Any] = {"format": {"duration": str(dur)}, "streams": []}
            if is_video and hv:
                mini_ff["streams"].append(
                    {
                        "codec_type": "video",
                        "width": w,
                        "height": h,
                        "display_aspect_ratio": str(vd.get("display_aspect_ratio") or ""),
                        "sample_aspect_ratio": str(vd.get("sample_aspect_ratio") or ""),
                    }
                )
            src_meta = {
                "likely_source_type": stype,
                "allow_full_duration_single_use": allow_full,
                "is_manual_timelapse": is_manual_tl,
                "orientation": ori,
            }
            if is_video and hv:
                pol_ix = is_valid_nyc_long_source(p, mini_ff, src_meta)
                la = bool(pol_ix.get("long_allowed"))
            else:
                pol_ix = {"long_allowed": False, "shorts_allowed": True, "reject_reasons": ["non_video_for_long_channel"]}
                la = False
            sa = bool(pol_ix.get("shorts_allowed", True))
            lrj = "|".join(str(x) for x in (pol_ix.get("reject_reasons") or []))
            chal = "|".join(channel_allowed_flags(la, sa))

            row = IndexRow(
                source_root=str(root),
                file_path=str(p),
                file_name=p.name,
                media_type=mtype,
                extension=suf,
                width=int(w),
                height=int(h),
                duration=float(dur),
                orientation=ori,
                aspect_ratio=float(ar),
                aspect_ratio_bucket=bucket,
                is_landscape=ori == "landscape",
                is_portrait=ori == "portrait",
                is_square=ori == "square",
                is_photo=is_photo,
                is_video=is_video and hv,
                file_size=int(size),
                created_time=float(ctime),
                modified_time=float(mtime),
                likely_source_type=stype,
                camera_motion_hint=motion,
                quality_hint=quality_hint,
                usable_for="|".join(use_list),
                allow_full_duration_single_use=allow_full,
                require_slice_segments=require_slice,
                max_segment_seconds=max_seg,
                is_manual_timelapse=is_manual_tl,
                long_allowed=la,
                shorts_allowed=sa,
                long_reject_reasons=lrj,
                channel_allowed=chal,
                **loc_fields,
            )
            rows.append(row)
            v3_rows.append(
                build_v3_entry(
                    row,
                    p,
                    fps=fps,
                    codec=codec,
                    has_audio=ha,
                    overrides=semantic_overrides.get(str(p), {}),
                )
            )

    loc_stats = compute_location_stats(rows)
    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "premium_source": str(premium_root),
            "raw_source": str(raw_root),
        },
        "output": {"json": str(out_json), "csv": str(out_csv), "fallback": used_fallback},
        "stats": stats,
        "location_stats": loc_stats,
        "items": [asdict(r) for r in rows],
    }
    payload_v3 = {
        "schema_version": "media_index_v3",
        "generated_at": payload["generated_at"],
        "sources": payload["sources"],
        "output": {"json": str(out_json_v3), "csv": str(out_csv_v3), "fallback": used_fallback},
        "stats": stats,
        "location_stats": loc_stats,
        "merge_strategy": {
            "key": "path",
            "semantic_sidecar": str(outdir / SEMANTIC_SIDECAR_NAME),
            "previous_v3": str(out_json_v3),
            "semantic_merge_order": [
                "media_semantics_sidecars.json (by path)",
                "previous media_index_v3.json semantic keys",
                "indexer heuristics (ffprobe, path tokens, mtime, optional mean luma)",
                "physical columns (duration, resolution, codec) always from current ffprobe unless --incremental metadata cache for exiftool fields only",
            ],
            "behavior": "semantic fields from sidecar/previous v3 override heuristic defaults; exiftool/ffprobe GPS may be skipped on --incremental when size+mtime match prior media_index.json; pass --refresh-gps to force a metadata re-read for GPS fixes",
        },
        "embeddings": {"sqlite": str(outdir / EMBEDDINGS_DB_NAME), "faiss_optional": str(outdir / "media_embeddings.faiss")},
        "items": v3_rows,
    }

    log(
        f"counts total={stats['total']} video={stats['video']} photo={stats['photo']} "
        f"landscape={stats['landscape']} portrait={stats['portrait']} square={stats['square']} unknown={stats['unknown']}"
    )
    log(
        f"location_stats with_gps={loc_stats.get('with_gps')} without_gps={loc_stats.get('without_gps')} "
        f"times_square={loc_stats.get('times_square')} midtown={loc_stats.get('midtown_manhattan')} "
        f"chinatown={loc_stats.get('chinatown_manhattan')} fidi={loc_stats.get('financial_district')}"
    )

    if args.dry_run:
        log(
            f"[dry-run] would write json={out_json} csv={out_csv} "
            f"v3_json={out_json_v3} v3_csv={out_csv_v3} items={len(rows)}"
        )
        return 0

    # Write JSON (atomic-ish).
    try:
        tmp = out_json.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(out_json)
    except OSError as exc:
        log(f"write json failed (fail-open): {exc}")

    # Write CSV
    fields = list(IndexRow.__annotations__.keys())
    try:
        with out_csv.open("w", encoding="utf-8", newline="") as f:
            wcsv = csv.DictWriter(f, fieldnames=fields)
            wcsv.writeheader()
            for r in rows:
                wcsv.writerow(csv_row_dict(r))
    except OSError as exc:
        log(f"write csv failed (fail-open): {exc}")

    # Write Media Intelligence v3 JSON/CSV additively; v2 writes above remain unchanged.
    try:
        tmp_v3 = out_json_v3.with_suffix(".json.tmp")
        tmp_v3.write_text(json.dumps(payload_v3, ensure_ascii=False), encoding="utf-8")
        tmp_v3.replace(out_json_v3)
    except OSError as exc:
        log(f"write v3 json failed (fail-open): {exc}")

    try:
        with out_csv_v3.open("w", encoding="utf-8", newline="") as f:
            wcsv = csv.DictWriter(f, fieldnames=V3_FIELDS)
            wcsv.writeheader()
            for r in v3_rows:
                wcsv.writerow(v3_csv_row_dict(r))
    except OSError as exc:
        log(f"write v3 csv failed (fail-open): {exc}")

    log(f"wrote items={len(rows)} json={out_json} csv={out_csv} v3_json={out_json_v3} v3_csv={out_csv_v3}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

