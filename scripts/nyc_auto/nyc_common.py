"""Shared paths and helpers for NYC_AUTO V1 pipelines. Fail-open oriented."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import CODE_ROOT, NYC_ROOT, SSD_ROOT  # noqa: E402

# Backward-compatible alias (same as CODE_ROOT).
STATEVERGE_ROOT = CODE_ROOT
LOCAL_LOG_DIR = STATEVERGE_ROOT / "logs" / "system"

RAW_VIDEO = NYC_ROOT / "raw" / "airdrop" / "video"
LONG_VIDEO_LIB = NYC_ROOT / "library" / "long_video"
SHORT_CAND_LIB = NYC_ROOT / "library" / "short_candidates"
PROJECTS = NYC_ROOT / "projects"
OUT_SHORTS = NYC_ROOT / "output" / "shorts"
OUT_LONG = NYC_ROOT / "output" / "long"
OUT_PACKAGES = NYC_ROOT / "output" / "packages"
NYC_LOGS = NYC_ROOT / "logs"
CACHE = NYC_ROOT / "cache"
VALIDATION_FRAMES_DIR = CACHE / "validation_frames"

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}


def ensure_nyc_dirs() -> None:
    for p in (
        LONG_VIDEO_LIB,
        SHORT_CAND_LIB,
        PROJECTS,
        OUT_SHORTS,
        OUT_LONG,
        OUT_PACKAGES,
        NYC_LOGS,
        CACHE,
        VALIDATION_FRAMES_DIR,
    ):
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def log_lines(prefix: str, messages: list[str], nyc_daily: Optional[Path] = None) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = "\n".join(f"[{ts}] {m}" for m in messages)
    print(block)
    try:
        sys.stdout.flush()
    except OSError:
        pass
    LOCAL_LOG_DIR.mkdir(parents=True, exist_ok=True)
    d = datetime.now().strftime("%Y-%m-%d")
    local = LOCAL_LOG_DIR / f"{prefix}_{d}.log"
    try:
        with local.open("a", encoding="utf-8") as f:
            f.write(block + "\n")
    except OSError:
        pass
    if nyc_daily:
        try:
            nyc_daily.parent.mkdir(parents=True, exist_ok=True)
            with nyc_daily.open("a", encoding="utf-8") as f:
                f.write(block + "\n")
        except OSError:
            pass


def nyc_daily_log(name: str) -> Path:
    d = datetime.now().strftime("%Y-%m-%d")
    return NYC_LOGS / f"{name}_{d}.log"


def slug_filename(name: str, max_len: int = 64) -> str:
    stem = Path(name).stem
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", stem).strip("-").lower()
    if not s:
        s = "video"
    return s[:max_len]


def stable_project_id(resolved: Path, mtime: float) -> str:
    try:
        st = resolved.stat()
        size = st.st_size
        mt = st.st_mtime
    except OSError:
        size = 0
        mt = mtime
    day = datetime.fromtimestamp(mt).strftime("%Y-%m-%d")
    slug = slug_filename(resolved.name)
    h = hashlib.sha256(f"{resolved}|{size}|{mt}".encode("utf-8")).hexdigest()[:8]
    return f"{day}_{slug}_{h}"


def run_ffprobe(path: Path) -> Optional[dict[str, Any]]:
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
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=120)
        return json.loads(out.decode("utf-8", errors="replace"))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError, subprocess.TimeoutExpired):
        return None


def parse_fraction(s: str) -> Optional[float]:
    if not s:
        return None
    if "/" in s:
        a, b = s.split("/", 1)
        try:
            return float(a) / float(b)
        except ValueError:
            return None
    try:
        return float(s)
    except ValueError:
        return None


def summarize_probe(data: dict[str, Any]) -> dict[str, Any]:
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    video = next((x for x in streams if x.get("codec_type") == "video"), None)
    audio = next((x for x in streams if x.get("codec_type") == "audio"), None)
    duration = float(fmt.get("duration") or 0) or None
    if not duration and video:
        dur_v = video.get("duration")
        if dur_v:
            try:
                duration = float(dur_v)
            except (TypeError, ValueError):
                duration = None
    duration = duration or 0.0
    width = int(video.get("width") or 0) if video else 0
    height = int(video.get("height") or 0) if video else 0
    fps = None
    if video:
        fps = parse_fraction(str(video.get("r_frame_rate") or video.get("avg_frame_rate") or ""))
    vcodec = video.get("codec_name") if video else None
    acodec = audio.get("codec_name") if audio else None
    br = fmt.get("bit_rate")
    try:
        bit_rate = int(br) if br else None
    except (TypeError, ValueError):
        bit_rate = None
    return {
        "duration": float(duration),
        "width": width,
        "height": height,
        "fps": fps or 0.0,
        "video_codec": vcodec or "",
        "audio_codec": acodec or "",
        "has_audio": audio is not None,
        "bit_rate": bit_rate,
    }


def project_dir(pid: str) -> Path:
    return PROJECTS / pid


def ensure_project_layout(pid: str) -> Path:
    root = project_dir(pid)
    for sub in ("output", "shorts", "thumbnails", "logs"):
        try:
            (root / sub).mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return root


def load_source_json(pid: str) -> Optional[dict[str, Any]]:
    p = project_dir(pid) / "source.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def with_each_project(
    project_ids: Optional[list[str]],
    all_mode: bool,
    filter_meta: Callable[[dict[str, Any]], bool],
) -> list[str]:
    if project_ids:
        return list(dict.fromkeys(project_ids))
    if not all_mode:
        return []
    found: list[str] = []
    if not PROJECTS.is_dir():
        return found
    try:
        subs = [p for p in PROJECTS.iterdir() if p.is_dir()]
        subs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        for p in subs:
            meta = load_source_json(p.name)
            if meta and filter_meta(meta):
                found.append(p.name)
    except OSError:
        pass
    return found


def ffprobe_verify_streams(path: Path, want_audio: bool = True) -> tuple[bool, str]:
    data = run_ffprobe(path)
    if not data:
        return False, "ffprobe_failed"
    streams = data.get("streams") or []
    has_v = any(s.get("codec_type") == "video" for s in streams)
    has_a = any(s.get("codec_type") == "audio" for s in streams)
    if not has_v:
        return False, "no_video"
    if want_audio and not has_a:
        return False, "no_audio"
    return True, "ok"


def index_csv_path() -> Path:
    return LONG_VIDEO_LIB / "index.csv"


def list_projects_recent() -> list[str]:
    if not PROJECTS.is_dir():
        return []
    try:
        subs = [p for p in PROJECTS.iterdir() if p.is_dir()]
        subs.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        return [p.name for p in subs if (p / "source.json").is_file()]
    except OSError:
        return []


# Substrings that flag lavfi/colorbar/synthetic test assets (path-based guard).
TEST_ASSET_MARKERS: tuple[str, ...] = (
    "synthetic",
    "test",
    "dummy",
    "placeholder",
    "colorbar",
    "bars",
    "lavfi",
    "smpte",
)


def path_matches_test_asset_marker(*candidates: Optional[str | Path]) -> bool:
    parts: list[str] = []
    for c in candidates:
        if c is None:
            continue
        s = str(c).strip()
        if s:
            parts.append(s)
    if not parts:
        return False
    blob = " ".join(parts).lower()
    return any(m in blob for m in TEST_ASSET_MARKERS)


def read_project_source_path(project_id: str) -> str:
    p = project_dir(project_id) / "source.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return str(data.get("source_path") or "")
    except (OSError, json.JSONDecodeError, TypeError):
        return ""
