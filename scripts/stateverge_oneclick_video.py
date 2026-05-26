"""
StateVerge one-click auto-video pipeline (CN subtitle hardened).

For a given Chinese topic + slug, runs end to end:

    A. Narration script  (OpenAI / DeepSeek / template)
    B. YouTube metadata  (titles, description, tags, shorts)
    C. ElevenLabs TTS    (mp3 + 48kHz stereo wav, auto-chunked)
    D. Stock media       (Pexels + Pixabay; envato fallback)
    E. Auto edit         (1920x1080 30fps h264 yuv420p, no orig audio)
    F. Background music  (envato library, 8-12% volume)
    G. Subtitles         (whisper.cpp if model present; else naive split;
                          SRT -> ASS with macOS CJK font auto-detect;
                          ass=... filter w/ fontsdir; subtitles=... fallback)
    H. Archive           (per-output, before overwrite)
    I. ffprobe verify

Usage:
    python scripts/stateverge_oneclick_video.py \\
        --topic "AI正在改变世界，普通人还能做什么" \\
        --slug ai_future_cn --minutes 10

Idempotent re-runs (skip steps whose primary output exists):
    --force-script   regenerate narration & youtube meta
    --force-tts      regenerate voice.mp3/voice.wav
    --force-media    re-fetch stock clips
    --force-edit     re-render silent_video + final_video
    --force-subs     regenerate srt/ass + re-burn final_video_with_subtitles
    --force-all      all of the above
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

LOG_PREFIX = "[oneclick]"
TARGET_W, TARGET_H, TARGET_FPS = 1920, 1080, 30


# ============================================================================
# logging utilities
# ============================================================================

def log(step: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"{LOG_PREFIX} [{ts}] [{step}] {msg}", flush=True)


def section(step: str, title: str) -> None:
    bar = "─" * 68
    print(f"\n{LOG_PREFIX} {bar}", flush=True)
    print(f"{LOG_PREFIX} STEP {step}: {title}", flush=True)
    print(f"{LOG_PREFIX} {bar}", flush=True)


# ============================================================================
# env / paths / utf-8 io
# ============================================================================

def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv(root: Path) -> None:
    p = root / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv as _ld  # type: ignore[import-not-found]
        _ld(p, override=False)
        return
    except ImportError:
        pass
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        k, v = k.strip(), v.strip()
        if v.startswith('"') and v.endswith('"'):
            v = v[1:-1]
        if k and k not in os.environ:
            os.environ[k] = v


def env_str(name: str, default: str = "") -> str:
    return (os.environ.get(name) or "").strip() or default


def write_utf8_text(path: Path, text: str) -> None:
    """Write text as UTF-8 (no BOM) with LF line endings, parents auto-created."""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)


# ============================================================================
# ffmpeg / ffprobe helpers
# ============================================================================

def ffprobe_duration(path: Path) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return 0.0
    try:
        return float((r.stdout or "0").strip())
    except ValueError:
        return 0.0


def ffprobe_summary(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration,size:stream=index,codec_type,codec_name,width,height,r_frame_rate,sample_rate,channels",
        "-of", "json", str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        return {"error": r.stderr.strip()}
    try:
        j = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"error": "invalid ffprobe json"}
    fmt = j.get("format") or {}
    streams = j.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})

    def _fps(rfr: str) -> float:
        try:
            n, d = rfr.split("/")
            d = float(d) or 1.0
            return float(n) / d
        except Exception:
            return 0.0

    return {
        "duration_sec": float(fmt.get("duration") or 0.0),
        "size_bytes": int(fmt.get("size") or 0),
        "has_video": bool(v),
        "has_audio": bool(a),
        "video_codec": v.get("codec_name", ""),
        "width": int(v.get("width") or 0),
        "height": int(v.get("height") or 0),
        "fps": _fps(v.get("r_frame_rate", "0/1")),
        "audio_codec": a.get("codec_name", ""),
        "audio_sample_rate": int(a.get("sample_rate") or 0),
        "audio_channels": int(a.get("channels") or 0),
    }


def run_ffmpeg(args: list[str], step: str, label: str) -> tuple[bool, str]:
    """Returns (ok, stderr)."""
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        log(step, f"ffmpeg {label} FAILED: {(r.stderr or '')[:600]}")
        return False, r.stderr or ""
    return True, r.stderr or ""


def fmt_ts_srt(secs: float) -> str:
    secs = max(0.0, secs)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s_int = int(secs % 60)
    ms = int(round((secs - int(secs)) * 1000))
    if ms == 1000:
        ms = 0
        s_int += 1
    return f"{h:02d}:{m:02d}:{s_int:02d},{ms:03d}"


def fmt_ts_ass(secs: float) -> str:
    """ASS uses H:MM:SS.cc (centiseconds, 1 hour digit minimum)."""
    secs = max(0.0, secs)
    h = int(secs // 3600)
    m = int((secs % 3600) // 60)
    s_int = int(secs % 60)
    cs = int(round((secs - int(secs)) * 100))
    if cs == 100:
        cs = 0
        s_int += 1
    return f"{h:d}:{m:02d}:{s_int:02d}.{cs:02d}"


def fmt_dur(secs: float) -> str:
    s = int(round(secs))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h:d}:{m:02d}:{ss:02d}" if h else f"{m:d}:{ss:02d}"


# ============================================================================
# archive (per-output)
# ============================================================================

def archive_one(path: Path, root: Path) -> None:
    """If `path` exists, move it to <path.parent>/archive/<YYYYmmdd_HHMMSS>/<name>."""
    if not path.is_file():
        return
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst_dir = path.parent / "archive" / ts
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / path.name
    shutil.move(str(path), str(dst))
    try:
        rel = dst.relative_to(root)
    except ValueError:
        rel = dst
    log("archive", f"{path.name} -> {rel}")


# ============================================================================
# LLM
# ============================================================================

def llm_chat(
    *, prompt: str, system: str, max_tokens: int = 4096, temperature: float = 0.7
) -> tuple[str | None, str]:
    openai_key = env_str("OPENAI_API_KEY")
    deepseek_key = env_str("DEEPSEEK_API_KEY")

    attempts: list[tuple[str, str, str, str]] = []
    if openai_key:
        attempts.append((
            "openai",
            "https://api.openai.com/v1/chat/completions",
            openai_key,
            env_str("OPENAI_MODEL", "gpt-4o-mini"),
        ))
    if deepseek_key:
        attempts.append((
            "deepseek",
            "https://api.deepseek.com/chat/completions",
            deepseek_key,
            env_str("DEEPSEEK_MODEL", "deepseek-chat"),
        ))

    for label, url, key, model in attempts:
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(url, headers=headers, json=body, timeout=180)
        except requests.RequestException as e:
            log("llm", f"{label} request_error: {e}")
            continue
        if r.status_code != 200:
            log("llm", f"{label} HTTP {r.status_code}: {(r.text or '')[:240]}")
            continue
        try:
            j = r.json()
            text = j["choices"][0]["message"]["content"].strip()
            return text, f"{label}:{model}"
        except (KeyError, IndexError, ValueError) as e:
            log("llm", f"{label} bad payload: {e}")
            continue
    return None, "none"


# ============================================================================
# Step A: narration
# ============================================================================

NARRATION_SYSTEM = (
    "你是 StateVerge 中文频道的资深纪录片撰稿人。"
    "你写的旁白属于：理性、现实、纪录片质感，关注社会变化、AI、商业、教育、普通人出路。"
    "风格要求：不是新闻播报；不是学术论文；不是煽动；不是营销话术。"
    "句子有节奏，可读性高，每段独立成行，段落之间用空行分隔，方便逐段配音。"
    "中文标点用中文标点。不要使用 markdown 标记。不要写标题列表。不要分点编号。"
    "整段输出就是可以直接拿去配音的连贯旁白文本。"
)


def narration_template(topic: str) -> str:
    return f"""《{topic}》

