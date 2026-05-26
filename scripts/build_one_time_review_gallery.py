#!/usr/bin/env python3
"""
One-time manual review gallery for StateVerge.
Creates symlinks + thumbnails + previews + HTML under 02_PROJECTS/ONE_TIME_REVIEW.

Safety: never descends into 私人别碰; does not move/delete originals.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from stateverge_paths import SSD_ROOT  # noqa: E402

try:
    from stateverge_protected import is_protected_path
except ImportError:
    def is_protected_path(path: str | Path) -> bool:
        return "私人别碰" in str(path)


# -----------------------------------------------------------------------------
# Constants
# -----------------------------------------------------------------------------

VIDEO_EXT = frozenset({".mov", ".mp4", ".m4v", ".hevc", ".mts", ".ts"})
IMAGE_EXT = frozenset({".jpg", ".jpeg", ".png", ".heic", ".dng", ".tif", ".tiff"})

EXCLUDE_DIR_NAMES = frozenset(
    {
        "私人别碰",
        "ONE_TIME_REVIEW",
        "03_OUTPUT",
        "04_ARCHIVE",
        "05_INDEX",
        "06_REPORTS",
        "07_AUTOMATION",
        "08_CONFIG",
        ".Trash",
        ".Trashes",
        ".Spotlight-V100",
        ".TemporaryItems",
        ".fseventsd",
        "__pycache__",
        "node_modules",
        ".git",
        ".venv",
    }
)

LOCATION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("chinatown", "chinatown"),
    ("times_square", "times_square"),
    ("times square", "times_square"),
    ("grand_central", "grand_central"),
    ("grand central", "grand_central"),
    ("financial_district", "financial_district"),
    ("financial district", "financial_district"),
    ("wall_street", "wall_street"),
    ("wall street", "wall_street"),
    ("central_park", "central_park"),
    ("central park", "central_park"),
    ("midtown", "midtown"),
    ("downtown", "downtown"),
    ("soho", "soho"),
    ("tribeca", "tribeca"),
    ("brooklyn", "brooklyn"),
    ("manhattan", "manhattan"),
)

TIMELAPSE_KW_RE = re.compile(
    r"timelapse|time-lapse|time_lapse|延时|缩时",
    re.IGNORECASE,
)

SV_NAME_RE = re.compile(r"^SV-(\d{8})__")


@dataclass
class ScanStats:
    video_candidates: int = 0
    image_candidates: int = 0
    timelapse_isolated: int = 0
    thumbs_ok: int = 0
    thumbs_skipped: int = 0
    thumbs_fail: int = 0
    previews_ok: int = 0
    previews_skipped: int = 0
    previews_fail: int = 0
    link_ok: int = 0
    link_skip: int = 0
    errors: list[str] = field(default_factory=list)


def log_line(log_fp: Path | None, msg: str) -> None:
    print(msg, flush=True)
    if log_fp is not None:
        try:
            with log_fp.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass


def ensure_review_dirs(root: Path) -> Path:
    base = root / "02_PROJECTS" / "ONE_TIME_REVIEW"
    sub = [
        base / "all_candidates" / "video",
        base / "all_candidates" / "image",
        base / "selected" / "video",
        base / "selected" / "image",
        base / "rejected" / "video",
        base / "rejected" / "image",
        base / "timelapse_candidates",
        base / "gallery" / "thumbs" / "video",
        base / "gallery" / "thumbs" / "image",
        base / "gallery" / "previews",
        base / "logs",
    ]
    for p in sub:
        p.mkdir(parents=True, exist_ok=True)
    return base


def should_exclude_dir(name: str) -> bool:
    return name in EXCLUDE_DIR_NAMES


def iter_media_files(root: Path) -> Iterable[Path]:
    """Walk root, skipping excluded subtrees entirely (never enters 私人别碰)."""
    root = root.resolve()
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for entry in it:
                    try:
                        name = entry.name
                    except OSError:
                        continue
                    if name.startswith("._"):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if should_exclude_dir(name):
                            continue
                        try:
                            stack.append(Path(entry.path))
                        except OSError:
                            continue
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    if name.startswith("._"):
                        continue
                    p = Path(entry.path)
                    suf = p.suffix.lower()
                    if suf in VIDEO_EXT or suf in IMAGE_EXT:
                        yield p
        except (PermissionError, OSError):
            continue


def time_bucket_from_dt(hour: int | None) -> str:
    if hour is None:
        return "unknown_time"
    if 5 <= hour <= 10:
        return "morning"
    if 11 <= hour <= 15:
        return "daytime"
    if 16 <= hour <= 19:
        return "golden_hour"
    # 20–23 and 0–4
    return "night"


def infer_location_slug(path: Path, gps_lat: float | None, gps_lon: float | None) -> str:
    if gps_lat is not None and gps_lon is not None:
        return "gps_tagged"
    blob = f"{path.as_posix().lower()} {path.name.lower()}"
    for needle, slug in LOCATION_KEYWORDS:
        if needle in blob:
            return slug
    return "unknown_location"


def device_tier_from_make_model(make: str, model: str) -> str:
    mk = (make or "").lower()
    md = (model or "").lower()
    if "apple" in mk:
        if "iphone 17 pro max" in md:
            return "iphone_17_pro_max"
        if "iphone" in md:
            return "iphone"
        return "apple_other"
    if make or model:
        return "camera_other"
    return "unknown"


def orientation_from_wh(w: int | None, h: int | None) -> str:
    if not w or not h:
        return "unknown"
    if h > w:
        return "portrait"
    if w > h:
        return "landscape"
    return "square"


def parse_exif_datetime(val: str | None) -> tuple[str | None, str | None, int | None]:
    """Return (shoot_date YYYY-MM-DD, shoot_time HH:MM:SS, hour)."""
    if not val or not isinstance(val, str):
        return None, None, None
    val = val.strip()
    for fmt in (
        "%Y:%m:%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y:%m:%d",
        "%Y-%m-%d",
    ):
        try:
            import datetime as _dt

            if fmt.endswith("%H:%M:%S"):
                dt = _dt.datetime.strptime(val.replace("-", ":").replace("T", " ")[:19], fmt)
            else:
                dt = _dt.datetime.strptime(val[:10].replace(":", "-"), "%Y-%m-%d")
            return dt.date().isoformat(), dt.time().isoformat(timespec="seconds"), dt.hour
        except ValueError:
            continue
    return None, None, None


def _first_tag(obj: dict, *keys: str):
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def exiftool_batch(paths: list[Path]) -> dict[str, dict]:
    """Run exiftool -json -n on a batch; return path -> first json object."""
    if not paths or not shutil.which("exiftool"):
        return {}
    cmd = ["exiftool", "-json", "-n", "-charset", "UTF8", *[str(p) for p in paths]]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=600)
        data = json.loads(out.decode("utf-8", errors="replace"))
        if not isinstance(data, list):
            return {}
        by_src: dict[str, dict] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            src = item.get("SourceFile")
            if isinstance(src, str):
                by_src[src] = item
        return by_src
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return {}


def ffprobe_meta(path: Path) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    cmd = [
        "ffprobe",
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=120)
        return json.loads(out.decode("utf-8", errors="replace"))
    except (subprocess.CalledProcessError, json.JSONDecodeError, OSError):
        return {}


def extract_media_metadata(path: Path, media_type: str, exif: dict | None) -> dict:
    exif = exif or {}
    fp = ffprobe_meta(path) if media_type == "video" else {}

    w = int(_first_tag(exif, "ImageWidth", "ExifImageWidth") or 0) or None
    h = int(_first_tag(exif, "ImageHeight", "ExifImageHeight") or 0) or None
    duration = None
    fps = None
    codec = None

    if media_type == "video":
        streams = fp.get("streams") if isinstance(fp, dict) else None
        if isinstance(streams, list):
            for s in streams:
                if not isinstance(s, dict):
                    continue
                if s.get("codec_type") == "video":
                    cw = s.get("width")
                    ch = s.get("height")
                    if cw:
                        w = int(float(cw))
                    if ch:
                        h = int(float(ch))
                    codec = s.get("codec_name")
                    fr = s.get("avg_frame_rate") or s.get("r_frame_rate")
                    if isinstance(fr, str) and "/" in fr:
                        a, b = fr.split("/", 1)
                        try:
                            fa, fb = float(a), float(b)
                            if fb:
                                fps = fa / fb
                        except ValueError:
                            pass
                    break
        fmt = fp.get("format") if isinstance(fp, dict) else None
        if isinstance(fmt, dict) and fmt.get("duration"):
            try:
                duration = float(fmt["duration"])
            except (TypeError, ValueError):
                duration = None
        if duration is None:
            dtags = _first_tag(exif, "Duration", "MediaDuration")
            if dtags is not None:
                try:
                    duration = float(dtags)
                except (TypeError, ValueError):
                    pass

    make = _first_tag(exif, "Make", "CameraMake")
    model = _first_tag(exif, "Model", "CameraModel")
    if isinstance(make, str):
        make = make.strip()
    else:
        make = ""
    if isinstance(model, str):
        model = model.strip()
    else:
        model = ""

    # Creation time priority
    ctime_raw = _first_tag(
        exif,
        "QuickTime:CreationDate",
        "CreationDate",
        "CreateDate",
        "DateTimeOriginal",
        "MediaCreateDate",
        "TrackCreateDate",
    )
    if isinstance(ctime_raw, str):
        pass
    else:
        ctime_raw = None

    shoot_date, shoot_time, hour = parse_exif_datetime(ctime_raw)
    if hour is None and path.is_file():
        try:
            import datetime as _dt

            mt = path.stat().st_mtime
            dt = _dt.datetime.fromtimestamp(mt)
            shoot_date = shoot_date or dt.date().isoformat()
            shoot_time = shoot_time or dt.time().isoformat(timespec="seconds")
            hour = dt.hour
        except OSError:
            pass

    tb = time_bucket_from_dt(hour)

    gps_lat = _first_tag(exif, "GPSLatitude")
    gps_lon = _first_tag(exif, "GPSLongitude")
    try:
        gps_lat_f = float(gps_lat) if gps_lat is not None else None
    except (TypeError, ValueError):
        gps_lat_f = None
    try:
        gps_lon_f = float(gps_lon) if gps_lon is not None else None
    except (TypeError, ValueError):
        gps_lon_f = None

    location_slug = infer_location_slug(path, gps_lat_f, gps_lon_f)

    orientation = orientation_from_wh(w, h)
    # EXIF orientation tag
    rot = _first_tag(exif, "Orientation")
    if isinstance(rot, int) and rot in (5, 6, 7, 8):
        orientation = "portrait" if orientation == "landscape" else orientation

    return {
        "creation_time_raw": ctime_raw,
        "shoot_date": shoot_date or "",
        "shoot_time": shoot_time or "",
        "time_bucket": tb,
        "make": make,
        "model": model,
        "device_tier": device_tier_from_make_model(make, model),
        "gps_lat": gps_lat_f,
        "gps_lon": gps_lon_f,
        "width": w or 0,
        "height": h or 0,
        "orientation": orientation,
        "duration_seconds": duration if duration is not None else 0.0,
        "fps": fps,
        "codec": codec or "",
        "location_slug": location_slug,
    }


def detect_timelapse(
    path: Path, media_type: str, meta: dict, exif_blob: str
) -> tuple[bool, str]:
    blob = f"{path.as_posix()} {path.name} {exif_blob}".lower()
    if TIMELAPSE_KW_RE.search(blob):
        return True, "keyword"
    if media_type != "video":
        return False, ""
    fps = meta.get("fps")
    dur = float(meta.get("duration_seconds") or 0)
    if fps is not None and dur > 10 and fps <= 1.0:
        return True, f"fps_duration fps={fps:.3f}"
    if fps is not None and dur > 120 and fps <= 2.0:
        return True, f"fps_duration fps={fps:.3f}"
    return False, ""


def create_link(src: Path, dst: Path, log_fp: Path | None, asset_code: str, stats: ScanStats) -> None:
    try:
        if dst.exists() or dst.is_symlink():
            dst.unlink()
    except OSError:
        pass
    try:
        os.symlink(src.resolve(), dst)
        stats.link_ok += 1
        log_line(log_fp, f"LINK_OK asset_code={asset_code} link={dst}")
    except OSError:
        try:
            os.link(src.resolve(), dst)
            stats.link_ok += 1
            log_line(log_fp, f"LINK_OK asset_code={asset_code} hardlink={dst}")
        except OSError as e:
            stats.link_skip += 1
            log_line(log_fp, f"LINK_SKIP asset_code={asset_code} reason={e}")


def ffmpeg_thumb(src: Path, dst: Path, *, media_type: str) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if media_type == "video":
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            "00:00:02",
            "-i",
            str(src),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(dst),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-vf",
            "scale=640:-1",
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(dst),
        ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.returncode == 0 and dst.is_file()
    except (OSError, subprocess.TimeoutExpired):
        return False


def ffmpeg_preview(src: Path, dst: Path) -> bool:
    dst.parent.mkdir(parents=True, exist_ok=True)
    for ss in ("00:00:02", "00:00:00"):
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            ss,
            "-t",
            "5",
            "-i",
            str(src),
            "-vf",
            "scale=480:-1",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            str(dst),
        ]
        try:
            if dst.is_file():
                dst.unlink()
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if r.returncode == 0 and dst.is_file():
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def build_html(gallery_dir: Path, rows: list[dict]) -> None:
    payload = json.dumps(rows, ensure_ascii=False)
    html = f"""<!DOCTYPE html>
