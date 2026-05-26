"""
Topic-status scanner for StateVerge Studio Dashboard.

Walks ~/StateVerge/topics/<slug>/ and reports which artifacts each topic has,
which pipeline stage it's reached, and what the operator should do next.

Also provides scanners for:
  - shorts pipeline (topics/<slug>/shorts/)
  - finance pipeline (topics/<slug>/finance/)
  - asset libraries (assets/envato, runway, pexels, pixabay, ~/Downloads)

Naming notes
------------
The long-form pipeline historically writes:
    output/final_video.mp4               (silent or muxed master)
    output/final_video_with_subtitles.mp4 (final delivery copy)

The user-facing spec calls these "final_mix.mp4" and "final_packaged.mp4".
We accept BOTH naming schemes as valid — preferring the modern names if both
exist — so legacy topics are not falsely flagged as incomplete.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.home_downloads import resolve_home_downloads

# ----------------------------------------------------------------------------
# Config
# ----------------------------------------------------------------------------

# Directory names we skip (test scaffolds, smoke fixtures, dummies).
SKIP_PREFIXES: tuple[str, ...] = ("_", "SCAFFOLD_", "does-not-exist")
SKIP_NAMES: frozenset[str] = frozenset(
    {"prod-test", "test-topic", "hidden-rules"}
)

# Pipeline stages — order matters (left → right in the dashboard).
STAGES: list[dict] = [
    {"id": 1, "name": "主题输入"},
    {"id": 2, "name": "资料抓取"},
    {"id": 3, "name": "AI文案"},
    {"id": 4, "name": "旁白生成"},
    {"id": 5, "name": "镜头Prompt"},
    {"id": 6, "name": "视频生成"},
    {"id": 7, "name": "包装合成"},
    {"id": 8, "name": "发布准备"},
]


# ----------------------------------------------------------------------------
# Data model
# ----------------------------------------------------------------------------


@dataclass
class TopicStatus:
    slug: str
    has_topic_dir: bool = True

    # Per-artifact flags
    has_script: bool = False
    has_voice: bool = False
    has_manifest: bool = False
    has_video_clips: bool = False
    has_envato: bool = False
    has_runway_prompts: bool = False
    has_runway_videos: bool = False
    has_ltx_videos: bool = False
    has_segments: bool = False
    has_final_mix: bool = False
    has_final_packaged: bool = False

    # Resolved mp4 paths (relative to repo root) for the table
    final_mix_path: str = ""
    final_packaged_path: str = ""

    # Cheap metadata
    script_chars: int = 0
    last_modified: float = 0.0

    # Status rollup
    status: str = ""             # Missing Script / Missing Voice / ... / Ready
    next_action: str = ""
    stages: dict[int, bool] = field(default_factory=dict)


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------


def _exists_any(*paths: Path) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def _glob_any(d: Path, pattern: str) -> bool:
    return d.is_dir() and any(d.glob(pattern))


def _newest_mtime(d: Path) -> float:
    """Latest mtime under d (cheap; bounded by topic size)."""
    latest = 0.0
    if not d.is_dir():
        return latest
    for p in d.rglob("*"):
        try:
            m = p.stat().st_mtime
        except OSError:
            continue
        if m > latest:
            latest = m
    return latest


# ----------------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------------


def scan_topic(topic_dir: Path, repo_root: Path) -> TopicStatus:
    s = TopicStatus(slug=topic_dir.name)

    # --- script ---
    script_p = topic_dir / "brief" / "narration_script.txt"
    s.has_script = script_p.is_file() and script_p.stat().st_size > 0
    if s.has_script:
        try:
            s.script_chars = len(script_p.read_text(encoding="utf-8"))
        except OSError:
            pass

    # --- voice ---
    s.has_voice = (
        (topic_dir / "audio" / "voice.wav").is_file()
        or (topic_dir / "audio" / "voice.mp3").is_file()
    )

    # --- assets manifest ---
    s.has_manifest = (topic_dir / "assets" / "media_manifest.json").is_file()

    # --- raw video clips & envato ---
    s.has_video_clips = (
        _glob_any(topic_dir / "video", "*.mp4")
        or _glob_any(topic_dir / "video" / "_segments", "*.mp4")
    )
    s.has_envato = (
        _glob_any(topic_dir / "envato", "*.mp4")
        or _glob_any(topic_dir / "envato", "**/*.mp4")
    )

    # --- runway / ltx ---
    s.has_runway_prompts = _glob_any(topic_dir / "runway", "*.txt") or \
                           _glob_any(topic_dir / "runway", "*.json")
    s.has_runway_videos = _glob_any(topic_dir / "runway", "*.mp4")
    s.has_ltx_videos = _glob_any(topic_dir / "ltx", "*.mp4") or \
                       _glob_any(topic_dir / "ltx" / "generated", "*.mp4")
    s.has_segments = bool(_exists_any(
        topic_dir / "mix" / "segments.json",
        topic_dir / "mix" / "timeline.json",
        topic_dir / "ltx" / "audio_chunks" / "audio_chunks_manifest.json",
    ))

    # --- final mix (master cut) — accept either modern or legacy filename ---
    fm = _exists_any(
        topic_dir / "output" / "final_mix.mp4",
        topic_dir / "output" / "final_video.mp4",
    )
    s.has_final_mix = fm is not None
    if fm is not None:
        try:
            s.final_mix_path = str(fm.relative_to(repo_root))
        except ValueError:
            s.final_mix_path = str(fm)

    # --- final packaged (subtitled, color-graded, deliverable) ---
    fp = _exists_any(
        topic_dir / "output" / "final_packaged.mp4",
        topic_dir / "output" / "final_video_with_subtitles.mp4",
    )
    s.has_final_packaged = fp is not None
    if fp is not None:
        try:
            s.final_packaged_path = str(fp.relative_to(repo_root))
        except ValueError:
            s.final_packaged_path = str(fp)

    s.last_modified = _newest_mtime(topic_dir)

    # --- stage rollup ---
    s.stages = {
        1: s.has_topic_dir,
        2: s.has_manifest or s.has_envato or s.has_video_clips,
        3: s.has_script,
        4: s.has_voice,
        5: s.has_runway_prompts or s.has_segments,
        6: s.has_video_clips or s.has_envato or s.has_runway_videos
           or s.has_ltx_videos,
        7: s.has_final_mix,
        8: s.has_final_packaged,
    }

    # --- single status & next action (most-broken-first wins) ---
    if not s.has_script:
        s.status = "Missing Script"
        s.next_action = "写或生成 narration_script.txt"
    elif not s.has_voice:
        s.status = "Missing Voice"
        s.next_action = "运行 ElevenLabs TTS → audio/voice.wav"
    elif not (s.has_manifest or s.has_envato or s.has_video_clips
              or s.has_ltx_videos or s.has_runway_videos):
        s.status = "Missing Assets"
        s.next_action = "下载/生成视频素材"
    elif not s.has_final_mix:
        s.status = "Need Mix"
        s.next_action = "运行剪辑 → output/final_mix.mp4"
    elif not s.has_final_packaged:
        s.status = "Need Package"
        s.next_action = "烧录字幕/包装 → output/final_packaged.mp4"
    else:
        s.status = "Ready"
        s.next_action = "可发布 ✓"

    return s


def list_topic_dirs(topics_root: Path) -> list[Path]:
    out: list[Path] = []
    if not topics_root.is_dir():
        return out
    for d in sorted(topics_root.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if name in SKIP_NAMES or name.startswith(SKIP_PREFIXES):
            continue
        out.append(d)
    return out


def scan_all(repo_root: Path) -> list[TopicStatus]:
    return [
        scan_topic(d, repo_root)
        for d in list_topic_dirs(repo_root / "topics")
    ]


# ----------------------------------------------------------------------------
# Aggregations for the dashboard
# ----------------------------------------------------------------------------


def _stage_color(done: int, total: int) -> str:
    if total == 0:
        return "gray"
    ratio = done / total
    if ratio >= 0.75:
        return "green"
    if ratio >= 0.30:
        return "yellow"
    return "red"


def summarize(statuses: list[TopicStatus]) -> dict:
    total = len(statuses)
    stage_stats = []
    for st in STAGES:
        sid = st["id"]
        done = sum(1 for s in statuses if s.stages.get(sid))
        stage_stats.append({
            "id": sid,
            "name": st["name"],
            "done": done,
            "missing": total - done,
            "color": _stage_color(done, total),
        })

    panel = {
        "ready": sum(1 for s in statuses if s.status == "Ready"),
        "missing_script": sum(1 for s in statuses if s.status == "Missing Script"),
        "missing_voice": sum(1 for s in statuses if s.status == "Missing Voice"),
        "missing_assets": sum(1 for s in statuses if s.status == "Missing Assets"),
        "need_mix": sum(1 for s in statuses if s.status == "Need Mix"),
        "need_package": sum(1 for s in statuses if s.status == "Need Package"),
        "total": total,
    }

    recent = sorted(statuses, key=lambda s: -s.last_modified)[:5]
    recent_out = [
        {
            "slug": r.slug,
            "status": r.status,
            "mtime": r.last_modified,
        }
        for r in recent
    ]

    # Suggest next action(s) — pick the worst cluster.
    suggestion = "全部就绪 ✓"
    if panel["missing_script"]:
        suggestion = f"先把 {panel['missing_script']} 个缺旁白脚本的 topic 补上"
    elif panel["missing_voice"]:
        suggestion = f"运行 TTS 补 {panel['missing_voice']} 个旁白音频"
    elif panel["missing_assets"]:
        suggestion = f"下载 {panel['missing_assets']} 个 topic 的视频素材"
    elif panel["need_mix"]:
        suggestion = f"剪辑 {panel['need_mix']} 个 topic → final_mix"
    elif panel["need_package"]:
        suggestion = f"烧录字幕 {panel['need_package']} 个 topic → final_packaged"

    return {
        "stage_stats": stage_stats,
        "panel": panel,
        "recent": recent_out,
        "suggestion": suggestion,
    }


# ============================================================================
# Shorts pipeline scanner — topics/<slug>/shorts/
# ============================================================================


@dataclass
class ShortStatus:
    slug: str
    has_script: bool = False
    has_voice: bool = False
    has_subtitles: bool = False
    has_final_short: bool = False
    final_short_path: str = ""
    last_modified: float = 0.0
    status: str = ""
    next_action: str = ""


def scan_short(topic_dir: Path, repo_root: Path) -> ShortStatus | None:
    shorts_dir = topic_dir / "shorts"
    if not shorts_dir.is_dir():
        return None
    s = ShortStatus(slug=topic_dir.name)
    s.has_script = (shorts_dir / "script.txt").is_file()
    s.has_voice = (shorts_dir / "voice.wav").is_file() or \
                  (shorts_dir / "voice.mp3").is_file()
    s.has_subtitles = (shorts_dir / "subtitles.srt").is_file() or \
                      (shorts_dir / "subtitles.ass").is_file()
    fp = _exists_any(
        shorts_dir / "output" / "final_short.mp4",
        shorts_dir / "output" / "final.mp4",
    )
    s.has_final_short = fp is not None
    if fp is not None:
        try:
            s.final_short_path = str(fp.relative_to(repo_root))
        except ValueError:
            s.final_short_path = str(fp)
    s.last_modified = _newest_mtime(shorts_dir)

    if not s.has_script:
        s.status = "Missing Script"
        s.next_action = "写或生成 shorts/script.txt"
    elif not s.has_voice:
        s.status = "Missing Voice"
        s.next_action = "运行 TTS → shorts/voice.wav"
    elif not s.has_subtitles:
        s.status = "Missing Subtitles"
        s.next_action = "生成 shorts/subtitles.srt"
    elif not s.has_final_short:
        s.status = "Need Render"
        s.next_action = "渲染 → shorts/output/final_short.mp4"
    else:
        s.status = "Ready"
        s.next_action = "可发布 ✓"
    return s


def scan_shorts_all(repo_root: Path) -> list[ShortStatus]:
    out = []
    for d in list_topic_dirs(repo_root / "topics"):
        s = scan_short(d, repo_root)
        if s is not None:
            out.append(s)
    return out


def summarize_shorts(statuses: list[ShortStatus]) -> dict:
    total = len(statuses)
    panel = {
        "total": total,
        "ready": sum(1 for s in statuses if s.status == "Ready"),
        "missing_script": sum(1 for s in statuses if s.status == "Missing Script"),
        "missing_voice": sum(1 for s in statuses if s.status == "Missing Voice"),
        "missing_subtitles": sum(1 for s in statuses if s.status == "Missing Subtitles"),
        "need_render": sum(1 for s in statuses if s.status == "Need Render"),
    }
    suggestion = "全部就绪 ✓"
    if panel["missing_script"]:
        suggestion = f"先补 {panel['missing_script']} 个缺脚本的 short"
    elif panel["missing_voice"]:
        suggestion = f"补 {panel['missing_voice']} 个 short 的 TTS"
    elif panel["missing_subtitles"]:
        suggestion = f"为 {panel['missing_subtitles']} 个 short 生成字幕"
    elif panel["need_render"]:
        suggestion = f"渲染 {panel['need_render']} 个 short"
    return {"panel": panel, "suggestion": suggestion}


# ============================================================================
# Finance pipeline scanner — topics/<slug>/finance/
# ============================================================================


@dataclass
class FinanceStatus:
    slug: str
    has_data: bool = False
    has_analysis: bool = False
    has_script: bool = False
    script_path: str = ""  # finance/narration_script.txt优先，否则 brief/narration_script.txt
    has_voice: bool = False
    has_final_short: bool = False
    final_short_path: str = ""
    last_modified: float = 0.0
    status: str = ""
    next_action: str = ""


def scan_finance(topic_dir: Path, repo_root: Path) -> FinanceStatus | None:
    fin_dir = topic_dir / "finance"
    if not fin_dir.is_dir():
        return None
    s = FinanceStatus(slug=topic_dir.name)
    s.has_data = (fin_dir / "data.json").is_file()
    s.has_analysis = (fin_dir / "analysis.json").is_file()

    fin_script = fin_dir / "narration_script.txt"
    brief_script = topic_dir / "brief" / "narration_script.txt"
    if fin_script.is_file():
        s.has_script = True
        try:
            s.script_path = str(fin_script.relative_to(repo_root))
        except ValueError:
            s.script_path = str(fin_script)
    elif brief_script.is_file():
        s.has_script = True
        try:
            s.script_path = str(brief_script.relative_to(repo_root))
        except ValueError:
            s.script_path = str(brief_script)

    s.has_voice = (fin_dir / "voice.wav").is_file() or \
                  (fin_dir / "voice.mp3").is_file()
    fp = _exists_any(
        fin_dir / "output" / "final_short.mp4",
        fin_dir / "output" / "final.mp4",
    )
    s.has_final_short = fp is not None
    if fp is not None:
        try:
            s.final_short_path = str(fp.relative_to(repo_root))
        except ValueError:
            s.final_short_path = str(fp)
    s.last_modified = _newest_mtime(fin_dir)

    if not s.has_data:
        s.status = "Missing Data"
        s.next_action = "拉财报数据 → finance/data.json"
    elif not s.has_analysis:
        s.status = "Missing Analysis"
        s.next_action = "跑分析 → finance/analysis.json"
    elif not s.has_script:
        s.status = "Missing Script"
        s.next_action = (
            "生成旁白 → finance/narration_script.txt "
            "或 brief/narration_script.txt"
        )
    elif not s.has_voice:
        s.status = "Missing Voice"
        s.next_action = "TTS → finance/voice.wav"
    elif not s.has_final_short:
        s.status = "Need Render"
        s.next_action = "渲染 → finance/output/final_short.mp4"
    else:
        s.status = "Ready"
        s.next_action = "可发布 ✓"
    return s


def scan_finance_all(repo_root: Path) -> list[FinanceStatus]:
    out = []
    for d in list_topic_dirs(repo_root / "topics"):
        s = scan_finance(d, repo_root)
        if s is not None:
            out.append(s)
    return out


def summarize_finance(statuses: list[FinanceStatus]) -> dict:
    total = len(statuses)
    panel = {
        "total": total,
        "ready": sum(1 for s in statuses if s.status == "Ready"),
        "missing_data": sum(1 for s in statuses if s.status == "Missing Data"),
        "missing_analysis": sum(1 for s in statuses if s.status == "Missing Analysis"),
        "missing_script": sum(1 for s in statuses if s.status == "Missing Script"),
        "missing_voice": sum(1 for s in statuses if s.status == "Missing Voice"),
        "need_render": sum(1 for s in statuses if s.status == "Need Render"),
    }
    suggestion = "全部就绪 ✓"
    for key, label in [
        ("missing_data", "拉数据"),
        ("missing_analysis", "跑分析"),
        ("missing_script", "写脚本"),
        ("missing_voice", "TTS"),
        ("need_render", "渲染"),
    ]:
        if panel[key]:
            suggestion = f"先 {label} {panel[key]} 个 finance topic"
            break
    return {"panel": panel, "suggestion": suggestion}


# ============================================================================
# Asset library scanner — assets/* and ~/Downloads
# ============================================================================


VIDEO_EXTS: frozenset[str] = frozenset(
    {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
)
IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".bmp"}
)
AUDIO_EXTS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
)
ZIP_EXTS: frozenset[str] = frozenset(
    {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz"}
)

ASSET_SOURCES: list[dict] = [
    {"id": "envato",    "label": "Envato",       "rel": "assets/envato"},
    {"id": "runway",    "label": "Runway",       "rel": "assets/runway"},
    {"id": "pexels",    "label": "Pexels",       "rel": "assets/pexels"},
    {"id": "pixabay",   "label": "Pixabay",      "rel": "assets/pixabay"},
    {"id": "xhs",       "label": "Xiaohongshu",  "rel": "assets/xhs"},
    {"id": "downloads", "label": "Downloads",    "rel": None},  # ~/Downloads
]

# Per-source recursion ceiling to keep the scanner cheap on huge directories.
_PER_SOURCE_MAX_FILES = 20000


@dataclass
class AssetSource:
    id: str
    label: str
    root: str
    exists: bool = False
    videos: int = 0
    images: int = 0
    audio: int = 0
    archives: int = 0
    total: int = 0


def _classify(p: Path) -> str | None:
    ext = p.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in ZIP_EXTS:
        return "archive"
    return None


def _scan_source(src: AssetSource, root: Path, recursive: bool,
                 collector: list[tuple[float, Path, str, str]]) -> None:
    if not root.is_dir():
        return
    src.exists = True
    count = 0
    iterator = root.rglob("*") if recursive else root.iterdir()
    for p in iterator:
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        kind = _classify(p)
        if not kind:
            continue
        if kind == "video":
            src.videos += 1
        elif kind == "image":
            src.images += 1
        elif kind == "audio":
            src.audio += 1
        elif kind == "archive":
            src.archives += 1
        src.total += 1
        try:
            mtime = p.stat().st_mtime
        except OSError:
            mtime = 0.0
        collector.append((mtime, p, kind, src.label))
        count += 1
        if count >= _PER_SOURCE_MAX_FILES:
            break


def scan_assets(repo_root: Path) -> dict:
    sources: list[AssetSource] = []
    collected: list[tuple[float, Path, str, str]] = []

    for cfg in ASSET_SOURCES:
        if cfg["id"] == "downloads":
            root = resolve_home_downloads()
            recursive = False
        else:
            root = repo_root / cfg["rel"]
            recursive = True
        src = AssetSource(
            id=cfg["id"], label=cfg["label"], root=str(root)
        )
        _scan_source(src, root, recursive=recursive, collector=collected)
        sources.append(src)

    # Top-10 most recently modified files across all sources
    collected.sort(key=lambda t: -t[0])
    home = Path.home()
    recent = []
    for mtime, p, kind, source_label in collected[:10]:
        try:
            display = "~/" + str(p.relative_to(home))
        except ValueError:
            display = str(p)
        try:
            size = p.stat().st_size
        except OSError:
            size = 0
        recent.append({
            "path": display,
            "size": size,
            "mtime": mtime,
            "kind": kind,
            "source": source_label,
        })

    # Downloads "uncategorized" rollup
    downloads = next((s for s in sources if s.id == "downloads"), None)
    downloads_uncat = {
        "videos": downloads.videos if downloads else 0,
        "images": downloads.images if downloads else 0,
        "archives": downloads.archives if downloads else 0,
        "total": downloads.total if downloads else 0,
    }

    # Pull "Missing Assets" topics from the main scanner so this page can
    # link operators back to action items.
    main_statuses = scan_all(repo_root)
    missing_assets_topics = [
        s.slug for s in main_statuses if s.status == "Missing Assets"
    ]

    # Aggregate counts for top pills
    totals = {
        "videos": sum(s.videos for s in sources),
        "images": sum(s.images for s in sources),
        "audio":  sum(s.audio for s in sources),
        "archives": sum(s.archives for s in sources),
        "total":  sum(s.total for s in sources),
    }

    return {
        "sources": sources,
        "recent": recent,
        "downloads_uncat": downloads_uncat,
        "missing_assets_topics": missing_assets_topics,
        "totals": totals,
    }

