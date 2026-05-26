#!/usr/bin/env python3
"""
StateVerge YouTube Shorts 自动成片：竖屏 1080x1920，主持人 + 素材 + 大字幕 + ffprobe 校验。

独立脚本：不修改 `src/`，不调用长视频 `auto_video` 管线。依赖本机 `ffmpeg` / `ffprobe`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

# --- layout & constants ---

W, H, FPS, AR = 1080, 1920, 30, 48_000
CJK_RE = re.compile(r"[\u4e00-\u9fff]")

MAC_FONT_CANDS: list[tuple[str, str]] = [
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    (
        "/System/Library/AssetsV2/com_apple_MobileAsset_Font8/"
        "86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc",
        "PingFang SC",
    ),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC"),
    ("/System/Library/Fonts/STHeiti Light.ttc", "Heiti SC"),
    ("/System/Library/Fonts/Hiragino Sans GB.ttc", "Hiragino Sans GB"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "Arial Unicode MS"),
]
SUBTITLE_FONT_FACE_KEYWORDS = (
    "PingFang SC", "Heiti SC", "Hiragino Sans GB", "Songti SC", "Arial Unicode MS",
)


def _root() -> Path:
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def log(msg: str) -> None:
    print(f"[shorts_factory] {msg}", flush=True)


def run_ffmpeg(
    args: list[str], *, label: str = "ffmpeg", cwd: Path | None = None
) -> None:
    p = subprocess.run(
        list(args), cwd=str(cwd) if cwd else None, capture_output=True, text=True
    )
    if p.returncode != 0:
        err = (p.stderr or p.stdout or "")[:12000]
        log(f"FAIL: {label}\n$ {' '.join(args)[:2000]}\n{err}")
        raise RuntimeError(f"{label} failed (exit={p.returncode})")
    if label:
        log(f"ok: {label}")


def cjk_count(s: str) -> int:
    return len(CJK_RE.findall(s or ""))


def ffprobe_duration(p: Path) -> float:
    p2 = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(p),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0:
        return 0.0
    try:
        return max(0.0, float((p2.stdout or "0").strip() or 0.0))
    except ValueError:
        return 0.0


def ffprobe_info(p: Path) -> dict[str, Any]:
    p2 = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(p),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if p2.returncode != 0:
        return {}
    try:
        return json.loads(p2.stdout or "{}")
    except json.JSONDecodeError:
        return {}


# --- .env loader (避免引入 python-dotenv 依赖) ---

def load_dotenv(root: Path) -> None:
    p = root / ".env"
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


# --- ElevenLabs TTS (stdlib only, 无需 requests) ---

ELEVEN_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
ELEVEN_OUTPUT_FORMAT = "mp3_44100_128"
ELEVEN_DEFAULT_MODEL = "eleven_multilingual_v2"


def _env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def _env_float(name: str, default: float) -> float:
    v = _env_str(name)
    try:
        return float(v) if v else default
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    v = _env_str(name).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def generate_elevenlabs_tts(text: str, mp3_out: Path, wav_out: Path) -> None:
    """生成中文旁白；失败必须 raise，绝不静默返回。"""
    api_key = _env_str("ELEVENLABS_API_KEY")
    voice_id = _env_str("ELEVENLABS_VOICE_ID")
    if not api_key or not voice_id:
        raise RuntimeError(
            "Missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID in environment / .env"
        )
    if not text or not text.strip():
        raise RuntimeError("TTS input text is empty")
    model_id = _env_str("ELEVENLABS_MODEL_ID", ELEVEN_DEFAULT_MODEL)
    settings = {
        "stability": _env_float("ELEVENLABS_STABILITY", 0.5),
        "similarity_boost": _env_float("ELEVENLABS_SIMILARITY_BOOST", 0.8),
        "style": _env_float("ELEVENLABS_STYLE", 0.0),
        "use_speaker_boost": _env_bool("ELEVENLABS_USE_SPEAKER_BOOST", True),
    }
    body = {
        "text": text.strip(),
        "model_id": model_id,
        "voice_settings": settings,
    }
    url = f"{ELEVEN_TTS_BASE}/{voice_id}?output_format={ELEVEN_OUTPUT_FORMAT}"
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": api_key,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "audio/*",
        },
        method="POST",
    )
    last_err = ""
    for attempt in range(1, 4):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310
                if resp.status != 200:
                    last_err = f"HTTP {resp.status}"
                    raise RuntimeError(last_err)
                mp3_out.parent.mkdir(parents=True, exist_ok=True)
                mp3_out.write_bytes(resp.read())
                break
        except urllib.error.HTTPError as e:
            body_snip = ""
            try:
                body_snip = (e.read() or b"")[:400].decode("utf-8", "replace")
            except Exception:
                pass
            last_err = f"HTTPError {e.code}: {body_snip}"
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(last_err) from e
        except urllib.error.URLError as e:
            last_err = f"URLError: {e}"
            if attempt < 3:
                time.sleep(2 * attempt)
                continue
            raise RuntimeError(last_err) from e
    if not mp3_out.is_file() or mp3_out.stat().st_size < 1000:
        raise RuntimeError(f"TTS mp3 missing or too small: {mp3_out}")
    wav_out.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(mp3_out),
            "-ar", str(AR),
            "-ac", "2",
            str(wav_out),
        ],
        capture_output=True, text=True, check=False,
    )
    if r.returncode != 0:
        raise RuntimeError(f"mp3->wav failed: {(r.stderr or '')[:600]}")
    dur = ffprobe_duration(wav_out)
    if dur < 5.0:
        raise RuntimeError(
            f"TTS voice.wav 太短: {dur:.2f}s (需要 > 5s; 检查 script 长度或 ElevenLabs 返回内容)"
        )


def archive_old_final(out_dir: Path) -> None:
    final = out_dir / "final_short.mp4"
    if not final.is_file():
        return
    arch = out_dir / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = arch / f"final_short_{ts}.mp4"
    try:
        shutil.move(str(final), str(dst))
        log(f"已归档旧 final → {dst.relative_to(out_dir.parent.parent.parent)}")
    except OSError as e:
        log(f"WARN: 归档旧 final 失败：{e}")


# --- 黑屏检测 ---

_BLACK_RE = re.compile(r"black_start:(\d+(?:\.\d+)?)\s+black_end:(\d+(?:\.\d+)?)")


def black_fraction(p: Path) -> float:
    if not p.is_file():
        return 1.0
    dur = ffprobe_duration(p)
    if dur <= 0:
        return 1.0
    r = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(p),
            "-vf", "blackdetect=d=0.3:pix_th=0.10",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    total = 0.0
    for m in _BLACK_RE.finditer(r.stderr or ""):
        total += max(0.0, float(m.group(2)) - float(m.group(1)))
    return min(1.0, total / dur)


# --- 音量检测 ---

_VOL_MEAN = re.compile(r"mean_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_VOL_MAX = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def volume_db(p: Path) -> tuple[float, float]:
    r = subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-nostats",
            "-i", str(p), "-vn", "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=False,
    )
    err = r.stderr or ""
    m = _VOL_MEAN.search(err)
    x = _VOL_MAX.search(err)
    return (float(m.group(1)) if m else -100.0, float(x.group(1)) if x else -100.0)


# --- templates (内置关键词 → 短文案) ---

T_AI = """{title}，已经开始淘汰普通人。
效率提升了，但岗位减少了。
问题不是你不努力。
而是重复劳动，正在被机器接管。
未来最危险的，是没有新技能的人。"""

T_YOUTH = """年轻人不躺，也得算账。
你拼的是时间，别人算的是系统。
看懂了规则，再谈内卷不迟。"""

T_HOUSE = """房价不骗人，情绪才骗人。
现金流、利率、城市分化——三个词拆开看。"""

T_US = """美国生活成本不只看汇率。
吃住行税，全都要换成人话。"""

T_MONEY = """存钱，不是抠门。
是先把支出摊在阳光下。"""

T_GEN = """{title}，先拆三段。
你听到的，是立场还是数据？
下结论前，看约束条件。"""


def pick_template(title: str) -> str:
    t = title
    if any(k in t for k in ("AI", "人工智能", "工作", "失业", "ai")) or "ai" in t.lower():
        return T_AI
    if any(k in t for k in ("年轻人", "躺平", "内卷")):
        return T_YOUTH
    if any(k in t for k in ("房价", "房产", "中介")):
        return T_HOUSE
    if "美国" in t or "生活成本" in t:
        return T_US
    if any(k in t for k in ("存钱", "消费")):
        return T_MONEY
    return T_GEN


def autogen_script(title: str) -> str:
    b = pick_template(title)
    s = (b.format(title=title) if "{title}" in b else b).strip()
    out: list[str] = []
    n = 0
    for line in s.splitlines():
        l = line.strip()
        if not l:
            continue
        if n + cjk_count(l) > 60 and out:
            break
        if len(l) > 1:
            out.append(l)
            n += cjk_count(l)
    if not out:
        out = [f"{title}。", "说三点。"]
    return "\n".join(out) + "\n"


# --- 关键词 (中英) ---

def keywords_for_title(title: str) -> list[str]:
    tl = title.lower()
    # AI / 失业 / 淘汰 / 技能 gap
    if any(k in title for k in ("AI", "ai", "人工智能", "淘汰", "失业", "技能", "危险的人群")) or "ai" in tl:
        return [
            "artificial intelligence office",
            "robot computer technology",
            "office worker computer",
            "data screen analytics",
            "automation factory",
        ]
    # 躺平 / 内卷 / 年轻人
    if any(k in title for k in ("躺平", "内卷", "年轻人")):
        return [
            "young people city night",
            "tired worker office",
            "subway crowd commute",
            "apartment buildings asia",
            "alone street night",
        ]
    # 房价 / 房产 / 中介
    if any(k in title for k in ("房价", "房产", "中介", "买房")):
        return [
            "real estate building",
            "apartment construction",
            "city skyline aerial",
            "mortgage paperwork",
            "empty residential building",
        ]
    # 中产 / 阶层
    if any(k in title for k in ("中产", "阶层", "工资")):
        return [
            "middle class family",
            "supermarket shopping",
            "office building city",
            "bills paperwork desk",
            "city commute traffic",
        ]
    # 存钱 / 消费 / 钱
    if any(k in title for k in ("存钱", "消费", "存不到", "理财")):
        return [
            "wallet money cash",
            "supermarket shopping cart",
            "online shopping phone",
            "budget calculator desk",
            "bills paperwork",
        ]
    # 逃离 / 一线 / 城市
    if any(k in title for k in ("逃离", "一线", "城市")):
        return [
            "train station suitcase",
            "city skyline aerial",
            "subway commute crowd",
            "apartment buildings night",
            "highway traffic city",
        ]
    # 打工 / 工作 / 加班
    if any(k in title for k in ("打工", "本质", "加班", "工作")):
        return [
            "office worker computer",
            "factory worker assembly",
            "meeting room business",
            "overtime late night office",
            "commute crowd subway",
        ]
    # 美国 / 生活成本
    if any(k in title for k in ("美国", "生活成本")):
        return [
            "american grocery store",
            "suburban house america",
            "gas station america",
            "supermarket cashier",
            "city street america",
        ]
    # 努力 / 选择
    if any(k in title for k in ("努力", "选择", "方向")):
        return [
            "tired worker office night",
            "laptop desk late",
            "empty street alone",
            "stressed person city",
            "office building night",
        ]
    b = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", " ", title, flags=re.U).strip()
    return [
        f"{b} city documentary",
        "night traffic street asia",
        "crowd walking slow motion",
        "data screen office",
        "aerial city skyline 4k",
    ][:5]


# --- media pool ---

_BROLL_BAD_PARTS = (
    "/lower_thirds/", "/title_openers/", "/overlays/", "/transitions/",
    "/sfx/", "/music/", "/host/", "/host_", "/presenter_masters",
    "/avatar_masters", "/_licenses/",
)
_BROLL_GENERIC_TOPICS = (
    "ai_future_cn", "pentagon", "ftx_cn", "ftx", "chernobyl",
    "watergate", "louvre_episode_01", "hidden-rules",
)
_BROLL_MIN_BYTES = 200_000


def _is_real_broll(p: Path) -> bool:
    s = p.as_posix().lower()
    if any(bad in s for bad in _BROLL_BAD_PARTS):
        return False
    if p.stat().st_size < _BROLL_MIN_BYTES:
        return False
    return True


def rglob_videos(d: Path, *, filter_real: bool = True) -> list[Path]:
    if not d.is_dir():
        return []
    ex = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
    out: list[Path] = []
    for p in d.rglob("*"):
        if p.is_file() and p.suffix.lower() in ex:
            try:
                if filter_real and not _is_real_broll(p):
                    continue
                if not filter_real and p.stat().st_size < 2000:
                    continue
                out.append(p)
            except OSError:
                continue
    return sorted({p.resolve() for p in out}, key=lambda x: str(x).lower())


def collect_broll(root: Path, slug: str) -> list[Path]:
    """优先本 topic 的 raw，再借用其它 topic 的 Pexels/Pixabay 素材，最后 envato 抽象背景。"""
    t = root / "topics" / slug
    out: list[Path] = []
    seen: set[Path] = set()

    def add(paths: list[Path]) -> None:
        for p in paths:
            if p not in seen:
                seen.add(p)
                out.append(p)

    add(rglob_videos(t / "assets" / "raw"))
    add(rglob_videos(t / "envato"))
    for ot in _BROLL_GENERIC_TOPICS:
        if ot == slug:
            continue
        add(rglob_videos(root / "topics" / ot / "assets" / "raw"))
    add(rglob_videos(root / "assets" / "envato" / "_raw_backup"))
    return out


def stage_broll(root: Path, slug: str, want: int = 8) -> list[Path]:
    """挑出至少 want 个 B-roll 复制到 topics/<slug>/shorts/assets/broll/。"""
    pool = collect_broll(root, slug)
    dst = root / "topics" / slug / "shorts" / "assets" / "broll"
    dst.mkdir(parents=True, exist_ok=True)
    for old in dst.iterdir():
        if old.is_file():
            try:
                old.unlink()
            except OSError:
                pass
    chosen: list[Path] = []
    for src in pool:
        if len(chosen) >= want:
            break
        target = dst / src.name
        try:
            shutil.copy2(str(src), str(target))
            chosen.append(target)
        except OSError:
            continue
    return chosen


def host_mp4s(host_dir: Path) -> list[Path]:
    if not host_dir.is_dir():
        return []
    return [p for p in rglob_videos(host_dir, filter_real=False) if p.suffix.lower() == ".mp4"]


# --- 在线抓取（不中断） ---

def _selector_python(root: Path) -> str:
    """优先用项目 venv 的 python（其中有 requests），否则用当前解释器。"""
    cand = root / ".venv" / "bin" / "python3"
    if cand.is_file():
        return str(cand)
    cand = root / ".venv" / "bin" / "python"
    if cand.is_file():
        return str(cand)
    return sys.executable


def run_selector_nofail(root: Path, slug: str, script: Path) -> None:
    sel = root / "src" / "integrations" / "media_sources" / "selector.py"
    if not sel.is_file():
        log("INFO: 未找到 src.integrations.media_sources.selector，跳过网络抓取。")
        return
    py = _selector_python(root)
    env = {**os.environ, "PYTHONPATH": f"{str(root)}" + os.pathsep + os.environ.get("PYTHONPATH", "")}
    cmd = [
        py,
        "-m",
        "src.integrations.media_sources.selector",
        "--topic", slug,
        "--root", str(root),
        "--input", str(script),
        "--per-page", "4",
        "--max-queries", "5",
    ]
    if (os.environ.get("USE_DVIDS", "") or "").strip().lower() not in ("1", "true", "yes"):
        cmd += ["--source", "pexels,pixabay"]
    log(f"selector: $ {' '.join(cmd[:4])} ... (python={py})")
    r = subprocess.run(
        cmd, cwd=str(root), env=env, capture_output=True, text=True, check=False
    )
    if (r.stdout or ""):
        for line in (r.stdout or "").splitlines()[-15:]:
            log(f"  selector> {line}")
    if r.returncode != 0:
        log(
            f"WARN: selector exit={r.returncode}（继续用本地素材兜底） stderr={(r.stderr or '')[:500]!r}"
        )
    if not (os.environ.get("PEXELS_API_KEY") or "").strip():
        log("WARN: 未设置 PEXELS_API_KEY（Pexels 结果会为空）")
    if not (os.environ.get("PIXABAY_API_KEY") or "").strip():
        log("WARN: 未设置 PIXABAY_API_KEY（Pixabay 结果会为空）")


# --- timeline ---

@dataclass
class TSeg:
    type: str
    start: float
    duration: float
    text: str
    query: str = ""


def parse_lines(txt: str) -> list[str]:
    ls = [x.strip() for x in (txt or "").splitlines() if x.strip()]
    return ls or ["点进来。", "收工。"]


HOST_OPENING_SEC = 2.0


def build_timeline(lines: list[str], target: float, queries: list[str]) -> list[TSeg]:
    """新结构：host 只出现在开头 2 秒（钩子），剩余 4 段全部 B-roll。
    例：host 2s | broll 7s | broll 7s | broll 7s | broll 7s ≈ 30s。
    """
    L = (lines + ["。"] * 5)[:5]
    host_dur = min(HOST_OPENING_SEC, max(1.5, target * 0.10))
    rem = max(0.0, target - host_dur)
    weights = [0.27, 0.27, 0.24, 0.22]
    broll_durs = [round(rem * w, 2) for w in weights]
    diff = round(rem - sum(broll_durs), 2)
    if broll_durs:
        broll_durs[-1] = round(broll_durs[-1] + diff, 2)
    out: List[TSeg] = [TSeg("host", 0.0, round(host_dur, 2), L[0], "")]
    for i, d in enumerate(broll_durs):
        q = queries[i % len(queries)] if queries else ""
        out.append(TSeg("broll", 0.0, d, L[i + 1] if i + 1 < len(L) else L[-1], q))
    acc = 0.0
    for s in out:
        s.start = round(acc, 3)
        acc += s.duration
    total = sum(s.duration for s in out)
    broll_pct = sum(s.duration for s in out if s.type == "broll") / max(0.001, total)
    log(f"timeline 结构：host {host_dur:.1f}s + broll x4 ≈ {total:.1f}s (broll={broll_pct:.0%})")
    return out


def recompute_starts(segs: list[TSeg]) -> None:
    t = 0.0
    for s in segs:
        s.start = round(t, 3)
        t += float(s.duration)


# --- ffmpeg: one vertical segment ---

VVF = (
    f"scale={W}:{H}:force_original_aspect_ratio=increase,"
    f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2,setsar=1,fps={FPS},format=yuv420p"
)

FI, FO = 0.12, 0.12


def vchain(dur: float) -> str:
    return f"{VVF},fade=t=in:st=0:d={FI},fade=t=out:st={max(0.0, dur - FO - 0.02)}:d={FO}"


def has_audio(p: Path) -> bool:
    d = ffprobe_info(p)
    for s in d.get("streams") or []:
        if s.get("codec_type") == "audio":
            return True
    return False


def render_seg(
    src: Path, dur: float, out: Path, *, keep_audio: bool, ss: float, tag: str
) -> None:
    """渲染单个 vertical 1080x1920 silent 段（音频统一在最后用 TTS 替换）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd: list[str] = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if ss > 0.05:
        cmd += ["-ss", f"{ss:.2f}"]
    cmd += ["-i", str(src)]
    cmd += [
        "-f", "lavfi", "-t", f"{dur:.3f}",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={AR}",
    ]
    cmd += [
        "-t", f"{dur:.3f}",
        "-vf", vchain(dur),
        "-map", "0:v:0", "-map", "1:a:0",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(AR), "-ac", "2",
        "-shortest",
        str(out),
    ]
    run_ffmpeg(cmd, label=tag)


