"""Detect iPhone / VFR / high-fps media that must not be muxed with ``-c:v copy``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.davinci_ffprobe import ffprobe_json


def _parse_rate(rate: str | None) -> float | None:
    if not rate or rate == "0/0":
        return None
    if "/" in rate:
        a, b = rate.split("/", 1)
        try:
            na, nb = float(a), float(b)
            if nb == 0:
                return None
            return na / nb
        except (ValueError, ZeroDivisionError):
            return None
    try:
        return float(rate)
    except ValueError:
        return None


def _norm_tag_value(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return " ".join(_norm_tag_value(x) for x in v)
    return str(v).lower()


def _tags_blob(path: Path, data: dict[str, Any]) -> str:
    parts: list[str] = []
    fmt = data.get("format") or {}
    for key in ("tags",):
        t = fmt.get(key) or {}
        if isinstance(t, dict):
            for _k, v in t.items():
                parts.append(_norm_tag_value(v))
    for s in data.get("streams") or []:
        if s.get("codec_type") != "video":
            continue
        t = s.get("tags") or {}
        if isinstance(t, dict):
            for _k, v in t.items():
                parts.append(_norm_tag_value(v))
        break
    parts.append(path.suffix.lower())
    return " ".join(parts)


def _primary_video_stream(data: dict[str, Any]) -> dict[str, Any] | None:
    for s in data.get("streams") or []:
        if s.get("codec_type") == "video":
            return s
    return None


def _container_lower(path: Path, data: dict[str, Any]) -> str:
    fmt = data.get("format") or {}
    name = str(fmt.get("format_name") or "")
    ext = path.suffix.lower().lstrip(".")
    if "mov" in name or ext == "mov":
        return "mov"
    if "mp4" in name or ext == "mp4":
        return "mp4"
    return ext or name.split(",")[0]


def is_iphone_or_apple_video(path: Path | str, *, timeout_sec: float = 120.0) -> bool:
    p = Path(path)
    data, err = ffprobe_json(p, timeout_sec=timeout_sec)
    if err or not data:
        return False
    blob = _tags_blob(p, data)
    needles = (
        "iphone",
        "apple",
        "quicktime",
        "com.apple.quicktime",
        "ipad",
    )
    return any(n in blob for n in needles)


def is_vfr_or_high_fps(path: Path | str, *, timeout_sec: float = 120.0) -> bool:
    p = Path(path)
    data, err = ffprobe_json(p, timeout_sec=timeout_sec)
    if err or not data:
        return False
    vs = _primary_video_stream(data)
    if not vs:
        return False
    r = _parse_rate(vs.get("r_frame_rate"))
    avg = _parse_rate(vs.get("avg_frame_rate"))
    if r is not None and avg is not None and abs(r - avg) > 0.05:
        return True
    peak = max(x for x in (r, avg) if x is not None)
    if peak is not None and peak > 60.5:
        return True
    return False


def requires_cfr_normalization(
    path: Path | str,
    *,
    force: bool = False,
    timeout_sec: float = 120.0,
) -> bool:
    """Return True when CFR re-encode should be used before muxing final deliverables."""
    if force:
        return True
    p = Path(path)
    data, err = ffprobe_json(p, timeout_sec=timeout_sec)
    if err or not data:
        return False

    if is_iphone_or_apple_video(p, timeout_sec=timeout_sec):
        return True
    if is_vfr_or_high_fps(p, timeout_sec=timeout_sec):
        return True

    vs = _primary_video_stream(data)
    container = _container_lower(p, data)
    codec = str(vs.get("codec_name") or "").lower() if vs else ""

    if container == "mov":
        return True
    if codec in {"hevc", "h265"}:
        return True

    fmt = data.get("format") or {}
    try:
        fdur = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        fdur = 0.0
    if vs:
        try:
            sdur = float(vs.get("duration") or 0.0)
        except (TypeError, ValueError):
            sdur = 0.0
        if fdur > 1.0 and sdur > 1.0 and abs(fdur - sdur) / fdur > 0.05:
            return True

    return False


def assert_safe_for_video_copy(path: Path | str, *, allow: bool = False, timeout_sec: float = 120.0) -> None:
    """Raise if ``allow`` is False and this file must not use ``-c:v copy`` for final mux outputs."""
    if allow:
        return
    if requires_cfr_normalization(path, force=False, timeout_sec=timeout_sec):
        raise RuntimeError(
            "UNSAFE_VIDEO_COPY_FOR_IPHONE_VFR: normalize to CFR before muxing audio."
        )


def probe_summary(path: Path | str, *, timeout_sec: float = 120.0) -> dict[str, Any]:
    """Flatten ffprobe fields useful for separation reports and CLI."""
    p = Path(path)
    out: dict[str, Any] = {
        "path": str(p),
        "error": None,
        "duration_sec": None,
        "codec": None,
        "width": None,
        "height": None,
        "r_frame_rate": None,
        "avg_frame_rate": None,
        "time_base": None,
        "encoder": None,
        "make": None,
        "model": None,
        "software": None,
        "format_tags": {},
        "container_guess": None,
    }
    data, err = ffprobe_json(p, timeout_sec=timeout_sec)
    if err or not data:
        out["error"] = err or "no_data"
        return out
    fmt = data.get("format") or {}
    tags = fmt.get("tags") if isinstance(fmt.get("tags"), dict) else {}
    out["format_tags"] = dict(tags)
    try:
        out["duration_sec"] = float(fmt.get("duration") or 0.0) or None
    except (TypeError, ValueError):
        out["duration_sec"] = None
    vs = _primary_video_stream(data)
    if vs:
        out["codec"] = vs.get("codec_name")
        out["width"] = vs.get("width")
        out["height"] = vs.get("height")
        out["r_frame_rate"] = vs.get("r_frame_rate")
        out["avg_frame_rate"] = vs.get("avg_frame_rate")
        out["time_base"] = vs.get("time_base")
        st = vs.get("tags") if isinstance(vs.get("tags"), dict) else {}
        for key in ("encoder", "ENCODER"):
            if key in st:
                out["encoder"] = st.get(key)
                break
        # QuickTime-style tags sometimes land on format tags
        for label, keys in (
            ("make", ("com.apple.quicktime.make", "make")),
            ("model", ("com.apple.quicktime.model", "model")),
            ("software", ("encoder", "major_brand")),
        ):
            for k in keys:
                v = tags.get(k) or st.get(k)
                if v:
                    out[label] = v
                    break
    out["container_guess"] = _container_lower(p, data)
    return out
