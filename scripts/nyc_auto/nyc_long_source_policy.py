"""NYC long-channel source hard filters (Pro-only). Shorts pools unaffected."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_NON_RAW_LONG_SOURCE_MARKERS = (
    "/ready_to_upload/",
    "/publish_pack/",
    "/renders/",
    "/audio_clean/",
    "/audio_separated/",
)

_LONG_RAW_FORBIDDEN_PATH_SEGMENTS = frozenset(
    {
        "video916",
        "picture916",
        "picture169",
        "photos",
        "images",
    }
)

LONG_SOURCE_POLICY_VERSION = "nyc_long_channel_source_policy_v2_1_video169"

_ASPECT_MIN = 1.70
_ASPECT_MAX = 1.90
_ASPECT_STRONG_LO = 1.70
_ASPECT_STRONG_HI = 1.90
_UNCERTAIN_MIN_DURATION_SEC = 300.0

_WALKING_RE = re.compile(
    r"(walking|walk_|_walk|footage_walk|handheld|sidewalk|pedestrian|street_walk|"
    r"walking_tour|on_foot|stabilized_walk|walk_timelapse|\bfoot_tour\b|\bfoot\b|"
    r"people_walking|street_walk|\bvlog\b|v_log|selfie_walk)",
    re.IGNORECASE,
)

_WALKING_EXTRA = (
    "walking",
    "people_walking",
    "street_walk",
)

_SHORTS_PATH_FRAGMENTS = (
    "youtube_shorts",
    "shorts_clips",
    "shorts_uploads",
    "image_motion_short",
    "mixed_video_image_short",
    "image_motion",
    "mixed_video_image",
)

_SHORTS_PATH_TOKENS = (
    "shorts_clips",
    "youtube_shorts",
    "shorts_uploads",
    "/shorts/",
    "/shorts",
    "reels",
    "tiktok",
    "portrait_city",
    "vertical_clip",
)

_DRIVING_STRONG_TOKENS = (
    "dashcam",
    "dash_cam",
    "dash-cam",
    "dvr",
    "driving",
    "drive_",
    "_drive",
    "car_cam",
    "vehicle_cam",
    "windshield",
    "rear_camera",
    "rearview",
    "rear_view",
    "interior_cam",
    "mounted_cam",
    "bridge_drive",
    "night_drive",
    "nyc_drive",
    "long_drive",
    "city_drive",
    "highway",
    "freeway",
    "fdr_drive",
    "west_side_highway",
    "belt_parkway",
    "bqe",
    "gopro",
    "hero11",
    "hero10",
    "hero9",
    "insta360",
    "osmo action",
    "osmo_action",
    "blackvue",
    "thinkware",
    "nextbase",
)

_DRIVING_WEAK_TOKENS = (
    "car",
    "vehicle",
    "automobile",
    "road",
    "route",
    "street",
    "avenue",
    "boulevard",
    "traffic",
    "lane",
    "tunnel",
    "bridge",
)

_AMBIGUOUS_FIXED_CAMERA_TOKENS = (
    "tripod",
    "fixed_tripod",
    "stable_street",
    "street_view",
    "streetview",
)

_FERRY_TOKENS = ("ferry", "water_taxi", "staten_island", "waterfront_ferry")

_IMAGE_EXT = {".jpg", ".jpeg", ".png", ".heic", ".dng", ".tif", ".tiff"}
_VIDEO_EXT = {".mov", ".mp4", ".m4v"}

_DRIVING_INDEX_TYPES = frozenset({"driving", "vehicle", "dashcam", "car_drive", "driving_fixed"})
_FERRY_INDEX_TYPES = frozenset({"ferry", "water_ferry", "staten_island_ferry"})
_NYC_LAT_RANGE = (40.48, 40.93)
_NYC_LON_RANGE = (-74.27, -73.68)

try:
    from utils.storage_paths import get_sv_cache, get_sv_transfer  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_sv_cache(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE")


def is_non_raw_long_source_path(path: Path) -> bool:
    """Paths under publish/render/audio staging are never Long **source** pool (outputs / history only)."""
    s = str(path).replace("\\", "/").lower()
    return any(m in s for m in _NON_RAW_LONG_SOURCE_MARKERS)


def _path_parts_lower(path: Path) -> list[str]:
    try:
        return [p.lower() for p in path.resolve().parts if p not in (".",)]
    except OSError:
        return [p.lower() for p in Path(path).parts if p not in (".",)]


def is_video169_long_source_path(path: Path) -> bool:
    """True only when a path segment is exactly ``video169`` (case-insensitive)."""
    return "video169" in _path_parts_lower(path)


def long_raw_forbidden_classification_segment(path: Path) -> bool:
    """Long raw must not live under video916 / picture* / photos / images (Shorts may still use those trees)."""
    for part in _path_parts_lower(path):
        if part in _LONG_RAW_FORBIDDEN_PATH_SEGMENTS:
            return True
        if part.startswith("picture"):
            return True
        if part in ("photo", "photographs"):
            return True
    return False


def is_allowed_long_video169_candidate_path(path: Path) -> bool:
    """Eligible NYC Long raw file path: under policy trees, inside ``video169``, not forbidden siblings, not staging."""
    if is_non_raw_long_source_path(path):
        return False
    if long_raw_forbidden_classification_segment(path):
        return False
    return is_video169_long_source_path(path)


def _path_is_under_root(root: Path, path: Path) -> bool:
    try:
        r = root.resolve()
        p = path.resolve()
    except OSError:
        r, p = root, path
    ap, ar = str(p), str(r)
    if ap == ar:
        return True
    ar_pref = ar if ar.endswith("/") else ar + "/"
    return ap.startswith(ar_pref)


def _long_policy_default_raw_roots() -> list[Path]:
    xfer = get_sv_transfer(verbose=False)
    cache = get_sv_cache(verbose=False)
    return [xfer / "00_INBOX" / "iphone", cache / "inbox"]


def _long_policy_extra_raw_roots_from_env() -> list[Path]:
    raw = (os.environ.get("NYC_LONG_RAW_SOURCE_ROOTS") or "").strip()
    out: list[Path] = []
    for chunk in raw.split(os.pathsep if os.pathsep in raw else ":"):
        t = chunk.strip()
        if not t:
            continue
        p = Path(t).expanduser()
        if p.is_dir():
            out.append(p)
    return out


def discover_long_video169_scan_directories(
    xfer: Path,
    cache: Path,
    warnings: list[str],
) -> list[Path]:
    """Directory roots for NYC Long scan: only ``.../video169/`` under iphone, SV_CACHE inbox, and env raw roots."""
    bases = [xfer / "00_INBOX" / "iphone", cache / "inbox"]
    extras = _long_policy_extra_raw_roots_from_env()
    seen: set[str] = set()
    out: list[Path] = []

    def _add_dir(d: Path) -> None:
        if not d.is_dir():
            return
        if long_raw_forbidden_classification_segment(d):
            return
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen:
            return
        seen.add(key)
        out.append(d)

    for base in (*bases, *extras):
        if not base.is_dir():
            continue
        if base.name.lower() == "video169":
            _add_dir(base)
            continue
        try:
            for p in base.rglob("*"):
                if not p.is_dir():
                    continue
                if p.name.lower() != "video169":
                    continue
                if long_raw_forbidden_classification_segment(p):
                    continue
                _add_dir(p)
        except OSError as exc:
            warnings.append(f"long_video169_dir_scan_error:{base}:{exc!r}")
    return sorted(out, key=lambda x: str(x))


def long_source_pool_origin(path: Path) -> str:
    """Classify a filesystem path for Long diagnostics (video169 raw vs other inbox vs artifacts vs publish history)."""
    s = str(path).replace("\\", "/").lower()
    if "/publish_pack/" in s:
        return "publish_history_pool"
    if any(
        m in s
        for m in (
            "/ready_to_upload/",
            "/renders/",
            "/audio_clean/",
            "/audio_separated/",
        )
    ):
        return "output_artifact_pool"
    roots = [
        p for p in (*_long_policy_default_raw_roots(), *_long_policy_extra_raw_roots_from_env()) if p.is_dir()
    ]
    under_inbox_tree = any(_path_is_under_root(root, path) for root in roots)
    if not under_inbox_tree:
        return "unknown_pool"
    if is_allowed_long_video169_candidate_path(path):
        return "raw_video169_source_pool"
    return "non_video169_raw_pool"


_MANUAL_LABELS_BY_KEY: dict[str, dict[str, Any]] | None = None
_MEDIA_INDEX_BY_KEY: dict[str, dict[str, Any]] | None = None


def _path_lookup_keys(path: Path) -> list[str]:
    keys: list[str] = [str(path)]
    try:
        keys.append(str(path.expanduser().resolve()))
    except OSError:
        try:
            keys.append(str(path.resolve()))
        except OSError:
            pass
    return list(dict.fromkeys([k for k in keys if k]))


def _ingest_index_items(items: Any, sink: dict[str, dict[str, Any]], *, tag: str) -> None:
    if not isinstance(items, list):
        return
    for it in items:
        if not isinstance(it, dict):
            continue
        raw = str(it.get("file_path") or it.get("path") or "").strip()
        if not raw:
            continue
        p = Path(raw)
        row = {**it, "_media_index_origin": tag}
        for k in _path_lookup_keys(p):
            sink.setdefault(k, row)


def _load_manual_labels_map() -> dict[str, dict[str, Any]]:
    global _MANUAL_LABELS_BY_KEY
    if _MANUAL_LABELS_BY_KEY is not None:
        return _MANUAL_LABELS_BY_KEY
    xfer = get_sv_transfer(verbose=False)
    paths = [
        xfer / "media_index" / "nyc_long_manual_source_labels.json",
        Path.home() / "StateVerge" / "data" / "media_index" / "nyc_long_manual_source_labels.json",
    ]
    sink: dict[str, dict[str, Any]] = {}
    for p in paths:
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        for lab in data.get("labels") or []:
            if not isinstance(lab, dict):
                continue
            raw = str(lab.get("path") or "").strip()
            if not raw:
                continue
            lp = Path(raw)
            ent = {**lab, "_manual_label_file": str(p)}
            for k in _path_lookup_keys(lp):
                sink.setdefault(k, ent)
    _MANUAL_LABELS_BY_KEY = sink
    return sink


def _load_media_index_maps() -> dict[str, dict[str, Any]]:
    global _MEDIA_INDEX_BY_KEY
    if _MEDIA_INDEX_BY_KEY is not None:
        return _MEDIA_INDEX_BY_KEY
    xfer = get_sv_transfer(verbose=False)
    sink: dict[str, dict[str, Any]] = {}
    mid = xfer / "media_index"
    for name, tag in (
        ("media_index_v3.json", "media_index_v3"),
        ("media_index.json", "media_index_v1"),
    ):
        fp = mid / name
        if not fp.is_file():
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, MemoryError):
            continue
        _ingest_index_items(data.get("items"), sink, tag=tag)
    pool = mid / "nyc_long_driving_sources.json"
    if pool.is_file():
        try:
            pdata = json.loads(pool.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            pdata = {}
        for it in pdata.get("items") or []:
            if not isinstance(it, dict):
                continue
            raw = str(it.get("path") or it.get("file_path") or "").strip()
            if not raw:
                continue
            p = Path(raw)
            row = {**it, "_media_index_origin": "nyc_long_driving_sources"}
            for k in _path_lookup_keys(p):
                sink.setdefault(k, row)
    _MEDIA_INDEX_BY_KEY = sink
    return sink


def lookup_manual_long_label(path: Path) -> dict[str, Any] | None:
    m = _load_manual_labels_map()
    for k in _path_lookup_keys(path):
        hit = m.get(k)
        if hit:
            return hit
    return None


def lookup_media_index_row(path: Path) -> dict[str, Any] | None:
    m = _load_media_index_maps()
    for k in _path_lookup_keys(path):
        hit = m.get(k)
        if hit:
            return hit
    return None


def _ffprobe_tag_map(j: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not j:
        return out
    fmt = j.get("format") or {}
    for k, v in (fmt.get("tags") or {}).items():
        out[str(k).lower()] = str(v)
    for st in j.get("streams") or []:
        if (st.get("codec_type") or "").lower() == "video":
            for k, v in (st.get("tags") or {}).items():
                out[str(k).lower()] = str(v)
            break
    return out


def extract_gps_summary(ffprobe_meta: dict[str, Any] | None) -> dict[str, Any]:
    tags = _ffprobe_tag_map(ffprobe_meta)
    loc = (
        tags.get("com.apple.quicktime.location.iso6709")
        or tags.get("location")
        or tags.get("location-eng")
        or ""
    ).strip()
    lat: float | None = None
    lon: float | None = None
    if loc:
        m = re.search(r"([+-]?\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)", loc.replace("/", " "))
        if m:
            try:
                lat = float(m.group(1))
                lon = float(m.group(2))
            except ValueError:
                lat = lon = None
    in_nyc = False
    if lat is not None and lon is not None:
        in_nyc = bool(_NYC_LAT_RANGE[0] <= lat <= _NYC_LAT_RANGE[1] and _NYC_LON_RANGE[0] <= lon <= _NYC_LON_RANGE[1])
    return {
        "location_tag_raw": loc[:500] if loc else "",
        "latitude": lat,
        "longitude": lon,
        "gps_in_nyc_bbox_guess": in_nyc,
        "creation_time": tags.get("creation_time") or tags.get("com.apple.quicktime.creationdate") or "",
    }


def _mi_text_blob(mi: dict[str, Any] | None) -> str:
    if not mi:
        return ""
    keys = (
        "likely_source_type",
        "usable_for",
        "semantic_class",
        "scene_label",
        "location_name",
        "borough",
        "quality_hint",
        "camera_motion_class",
        "notes",
        "classification",
        "route_group",
    )
    parts = [str(mi.get(k) or "") for k in keys]
    return " ".join(parts).lower()


def _media_index_driving_evidence(mi: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not mi:
        return False, []
    reasons: list[str] = []
    lt = str(mi.get("likely_source_type") or "").strip().lower()
    if lt in _DRIVING_INDEX_TYPES:
        reasons.append(f"media_index.likely_source_type={lt}")
        return True, reasons
    if lt in _FERRY_INDEX_TYPES:
        return False, []
    blob = _mi_text_blob(mi)
    if any(t in blob for t in _DRIVING_STRONG_TOKENS):
        reasons.append("media_index_semantic_strong_driving_token")
        return True, reasons
    cls = str(mi.get("classification") or "").lower()
    if "driving" in cls or "dash" in cls:
        reasons.append(f"media_index.classification={cls}")
        return True, reasons
    return False, reasons


def _media_index_ferry_evidence(mi: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not mi:
        return False, []
    lt = str(mi.get("likely_source_type") or "").strip().lower()
    if lt in _FERRY_INDEX_TYPES:
        return True, [f"media_index.likely_source_type={lt}"]
    blob = _mi_text_blob(mi)
    if any(x in blob for x in _FERRY_TOKENS):
        return True, ["media_index_semantic_ferry_token"]
    return False, []


def _strong_driving_path_or_blob(low_path: str, blob: str) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for t in _DRIVING_STRONG_TOKENS:
        if t in blob or t in low_path:
            reasons.append(f"strong_token:{t}")
    return (len(reasons) > 0), reasons


def _ambiguous_fixed_camera(low_path: str, blob: str) -> bool:
    return any(t in low_path or t in blob for t in _AMBIGUOUS_FIXED_CAMERA_TOKENS)


def _blob(path: Path, metadata: dict[str, Any] | None, ffprobe_meta: dict[str, Any] | None) -> str:
    parts: list[str] = [str(path).replace("\\", "/")]
    if metadata:
        try:
            parts.append(json.dumps(metadata, ensure_ascii=False)[:8000])
        except (TypeError, ValueError):
            parts.append(str(metadata)[:4000])
    if ffprobe_meta:
        try:
            fmt = ffprobe_meta.get("format") or {}
            parts.append(json.dumps(fmt.get("tags") or {}, ensure_ascii=False))
            for st in ffprobe_meta.get("streams") or []:
                if st.get("codec_type") == "video":
                    parts.append(json.dumps(st.get("tags") or {}, ensure_ascii=False))
                    break
        except (TypeError, ValueError):
            pass
    return "\n".join(parts).lower()


def _first_video_stream(j: dict[str, Any] | None) -> dict[str, Any] | None:
    if not j:
        return None
    for st in j.get("streams") or []:
        if isinstance(st, dict) and (st.get("codec_type") or "").lower() == "video":
            return st
    return None


def _parse_ratio_fraction(s: str) -> float | None:
    t = (s or "").strip()
    if not t or t.lower() in ("n/a", "0:0"):
        return None
    if ":" in t:
        a, b = t.split(":", 1)
        try:
            na, nb = float(a), float(b)
            if nb:
                return na / nb
        except (TypeError, ValueError):
            return None
    return None


def _display_aspect_ratio_str(stream: dict[str, Any] | None) -> str:
    if not stream:
        return ""
    dar = str(stream.get("display_aspect_ratio") or "").strip()
    if dar:
        return dar
    tags = stream.get("tags") or {}
    if isinstance(tags, dict):
        for k in ("DAR", "display_aspect_ratio"):
            v = tags.get(k)
            if v:
                return str(v).strip()
    return ""


def _sample_aspect_ratio_str(stream: dict[str, Any] | None) -> str:
    if not stream:
        return ""
    return str(stream.get("sample_aspect_ratio") or "").strip()


def _dims_from_ffprobe(j: dict[str, Any] | None) -> tuple[int, int, float]:
    if not j:
        return 0, 0, 0.0
    try:
        dur = float((j.get("format") or {}).get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    w = h = 0
    st = _first_video_stream(j)
    if st:
        try:
            w = int(st.get("width") or 0)
            h = int(st.get("height") or 0)
        except (TypeError, ValueError):
            w = h = 0
    return w, h, dur


def _orientation_wh(w: int, h: int) -> str:
    if w <= 0 or h <= 0:
        return "unknown"
    if h > w:
        return "portrait"
    if w > h * 1.05:
        return "landscape"
    return "square"


def nyc_long_aspect_gate(
    w: int,
    h: int,
    *,
    display_aspect_ratio: str = "",
    sample_aspect_ratio: str = "",
) -> dict[str, Any]:
    """
    NYC long aspect gate (v2): landscape only, coded aspect 1.70–1.90 (no portrait / no mixed-aspect masters).
    Rejects portrait coded pixels, DAR/SAR that imply 9:16 / tall phone frames (e.g. 6:19).
    """
    reject: list[str] = []
    aspect_policy = ""
    dar = (display_aspect_ratio or "").strip()
    sar = (sample_aspect_ratio or "").strip()
    dar_r = _parse_ratio_fraction(dar)
    _sar_r = _parse_ratio_fraction(sar)

    low_dar = str(dar).lower().replace(" ", "")
    if low_dar in ("9:16", "10:16", "6:19", "9/16", "6/19"):
        reject.append("rejected_bad_aspect_ratio")
    if sar and sar.lower().replace(" ", "") in ("9:16", "6:19"):
        reject.append("rejected_bad_aspect_ratio")

    if dar_r is not None and dar_r < 1.0:
        reject.append("rejected_vertical_video")
    if dar_r is not None and 0.52 <= dar_r <= 0.68:
        reject.append("rejected_vertical_video")
    if dar_r is not None and 0.28 <= dar_r <= 0.36:
        reject.append("rejected_vertical_video")

    if w <= 0 or h <= 0:
        if reject:
            return {"ok": False, "aspect_ratio": 0.0, "aspect_policy": "", "reject_reasons": reject}
        return {"ok": False, "aspect_ratio": 0.0, "aspect_policy": "", "reject_reasons": ["rejected_bad_aspect_ratio"]}

    if h > w:
        reject.append("rejected_vertical_video")

    ar = float(w) / float(h)
    if 0.85 <= ar <= 1.20:
        reject.append("rejected_bad_aspect_ratio")
    elif ar < _ASPECT_MIN or ar > _ASPECT_MAX:
        reject.append("rejected_bad_aspect_ratio")
    else:
        aspect_policy = "landscape_16_9_ok"

    orient = _orientation_wh(w, h)
    ok = len(reject) == 0 and orient == "landscape" and w > h

    return {
        "ok": ok,
        "aspect_ratio": round(ar, 6) if w > 0 and h > 0 else 0.0,
        "aspect_policy": aspect_policy if ok else "",
        "reject_reasons": list(dict.fromkeys(reject)),
    }


def is_valid_nyc_long_source(
    item_or_path: Path | dict[str, Any] | str,
    ffprobe_meta: dict[str, Any] | None = None,
    source_info: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Validation envelope for NYC **long** channel automation.

    ``item_or_path``: Path, str path, or dict with ``path`` / ``file_path``.
    ``source_info``: optional merge into metadata (likely_source_type, allow_full_duration_single_use, …).
    """
    reject_reasons: list[str] = []
    metadata: dict[str, Any] | None = None
    if isinstance(item_or_path, dict):
        raw = str(item_or_path.get("path") or item_or_path.get("file_path") or "").strip()
        path = Path(raw).expanduser() if raw else Path(".")
        metadata = {k: v for k, v in item_or_path.items() if k not in ("path", "file_path")}
    else:
        path = Path(str(item_or_path)).expanduser()

    if source_info:
        metadata = {**(metadata or {}), **source_info}

    suf = path.suffix.lower()
    if suf in _IMAGE_EXT:
        rr = "image_not_allowed_for_long_channel"
        return _envelope(
            valid=False,
            reason=rr,
            source_type="unknown",
            orientation="unknown",
            ar=0.0,
            w=0,
            h=0,
            long_allowed=False,
            shorts_allowed=False,
            reject_reasons=[rr],
            uncertain=False,
            aspect_policy="",
            display_aspect_ratio="",
        )

    if suf and suf not in _VIDEO_EXT:
        rr = "non_video_extension_for_long_channel"
        return _envelope(
            valid=False,
            reason=rr,
            source_type="unknown",
            orientation="unknown",
            ar=0.0,
            w=0,
            h=0,
            long_allowed=False,
            shorts_allowed=False,
            reject_reasons=[rr],
            uncertain=False,
            aspect_policy="",
            display_aspect_ratio="",
        )

    j = ffprobe_meta
    w, h, dur = _dims_from_ffprobe(j)
    vst = _first_video_stream(j)
    dar_s = _display_aspect_ratio_str(vst)
    sar_s = _sample_aspect_ratio_str(vst)
    ag = nyc_long_aspect_gate(w, h, display_aspect_ratio=dar_s, sample_aspect_ratio=sar_s)
    ar = float(ag["aspect_ratio"] or 0.0)
    aspect_policy = str(ag.get("aspect_policy") or "")
    orientation = _orientation_wh(w, h)

    blob = _blob(path, metadata, j)
    low_path = str(path).replace("\\", "/").lower()

    if is_non_raw_long_source_path(path):
        reject_reasons.append("rejected_non_raw_long_staging_path")
    elif long_raw_forbidden_classification_segment(path) or not is_video169_long_source_path(path):
        reject_reasons.append("rejected_not_video169_long_source")

    for frag in _SHORTS_PATH_FRAGMENTS:
        if frag in low_path:
            reject_reasons.append("rejected_shorts_path")
            break
    for tok in _SHORTS_PATH_TOKENS:
        if tok in low_path:
            reject_reasons.append("rejected_shorts_path")
            break
    if re.search(r"(^|/)(portrait|vertical|reels|tiktok)(/|_|\.|$)", low_path):
        reject_reasons.append("rejected_shorts_path")

    if _WALKING_RE.search(blob):
        reject_reasons.append("walking_not_allowed_for_nyc_long_channel")
    for t in _WALKING_EXTRA:
        if t in blob:
            reject_reasons.append("walking_not_allowed_for_nyc_long_channel")
            break

    if metadata:
        if metadata.get("is_manual_timelapse") is True:
            reject_reasons.append("timelapse_whole_clip_not_allowed_for_long_channel")
        if metadata.get("allow_full_duration_single_use") is False:
            reject_reasons.append("timelapse_whole_clip_not_allowed_for_long_channel")
        if metadata.get("_timelapse_heuristic") is True:
            reject_reasons.append("timelapse_whole_clip_not_allowed_for_long_channel")
        lk = str(metadata.get("likely_source_type") or "").lower()
        if lk in ("walking_handheld", "walking", "on_foot"):
            reject_reasons.append("walking_not_allowed_for_nyc_long_channel")

    for rr in ag.get("reject_reasons") or []:
        if rr and rr not in reject_reasons:
            reject_reasons.append(str(rr))

    if w < 1280 or h < 720:
        reject_reasons.append("rejected_insufficient_resolution_for_long_channel")

    likely = str((metadata or {}).get("likely_source_type") or "").strip().lower()

    mi_row: dict[str, Any] | None = None
    if metadata and isinstance(metadata.get("_media_index_row"), dict):
        mi_row = metadata["_media_index_row"]
    else:
        mi_row = lookup_media_index_row(path)

    manual = lookup_manual_long_label(path)
    classification_evidence: list[str] = []

    manual_override = False
    manual_non_raw_rejected = False
    manual_non_video169_rejected = False
    if manual and manual.get("allowed_for_long") is True:
        if is_non_raw_long_source_path(path):
            manual_non_raw_rejected = True
        elif not is_allowed_long_video169_candidate_path(path):
            manual_non_video169_rejected = True
        else:
            st_m = str(manual.get("source_type") or "").strip().lower()
            if st_m in ("driving", "ferry"):
                manual_override = True
                classification_evidence.append(f"manual_label_allowed:{st_m}")

    strong_hit, strong_reasons = _strong_driving_path_or_blob(low_path, blob)
    classification_evidence.extend(strong_reasons[:12])

    mi_drive, mi_dr_reasons = _media_index_driving_evidence(mi_row)
    classification_evidence.extend(mi_dr_reasons)

    mi_ferry, mi_fe_reasons = _media_index_ferry_evidence(mi_row)
    classification_evidence.extend(mi_fe_reasons)

    likely_ferry = likely in _FERRY_INDEX_TYPES or likely == "ferry"
    meta_driving = likely in _DRIVING_INDEX_TYPES and not likely_ferry

    ferry_blob = any(x in blob for x in _FERRY_TOKENS) or likely_ferry or mi_ferry
    explicit_driving = bool(
        strong_hit
        or mi_drive
        or meta_driving
        or (manual_override and str(manual.get("source_type") or "").strip().lower() == "driving")
    )

    ambiguous_fixed = _ambiguous_fixed_camera(low_path, blob)

    source_type = "unknown"
    if manual_override:
        source_type = str(manual.get("source_type") or "unknown").strip().lower()
        if source_type not in ("driving", "ferry"):
            source_type = "unknown"
    elif ferry_blob:
        source_type = "ferry"
    elif _WALKING_RE.search(blob) or "walking" in likely:
        source_type = "walking"
    elif "shorts" in low_path or "reels" in low_path:
        source_type = "shorts_only"
    elif explicit_driving:
        source_type = "driving"

    geometry_ok = bool(ag.get("ok")) and orientation == "landscape" and w > h

    if ambiguous_fixed and source_type == "unknown" and not manual_override:
        reject_reasons.append("rejected_fixed_street_view_needs_manual_label_or_vehicle_evidence")

    if source_type not in ("driving", "ferry"):
        reject_reasons.append("rejected_long_source_type_not_allowed")

    base_ok = len(reject_reasons) == 0 and geometry_ok
    uncertain = bool(
        base_ok
        and source_type == "driving"
        and not strong_hit
        and not mi_drive
        and dur >= _UNCERTAIN_MIN_DURATION_SEC
    )

    long_allowed = base_ok
    reason = "ok"
    if reject_reasons:
        reason = reject_reasons[0]
    elif uncertain:
        reason = "long_candidate_uncertain"
    elif not geometry_ok:
        reason = "geometry_not_ok_for_long_channel"

    valid = long_allowed

    shorts_allowed = bool(orientation == "portrait" or (w > 0 and h > 0 and h > w))
    if any(
        x in reject_reasons
        for x in (
            "rejected_shorts_path",
            "walking_not_allowed_for_nyc_long_channel",
            "image_not_allowed_for_long_channel",
        )
    ):
        shorts_allowed = False

    out = _envelope(
        valid=valid,
        reason=reason,
        source_type=source_type,
        orientation=orientation,
        ar=ar,
        w=w,
        h=h,
        long_allowed=long_allowed,
        shorts_allowed=shorts_allowed,
        reject_reasons=list(dict.fromkeys(reject_reasons)),
        uncertain=uncertain,
        aspect_policy=aspect_policy,
        display_aspect_ratio=dar_s,
    )
    out["source_classification_evidence"] = list(dict.fromkeys(classification_evidence))[:48]
    out["manual_label_hit"] = bool(manual_override)
    out["media_index_origin"] = str((mi_row or {}).get("_media_index_origin") or "")
    if manual_non_raw_rejected:
        wlist = list(out.get("warnings") or [])
        if "manual_label_rejected_non_raw_source_path" not in wlist:
            wlist.append("manual_label_rejected_non_raw_source_path")
        out["warnings"] = wlist
    if manual_non_video169_rejected:
        wlist = list(out.get("warnings") or [])
        if "manual_label_rejected_non_video169_path" not in wlist:
            wlist.append("manual_label_rejected_non_video169_path")
        out["warnings"] = wlist
    return out