def render_seg_with_fallback(
    primary_src: Path,
    fallbacks: list[Path],
    dur: float,
    out: Path,
    *,
    ss: float,
    tag: str,
    title: str,
    seg_text: str,
) -> Path:
    """渲染一段，遇到黑屏自动换 fallback；最坏情况渲染纯色标题卡。"""
    candidates = [primary_src] + [p for p in fallbacks if p != primary_src]
    last_err: str = ""
    for i, src in enumerate(candidates):
        try:
            render_seg(src, dur, out, keep_audio=False, ss=ss if i == 0 else 0.0, tag=f"{tag}_try{i}")
        except RuntimeError as e:
            last_err = str(e)
            continue
        bf = black_fraction(out)
        if bf < 0.30:
            return src
        log(f"BLACK SEGMENT detected ({bf*100:.0f}%) for {tag}, source={src.name} → 切换素材")
    # 最终兜底：纯色渐变标题卡
    log(f"FALLBACK_TITLE_CARD: {tag}（所有 B-roll 都黑屏，使用渐变标题卡，{last_err=})")
    render_title_card(title, seg_text, dur, out, tag=f"{tag}_card")
    bf = black_fraction(out)
    if bf >= 0.50:
        raise RuntimeError(f"{tag} 渲染后仍为黑屏：bf={bf:.2f}")
    return out


