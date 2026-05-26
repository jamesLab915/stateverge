#!/usr/bin/env python3
"""
NYC 自动化：阶段一请先用 ``stateverge_asset_inventory`` 建立索引与
``07_AUTOMATION/queues/nyc_*_ready.jsonl``；成片发布前查 ``05_INDEX/published_assets.jsonl``。
禁止索引或读取 ``/Volumes/StateVerge/私人别碰``；普通 long/short 不得扫描 ``01_ASSET_LIBRARY/timelapse``。
"""
import argparse
import os, subprocess, sys, json, time, shutil
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from stateverge_paths import SSD_ROOT as SV  # noqa: E402
try:
    from stateverge_protected import is_protected_path
except ImportError:
    def is_protected_path(p):
        return "私人别碰" in str(p)

try:
    from stateverge_publish_dedupe import (
        PUBLISHED_JSONL,
        append_published,
        build_candidate_from_meta,
        check_publish_allowed,
        load_assets_master_index,
        load_published_entries,
        log_line as publish_log,
        update_assets_usage,
    )
except ImportError:
    PUBLISHED_JSONL = None
    append_published = None
    build_candidate_from_meta = None
    check_publish_allowed = None
    load_assets_master_index = None
    load_published_entries = None
    publish_log = None
    update_assets_usage = None


# 统一 AirDrop / 手动丢入入口（全卷唯一入口，不依赖项目内 raw/airdrop）
CENTRAL_INBOX_AIRDROP = SV / "00_INBOX" / "airdrop"
ASSET_INDEX_JSON = SV / "01_ASSET_LIBRARY" / "_index" / "asset_index.json"
ASSET_TIMELAPSE_LIB = SV / "01_ASSET_LIBRARY" / "03_TIMELAPSE"

# YouTube 频道 ID（下一步接 API 时使用；当前仅用于模拟打印）
SHORTS_CHANNEL_ID = "你的Shorts频道ID"
LONG_CHANNEL_ID = "你的Long频道ID"

UPLOAD_HISTORY_PATH = SV / "07_AUTOMATION" / "upload_history.json"
PUBLISH_DATE_TZ = "America/New_York"

ROOT: Path
RAW: Path
AIRDROP: Path
TIMELAPSE_RAW: Path
PROCESSED: Path
VIDEO: Path
TIMELAPSE: Path
OUTPUT: Path
OUT_LONG: Path
OUT_SHORTS: Path
OUT_TIMELAPSE: Path
MUSIC: Path
LOG: Path


def get_nyc_root() -> Path:
    """优先新布局 02_PROJECTS/NYC_AUTO，否则旧路径 ``<STATEVERGE_VOL>/NYC_AUTO``。"""
    p = SV / "02_PROJECTS" / "NYC_AUTO"
    if p.is_dir():
        return p
    return SV / "NYC_AUTO"


def refresh_paths() -> None:
    global ROOT, RAW, AIRDROP, TIMELAPSE_RAW, PROCESSED, VIDEO, TIMELAPSE
    global OUTPUT, OUT_LONG, OUT_SHORTS, OUT_TIMELAPSE, MUSIC, LOG
    ROOT = get_nyc_root()
    RAW = ROOT / "raw"
    AIRDROP = RAW / "airdrop"
    TIMELAPSE_RAW = RAW / "timelapse"
    PROCESSED = ROOT / "processed"
    VIDEO = PROCESSED / "video"
    TIMELAPSE = PROCESSED / "timelapse"
    OUTPUT = ROOT / "output"
    OUT_LONG = OUTPUT / "long"
    OUT_SHORTS = OUTPUT / "shorts"
    OUT_TIMELAPSE = OUTPUT / "timelapse"
    MUSIC = ROOT / "assets" / "music"
    LOG = ROOT / "logs"
    LOG.mkdir(parents=True, exist_ok=True)


refresh_paths()


def detect_video_type(path: str | Path) -> str:
    """根据输出路径判断长/短：shorts 与 timelapse 视为 Short，long 为长视频。"""
    p = str(path).replace("\\", "/")
    if "/shorts/" in p or "/timelapse/" in p:
        return "short"
    if "/long/" in p:
        return "long"
    return "unknown"