<html lang="zh-Hans">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>StateVerge ONE_TIME_REVIEW</title>
  <style>
    :root {{
      font-family: system-ui, sans-serif;
      background: #0f1115;
      color: #e8eaef;
    }}
    header {{
      padding: 1rem 1.25rem;
      background: #161a22;
      position: sticky;
      top: 0;
      z-index: 10;
      border-bottom: 1px solid #2a3140;
    }}
    .controls {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.5rem;
      align-items: center;
      margin-top: 0.5rem;
    }}
    label {{ font-size: 0.85rem; color: #9aa3b5; }}
    select, input {{
      background: #1e2430;
      color: #e8eaef;
      border: 1px solid #3d465a;
      border-radius: 6px;
      padding: 0.35rem 0.5rem;
    }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
      gap: 1rem;
      padding: 1rem;
    }}
    .card {{
      background: #161a22;
      border-radius: 10px;
      border: 1px solid #2a3140;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }}
    .thumb-wrap {{
      position: relative;
      background: #000;
      aspect-ratio: 16/10;
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .thumb-wrap img {{
      max-width: 100%;
      max-height: 100%;
      object-fit: contain;
    }}
    .thumb-wrap video {{
      max-width: 100%;
      max-height: 100%;
    }}
    .card-body {{ padding: 0.65rem 0.75rem; font-size: 0.8rem; line-height: 1.35; }}
    .code {{ font-weight: 700; color: #7ab8ff; }}
    .muted {{ color: #9aa3b5; font-size: 0.72rem; word-break: break-all; }}
    a {{ color: #9dceff; }}
    .stat {{ color: #f2c14e; }}
  </style>
</head>
<body>
  <header>
    <h1 style="margin:0;font-size:1.1rem">StateVerge 一次性人工筛选库</h1>
    <div class="controls">
      <label>类型 <select id="fMedia"><option value="">all</option><option value="video">video</option><option value="image">image</option></select></label>
      <label>方向 <select id="fOrient"><option value=""></option></select></label>
      <label>设备 <select id="fDev"><option value=""></option></select></label>
      <label>时段 <select id="fBucket"><option value=""></option></select></label>
      <label>地点 <select id="fLoc"><option value=""></option></select></label>
      <label>日期 <select id="fDate"><option value=""></option></select></label>
      <label>搜索 <input id="fSearch" type="search" placeholder="SV / 文件名 / 地点…" size="28"/></label>
      <span class="stat" id="countLab"></span>
    </div>
  </header>
  <div class="grid" id="grid"></div>
  <script>
const RAW = {payload};

function uniq(vals) {{
  return [...new Set(vals.filter(Boolean))].sort();
}}

function fillSelect(sel, values) {{
  const cur = sel.value;
  sel.innerHTML = '';
  const o0 = document.createElement('option');
  o0.value = '';
  o0.textContent = '(全部)';
  sel.appendChild(o0);
  values.forEach(v => {{
    const o = document.createElement('option');
    o.value = v;
    o.textContent = v;
    sel.appendChild(o);
  }});
  if ([...sel.options].some(x => x.value === cur)) sel.value = cur;
}}

function fileUrl(p) {{
  if (!p) return '#';
  if (p.startsWith('/')) return 'file://' + p;
  return 'file:///' + p.replace(/\\\\/g,'/');
}}

function cardHtml(r) {{
  const isVid = r.media_type === 'video';
  const preview = r.preview_rel || '';
  const thumb = r.thumb_rel || '';
  const imgTag = thumb
    ? `<img src="${{thumb}}" alt="${{r.asset_code}}" loading="lazy"/>`
    : '<div class="muted">no thumb</div>';
  const vidOverlay = isVid && preview
    ? `<video src="${{preview}}" muted playsinline loop preload="metadata"
        style="display:none;width:100%;height:100%;object-fit:contain"
        onmouseenter="this.style.display='block';this.previousElementSibling.style.display='none';this.play().catch(()=>{{}});"
        onmouseleave="this.pause();this.currentTime=0;this.style.display='none';this.previousElementSibling.style.display='block';"></video>`
    : '';
  const clickPlay = isVid && preview
    ? `<div style="margin-top:4px"><a href="#" onclick="event.preventDefault();var v=this.nextElementSibling; v.style.display=v.style.display==='block'?'none':'block'; if(v.style.display==='block'){{v.play();}} else {{v.pause();}}">切换预览播放</a><video src="${{preview}}" controls muted style="display:none;width:100%;margin-top:4px"></video></div>`
    : '';
  return `
  <div class="card" data-code="${{r.asset_code}}">
    <div class="thumb-wrap">
      ${{imgTag}}
      ${{vidOverlay}}
    </div>
    <div class="card-body">
      <div><span class="code">${{r.asset_code}}</span> · ${{r.media_type}} · <span class="stat">${{r.status}}</span></div>
      <div>${{r.shoot_date || '?'}} · ${{r.time_bucket}} · ${{r.location_slug}} · ${{r.device_tier}}</div>
      <div>${{r.orientation}} ${{isVid ? '· ' + (r.duration_seconds||0).toFixed(1) + 's' : ''}}</div>
      <div class="muted">${{(r.original_path || '')}}</div>
      <div><a href="${{fileUrl(r.original_path)}}">打开原文件</a> · <a href="${{fileUrl(r.review_link_path)}}">review 链接</a></div>
      ${{clickPlay}}
    </div>
  </div>`;
}}

function populateFilters(rows) {{
  fillSelect(document.getElementById('fOrient'), uniq(rows.map(r => r.orientation)));
  fillSelect(document.getElementById('fDev'), uniq(rows.map(r => r.device_tier)));
  fillSelect(document.getElementById('fBucket'), uniq(rows.map(r => r.time_bucket)));
  fillSelect(document.getElementById('fLoc'), uniq(rows.map(r => r.location_slug)));
  fillSelect(document.getElementById('fDate'), uniq(rows.map(r => r.shoot_date)));
}}

function apply() {{
  const mt = document.getElementById('fMedia').value;
  const or = document.getElementById('fOrient').value;
  const dv = document.getElementById('fDev').value;
  const tb = document.getElementById('fBucket').value;
  const lc = document.getElementById('fLoc').value;
  const dt = document.getElementById('fDate').value;
  const q = document.getElementById('fSearch').value.trim().toLowerCase();
  const rows = RAW.filter(r => {{
    if (mt && r.media_type !== mt) return false;
    if (or && r.orientation !== or) return false;
    if (dv && r.device_tier !== dv) return false;
    if (tb && r.time_bucket !== tb) return false;
    if (lc && r.location_slug !== lc) return false;
    if (dt && r.shoot_date !== dt) return false;
    if (q) {{
      const blob = (r.asset_code + ' ' + r.original_path + ' ' + r.location_slug + ' ' + r.shoot_date + ' ' + r.device_tier).toLowerCase();
      if (!blob.includes(q)) return false;
    }}
    return true;
  }});
  document.getElementById('grid').innerHTML = rows.map(cardHtml).join('');
  document.getElementById('countLab').textContent = rows.length + ' / ' + RAW.length;
}}

['fMedia','fOrient','fDev','fBucket','fLoc','fDate'].forEach(id => {{
  document.getElementById(id).addEventListener('change', apply);
}});
document.getElementById('fSearch').addEventListener('input', apply);

populateFilters(RAW);
apply();
  </script>
</body>
</html>
"""
    (gallery_dir / "index.html").write_text(html, encoding="utf-8")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            flat = dict(r)
            for k, v in list(flat.items()):
                if isinstance(v, (dict, list)):
                    flat[k] = json.dumps(v, ensure_ascii=False)
            w.writerow(flat)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build ONE_TIME_REVIEW gallery (symlinks + index + HTML).")
    ap.add_argument("--root", type=Path, default=SSD_ROOT)
    ap.add_argument("--max-files", type=int, default=0, help="Limit processed files (0 = no limit)")
    ap.add_argument("--media-filter", choices=("all", "video", "image"), default="all")
    ap.add_argument("--no-preview", action="store_true", help="Skip video preview mp4 generation")
    ap.add_argument("--rebuild-thumbs", action="store_true", help="Regenerate thumbnails/previews even if cached")
    ns = ap.parse_args()
    root = ns.root.expanduser().resolve()
    if not root.is_dir():
        print(f"ROOT_NOT_FOUND: {root}", file=sys.stderr)
        return 1

    base = ensure_review_dirs(root)
    gallery = base / "gallery"
    logs_dir = base / "logs"
    ts = time.strftime("%Y%m%d_%H%M%S")
    log_fp = logs_dir / f"build_one_time_review_{ts}.log"

    stats = ScanStats()
    log_line(log_fp, f"START root={root} max_files={ns.max_files} filter={ns.media_filter}")

    collected: list[Path] = []
    for p in sorted(iter_media_files(root), key=lambda x: str(x).lower()):
        suf = p.suffix.lower()
        is_vid = suf in VIDEO_EXT
        is_img = suf in IMAGE_EXT
        if ns.media_filter == "video" and not is_vid:
            continue
        if ns.media_filter == "image" and not is_img:
            continue
        collected.append(p)
        if ns.max_files and len(collected) >= ns.max_files:
            break

    collected = [
        p for p in collected if not is_protected_path(p)
    ]

    # Batch exiftool in chunks
    exif_by_path: dict[str, dict] = {}
    chunk = 80
    for i in range(0, len(collected), chunk):
        batch = collected[i : i + chunk]
        exif_by_path.update(exiftool_batch(batch))

    jsonl_path = base / "review_index.jsonl"
    csv_path = base / "review_index.csv"

    rows_out: list[dict] = []
    fieldnames = [
        "asset_code",
        "asset_id",
        "media_type",
        "original_path",
        "review_link_path",
        "thumbnail_path",
        "preview_path",
        "shoot_date",
        "shoot_time",
        "time_bucket",
        "location_slug",
        "gps_lat",
        "gps_lon",
        "device_tier",
        "duration_seconds",
        "width",
        "height",
        "orientation",
        "fps",
        "codec",
        "is_timelapse",
        "timelapse_reason",
        "status",
        "notes",
    ]

    with jsonl_path.open("w", encoding="utf-8") as jf:
        for idx, src in enumerate(collected, start=1):
            asset_code = f"SV-{idx:08d}"
            asset_id = f"{idx:08d}"
            suf = src.suffix.lower()
            media_type = "video" if suf in VIDEO_EXT else "image"

            exif = exif_by_path.get(str(src), {})
            exif_blob = json.dumps(exif, ensure_ascii=False) if exif else ""
            meta = extract_media_metadata(src, media_type, exif)

            is_tl, tl_reason = detect_timelapse(src, media_type, meta, exif_blob)
            if is_tl:
                stats.timelapse_isolated += 1
                review_sub = base / "timelapse_candidates"
                log_line(log_fp, f"TIMELAPSE_REVIEW_ISOLATED asset_code={asset_code} path={src}")
            elif media_type == "video":
                review_sub = base / "all_candidates" / "video"
                stats.video_candidates += 1
            else:
                review_sub = base / "all_candidates" / "image"
                stats.image_candidates += 1

            link_name = f"{asset_code}__{src.name}"
            review_dst = review_sub / link_name
            create_link(src, review_dst, log_fp, asset_code, stats)

            thumb_dir = gallery / "thumbs" / ("video" if media_type == "video" else "image")
            thumb_path = thumb_dir / f"{asset_code}.jpg"
            if ns.rebuild_thumbs and thumb_path.exists():
                try:
                    thumb_path.unlink()
                except OSError:
                    pass
            if thumb_path.is_file():
                stats.thumbs_skipped += 1
            else:
                if ffmpeg_thumb(src, thumb_path, media_type=media_type):
                    stats.thumbs_ok += 1
                else:
                    stats.thumbs_fail += 1
                    log_line(log_fp, f"THUMB_FAIL asset_code={asset_code} path={src}")

            preview_path_obj: Path | None = None
            if media_type == "video" and not ns.no_preview:
                preview_path_obj = gallery / "previews" / f"{asset_code}.mp4"
                if ns.rebuild_thumbs and preview_path_obj.exists():
                    try:
                        preview_path_obj.unlink()
                    except OSError:
                        pass
                if preview_path_obj.is_file():
                    stats.previews_skipped += 1
                else:
                    if ffmpeg_preview(src, preview_path_obj):
                        stats.previews_ok += 1
                    else:
                        stats.previews_fail += 1
                        log_line(log_fp, f"PREVIEW_FAIL asset_code={asset_code} path={src}")
            elif media_type == "video" and ns.no_preview:
                preview_path_obj = gallery / "previews" / f"{asset_code}.mp4"

            thumb_rel = thumb_path.relative_to(gallery).as_posix() if thumb_path.is_file() else ""
            preview_rel = (
                preview_path_obj.relative_to(gallery).as_posix()
                if preview_path_obj and preview_path_obj.is_file()
                else ""
            )

            row = {
                "asset_code": asset_code,
                "asset_id": asset_id,
                "media_type": media_type,
                "original_path": str(src.resolve()),
                # 使用筛选区 symlink 自身路径；勿用 .resolve()（会跟链到 original_path）
                "review_link_path": str(review_dst) if review_dst else "",
                "thumbnail_path": str(thumb_path.resolve()) if thumb_path.is_file() else "",
                "preview_path": str(preview_path_obj.resolve())
                if preview_path_obj and preview_path_obj.is_file()
                else "",
                "shoot_date": meta["shoot_date"],
                "shoot_time": meta["shoot_time"],
                "time_bucket": meta["time_bucket"],
                "location_slug": meta["location_slug"],
                "gps_lat": meta.get("gps_lat"),
                "gps_lon": meta.get("gps_lon"),
                "device_tier": meta["device_tier"],
                "duration_seconds": float(meta.get("duration_seconds") or 0),
                "width": int(meta.get("width") or 0),
                "height": int(meta.get("height") or 0),
                "orientation": meta["orientation"],
                "fps": meta.get("fps"),
                "codec": meta.get("codec") or "",
                "is_timelapse": is_tl,
                "timelapse_reason": tl_reason,
                "status": "pending",
                "notes": "",
                # HTML helpers (stripped on CSV via extrasaction)
                "thumb_rel": thumb_rel,
                "preview_rel": preview_rel,
            }

            jf.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows_out.append(row)

    # Strip helper keys for CSV
    csv_rows = []
    for r in rows_out:
        cr = {k: r.get(k) for k in fieldnames}
        csv_rows.append(cr)

    write_csv(csv_path, fieldnames, csv_rows)

    # HTML rows: drop None fps for JSON
    html_rows = []
    for r in rows_out:
        hr = {k: r[k] for k in r if k in fieldnames or k in ("thumb_rel", "preview_rel")}
        if hr.get("fps") is None:
            hr["fps"] = ""
        html_rows.append(hr)

    build_html(gallery, html_rows)

    log_line(log_fp, "DONE")
    print(
        "\n=== SUMMARY ===\n"
        f"video_candidates(non-timelapse): {stats.video_candidates}\n"
        f"image_candidates: {stats.image_candidates}\n"
        f"timelapse_isolated: {stats.timelapse_isolated}\n"
        f"thumbs_ok: {stats.thumbs_ok} skipped: {stats.thumbs_skipped} fail: {stats.thumbs_fail}\n"
        f"previews_ok: {stats.previews_ok} skipped: {stats.previews_skipped} fail: {stats.previews_fail}\n"
        f"links_ok: {stats.link_ok} link_skip: {stats.link_skip}\n"
        f"review_index.jsonl: {jsonl_path}\n"
        f"gallery/index.html: {gallery / 'index.html'}\n"
        f"log: {log_fp}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