def _ff_drawtext(text: str, ff: str, *, size: int, color: str, y_expr: str,
                 box: bool = False, border: int = 0, shadow: bool = False) -> str:
    safe = (
        text.replace("\\", r"\\")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
    )
    parts = [
        "drawtext=",
        f"fontfile='{ff}'",
        f"text='{safe}'",
        f"fontcolor={color}",
        f"fontsize={size}",
        "x=(w-text_w)/2",
        f"y={y_expr}",
    ]
    if border > 0:
        parts.append(f"borderw={border}")
        parts.append("bordercolor=black")
    if shadow:
        parts.append("shadowcolor=0x000000@0.85")
        parts.append("shadowx=4")
        parts.append("shadowy=6")
    if box:
        parts.append("box=1")
        parts.append("boxcolor=0x000000@0.55")
        parts.append("boxborderw=18")
    return parts[0] + ":".join(parts[1:])


def render_cover(
    title: str,
    hook_text: str,
    out_jpg: Path,
    *,
    fontfile: str,
) -> None:
    """生成 1080x1920 封面 jpg：渐变背景 + 标题 + 钩子大字 + 频道角标。"""
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    ff = fontfile.replace(":", r"\:").replace(",", r"\,")
    titles = _wrap_cjk(title, max_chars=10)[:2]
    hook_lines = _wrap_cjk(hook_text, max_chars=6)[:3]

    overlays: list[str] = []
    overlays.append(
        f"drawbox=x=0:y=0:w={W}:h={H}:color=0x142A55@0.55:t=fill"
    )
    overlays.append(
        f"drawbox=x=0:y={int(H*0.34)}:w={W}:h={int(H*0.40)}:color=0x000000@0.45:t=fill"
    )
    for i, t in enumerate(titles):
        overlays.append(_ff_drawtext(
            t, ff, size=70, color="0xffd166",
            y_expr=f"h*{0.20 + i*0.06:.3f}", box=True,
        ))
    for i, t in enumerate(hook_lines):
        overlays.append(_ff_drawtext(
            t, ff, size=180, color="white",
            y_expr=f"h*{0.40 + i*0.13:.3f}",
            border=8, shadow=True,
        ))
    overlays.append(_ff_drawtext(
        "STATEVERGE", ff, size=40, color="0xffd166",
        y_expr="h*0.93", box=True,
    ))

    vf_chain = ",".join(overlays)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", "0.04",
        "-i", f"color=c=0x0a1532:s={W}x{H}:d=0.04",
        "-vf", vf_chain,
        "-frames:v", "1",
        "-q:v", "3",
        str(out_jpg),
    ]
    run_ffmpeg(cmd, label="cover_jpg")