def assert_path_matches_video_type(video_path: Path, video_type: str) -> None:
    """防止 Short 进 long 输出目录或 Long 进 shorts/timelapse（仅看路径段，避免文件名含 long/shorts 误报）。"""
    p = str(video_path).replace("\\", "/")
    if video_type == "short" and "/long/" in p:
        raise Exception("SHORT_WRONG_CHANNEL")
    if video_type == "long" and ("/shorts/" in p or "/timelapse/" in p):
        raise Exception("LONG_WRONG_CHANNEL")


def get_file_hash(path: Path) -> str:
    """全文件 SHA1（流式），用于去重。"""
    import hashlib

    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def load_upload_history() -> dict:
    if not UPLOAD_HISTORY_PATH.is_file():
        return {}
    try:
        return json.loads(UPLOAD_HISTORY_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_upload_history(data: dict) -> None:
    UPLOAD_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOAD_HISTORY_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def sh(cmd):
    print(">>", cmd)
    return subprocess.run(cmd, shell=True)

def ensure_dirs():
    for p in [
        CENTRAL_INBOX_AIRDROP,
        AIRDROP,
        TIMELAPSE_RAW,
        VIDEO,
        TIMELAPSE,
        OUT_LONG,
        OUT_SHORTS,
        OUT_TIMELAPSE,
        MUSIC,
    ]:
        p.mkdir(parents=True, exist_ok=True)

def get_duration_safe(path: str | Path) -> float:
    """ffprobe 实时时长；长视频候选逻辑专用，不读索引 duration。"""
    try:
        r = subprocess.check_output(
            f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{path}"',
            shell=True,
            timeout=120,
        )
        return float(r.strip())
    except Exception:
        return 0.0


def get_duration(file):
    return get_duration_safe(file)

def classify():
    """00_INBOX/airdrop 由素材库 ingest（stateverge_asset_library_rebuild）处理；本流水线不再移动 inbox 文件。"""
    if "私人别碰" in str(CENTRAL_INBOX_AIRDROP):
        return
    if not CENTRAL_INBOX_AIRDROP.is_dir():
        return
    n = sum(
        1
        for x in CENTRAL_INBOX_AIRDROP.iterdir()
        if x.is_file() and not x.name.startswith(".")
    )
    if n:
        print(
            f"INBOX_LEAVE_IN_PLACE: {CENTRAL_INBOX_AIRDROP} ({n} files; run asset_library_rebuild)"
        )

def build_concat_list(files, path):
    with open(path, "w") as f:
        for x in files:
            f.write(f"file '{x}'\n")

def process_video():
    vids = list(VIDEO.glob("*.mp4")) + list(VIDEO.glob("*.mov"))
    if not vids:
        return

    vids = sorted(vids)
    lst = ROOT / "list.txt"
    build_concat_list(vids, lst)

    merged = ROOT / "merged.mp4"
    sh(f'ffmpeg -y -f concat -safe 0 -i "{lst}" -c copy "{merged}"')

    # 25分钟版本（主视频）
    out25 = OUT_LONG / f"nyc_25min_{int(time.time())}.mp4"
    sh(f'ffmpeg -y -i "{merged}" -t 1500 -r 60 -c:v libx264 -crf 20 -preset fast "{out25}"')

    # 2小时循环版（收益）
    out2h = OUT_LONG / f"nyc_2h_{int(time.time())}.mp4"
    sh(f'ffmpeg -y -stream_loop -1 -i "{out25}" -t 7200 -r 30 -c:v libx264 -crf 23 "{out2h}"')


def get_mtime(file: Path):
    try:
        return file.stat().st_mtime
    except:
        return 0

def time_bucket(file: Path):
    """
    根据文件修改时间粗分时段：
    morning / day / golden_hour / night
    后续可升级为读取 EXIF/GPS 时间。
    """
    import datetime
    h = datetime.datetime.fromtimestamp(get_mtime(file)).hour
    if 5 <= h < 11:
        return "morning"
    if 11 <= h < 16:
        return "day"
    if 16 <= h < 20:
        return "golden_hour"
    return "night"

def is_valid_clip(file: Path):
    """
    基础质量门槛：
    - 视频必须大于10秒
    - 文件大小不能太小
    """
    if not file.exists():
        return False
    if file.stat().st_size < 5 * 1024 * 1024:
        return False
    d = get_duration(file)
    if d < 10:
        return False
    return True


def is_nyc_asset_entry(e: dict) -> bool:
    loc = (e.get("location") or "unknown").lower()
    if loc in ("manhattan", "brooklyn", "queens", "nyc"):
        return True
    op = (e.get("original_path") or "").lower()
    lp = (e.get("library_path") or "").lower()
    blob = f"{op} {lp}"
    if any(
        x in blob
        for x in ("nyc", "new_york", "new york", "new-york", "manhattan", "brooklyn", "queens")
    ):
        return True
    if "nyc_auto" in blob:
        return True
    return False


def load_asset_index_entries() -> list[dict]:
    """解析 asset_index.json：支持顶层 list、{entries: [...]}，或其它 dict 形态（避免对 dict 做切片）。"""
    if not ASSET_INDEX_JSON.is_file():
        return []
    try:
        raw = json.loads(ASSET_INDEX_JSON.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [x for x in raw if isinstance(x, dict)]
        if isinstance(raw, dict):
            ent = raw.get("entries")
            if isinstance(ent, list):
                return [x for x in ent if isinstance(x, dict)]
            # 无 entries：可能是 id -> record，或误把整包当 dict
            out: list[dict] = []
            for v in raw.values():
                if isinstance(v, dict):
                    out.append(v)
                elif isinstance(v, list):
                    out.extend(x for x in v if isinstance(x, dict))
            return out
        return []
    except (json.JSONDecodeError, OSError, TypeError):
        return []


LONG_INDEX_MIN_SEC = 600.0
_LONG_INDEX_EXTS = frozenset({".mp4", ".mov", ".m4v"})


def select_clips_from_asset_index() -> list[Path] | None:
    """
    从 asset_index.json 选取长视频候选（临时规则）：
    - 非延时类目；.mp4/.mov/.m4v；时长仅用 ffprobe（不用索引 duration）
    - duration >= 600s；按 time_bucket 分组，选总时长最长的一组，组内按 mtime 排序。
    """
    entries = load_asset_index_entries()
    candidates: list[tuple[Path, dict, float]] = []
    for e in entries:
        if e.get("scene") == "timelapse":
            continue
        cat = str(e.get("category") or "")
        if "03_TIMELAPSE" in cat:
            continue
        lp = e.get("library_path")
        if not lp:
            continue
        p = Path(lp)
        if not p.is_file():
            continue
        if p.suffix.lower() not in _LONG_INDEX_EXTS:
            continue
        if p.name.startswith("._"):
            continue
        duration = get_duration_safe(p)
        print("CHECK_VIDEO:", p, "duration=", duration)
        if duration < LONG_INDEX_MIN_SEC:
            continue
        try:
            if p.stat().st_size < 5 * 1024 * 1024:
                continue
        except OSError:
            continue
        candidates.append((p, e, duration))

    print(f"INDEX_LONG_CANDIDATES: {len(candidates)}")
    if not candidates:
        print("NO_INDEX_LONG_CANDIDATES")
        return None

    groups: dict[str, list[tuple[Path, dict, float]]] = {}
    for p, e, dur in candidates:
        tb = str(e.get("time_bucket") or "").strip()
        if not tb:
            tb = time_bucket(p)
        groups.setdefault(tb, []).append((p, e, dur))

    best_key: str | None = None
    best_total = 0.0
    best_items: list[tuple[Path, dict, float]] = []
    for bucket, items in groups.items():
        items.sort(key=lambda x: get_mtime(x[0]))
        total = sum(x[2] for x in items)
        if total > best_total:
            best_total = total
            best_key = bucket
            best_items = items

    if not best_items or best_key is None:
        print("NO_INDEX_LONG_CANDIDATES")
        return None

    print(
        f"SELECT_GROUP: {best_key} clips={len(best_items)} duration={int(best_total)}s"
    )
    return [x[0] for x in best_items]


def collect_timelapse_from_asset_index() -> list[Path]:
    """仅用于切段输出，不做整条长片拼接。"""
    out: list[Path] = []
    for e in load_asset_index_entries():
        if e.get("scene") != "timelapse" and "03_TIMELAPSE" not in str(
            e.get("category") or ""
        ):
            continue
        lp = e.get("library_path")
        if not lp:
            continue
        p = Path(lp)
        if p.is_file() and is_nyc_asset_entry(e):
            out.append(p)
    if ASSET_TIMELAPSE_LIB.is_dir():
        for p in list(ASSET_TIMELAPSE_LIB.glob("*.mp4")) + list(
            ASSET_TIMELAPSE_LIB.glob("*.mov")
        ):
            if p.name.startswith("._"):
                continue
            nl = p.name.lower()
            if any(
                x in nl
                for x in ("nyc", "new_york", "manhattan", "brooklyn", "queens")
            ):
                if p not in out:
                    out.append(p)
    return out


def group_clips_by_bucket(files):
    groups = {}
    for f in files:
        if not is_valid_clip(f):
            print("SKIP_BAD_CLIP:", f.name)
            continue
        b = time_bucket(f)
        groups.setdefault(b, []).append(f)

    for b in groups:
        groups[b] = sorted(groups[b], key=get_mtime)

    return groups

def select_best_group(groups):
    """
    专业拼接原则：
    1. 同时段优先
    2. 总时长最长优先
    3. 时间顺序拼接
    """
    if not groups:
        return []

    scored = []
    for bucket, files in groups.items():
        total = sum(get_duration(f) for f in files)
        scored.append((total, bucket, files))

    scored.sort(reverse=True, key=lambda x: x[0])
    total, bucket, files = scored[0]

    print(f"SELECT_GROUP: {bucket}, clips={len(files)}, duration={int(total)}s")
    return files

def render_normalized_clip(src: Path, dst: Path):
    """
    每个片段先统一规格，避免直接 concat 出问题：
    - 4K
    - 60fps
    - H.264
    - AAC
    - 音频轻度归一
    """
    if dst.exists():
        return

    cmd = (
        f'ffmpeg -y -i "{src}" '
        f'-vf "scale=3840:2160:force_original_aspect_ratio=decrease,'
        f'pad=3840:2160:(ow-iw)/2:(oh-ih)/2,fps=60" '
        f'-c:v libx264 -preset fast -crf 20 '
        f'-c:a aac -b:a 192k '
        f'-af "loudnorm=I=-16:TP=-1.5:LRA=11" '
        f'"{dst}"'
    )
    sh(cmd)

def smart_concat(files, out: Path):
    """
    专业拼接：
    - 先统一每个片段
    - 再 concat
    - 不混白天/夜晚
    - 不混走路/车载逻辑，后续可加 metadata 标签
    """
    if not files:
        print("NO_FILES_FOR_SMART_CONCAT")
        return None

    work = ROOT / "_smart_concat"
    work.mkdir(parents=True, exist_ok=True)

    normalized = []
    for i, f in enumerate(files):
        nf = work / f"clip_{i:04d}.mp4"
        render_normalized_clip(f, nf)
        if nf.exists() and get_duration(nf) > 5:
            normalized.append(nf)

    if not normalized:
        print("NO_NORMALIZED_CLIPS")
        return None

    lst = work / "concat_list.txt"
    build_concat_list(normalized, lst)

    cmd = f'ffmpeg -y -f concat -safe 0 -i "{lst}" -c copy "{out}"'
    sh(cmd)

    return out if out.exists() else None

def process_video_smart():
    """
    替代旧 process_video：
    使用专业拼接逻辑：
    1. 优先 asset_index.json（长视频 >=600s，按 time_bucket 选最长总时长组）
    2. 否则 fallback processed/video（旧路径仍可用）
    3. 过滤垃圾片段
    4. 输出25分钟主视频 + 2小时循环版
    """
    selected: list[Path] = []
    from_index = select_clips_from_asset_index()
    if from_index:
        for f in from_index:
            if is_valid_clip(f):
                selected.append(f)
            else:
                print("SKIP_BAD_CLIP:", f.name)
    if not selected:
        vids: list[Path] = []
        for ext in ("*.mp4", "*.mov", "*.m4v"):
            vids.extend(VIDEO.glob(ext))
        if not vids:
            legacy = SV / "NYC_AUTO/processed/video"
            if legacy.is_dir() and legacy != VIDEO:
                print("FALLBACK_LEGACY_VIDEO:", legacy)
                for ext in ("*.mp4", "*.mov", "*.m4v"):
                    vids.extend(legacy.glob(ext))
        if not vids:
            print("NO_VIDEO_INPUT")
            return
        groups = group_clips_by_bucket(vids)
        selected = select_best_group(groups)

    if not selected:
        print("NO_SELECTED_CLIPS")
        return

    merged = ROOT / f"smart_merged_{int(time.time())}.mp4"
    smart_concat(selected, merged)

    if not merged.exists():
        print("MERGE_FAILED")
        return

    bucket = time_bucket(selected[0])
    ts = int(time.time())

    # 25分钟主视频
    out25 = OUT_LONG / f"nyc_{bucket}_25min_{ts}.mp4"
    sh(
        f'ffmpeg -y -i "{merged}" '
        f'-t 1500 '
        f'-c:v libx264 -preset fast -crf 20 '
        f'-c:a aac -b:a 192k '
        f'"{out25}"'
    )

    # 2小时循环收益版
    out2h = OUT_LONG / f"nyc_{bucket}_2h_{ts}.mp4"
    sh(
        f'ffmpeg -y -stream_loop -1 -i "{out25}" '
        f'-t 7200 -r 30 '
        f'-c:v libx264 -preset fast -crf 23 '
        f'-c:a aac -b:a 160k '
        f'"{out2h}"'
    )

    # 写 metadata，方便以后 NIW / GPS / 运营追踪
    meta = {
        "bucket": bucket,
        "created_at": ts,
        "source_count": len(selected),
        "sources": [str(x) for x in selected],
        "outputs": {
            "main_25min": str(out25),
            "long_2h": str(out2h),
        },
        "logic": "same_time_bucket + chronological_order + quality_gate + normalized_concat"
    }

    meta_path = OUT_LONG / f"nyc_{bucket}_{ts}_metadata.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
    print("METADATA:", meta_path)


def process_timelapse():
    """
    延时素材：必须切段输出到 output/timelapse，禁止整条当长片发布。
    来源：01_ASSET_LIBRARY 索引 + 03_TIMELAPSE + processed/timelapse。
    """
    clips: list[Path] = []
    for f in collect_timelapse_from_asset_index():
        if f not in clips:
            clips.append(f)
    for f in list(TIMELAPSE.glob("*.mp4")) + list(TIMELAPSE.glob("*.mov")):
        if f.name.startswith("._"):
            continue
        if f not in clips:
            clips.append(f)

    for f in clips:
        dur = get_duration(f)
        if dur < 10:
            continue

        base = f.stem.replace(" ", "_")[:80]
        seg_len = 15
        i = 0
        start = 0

        while start < dur:
            out = OUT_TIMELAPSE / f"{base}_{i}.mp4"
            sh(f'ffmpeg -y -i "{f}" -ss {start} -t {seg_len} -c copy "{out}"')
            start += seg_len
            i += 1

def make_shorts():
    for f in OUT_TIMELAPSE.glob("*.mp4"):
        out = OUT_SHORTS / f"{f.stem}_short.mp4"
        if out.exists():
            continue

        sh(f'ffmpeg -y -i "{f}" -vf "scale=1080:1920" -r 60 "{out}"')


def make_thumbnail(video_path: Path):
    """
    从视频中间抽一帧做封面，并加淡水印。
    输出：同目录 thumbnail_视频名.jpg
    """
    # macOS AppleDouble / Finder 副本（._*.mp4）不是有效媒体，跳过
    if video_path.name.startswith("._") or video_path.name.startswith("."):
        return

    if not video_path.exists():
        return

    out = video_path.parent / f"thumbnail_{video_path.stem}.jpg"
    if out.exists():
        return

    dur = get_duration(video_path)
    ss = max(3, int(dur * 0.35)) if dur else 5

    watermark = "STATEVERGE NYC"
    cmd = (
        f'ffmpeg -y -ss {ss} -i "{video_path}" -frames:v 1 '
        f'-vf "scale=3840:2160,'
        f"drawtext=text='{watermark}':"
        f"fontcolor=white@0.35:"
        f"fontsize=72:"
        f"x=w-tw-90:y=h-th-70:"
        f"box=1:boxcolor=black@0.18:boxborderw=28"
        f'" -update 1 '
        f'"{out}"'
    )
    sh(cmd)

def make_all_thumbnails():
    """
    给 output/long、output/shorts、output/timelapse 里的视频生成封面。
    """
    for folder in [OUT_LONG, OUT_SHORTS, OUT_TIMELAPSE]:
        if not folder.exists():
            continue
        for v in list(folder.glob("*.mp4")) + list(folder.glob("*.mov")):
            if v.name.startswith("._"):
                continue
            make_thumbnail(v)


def guess_location_from_name(name: str):
    name = name.lower()
    if "brooklyn" in name:
        return "Brooklyn"
    if "manhattan" in name:
        return "Manhattan"
    return "NYC"


def detect_type(name: str):
    name = name.lower()
    if "2h" in name:
        return "Long Drive"
    if "25min" in name:
        return "Street Drive"
    return "City Walk"


def generate_metadata(video_path: Path):
    """
    为每个视频生成：
    title.txt / description.txt / tags.txt / {stem}_meta.json
    """
    if not video_path.exists():
        return
    if video_path.name.startswith("._"):
        return

    base = video_path.stem
    folder = video_path.parent

    video_type = detect_video_type(video_path)
    assert_path_matches_video_type(video_path, video_type)

    if video_type == "short":
        channel = "shorts"
    elif video_type == "long":
        channel = "long"
    else:
        channel = "skip"

    location = guess_location_from_name(base)
    vtype = detect_type(base)

    # 标题（模板化，不用AI）
    title = f"{location} {vtype} 🇺🇸 4K Real NYC Street Sounds"

    # 描述
    desc = f"""
Real street footage recorded in {location}, New York City.

No music, no talking — just authentic urban sound and atmosphere.

Captured as part of a real-world NYC environment recording system.

#nyc #manhattan #brooklyn #streetsounds #4k
""".strip()

    if video_type == "short":
        desc += "\n\nFull video available on main channel."

    # 标签
    tags = [
        "nyc", "new york", "manhattan", "brooklyn",
        "4k", "street", "drive", "real sounds",
        "city ambience", "urban life"
    ]

    # 写文件
    (folder / f"{base}_title.txt").write_text(title, encoding="utf-8")
    (folder / f"{base}_description.txt").write_text(desc, encoding="utf-8")
    (folder / f"{base}_tags.txt").write_text(",".join(tags), encoding="utf-8")

    meta = {
        "title": title,
        "description": desc,
        "tags": tags,
        "file": str(video_path),
        "video_type": video_type,
        "channel": channel,
    }

    # Optional: attach source asset lineage for publish dedupe / cooling.
    # This is intentionally offline-only and does not scan asset library here.
    # If a producer step created an output->assets map, we stitch it in.
    try:
        mpath = SV / "07_AUTOMATION" / "queues" / "output_source_map.json"
        if mpath.is_file():
            raw_map = json.loads(mpath.read_text(encoding="utf-8"))
            if isinstance(raw_map, dict):
                # Build robust lookup:
                # - prefer resolved absolute path
                # - then raw str(video_path)
                # - then basename
                # Also normalize map keys: if relative, resolve under SV.
                lookup: dict[str, dict] = {}
                for k, v in raw_map.items():
                    if not isinstance(k, str) or not isinstance(v, dict):
                        continue
                    # raw key
                    lookup[k] = v
                    # resolved key
                    try:
                        kp = Path(k)
                        if not kp.is_absolute():
                            kp = (SV / kp).resolve()
                        else:
                            kp = kp.expanduser().resolve()
                        lookup[str(kp)] = v
                        lookup[kp.name] = v
                    except Exception:
                        pass

                candidates = [
                    str(video_path.resolve()),
                    str(video_path),
                    video_path.name,
                ]
                row = None
                for ck in candidates:
                    if ck in lookup:
                        row = lookup[ck]
                        break

                if isinstance(row, dict):
                    if isinstance(row.get("source_assets"), list):
                        meta["source_assets"] = row.get("source_assets")
                    if row.get("derived_from_long"):
                        meta["derived_from_long"] = row.get("derived_from_long")
                    if row.get("audio_file"):
                        meta["audio_file"] = row.get("audio_file")
                    if row.get("duration_seconds") is not None:
                        meta["duration_seconds"] = row.get("duration_seconds")
                    if row.get("source_policy"):
                        meta["source_policy"] = row.get("source_policy")
                else:
                    print(f"MAP_MISS path={video_path} basename={video_path.name}")
    except Exception:
        pass

    (folder / f"{base}_meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def generate_all_metadata():
    for folder in [OUT_LONG, OUT_SHORTS, OUT_TIMELAPSE]:
        if not folder.exists():
            continue
        for v in list(folder.glob("*.mp4")):
            if v.name.startswith("._"):
                continue
            generate_metadata(v)


def upload_video(video_path: Path, meta: dict, *, history: dict | None = None) -> None:
    """模拟上传（阶段一）：改为发布去重系统 + 发布索引写入。"""
    video_type = meta.get("video_type") or detect_video_type(video_path)
    assert_path_matches_video_type(video_path, str(video_type))

    if video_type == "short":
        channel_id = SHORTS_CHANNEL_ID
        ch_key = "shorts"
    elif video_type == "long":
        channel_id = LONG_CHANNEL_ID
        ch_key = "long"
    else:
        print("SKIP_UNKNOWN_TYPE:", video_path)
        return

    # New: publish dedupe gates.
    if build_candidate_from_meta and check_publish_allowed and append_published:
        try:
            from datetime import datetime
            from zoneinfo import ZoneInfo

            today = datetime.now(ZoneInfo(PUBLISH_DATE_TZ)).date().isoformat()
        except Exception:
            today = time.strftime("%Y-%m-%d")

        cand = build_candidate_from_meta(
            video_path=video_path,
            meta=meta,
            channel=str(meta.get("channel") or ch_key),
            publish_date=today,
        )
        if cand is None:
            return
        published = load_published_entries() if load_published_entries else []
        assets_idx = load_assets_master_index() if load_assets_master_index else {}
        ok, reason, _pol = check_publish_allowed(cand, published=published, assets_index=assets_idx)
        if not ok:
            if publish_log:
                publish_log(f"SKIP candidate={video_path} reason={reason}")
            return

        print("UPLOAD:", video_path, "->", channel_id)
        entry = {
            "content_id": cand.content_id,
            "video_hash": cand.video_hash,
            "audio_hash": cand.audio_hash,
            "title": cand.title,
            "title_hash": cand.title_hash,
            "duration_seconds": cand.duration_seconds,
            "channel": cand.channel,
            "publish_date": cand.publish_date,
            "source_assets": cand.source_assets,
            "is_long": cand.is_long,
            "is_short": cand.is_short,
            "derived_from_long": cand.derived_from_long,
        }
        append_published(entry)
        if update_assets_usage:
            update_assets_usage(
                content_id=cand.content_id,
                asset_ids=cand.source_assets,
                today=cand.publish_date,
            )
        if publish_log:
            publish_log(f"PUBLISH_SUCCESS content_id={cand.content_id} channel={cand.channel}")
        return

    # Fallback legacy upload_history (kept for safety).
    h = get_file_hash(video_path)
    hist = history if history is not None else load_upload_history()
    if h in hist:
        print("SKIP_DUPLICATE:", video_path)
        return
    print("UPLOAD:", video_path, "->", channel_id)
    hist[h] = {"uploaded": True, "channel": ch_key, "video_id": "SIMULATED"}
    if history is None:
        save_upload_history(hist)


def upload_all() -> None:
    """扫描成片目录，仅处理已有 *_meta.json 的视频；用 published_assets.jsonl 做发布去重（SIMULATED）。"""
    counts = {
        "short": 0,
        "long": 0,
        "unknown": 0,
        "blocked": 0,
        "uploaded": 0,
        "missing_source_assets": 0,
    }

    published = load_published_entries() if load_published_entries else []
    assets_idx = load_assets_master_index() if load_assets_master_index else {}

    for folder in [OUT_LONG, OUT_SHORTS, OUT_TIMELAPSE]:
        if not folder.exists():
            continue
        for v in folder.glob("*.mp4"):
            if v.name.startswith("._"):
                continue
            meta_path = v.parent / f"{v.stem}_meta.json"
            if not meta_path.is_file():
                continue
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            vt = meta.get("video_type") or detect_video_type(v)
            if vt == "short":
                counts["short"] += 1
            elif vt == "long":
                counts["long"] += 1
            else:
                counts["unknown"] += 1

            if not build_candidate_from_meta or not check_publish_allowed or not append_published:
                # Legacy fallback (kept to avoid breaking old environments).
                upload_video(v, meta)
                continue

            try:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                today = datetime.now(ZoneInfo(PUBLISH_DATE_TZ)).date().isoformat()
            except Exception:
                today = time.strftime("%Y-%m-%d")

            cand = build_candidate_from_meta(
                video_path=v,
                meta=meta,
                channel=str(meta.get("channel") or ("shorts" if vt == "short" else "long")),
                publish_date=today,
            )
            if cand is None:
                counts["missing_source_assets"] += 1
                continue

            ok, reason, _pol = check_publish_allowed(cand, published=published, assets_index=assets_idx)
            if not ok:
                counts["blocked"] += 1
                if publish_log:
                    publish_log(f"SKIP candidate={v} reason={reason}")
                continue

            # simulate publish success
            print("UPLOAD:", v, "->", ("SHORTS" if vt == "short" else "LONG"))
            entry = {
                "content_id": cand.content_id,
                "video_hash": cand.video_hash,
                "audio_hash": cand.audio_hash,
                "title": cand.title,
                "title_hash": cand.title_hash,
                "duration_seconds": cand.duration_seconds,
                "channel": cand.channel,
                "publish_date": cand.publish_date,
                "source_assets": cand.source_assets,
                "is_long": cand.is_long,
                "is_short": cand.is_short,
                "derived_from_long": cand.derived_from_long,
            }
            append_published(entry)
            published.append(entry)
            if update_assets_usage:
                update_assets_usage(
                    content_id=cand.content_id,
                    asset_ids=cand.source_assets,
                    today=cand.publish_date,
                )
            counts["uploaded"] += 1
            if publish_log:
                publish_log(f"PUBLISH_SUCCESS content_id={cand.content_id} channel={cand.channel}")

    print(
        "UPLOAD_SUMMARY:",
        f"short={counts['short']} long={counts['long']} unknown={counts['unknown']} "
        f"blocked={counts['blocked']} missing_source_assets={counts['missing_source_assets']} simulated_new={counts['uploaded']}",
    )


def main(*, dry_upload: bool = False) -> None:
    refresh_paths()
    ensure_dirs()
    classify()
    process_video_smart()
    process_timelapse()
    make_shorts()
    make_all_thumbnails()
    generate_all_metadata()
    if dry_upload:
        upload_all()
    print("DONE")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="NYC_AUTO pipeline (upload 仅 --dry-upload)")
    ap.add_argument(
        "--dry-upload",
        action="store_true",
        help="模拟上传并写入 upload_history.json（默认不写）",
    )
    ns = ap.parse_args()
    main(dry_upload=ns.dry_upload)