这是一个普通人正在被时代重新定义的时代。

技术的进步并不是均匀分布的。有人正在抢先利用新工具，有人还在原地等待。

{topic}，本身不是一个抽象问题。它牵扯到每一个人的工作、收入、判断力，以及未来几年的选择。

人工智能、信息洪流、内容过剩、注意力稀缺，这些事情同时发生。普通人面对的不是单一变化，而是结构性的重塑。

所以问题已经不是会不会改变，而是怎么应对。

第一，看清趋势。技术革命不会等谁。能持续输出、能持续学习、能搭建系统的人，会拿到红利。

第二，建立自己的产出。不管做内容、做服务、做小生意，都要留下作品和案例。说自己会什么没用，看做出了什么。

第三，选择长期方向。短线机会不稀缺，能坚持一年两年三年的人才稀缺。

未来不会属于最早焦虑的人，也不会属于最会喊口号的人。未来属于那些愿意学习、愿意行动、愿意把新工具变成自己生产系统的人。

你准备从哪里开始？
"""


def step_narration(
    *, slug: str, topic: str, minutes: int, root: Path, force: bool
) -> Path:
    section("A", "narration script")
    out = root / "topics" / slug / "brief" / "narration_script.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and out.stat().st_size > 200 and not force:
        log("A", f"reuse existing  {out.relative_to(root)}  "
                 f"({out.stat().st_size} bytes)")
        return out

    target_chars = int(minutes * 380)
    user_prompt = textwrap.dedent(f"""
        请为 StateVerge 中文频道写一段中文旁白。

        主题：{topic}
        目标时长：约 {minutes} 分钟（汉字 {target_chars}-{target_chars + 600} 字之间）

        要求：
        - 第一行写一个《标题》。
        - 用空行分段，每段一句到三句话，方便逐段 TTS 配音。
        - 风格：纪录片旁白，理性现实，不夸张，不煽动，不学术。
        - 内容贴近普通人：解释这件事在发生什么，谁会受影响，普通人有哪些可执行的方向。
        - 结尾用一个开放式问题。
        - 不要 markdown，不要分点编号，不要 emoji。
    """).strip()

    log("A", "calling LLM ...")
    text, provider = llm_chat(prompt=user_prompt, system=NARRATION_SYSTEM, max_tokens=4500)
    if not text:
        log("A", "no LLM available; using local template")
        text = narration_template(topic)
        provider = "template"
    write_utf8_text(out, text.strip() + "\n")
    chars = sum(1 for c in text if c.strip())
    paras = sum(1 for p in re.split(r"\n\s*\n+", text) if p.strip())
    log("A", f"wrote {out.relative_to(root)}  provider={provider}  chars={chars}  paragraphs={paras}")
    return out


# ============================================================================
# Step B: YouTube metadata
# ============================================================================

YT_SYSTEM = (
    "你是 StateVerge 中文频道的资深内容策略师。你为纪录片风格的 YouTube 频道写"
    "标题、描述、标签和 Shorts 切片建议。风格：克制、现实、纪录片感。"
    "禁止 clickbait、禁止全大写、禁止 emoji 滥用、禁止"
    "“震惊”“爆炸”“看哭”这一类词。"
)

YT_PROMPT_TPL = """
请基于下面的中文旁白，输出严格的 JSON（不要任何 markdown、不要前后说明），结构如下：

{{
  "titles": ["候选1","候选2","候选3","候选4","候选5"],
  "recommended_title": "...",
  "description": "...",
  "tags": ["...","...","..."],
  "shorts_clips": [
    {{"hook": "...", "what_to_show": "...", "duration_sec": 45}}
  ]
}}

约束：
- titles 5 条，长度都在 22-38 个汉字之间。
- recommended_title 选 titles 里的一条，不能新造。
- description 200-380 字，分 3-5 段（用空行分段），结尾留一个#标签行（5-10 个 #标签）。
- tags 用半角逗号风格的关键词数组，10-20 个，不要 # 号，全部小写或中文。
- shorts_clips 4-6 条，每条 30-60 秒，给出 hook、what_to_show、duration_sec。

主题：{topic}