def render_title_card(title: str, sub: str, dur: float, out: Path, *, tag: str) -> None:
    """生成纯色渐变 + 标题文字的 1080x1920 段，保证不黑屏。"""
    safe_title = (title or "").replace(":", "\\:").replace("'", "\\'")
    safe_sub = (sub or "").replace(":", "\\:").replace("'", "\\'")
    fpath, fontsdir, _ = find_font()
    fontfile = fpath.replace(":", r"\:")
    vf = (
        f"color=c=0x0e1a3a:s={W}x{H}:r={FPS}:d={dur:.3f},"
        f"format=yuv420p,"
        f"drawbox=x=0:y=0:w={W}:h={H}:color=0x142A55@0.6:t=fill,"
        f"drawtext=fontfile='{fontfile}':text='{safe_title}':fontcolor=white:fontsize=72:"
        f"x=(w-text_w)/2:y=h*0.32:box=1:boxcolor=0x000000@0.4:boxborderw=24,"
        f"drawtext=fontfile='{fontfile}':text='{safe_sub[:32]}':fontcolor=0xffd166:fontsize=44:"
        f"x=(w-text_w)/2:y=h*0.45:box=1:boxcolor=0x000000@0.35:boxborderw=18"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-t", f"{dur:.3f}",
        "-i", f"color=c=0x0e1a3a:s={W}x{H}:r={FPS}",
        "-f", "lavfi", "-t", f"{dur:.3f}",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={AR}",
        "-vf", vf,
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "128k", "-ar", str(AR), "-ac", "2",
        "-shortest", str(out),
    ]
    run_ffmpeg(cmd, label=tag)