def refresh_nyc_long_policy_caches() -> None:
    """Reload manual labels + media index maps after manifest edits."""
    global _MANUAL_LABELS_BY_KEY, _MEDIA_INDEX_BY_KEY
    _MANUAL_LABELS_BY_KEY = None
    _MEDIA_INDEX_BY_KEY = None


def nyc_long_source_policy_summary() -> str:
    """One-line summary for dry-run / ops dashboards."""
    return (
        f"{LONG_SOURCE_POLICY_VERSION}: long raw only under **/video169/ (iphone + SV_CACHE inbox + NYC_LONG_RAW_SOURCE_ROOTS); "
        f"forbid video916/picture*/photos/images and non-video169 inbox files; landscape AR {_ASPECT_MIN:.2f}–{_ASPECT_MAX:.2f}; "
        f"min {1280}x{720}; driving|ferry evidence as before; Shorts pools unchanged."
    )


def _envelope(
    *,
    valid: bool,
    reason: str,
    source_type: str,
    orientation: str,
    ar: float,
    w: int,
    h: int,
    long_allowed: bool,
    shorts_allowed: bool,
    reject_reasons: list[str],
    uncertain: bool,
    aspect_policy: str,
    display_aspect_ratio: str,
) -> dict[str, Any]:
    return {
        "valid": valid,
        "reason": reason,
        "source_type": source_type,
        "orientation": orientation,
        "aspect_ratio": round(ar, 6) if ar else 0.0,
        "width": int(w),
        "height": int(h),
        "long_allowed": long_allowed,
        "shorts_allowed": shorts_allowed,
        "reject_reasons": reject_reasons,
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "long_candidate_uncertain": uncertain,
        "warnings": (["long_source_type_uncertain"] if uncertain else []),
        "aspect_policy": aspect_policy,
        "display_aspect_ratio": display_aspect_ratio or "",
    }


def summarize_rejections(rows: list[dict[str, Any]]) -> dict[str, int]:
    ctr: dict[str, int] = {}
    for r in rows:
        for x in r.get("reject_reasons") or []:
            ctr[str(x)] = ctr.get(str(x), 0) + 1
        rr = r.get("reason")
        if rr and not r.get("reject_reasons"):
            ctr[str(rr)] = ctr.get(str(rr), 0) + 1
    return dict(sorted(ctr.items(), key=lambda kv: (-kv[1], kv[0])))


def policy_header_counts(*, accepted: int, rejected: int, by_reason: dict[str, int]) -> dict[str, Any]:
    return {
        "long_source_policy_version": LONG_SOURCE_POLICY_VERSION,
        "long_channel_requires_landscape": True,
        "long_channel_rejects_portrait": True,
        "long_channel_rejects_walking": True,
        "long_channel_rejects_shorts_assets": True,
        "long_candidates_count": accepted,
        "rejected_long_candidates_count": rejected,
        "rejected_by_reason": by_reason,
    }


def channel_allowed_flags(long_allowed: bool, shorts_allowed: bool) -> list[str]:
    out: list[str] = []
    if long_allowed:
        out.append("long_nyc")
    if shorts_allowed:
        out.append("shorts")
    return out