旁白原文：
"""

YT_FALLBACK = {
    "titles": [
        "AI正在改变世界，普通人还能做什么",
        "AI不是终点，是普通人新的起点",
        "当AI成为每个人的工具，你准备做什么",
        "AI时代，普通人真正的机会在哪里",
        "AI不会取代你，但准备好的人会取代你",
    ],
    "shorts_clips_default": [
        {"hook": "AI不是替代你，AI是放大你", "what_to_show": "对比有/无 AI 的工作流程", "duration_sec": 45},
        {"hook": "未来稀缺的不是工具，是判断力", "what_to_show": "讨论“执行变便宜，判断变贵”", "duration_sec": 45},
        {"hook": "三个普通人也能上的方向", "what_to_show": "内容生产、小生意自动化、个人知识系统", "duration_sec": 55},
        {"hook": "AI时代最容易犯的错误", "what_to_show": "到处追热点 vs 一个方向做三年", "duration_sec": 45},
    ],
}


def step_youtube_meta(
    *, slug: str, topic: str, narration_text: str, root: Path, force: bool
) -> None:
    section("B", "YouTube metadata")
    out_dir = root / "topics" / slug / "script"
    out_dir.mkdir(parents=True, exist_ok=True)
    title_file = out_dir / "youtube_title.txt"
    desc_file = out_dir / "youtube_description.txt"
    tags_file = out_dir / "youtube_tags.txt"
    shorts_file = out_dir / "shorts_clips.md"

    if (
        all(p.is_file() and p.stat().st_size > 0 for p in (title_file, desc_file, tags_file, shorts_file))
        and not force
    ):
        log("B", f"reuse existing metadata in {out_dir.relative_to(root)}")
        return

    user_prompt = YT_PROMPT_TPL.format(topic=topic) + narration_text[:6000]

    log("B", "calling LLM ...")
    text, provider = llm_chat(
        prompt=user_prompt, system=YT_SYSTEM, max_tokens=2500, temperature=0.5
    )

    data: dict[str, Any] | None = None
    if text:
        s = text.strip()
        if s.startswith("```"):
            s = re.sub(r"^```(?:json)?\s*", "", s).rstrip("`").rstrip()
            s = s.rstrip("`").strip()
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", s, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(0))
                except json.JSONDecodeError:
                    data = None

    if not isinstance(data, dict):
        log("B", "LLM unavailable or returned non-JSON; using fallback")
        data = {
            "titles": YT_FALLBACK["titles"],
            "recommended_title": YT_FALLBACK["titles"][0],
            "description": (
                f"《{topic}》\n\n"
                "StateVerge 中文频道：理性、现实、纪录片视角，关注 AI、社会变化、普通人出路。\n\n"
                "本期讲清三件事：AI 在改变什么、普通人真正的机会在哪、应该怎么开始。\n\n"
                "如果你也在思考这些问题，欢迎订阅。\n\n"
                "#AI #普通人 #未来 #StateVerge #自媒体"
            ),
            "tags": [
                "AI", "人工智能", "普通人", "副业", "自媒体",
                "纪录片", "中文频道", "未来", "stateverge", "ai future",
            ],
            "shorts_clips": YT_FALLBACK["shorts_clips_default"],
        }
        provider = "fallback"

    titles_list = [str(t).strip() for t in (data.get("titles") or []) if str(t).strip()]
    recommended = (data.get("recommended_title") or (titles_list[0] if titles_list else topic)).strip()
    description = str(data.get("description") or "").strip()
    tags_list = [str(t).strip() for t in (data.get("tags") or []) if str(t).strip()]
    shorts_list = data.get("shorts_clips") or []

    title_lines = [f"# 推荐标题\n{recommended}\n", "# 候选标题"]
    for i, t in enumerate(titles_list, 1):
        title_lines.append(f"{i}. {t}")
    write_utf8_text(title_file, "\n".join(title_lines).strip() + "\n")
    write_utf8_text(desc_file, description + "\n")
    write_utf8_text(tags_file, ", ".join(tags_list) + "\n")

    md = ["# Shorts 切片建议\n"]
    if isinstance(shorts_list, list):
        for i, sc in enumerate(shorts_list, 1):
            if not isinstance(sc, dict):
                continue
            hook = str(sc.get("hook", "")).strip()
            show = str(sc.get("what_to_show", "")).strip()
            dur = sc.get("duration_sec", 45)
            md.append(f"## 切片 {i:02d}（{dur}s）")
            md.append(f"- **Hook**: {hook}")
            md.append(f"- **画面**: {show}")
            md.append("")
    write_utf8_text(shorts_file, "\n".join(md).strip() + "\n")

    log("B", f"wrote 4 files in {out_dir.relative_to(root)}  provider={provider}")
    log("B", f"  recommended_title: {recommended}")


# ============================================================================
# Step C: ElevenLabs TTS (delegates to existing chunked script)
# ============================================================================

def step_tts(*, slug: str, root: Path, force: bool) -> Path | None:
    section("C", "ElevenLabs TTS")
    audio_dir = root / "topics" / slug / "audio"
    voice_mp3 = audio_dir / "voice.mp3"
    voice_wav = audio_dir / "voice.wav"

    if not env_str("ELEVENLABS_API_KEY"):
        log("C", "ELEVENLABS_API_KEY missing in .env; SKIP voice generation. "
                 "narration_script.txt is preserved.")
        return voice_wav if voice_wav.is_file() else None
    if not env_str("ELEVENLABS_VOICE_ID"):
        log("C", "ELEVENLABS_VOICE_ID missing in .env; SKIP voice generation. "
                 "Set a voice id explicitly to enable TTS (refusing to pick a default voice).")
        return voice_wav if voice_wav.is_file() else None

    if voice_mp3.is_file() and voice_wav.is_file() and not force:
        log("C", f"reuse existing voice.mp3 + voice.wav ({fmt_dur(ffprobe_duration(voice_wav))})")
        return voice_wav

    py = sys.executable
    tts_script = root / "scripts" / "generate_eleven_tts_for_topic.py"
    if not tts_script.is_file():
        log("C", f"missing {tts_script}; SKIP TTS")
        return voice_wav if voice_wav.is_file() else None

    log("C", f"running {tts_script.name} --topic {slug}")
    r = subprocess.run(
        [py, str(tts_script), "--topic", slug],
        cwd=str(root),
    )
    if r.returncode != 0:
        log("C", f"TTS subprocess returned exit={r.returncode}; continuing without voice")
        return voice_wav if voice_wav.is_file() else None
    if not voice_wav.is_file():
        log("C", "TTS finished but voice.wav missing; SKIP downstream audio")
        return None
    log("C", f"voice.wav  {fmt_dur(ffprobe_duration(voice_wav))}")
    return voice_wav


# ============================================================================
# Step D: stock media (Pexels + Pixabay; envato fallback)
# ============================================================================

def step_media(
    *, slug: str, root: Path, force: bool, max_per_segment: int = 25
) -> tuple[list[Path], Path | None]:
    section("D", "stock media (pexels + pixabay)")
    raw_dir = root / "topics" / slug / "assets" / "raw"
    manifest = root / "topics" / slug / "assets" / "media_manifest.json"
    raw_dir.mkdir(parents=True, exist_ok=True)

    have_pexels = bool(env_str("PEXELS_API_KEY"))
    have_pixabay = bool(env_str("PIXABAY_API_KEY"))

    def _existing_videos() -> list[Path]:
        return sorted(
            [p for p in raw_dir.rglob("*")
             if p.is_file() and p.suffix.lower() in (".mp4", ".mov", ".mkv")]
        )

    if not (have_pexels or have_pixabay):
        log("D", "no PEXELS_API_KEY / PIXABAY_API_KEY in .env; falling back to local envato")
        return _envato_fallback_videos(root), None

    if manifest.is_file() and _existing_videos() and not force:
        clips = _existing_videos()
        log("D", f"reuse existing {len(clips)} clips in {raw_dir.relative_to(root)} "
                 f"(manifest present)")
        return clips, manifest

    sources_used = ",".join(s for s in [
        "pexels" if have_pexels else None,
        "pixabay" if have_pixabay else None,
    ] if s)

    py = sys.executable
    cmd = [
        py, "-m", "src.integrations.media_sources.selector",
        "--topic", slug,
        "--source", sources_used,
        "--max-per-segment", str(max_per_segment),
        "--max-queries", "8",
        "--per-page", "8",
    ]
    if force:
        cmd.append("--force")
    log("D", "selector " + " ".join(cmd[3:]))
    r = subprocess.run(cmd, cwd=str(root))
    if r.returncode != 0:
        log("D", f"selector exit={r.returncode}; using whatever already downloaded + envato fallback")

    clips = _existing_videos()
    if clips:
        log("D", f"got {len(clips)} stock video clips in {raw_dir.relative_to(root)}")
        return clips, manifest if manifest.is_file() else None

    log("D", "no stock clips downloaded; falling back to local envato")
    return _envato_fallback_videos(root), manifest if manifest.is_file() else None


ENVATO_VIDEO_DIRS = [
    "assets/envato/video",
    "assets/envato/videos",
    "assets/envato/backgrounds",
    "assets/envato/stock",
    "assets/envato",
]
ENVATO_PREFER_TOKENS = (
    "ai", "technology", "tech", "city", "people", "business", "office",
    "computer", "future", "data", "documentary", "economy", "work", "education",
)
VIDEO_EXTS = (".mp4", ".mov", ".mkv")


def _envato_fallback_videos(root: Path) -> list[Path]:
    found: list[Path] = []
    seen: set[Path] = set()
    for rel in ENVATO_VIDEO_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and p not in seen:
                found.append(p)
                seen.add(p)
    if not found:
        return []
    preferred = [p for p in found if any(t in p.name.lower() for t in ENVATO_PREFER_TOKENS)]
    if preferred:
        log("D", f"envato fallback: {len(preferred)} preferred / {len(found)} total")
        return preferred
    log("D", f"envato fallback: {len(found)} total (no keyword match, using all)")
    return found


# ============================================================================
# Step E: auto edit -> 1920x1080 30fps silent video
# ============================================================================

def step_edit(
    *, slug: str, voice_wav: Path | None, clips: list[Path],
    target_minutes: int, root: Path, force: bool,
) -> Path | None:
    section("E", "auto edit (1920x1080 30fps)")
    video_dir = root / "topics" / slug / "video"
    seg_dir = video_dir / "_segments"
    silent_path = video_dir / "silent_video.mp4"
    seg_dir.mkdir(parents=True, exist_ok=True)

    if silent_path.is_file() and not force:
        info = ffprobe_summary(silent_path)
        log("E", f"reuse existing silent_video.mp4  dur={fmt_dur(info['duration_sec'])}  "
                 f"{info['width']}x{info['height']}@{info['fps']:.2f}fps")
        return silent_path

    if not clips:
        log("E", "no video clips at all (stock + envato both empty); SKIP edit")
        return None

    target_dur = ffprobe_duration(voice_wav) if (voice_wav and voice_wav.is_file()) else 0.0
    if target_dur < 1.0:
        target_dur = float(target_minutes) * 60.0
        log("E", f"voice missing -> using --minutes -> target_dur={target_dur:.1f}s")
    else:
        log("E", f"target_dur from voice.wav = {fmt_dur(target_dur)} ({target_dur:.2f}s)")

    rng = random.Random(42)

    src_durs: dict[Path, float] = {}
    usable: list[Path] = []
    for p in clips:
        d = ffprobe_duration(p)
        if d > 1.0:
            src_durs[p] = d
            usable.append(p)
    if not usable:
        log("E", "all clips have <1s duration or unreadable; SKIP edit")
        return None
    log("E", f"usable clips: {len(usable)}")

    plan: list[tuple[Path, float, float]] = []
    elapsed = 0.0
    last_idx = -1
    while elapsed < target_dur:
        seg_dur = rng.uniform(5.0, 8.0)
        seg_dur = min(seg_dur, target_dur - elapsed)
        if seg_dur < 1.0:
            break
        if len(usable) > 1:
            choices = [i for i in range(len(usable)) if i != last_idx]
        else:
            choices = [0]
        idx = rng.choice(choices)
        last_idx = idx
        src = usable[idx]
        s_dur = src_durs[src]
        offset = rng.uniform(0.0, max(0.0, s_dur - seg_dur)) if s_dur > seg_dur + 0.5 else 0.0
        plan.append((src, offset, seg_dur))
        elapsed += seg_dur

    log("E", f"plan: {len(plan)} segments, total {fmt_dur(elapsed)} ({elapsed:.2f}s)")

    archive_one(silent_path, root)
    for old in seg_dir.glob("seg_*.mp4"):
        try: old.unlink()
        except OSError: pass

    seg_paths: list[Path] = []
    vf = (
        f"scale={TARGET_W}:{TARGET_H}:force_original_aspect_ratio=increase,"
        f"crop={TARGET_W}:{TARGET_H},fps={TARGET_FPS},setsar=1,format=yuv420p"
    )
    t0 = time.time()
    for i, (src, offset, dur) in enumerate(plan, start=1):
        out = seg_dir / f"seg_{i:04d}.mp4"
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-ss", f"{offset:.3f}",
            "-i", str(src),
            "-t", f"{dur:.3f}",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
            "-pix_fmt", "yuv420p",
            "-r", str(TARGET_FPS),
            "-an",
            "-movflags", "+faststart",
            str(out),
        ]
        ok, _ = run_ffmpeg(cmd, "E", f"seg {i:04d}")
        if not ok or not out.is_file():
            log("E", f"  seg {i:04d} skip (render failed)")
            continue
        seg_paths.append(out)
        if i % 10 == 0 or i == len(plan):
            log("E", f"  rendered {i}/{len(plan)} ({time.time() - t0:.1f}s elapsed)")

    if not seg_paths:
        log("E", "no segments rendered; SKIP edit")
        return None

    list_file = seg_dir / "_concat.txt"
    write_utf8_text(
        list_file,
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in seg_paths) + "\n",
    )
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(silent_path),
    ]
    ok, _ = run_ffmpeg(cmd, "E", "concat")
    if not ok:
        log("E", "concat failed; SKIP edit")
        return None
    try: list_file.unlink()
    except OSError: pass

    info = ffprobe_summary(silent_path)
    log("E", f"silent_video.mp4  dur={fmt_dur(info['duration_sec'])}  "
             f"{info['width']}x{info['height']}@{info['fps']:.2f}fps  "
             f"codec={info['video_codec']}")
    return silent_path


# ============================================================================
# Step F: background music + final mux
# ============================================================================

BGM_SEARCH_DIRS = [
    "assets/envato/music",
    "assets/envato/audio",
    "assets/envato",
]
BGM_PREFER_TOKENS = (
    "documentary", "ambient", "technology", "cinematic",
    "corporate", "inspiring", "serious", "thoughtful",
)
BGM_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".flac")


def _find_bgm(root: Path) -> Path | None:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for rel in BGM_SEARCH_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in BGM_EXTS and p not in seen:
                candidates.append(p)
                seen.add(p)
    if not candidates:
        return None
    preferred = [p for p in candidates if any(t in p.name.lower() for t in BGM_PREFER_TOKENS)]
    pool = preferred or candidates
    rng = random.Random(7)
    return rng.choice(pool)


def step_bgm(
    *, slug: str, silent_video: Path, voice_wav: Path | None, root: Path, force: bool,
) -> Path | None:
    section("F", "background music + final mux")
    out_dir = root / "topics" / slug / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / "final_video.mp4"

    if final_path.is_file() and not force:
        info = ffprobe_summary(final_path)
        log("F", f"reuse existing final_video.mp4  dur={fmt_dur(info['duration_sec'])}  "
                 f"{info['width']}x{info['height']}@{info['fps']:.2f}  "
                 f"a={info['audio_codec'] or 'none'}")
        return final_path

    if not silent_video.is_file():
        log("F", "missing silent_video.mp4; SKIP")
        return None

    archive_one(final_path, root)

    if not (voice_wav and voice_wav.is_file()):
        log("F", "no voice.wav; muxing silent video with no audio")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(silent_video),
            "-c", "copy",
            "-movflags", "+faststart",
            str(final_path),
        ]
        run_ffmpeg(cmd, "F", "copy silent")
        return final_path if final_path.is_file() else None

    bgm = _find_bgm(root)
    if bgm is None:
        log("F", "no BGM found in envato; using voice only")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(silent_video),
            "-i", str(voice_wav),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(final_path),
        ]
        run_ffmpeg(cmd, "F", "mux voice-only")
    else:
        try:
            rel_bgm = bgm.relative_to(root)
        except ValueError:
            rel_bgm = bgm
        bgm_vol = 0.10
        log("F", f"bgm = {rel_bgm}  voice_vol=1.00  bgm_vol={bgm_vol:.2f}")
        filt = (
            "[2:a]aloop=loop=-1:size=2147483647,"
            f"volume={bgm_vol:.3f},aresample=48000[bgm];"
            "[1:a]aresample=48000[v];"
            "[v][bgm]amix=inputs=2:duration=first:normalize=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(silent_video),
            "-i", str(voice_wav),
            "-i", str(bgm),
            "-filter_complex", filt,
            "-map", "0:v",
            "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-movflags", "+faststart",
            str(final_path),
        ]
        ok, _ = run_ffmpeg(cmd, "F", "mux voice+bgm")
        if not ok:
            log("F", "voice+bgm mux failed; falling back to voice-only")
            cmd2 = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(silent_video),
                "-i", str(voice_wav),
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", "-movflags", "+faststart",
                str(final_path),
            ]
            run_ffmpeg(cmd2, "F", "mux voice-only fallback")

    if final_path.is_file():
        info = ffprobe_summary(final_path)
        log("F", f"final_video.mp4  dur={fmt_dur(info['duration_sec'])}  "
                 f"video={info['video_codec']} {info['width']}x{info['height']}@"
                 f"{info['fps']:.2f}  audio={info['audio_codec']} "
                 f"{info['audio_sample_rate']}Hz x{info['audio_channels']}")
        return final_path
    return None


# ============================================================================
# Step G: subtitles (CN-hardened: SRT -> ASS -> ass= filter w/ fontsdir)
# ============================================================================

# (font_path, ass_fontname). Order = priority.
CN_FONT_CANDIDATES: list[tuple[str, str]] = [
    ("/System/Library/Fonts/PingFang.ttc", "PingFang SC"),
    ("/System/Library/Fonts/STHeiti Medium.ttc", "Heiti SC"),
    ("/System/Library/Fonts/STHeiti Light.ttc", "Heiti SC"),
    ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", "Arial Unicode MS"),
    ("/System/Library/Fonts/Supplemental/Songti.ttc", "Songti SC"),
    ("/Library/Fonts/PingFang.ttc", "PingFang SC"),
    (str(Path.home() / "Library/Fonts/PingFang.ttc"), "PingFang SC"),
]


def find_chinese_font() -> tuple[Path | None, str]:
    """
    Return (font_path, ass_fontname). If no candidate exists, returns
    (None, "PingFang SC") — caller should treat None as "no fontsdir;
    rely on system fontconfig/CoreText for whatever it can find".
    """
    for path_s, name in CN_FONT_CANDIDATES:
        p = Path(path_s).expanduser()
        if p.is_file():
            return p, name
    return None, "PingFang SC"


_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text_for_subtitle(text: str) -> str:
    """
    Strip BOM, normalize line endings, drop illegal control chars, normalize NFC,
    keep CJK punctuation untouched, collapse runs of whitespace.
    """
    if not text:
        return ""
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ----------------------------------------------------------------------------
# whisper.cpp detection
# ----------------------------------------------------------------------------

WHISPER_MODEL_CANDIDATES = [
    "~/whisper.cpp/models/ggml-medium.bin",
    "~/whisper.cpp/models/ggml-small.bin",
    "~/whisper.cpp/models/ggml-base.bin",
    "~/.cache/whisper.cpp/ggml-medium.bin",
    "~/.cache/whisper.cpp/ggml-small.bin",
    "~/.cache/whisper.cpp/ggml-base.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-medium.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-small.bin",
    "/opt/homebrew/share/whisper.cpp/ggml-base.bin",
]


def _find_whisper_setup() -> tuple[Path, Path] | None:
    cli = shutil.which("whisper-cli") or shutil.which("whisper-cpp") or shutil.which("whisper")
    if not cli:
        return None
    cli_path = Path(cli)
    env_model = env_str("WHISPER_MODEL")
    if env_model:
        mp = Path(env_model).expanduser()
        if mp.is_file():
            return cli_path, mp
    for s in WHISPER_MODEL_CANDIDATES:
        mp = Path(s).expanduser()
        if mp.is_file():
            return cli_path, mp
    return None


# ----------------------------------------------------------------------------
# cue generation
# ----------------------------------------------------------------------------

Cue = tuple[float, float, str]  # (start_sec, end_sec, text)


def naive_subtitles(text: str, total_dur: float, max_chars: int = 22) -> list[Cue]:
    text = clean_text_for_subtitle(text)
    text = re.sub(r"^\s*《.*?》\s*\n+", "", text)
    raw_sentences = re.split(r"(?<=[。！？!?\.])\s*", text)
    sentences = [s.strip() for s in raw_sentences if s.strip()]

    segments: list[str] = []
    for s in sentences:
        if len(s) <= max_chars:
            segments.append(s)
            continue
        parts = re.split(r"(?<=[，、,;；])", s)
        cur = ""
        for p in parts:
            if not p.strip():
                continue
            if len(cur) + len(p) > max_chars and cur:
                segments.append(cur)
                cur = p
            else:
                cur += p
        if cur:
            segments.append(cur)

    segments = [s for s in (s.strip() for s in segments) if s]
    if not segments:
        return []
    total_chars = sum(len(s) for s in segments)
    cues: list[Cue] = []
    t = 0.0
    for s in segments:
        dur = total_dur * len(s) / total_chars if total_chars else 0.0
        end = min(t + dur, total_dur)
        cues.append((t, end, s))
        t = end
    if cues:
        last = cues[-1]
        cues[-1] = (last[0], total_dur, last[2])
    return cues


_SRT_TS = r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})"
_SRT_LINE_RE = re.compile(
    rf"\d+\s*\n\s*({_SRT_TS})\s*-->\s*({_SRT_TS})\s*\n(.*?)(?=\n\s*\n|\Z)",
    re.DOTALL,
)


def _parse_srt_secs(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_srt_to_cues(srt_text: str) -> list[Cue]:
    cues: list[Cue] = []
    for match in _SRT_LINE_RE.finditer(srt_text):
        h1, m1, s1, ms1 = match.group(2), match.group(3), match.group(4), match.group(5)
        h2, m2, s2, ms2 = match.group(7), match.group(8), match.group(9), match.group(10)
        start = _parse_srt_secs(h1, m1, s1, ms1)
        end = _parse_srt_secs(h2, m2, s2, ms2)
        text = clean_text_for_subtitle(match.group(11))
        text = re.sub(r"\s*\n\s*", " ", text).strip()
        if text:
            cues.append((start, end, text))
    return cues


# ----------------------------------------------------------------------------
# SRT / ASS writers
# ----------------------------------------------------------------------------

def write_srt(cues: list[Cue], path: Path) -> None:
    out_lines: list[str] = []
    for i, (start, end, text) in enumerate(cues, start=1):
        text = clean_text_for_subtitle(text)
        out_lines.append(str(i))
        out_lines.append(f"{fmt_ts_srt(start)} --> {fmt_ts_srt(end)}")
        out_lines.append(text)
        out_lines.append("")
    write_utf8_text(path, "\n".join(out_lines))


_ASS_HEADER_TPL = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},46,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,0,0,0,0,100,100,0,0,1,3,0,2,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ass_escape_text(t: str) -> str:
    t = t.replace("\\", "\\\\")
    t = t.replace("{", "\\{").replace("}", "\\}")
    t = t.replace("\n", "\\N")
    return t


def convert_srt_to_ass(srt_path: Path, ass_path: Path, font_name: str) -> int:
    """
    Read UTF-8 SRT, emit UTF-8 ASS w/ embedded font_name in Default style.
    Returns number of dialogue lines written.
    """
    srt_text = srt_path.read_text(encoding="utf-8")
    if srt_text.startswith("\ufeff"):
        srt_text = srt_text.lstrip("\ufeff")
    cues = parse_srt_to_cues(srt_text)
    body = [_ASS_HEADER_TPL.format(font_name=font_name)]
    for start, end, text in cues:
        body.append(
            f"Dialogue: 0,{fmt_ts_ass(start)},{fmt_ts_ass(end)},Default,,0,0,0,,{_ass_escape_text(text)}"
        )
    write_utf8_text(ass_path, "\n".join(body) + "\n")
    return len(cues)


# ----------------------------------------------------------------------------
# whisper srt
# ----------------------------------------------------------------------------

def whisper_srt(
    *, voice_wav: Path, out_srt: Path, cli: Path, model: Path
) -> bool:
    work = out_srt.parent / "_whisper_work"
    work.mkdir(parents=True, exist_ok=True)
    out_prefix = work / "voice"
    cmd = [
        str(cli),
        "-m", str(model),
        "-f", str(voice_wav),
        "-l", "zh",
        "-osrt",
        "-of", str(out_prefix),
        "-t", "4",
    ]
    log("G", f"whisper-cli model={model.name}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    produced = Path(str(out_prefix) + ".srt")
    if r.returncode != 0 or not produced.is_file():
        log("G", f"whisper-cli failed: {(r.stderr or '')[:300]}")
        return False
    raw = produced.read_text(encoding="utf-8", errors="replace")
    cues = parse_srt_to_cues(raw)
    if not cues:
        log("G", "whisper-cli produced empty cue list; falling back to naive")
        return False
    write_srt(cues, out_srt)
    try: shutil.rmtree(work)
    except OSError: pass
    return True


# ----------------------------------------------------------------------------
# fontsdir setup + filter path escape
# ----------------------------------------------------------------------------

def _build_fontsdir(slug: str, root: Path, font_path: Path | None) -> Path | None:
    """Create topics/<slug>/subtitles/_fonts and symlink the chosen CN font into it."""
    if font_path is None:
        return None
    fonts_dir = root / "topics" / slug / "subtitles" / "_fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    for old in fonts_dir.iterdir():
        try: old.unlink()
        except OSError:
            try: shutil.rmtree(old)
            except OSError: pass
    target = fonts_dir / font_path.name
    try:
        os.symlink(str(font_path), str(target))
    except OSError:
        try:
            shutil.copy2(str(font_path), str(target))
        except OSError as e:
            log("G", f"fontsdir setup failed ({e}); will try without fontsdir")
            return None
    return fonts_dir


def _filter_path_escape(path: Path) -> str:
    """
    Escape a filesystem path for use inside an ffmpeg filter graph value
    (e.g. -vf "ass=PATH:..."). Backslash, colon, single quote, comma, brackets.
    """
    s = str(path)
    s = s.replace("\\", "\\\\")
    s = s.replace(":", "\\:")
    s = s.replace("'", "\\'")
    s = s.replace(",", "\\,")
    s = s.replace("[", "\\[").replace("]", "\\]")
    return s


# ----------------------------------------------------------------------------
# burn-in: ass first, srt fallback
# ----------------------------------------------------------------------------

def burn_ass_subtitles(
    *,
    in_video: Path,
    ass_path: Path,
    fonts_dir: Path | None,
    out_video: Path,
) -> tuple[bool, str]:
    vf_parts = [f"ass={_filter_path_escape(ass_path)}"]
    if fonts_dir is not None and fonts_dir.is_dir():
        vf_parts[-1] += f":fontsdir={_filter_path_escape(fonts_dir)}"
    vf = ":".join(vf_parts) if len(vf_parts) > 1 else vf_parts[0]
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-i", str(in_video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ]
    return run_ffmpeg(cmd, "G", "burn ass")


def _burn_srt_fallback(
    *,
    in_video: Path,
    srt_path: Path,
    font_name: str,
    fonts_dir: Path | None,
    out_video: Path,
) -> tuple[bool, str]:
    style = (
        f"FontName={font_name},"
        "FontSize=46,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,"
        "BackColour=&H64000000,"
        "BorderStyle=1,"
        "Outline=3,"
        "Shadow=0,"
        "Alignment=2,"
        "MarginV=60,"
        "Bold=1"
    )
    parts = [
        f"subtitles={_filter_path_escape(srt_path)}",
        "charenc=UTF-8",
        f"force_style='{style}'",
    ]
    if fonts_dir is not None and fonts_dir.is_dir():
        parts.insert(1, f"fontsdir={_filter_path_escape(fonts_dir)}")
    vf = ":".join(parts)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-i", str(in_video),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out_video),
    ]
    return run_ffmpeg(cmd, "G", "burn srt")


def _scan_libass_warnings(stderr: str) -> tuple[int, list[str]]:
    """Count missing-glyph / font warnings emitted by libass; return (count, samples)."""
    if not stderr:
        return 0, []
    patterns = [
        r"Glyph 0x[0-9A-Fa-f]+ not found",
        r"Could not find a glyph",
        r"fontselect: failed to find any fallback",
        r"No usable fontconfig configuration file found",
        r"libass: failed to open font",
    ]
    samples: list[str] = []
    count = 0
    for line in stderr.splitlines():
        for pat in patterns:
            if re.search(pat, line):
                count += 1
                if len(samples) < 5:
                    samples.append(line.strip())
                break
    return count, samples


# ----------------------------------------------------------------------------
# step orchestrator
# ----------------------------------------------------------------------------

def step_subtitles(
    *,
    slug: str,
    voice_wav: Path | None,
    narration_path: Path,
    final_video: Path | None,
    root: Path,
    force: bool,
) -> tuple[Path | None, Path | None]:
    section("G", "subtitles (SRT -> ASS -> ass= filter w/ fontsdir)")
    sub_dir = root / "topics" / slug / "subtitles"
    sub_dir.mkdir(parents=True, exist_ok=True)
    srt_path = sub_dir / "subtitles.srt"
    ass_path = sub_dir / "subtitles.ass"
    out_dir = root / "topics" / slug / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_burned = out_dir / "final_video_with_subtitles.mp4"

    if (
        out_burned.is_file()
        and srt_path.is_file()
        and ass_path.is_file()
        and not force
    ):
        info = ffprobe_summary(out_burned)
        log("G", f"reuse existing  {out_burned.relative_to(root)}  "
                 f"dur={fmt_dur(info['duration_sec'])}")
        return srt_path, out_burned

    audio_dur = ffprobe_duration(voice_wav) if (voice_wav and voice_wav.is_file()) else 0.0
    if audio_dur <= 0:
        if final_video and final_video.is_file():
            audio_dur = ffprobe_duration(final_video)

    used = "naive"
    if voice_wav and voice_wav.is_file():
        ws = _find_whisper_setup()
        if ws is not None:
            cli, model = ws
            if whisper_srt(voice_wav=voice_wav, out_srt=srt_path, cli=cli, model=model):
                used = f"whisper:{model.name}"

    if used == "naive":
        if not narration_path.is_file():
            log("G", "narration_script.txt missing and whisper unavailable; SKIP subs")
            return None, None
        if audio_dur <= 0:
            log("G", "no audio duration available; SKIP subs")
            return None, None
        text = narration_path.read_text(encoding="utf-8")
        cues = naive_subtitles(text, audio_dur, max_chars=22)
        if not cues:
            log("G", "naive_subtitles produced no cues; SKIP subs")
            return None, None
        write_srt(cues, srt_path)

    cue_count = sum(
        1 for line in srt_path.read_text(encoding="utf-8").splitlines()
        if line.strip().isdigit()
    )
    log("G", f"wrote {srt_path.relative_to(root)}  cues={cue_count}  source={used}")

    font_path, font_name = find_chinese_font()
    if font_path is None:
        log("G", "WARN  no CJK font file found at any expected path; "
                 "relying on libass system fallback (may render boxes)")
    else:
        try:
            rel_fp = font_path.relative_to(Path("/"))
        except ValueError:
            rel_fp = font_path
        log("G", f"chosen CJK font: /{rel_fp}  ass_fontname={font_name!r}")

    fonts_dir = _build_fontsdir(slug, root, font_path)
    if fonts_dir:
        try:
            log("G", f"fontsdir = {fonts_dir.relative_to(root)}/  "
                     f"contents={[p.name for p in fonts_dir.iterdir()]}")
        except ValueError:
            log("G", f"fontsdir = {fonts_dir}")

    n_dlg = convert_srt_to_ass(srt_path, ass_path, font_name)
    log("G", f"wrote {ass_path.relative_to(root)}  dialogues={n_dlg}  fontname={font_name!r}")

    if not (final_video and final_video.is_file()):
        log("G", "no final_video.mp4; SKIP burn-in (srt+ass sidecars saved)")
        return srt_path, None

    archive_one(out_burned, root)

    log("G", "burn pass 1: ass= filter ...")
    ok, stderr_ass = burn_ass_subtitles(
        in_video=final_video, ass_path=ass_path,
        fonts_dir=fonts_dir, out_video=out_burned,
    )
    pass_used = "ass"
    if not ok or not out_burned.is_file():
        log("G", "ass= filter failed; trying subtitles= filter w/ charenc=UTF-8 ...")
        ok2, stderr_srt = _burn_srt_fallback(
            in_video=final_video, srt_path=srt_path,
            font_name=font_name, fonts_dir=fonts_dir, out_video=out_burned,
        )
        if not ok2 or not out_burned.is_file():
            log("G", "BOTH burn-ins failed; final_video.mp4 is preserved, "
                     "no final_video_with_subtitles.mp4 produced")
            return srt_path, None
        pass_used = "subtitles"
        stderr_used = stderr_srt
    else:
        stderr_used = stderr_ass

    warn_count, warn_samples = _scan_libass_warnings(stderr_used)
    if warn_count:
        log("G", f"libass emitted {warn_count} font/glyph warning(s):")
        for s in warn_samples:
            log("G", f"  WARN: {s}")

    info = ffprobe_summary(out_burned)
    log("G", f"final_video_with_subtitles.mp4  dur={fmt_dur(info['duration_sec'])}  "
             f"{info['width']}x{info['height']}@{info['fps']:.2f}  "
             f"size={info['size_bytes'] / (1024 * 1024):.1f} MiB  "
             f"burn_pass={pass_used}")
    return srt_path, out_burned


# ============================================================================
# Step I: verify
# ============================================================================

def verify_final(*, slug: str, root: Path) -> int:
    section("I", "ffprobe verification")
    out_dir = root / "topics" / slug / "output"
    targets = [
        out_dir / "final_video.mp4",
        out_dir / "final_video_with_subtitles.mp4",
    ]
    rc = 0
    for t in targets:
        if not t.is_file():
            log("I", f"MISSING  {t.relative_to(root)}")
            rc = 1
            continue
        info = ffprobe_summary(t)
        if "error" in info:
            log("I", f"ERROR    {t.relative_to(root)}  {info['error']}")
            rc = 1
            continue
        ok = (
            info["has_video"]
            and info["width"] == TARGET_W
            and info["height"] == TARGET_H
            and abs(info["fps"] - TARGET_FPS) < 0.5
        )
        ok_a = info["has_audio"]
        flag = "OK" if (ok and ok_a) else "WARN"
        log(
            "I",
            f"{flag}  {t.relative_to(root)}  "
            f"dur={fmt_dur(info['duration_sec'])}  "
            f"{info['width']}x{info['height']}@{info['fps']:.2f}  "
            f"v={info['video_codec']}  a={info['audio_codec'] or 'none'}  "
            f"size={info['size_bytes'] / (1024 * 1024):.1f} MiB",
        )
        if not (ok and ok_a):
            rc = 1
    return rc


# ============================================================================
# main
# ============================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="StateVerge one-click Chinese auto-video pipeline (CN-subs hardened)."
    )
    ap.add_argument("--topic", required=True, help="Chinese topic phrase, used in LLM prompt.")
    ap.add_argument("--slug", required=True, help="Topic slug under topics/<slug>/")
    ap.add_argument("--minutes", type=int, default=10, help="Target narration length (default 10).")
    ap.add_argument("--force-script", action="store_true")
    ap.add_argument("--force-tts", action="store_true")
    ap.add_argument("--force-media", action="store_true")
    ap.add_argument("--force-edit", action="store_true",
                    help="Re-render silent_video + final_video.")
    ap.add_argument("--force-subs", action="store_true",
                    help="Regenerate srt/ass + re-burn final_video_with_subtitles.")
    ap.add_argument("--force-all", action="store_true")
    ap.add_argument("--max-per-segment", type=int, default=25)
    ns = ap.parse_args(argv)

    fa = bool(ns.force_all)
    f_script = ns.force_script or fa
    f_tts = ns.force_tts or fa
    f_media = ns.force_media or fa
    f_edit = ns.force_edit or fa
    f_subs = ns.force_subs or fa

    root = repo_root()
    load_dotenv(root)

    topic: str = ns.topic
    slug: str = ns.slug
    minutes: int = int(ns.minutes)

    print(f"\n{LOG_PREFIX} ╔════════════════════════════════════════════════════╗")
    print(f"{LOG_PREFIX} ║  StateVerge one-click pipeline (CN-subs)           ║")
    print(f"{LOG_PREFIX} ║  topic   : {topic[:38]:<38}  ║")
    print(f"{LOG_PREFIX} ║  slug    : {slug[:38]:<38}  ║")
    print(f"{LOG_PREFIX} ║  minutes : {minutes:<38}  ║")
    print(f"{LOG_PREFIX} ║  root    : {str(root)[:38]:<38}  ║")
    print(f"{LOG_PREFIX} ╚════════════════════════════════════════════════════╝\n", flush=True)

    narration_path = step_narration(slug=slug, topic=topic, minutes=minutes, root=root, force=f_script)
    narration_text = narration_path.read_text(encoding="utf-8") if narration_path.is_file() else ""

    step_youtube_meta(slug=slug, topic=topic, narration_text=narration_text, root=root, force=f_script)

    voice_wav = step_tts(slug=slug, root=root, force=f_tts)

    clips, _manifest = step_media(
        slug=slug, root=root, force=f_media, max_per_segment=int(ns.max_per_segment)
    )

    silent_video = step_edit(
        slug=slug, voice_wav=voice_wav, clips=clips,
        target_minutes=minutes, root=root, force=f_edit,
    )

    final_video: Path | None = None
    if silent_video and silent_video.is_file():
        final_video = step_bgm(
            slug=slug, silent_video=silent_video, voice_wav=voice_wav,
            root=root, force=f_edit,
        )
    else:
        log("F", "no silent video; SKIP bgm/mux")

    step_subtitles(
        slug=slug,
        voice_wav=voice_wav,
        narration_path=narration_path,
        final_video=final_video,
        root=root,
        force=f_subs,
    )

    rc = verify_final(slug=slug, root=root)

    print(f"\n{LOG_PREFIX} ────────────────────────────────────────────────────")
    print(f"{LOG_PREFIX} pipeline DONE for slug={slug}  exit={rc}")
    print(f"{LOG_PREFIX} ────────────────────────────────────────────────────\n", flush=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