def concat_parts(parts: list[Path], out: Path, work: Path) -> None:
    lst = work / "concat.txt"
    lines: list[str] = []
    for p in parts:
        s = p.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{s}'\n")
    lst.write_text("".join(lines), encoding="utf-8")
    try:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-c",
                "copy",
                str(out),
            ],
            label="concat_copy",
        )
    except RuntimeError:
        run_ffmpeg(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(lst),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                str(out),
            ],
            label="concat_reencode",
        )


def mux_voice(
    vid: Path, voice_wav: Path, music: Optional[Path], out: Path
) -> None:
    """final mux：旁白 = voice.wav (主)，背景音乐音量很低 (可选)。原视频音轨弃用。"""
    if not voice_wav.is_file():
        raise RuntimeError(f"voice.wav 不存在：{voice_wav}")
    d_vid = ffprobe_duration(vid)
    d_voice = ffprobe_duration(voice_wav)
    if d_vid <= 0 or d_voice <= 0:
        raise RuntimeError(f"无效时长 video={d_vid} voice={d_voice}")
    if music and music.is_file():
        fc = (
            f"[1:a]aresample={AR},apad,atrim=0:{d_vid:.3f},asetpts=PTS-STARTPTS,volume=1.4[v0];"
            f"[2:a]aresample={AR},aloop=loop=-1:size=2e9,atrim=0:{d_vid:.3f},"
            f"asetpts=PTS-STARTPTS,volume=0.08[bed];"
            f"[v0][bed]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(vid),
            "-i", str(voice_wav),
            "-i", str(music),
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
            "-t", f"{d_vid:.3f}",
            str(out),
        ]
        run_ffmpeg(cmd, label="mux_voice_music")
    else:
        fc = (
            f"[1:a]aresample={AR},apad,atrim=0:{d_vid:.3f},asetpts=PTS-STARTPTS,volume=1.4[aout]"
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(vid),
            "-i", str(voice_wav),
            "-filter_complex", fc,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
            "-t", f"{d_vid:.3f}",
            str(out),
        ]
        run_ffmpeg(cmd, label="mux_voice_only")


def ass_escape(s: str) -> str:
    return s.replace("{", r"\{").replace("}", r"\}")


def t_ass(sec: float) -> str:
    s = max(0.0, float(sec))
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    s2 = s - 3600 * h - 60 * m
    return f"{h}:{m:02d}:{s2:05.2f}"


def _wrap_cjk(text: str, max_chars: int = 11) -> list[str]:
    """按字符数切行，CJK/英文混排都按 1 字符宽计；不破坏单词。"""
    s = (text or "").replace("\n", " ").strip()
    if not s:
        return ["…"]
    out: list[str] = []
    cur = ""
    for ch in s:
        if len(cur) >= max_chars and ch in (" ", "，", ",", "、", "；", ";"):
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
        if len(cur) >= max_chars + 4:
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out or [s]


def write_ass(
    out: Path, segs: list[TSeg], family: str, size: int, fontsdir: str
) -> None:
    """竖屏 Shorts 字幕：底部居中，大字号，黑底白字 + 厚描边；
    每段一条 Dialogue（用 \\N 换行），杜绝多行重叠。"""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
ScaledBorderAndShadow: yes
WrapStyle: 0
[V4+ Styles]
Format: Name,Fontname,FontSize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,UnderLine,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding
Style: Default,{family},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,1,0,0,0,100,100,0,0,1,5,2,2,40,40,260,1
[Events]
Format: Layer, Start, End, Style, Text
"""
    ev: list[str] = []
    for c in segs:
        t0 = c.start
        t1 = c.start + c.duration
        wrapped = _wrap_cjk(c.text, max_chars=11)
        text_ass = "\\N".join(ass_escape(line) for line in wrapped)
        ev.append(
            f"Dialogue: 0,{t_ass(t0)},{t_ass(t1)},Default,{text_ass}"
        )
    out.write_text(head + "\n".join(ev) + "\n", encoding="utf-8")


def burn_subs(v: Path, ass: Path, fontsdir: str, o: Path, family: str = "PingFang SC") -> None:
    ap = ass.resolve().as_posix().replace(":", r"\:").replace(",", r"\,")
    fd = Path(fontsdir).resolve().as_posix().replace(":", r"\:").replace(",", r"\,")
    fam = family.replace(",", r"\,")
    force = f"FontName={fam},Fontsize=76,Bold=1,Outline=5,Shadow=2,BorderStyle=1"
    vf = f"subtitles=filename={ap}:fontsdir={fd}:force_style='{force}'"
    run_ffmpeg(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(v),
            "-vf", vf,
            "-c:a", "copy",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p",
            str(o),
        ],
        label="subs_burn",
    )


def _extract_face_to_ttf(ttc_path: Path, face_name: str, out_ttf: Path) -> Optional[str]:
    """从 .ttc 里提取指定 family 的子字体，存为独立 .ttf。
    返回真正写入的 family name，失败返回 None。
    """
    try:
        from fontTools.ttLib import TTCollection, TTFont  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        if ttc_path.suffix.lower() == ".ttc":
            ttc = TTCollection(str(ttc_path))
            target = None
            for f in ttc.fonts:
                fam = f["name"].getDebugName(1) or ""
                if fam == face_name:
                    target = f
                    break
            if target is None:
                return None
        else:
            target = TTFont(str(ttc_path))
        out_ttf.parent.mkdir(parents=True, exist_ok=True)
        target.flavor = None
        target.save(str(out_ttf))
        return face_name
    except Exception:
        return None


def find_font(work: Optional[Path] = None) -> tuple[str, str, str]:
    """返回 (font_file, fontsdir, family_name)。
    优先从 .ttc 里提取一个独立 .ttf 到 work/_fonts/，避免 libass 在 macOS 上找不到 face。"""
    for path, name in MAC_FONT_CANDS:
        p = Path(path)
        if not p.is_file():
            continue
        if work is not None and p.suffix.lower() == ".ttc":
            fonts_dir = work / "_fonts"
            ttf_out = fonts_dir / f"{name.replace(' ', '_')}.ttf"
            if not ttf_out.is_file():
                fam = _extract_face_to_ttf(p, name, ttf_out)
                if not fam:
                    continue
            if ttf_out.is_file():
                return str(ttf_out), fonts_dir.as_posix(), name
        return str(p), p.parent.as_posix(), name
    return "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts", "DejaVu Sans"


def find_music(root: Path) -> Optional[Path]:
    d = root / "assets" / "envato" / "music"
    if not d.is_dir():
        return None
    for e in (".mp3", ".m4a", ".aac", ".wav", ".flac"):
        for f in sorted(d.glob(f"*{e}")):
            if f.is_file():
                return f
    return None


# --- 分配素材 ---

def build_render_plan(
    segs: list[TSeg],
    host: list[Path],
    broll: list[Path],
) -> tuple[list[dict[str, Any]], bool]:
    if not broll:
        raise SystemExit(2)
    h_use: dict[Path, int] = {p: 0 for p in host} if host else {}
    bi = 0
    h_i = 0
    out: list[dict[str, Any]] = []
    host_fb = not bool(host)
    n_br = 0
    for s in segs:
        d = float(s.duration)
        if s.type == "host":
            if not host:
                p = broll[bi % len(broll)]
                bi += 1
                out.append(
                    {
                        "src": p,
                        "dur": d,
                        "keep": True,
                        "ss": 0.0,
                    }
                )
            else:
                p = host[h_i % len(host)]
                h_i += 1
                if h_use and h_use.get(p, 0) >= 2 and len(host) > 1:
                    p = host[(h_i + 1) % len(host)]
                h_use[p] = h_use.get(p, 0) + 1
                d_in = ffprobe_duration(p) or 5.0
                want = 10.0 if d_in >= 8.0 else 5.0
                d_use = min(d, want, d_in) if d_in else d
                out.append({"src": p, "dur": d_use, "keep": True, "ss": 0.0})
        else:
            a = broll[bi % len(broll)]
            n_br += 1
            src = a
            ss = 0.0
            if len(broll) == 1:
                base = max(1.0, (ffprobe_duration(broll[0]) or 6.0) * 0.2)
                ss = min(
                    n_br * 0.6,
                    max(0.0, (ffprobe_duration(broll[0]) or 6.0) - d - 0.2),
                ) % (base * 2 + 0.01)
            else:
                b = broll[(bi + 1) % len(broll)]
                if b != a and n_br % 2:
                    src = b
            bi += 1
            out.append({"src": src, "dur": d, "keep": False, "ss": ss})
    if len(broll) == 1 and len([x for x in segs if x.type == "broll"]) > 1:
        log("WARN: 单素材 b-roll 多次取不同起剪点，以满足「不全片一段撑满」的约束。")
    return out, host_fb


def write_youtube_md(
    path: Path, title: str, segs: list[TSeg], kws: list[str]
) -> None:
    tags = "AI, 职场, 财经, 纪录片, 中文"
    t5 = [
        f"「{title}」一句话把事说清",
        f"{title} 真相，30 秒说穿",
        f"拆「{title}」：不灌鸡汤",
        f"别用情绪看「{title}」",
        f"{title} 之后，你怎么选？",
    ]
    md = f"""# {title} — YouTube Shorts 元信息（机器生成，可手改）

## 标题候选（5）
{chr(10).join('- ' + t for t in t5)}

## 描述
- 用数据与约束条件拆解「{title}」。
- 不替代专业建议，欢迎理性讨论。

## Tags
{tags}

## Hashtags
#知识分享 #社会观察 #经济常识 #个人成长

## 置顶评论（可粘贴）
- 你认同一句话里哪个数字最重要？我下期拆给你看。

## 长视频引流 CTA
- 评论区置顶：长视频/深度稿见频道简介链接（StateVerge 长视频主栏目）。

## 本支素材关键词
{", ".join(kws)}
"""
    path.write_text(md, encoding="utf-8")


def verify(p: Path, t_target: float) -> int:
    st = ffprobe_info(p)
    w = h = 0
    vok = aok = False
    for s in st.get("streams", []) or []:
        if s.get("codec_type") == "video":
            vok = True
            w = int(s.get("width") or 0)
            h = int(s.get("height") or 0)
        if s.get("codec_type") == "audio":
            aok = True
    d = float((st.get("format") or {}).get("duration") or 0.0)
    if not (vok and aok and w == W and h == H and 20.0 <= d <= 35.0):
        log(
            f"VERIFY_FAIL: v={vok} a={aok} {w}x{h} need {W}x{H} dur={d:.3f} need[20,35] target~{t_target}"
        )
        return 1
    mean_db, max_db = volume_db(p)
    if max_db < -30.0 or mean_db < -45.0:
        log(
            f"VERIFY_FAIL_AUDIO: mean={mean_db:.1f}dB max={max_db:.1f}dB (静音/过低)"
        )
        return 1
    log(
        f"VERIFY_OK: {W}x{H} dur={d:.2f}s vol mean={mean_db:.1f}dB max={max_db:.1f}dB"
    )
    return 0


# --- main ---

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--host-dir", default="assets/host/runway")
    ap.add_argument("--minutes", type=float, default=0.5)
    ap.add_argument("--script-file", type=Path, default=None)
    ap.add_argument(
        "--allow-no-tts",
        action="store_true",
        help="（不推荐）允许在没有 ElevenLabs 时继续，输出会被打回 FAIL_NO_VOICE",
    )
    args = ap.parse_args(argv)

    root = _root()
    os.chdir(root)
    load_dotenv(root)

    slug = args.slug.strip()
    title = (args.title or "").strip() or "未命名"
    tdir = root / "topics" / slug
    sdir = tdir / "shorts"
    outd = sdir / "output"
    assd = sdir / "assets"
    work = assd / "_work"
    audio_d = sdir / "audio"
    broll_d = assd / "broll"
    for d in (sdir, outd, assd, work, audio_d, broll_d, tdir / "assets" / "raw", tdir / "brief"):
        d.mkdir(parents=True, exist_ok=True)
    log(f"输出目录就绪：{sdir}")

    if args.script_file and args.script_file.is_file():
        script = args.script_file.read_text(encoding="utf-8", errors="replace")
    else:
        script = autogen_script(title)
    sdir.joinpath("script.txt").write_text(script, encoding="utf-8")
    fbrief = tdir / "brief" / "narration_script.txt"
    if not fbrief.is_file():
        fbrief.write_text(script, encoding="utf-8")

    # === 1. ElevenLabs TTS（强制） ===
    voice_mp3 = audio_d / "voice.mp3"
    voice_wav = audio_d / "voice.wav"
    has_tts_env = bool(_env_str("ELEVENLABS_API_KEY")) and bool(_env_str("ELEVENLABS_VOICE_ID"))
    if not has_tts_env and not args.allow_no_tts:
        log("[FAIL] Missing ELEVENLABS_API_KEY or ELEVENLABS_VOICE_ID")
        return 3
    if has_tts_env:
        try:
            log("ElevenLabs TTS 生成中...")
            generate_elevenlabs_tts(script, voice_mp3, voice_wav)
            log(
                f"voice.wav OK dur={ffprobe_duration(voice_wav):.2f}s "
                f"({voice_wav.stat().st_size/1024:.1f} KiB)"
            )
        except RuntimeError as e:
            log(f"[FAIL] ElevenLabs TTS 失败：{e}")
            if not args.allow_no_tts:
                return 4
    if not voice_wav.is_file() or ffprobe_duration(voice_wav) < 5.0:
        log("[FAIL] voice.wav 不存在或 < 5s")
        return 5

    voice_dur = ffprobe_duration(voice_wav)
    tsec = max(20.0, min(30.0, voice_dur))
    log(f"目标视频时长 = {tsec:.2f}s（voice={voice_dur:.2f}s）")

    # === 2. 生成英文关键词 → 喂给 selector 抓 Pexels/Pixabay ===
    kws = keywords_for_title(title)
    queries_txt = tdir / "brief" / "queries_for_selector.txt"
    # 每行一段独立的"chunk"，selector 会把它们当独立 segment 提关键字
    queries_txt.write_text(
        "\n\n---\n\n".join(f"segment_{i+1:02d}\n{kw}" for i, kw in enumerate(kws)) + "\n",
        encoding="utf-8",
    )
    log(f"selector 关键词 ({len(kws)}): {kws}")
    run_selector_nofail(root, slug, queries_txt)
    chosen = stage_broll(root, slug, want=8)
    log(f"已 stage {len(chosen)} 个 B-roll → {broll_d.relative_to(root)}")
    if len(chosen) < 1:
        log("[FAIL] 没有任何可用 B-roll（topic raw / envato _raw_backup 都为空）")
        return 6
    broll = chosen[:]

    host_dir = (root / args.host_dir) if not Path(args.host_dir).is_absolute() else Path(args.host_dir)
    hlist = host_mp4s(host_dir)
    if hlist:
        log(f"主持人池：{len(hlist)} 个 mp4 (dir={host_dir})")
    else:
        log(f"HOST_FALLBACK: no runway host video found at {host_dir} → 主持人段用 B-roll 代替")

    # === 3. timeline 跟 voice 对齐 ===
    lines = parse_lines(script)
    tl = build_timeline(lines, tsec, kws)
    if not tl:
        log("FATAL: timeline is empty")
        return 7
    cur = 0.0
    capped: List[TSeg] = []
    for seg in tl:
        if cur >= tsec:
            break
        if cur + seg.duration > tsec:
            seg.duration = round(tsec - cur, 3)
        if seg.duration <= 0.05:
            break
        capped.append(seg)
        cur += seg.duration
    tl = capped
    recompute_starts(tl)
    log(
        f"timeline 段数={len(tl)} 总时长={sum(s.duration for s in tl):.2f}s "
        f"broll占比={sum(s.duration for s in tl if s.type=='broll')/max(0.001,sum(s.duration for s in tl)):.0%}"
    )
    try:
        plan, _hf = build_render_plan(tl, hlist, broll)
    except SystemExit:
        log("[FAIL] build_render_plan: 没有可用视频素材")
        return 8
    for i, s in enumerate(tl):
        s.duration = float(plan[i]["dur"])
    recompute_starts(tl)
    sdir.joinpath("timeline.json").write_text(
        json.dumps([asdict(x) for x in tl], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # === 4. 渲染每段（带黑屏 fallback） ===
    for i, (seg, p) in enumerate(zip(tl, plan)):
        out_seg = work / f"seg_{i:02d}.mp4"
        if out_seg.exists():
            try:
                out_seg.unlink()
            except OSError:
                pass
        fallbacks = broll[:] + (hlist[:] if hlist else [])
        render_seg_with_fallback(
            p["src"],
            fallbacks,
            p["dur"],
            out_seg,
            ss=float(p.get("ss") or 0.0),
            tag=f"seg_{i:02d}",
            title=title,
            seg_text=seg.text,
        )
        bf = black_fraction(out_seg)
        if bf >= 0.30:
            log(f"[FAIL] seg_{i:02d} 仍然黑屏 ({bf*100:.0f}%)")
            return 9

    parts = sorted(work.glob("seg_*.mp4"))
    if not parts:
        log("[FAIL] 没有渲染出任何 seg_*.mp4")
        return 10
    c1 = work / "concat1.mp4"
    concat_parts(parts, c1, work)

    # === 5. 替换音频为 voice.wav (+ 极轻背景音乐) ===
    mus = find_music(root)
    c2 = work / "with_voice.mp4"
    mux_voice(c1, voice_wav, mus, c2)

    fpath, fontsdir, family = find_font(work)
    if not Path(fpath).is_file():
        log("FATAL: 无可用中文字体")
        return 11
    log(f"字幕字体: family={family!r} file={fpath}")
    as_f = work / "subs.ass"
    write_ass(as_f, tl, family, 76, fontsdir)

    final = outd / "final_short.mp4"
    archive_old_final(outd)
    burn_subs(c2, as_f, fontsdir, final, family=family)

    # 封面：取 script 第一行作为大字钩子
    hook = (lines[0] if lines else title).strip().rstrip("。.!?！？")
    cover_path = outd / "cover.jpg"
    try:
        render_cover(title, hook, cover_path, fontfile=fpath)
        log(f"封面已生成：{cover_path.relative_to(root)}")
    except RuntimeError as e:
        log(f"WARN: 封面生成失败：{e}")

    write_youtube_md(sdir / "youtube_metadata.md", title, tl, kws)

    v = verify(final, tsec)
    return v


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
