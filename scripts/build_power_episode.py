#!/usr/bin/env python3
"""
Build a ~10-minute "power" documentary video for an arbitrary ``--topic`` slug.

Outputs (under ``topics/<topic>/``)::
  narration_script.txt
  voice.wav
  visuals/*.mp4
  output/final.mp4

Flow:
  1) OpenAI- or OpenAI-compatible API (e.g. DeepSeek via OPENAI_BASE_URL) -> narration
  2) ElevenLabs (needs ``requests``) or macOS ``say`` only with ``--allow-local-tts`` else silence -> voice.wav @ 48 kHz
  3) Stock: Pexels -> Pixabay -> local assets, then dummy (USE_DVIDS is never used)
  4) 6–10s H.264 TS segments -> concat silent MP4 -> mux with voice only
  5) No background music; audio = speech only

Usage::
  python scripts/build_power_episode.py --topic pentagon
  python scripts/build_power_episode.py --topic pentagon --topic-title "The Pentagon and U.S. Defense"
  python scripts/build_power_episode.py --topic pentagon --topic-title "五角大楼" --duration-seconds 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from urllib.parse import quote_plus
from typing import Any

# ---------------------------------------------------------------------------
# project layout
# ---------------------------------------------------------------------------

ROOT = Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

W, H, FPS, AR = 1920, 1080, 30, 48000
SEG_MIN, SEG_MAX = 6.0, 10.0
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
WORDS_PER_MIN = 180
TARGET_MINUTES = 10
TARGET_WORDS = WORDS_PER_MIN * TARGET_MINUTES
MIN_WORDS_FLOOR = int(TARGET_WORDS * 0.9)
TTS_CHUNK_CHARS = 2400
MAX_SEGMENTS = 800
MAX_CYCLES = 200

LOG_PREFIX = "build_power_episode"

# Fixed six-part structure
POWER_SECTIONS: tuple[str, ...] = (
    "Hook",
    "Origin",
    "Power Structure",
    "Turning Point",
    "Present Day",
    "Conclusion",
)


# ---------------------------------------------------------------------------
# logging & subprocess
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[{LOG_PREFIX}] {msg}", flush=True)


def run_cap(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def run_stream(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, **kw)


# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

def load_env() -> dict[str, str]:
    env = dict(os.environ)
    p = ROOT / ".env"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, _, v = s.partition("=")
        key = k.strip()
        if key and key not in os.environ:  # allow OS env to win
            env[key] = v.strip().strip("'\"")
    return env


# ---------------------------------------------------------------------------
# ffprobe
# ---------------------------------------------------------------------------

def probe_duration(p: Path) -> float:
    r = run_cap(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(p),
        ],
    )
    s = (r.stdout or "").strip()
    if r.returncode != 0 or not s:
        return -1.0
    try:
        return float(s)
    except ValueError:
        return -1.0


def has_stream(p: Path, kind: str) -> bool:
    r = run_cap(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            f"{kind[0]}:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "default=nw=1:nk=1",
            str(p),
        ],
    )
    return r.returncode == 0 and (r.stdout or "").strip() == kind


def probe_final_streams(p: Path) -> tuple[bool, bool, float]:
    """Return (has_video, has_audio, duration_sec). Missing file -> all false / -1."""
    if not p.is_file():
        return False, False, -1.0
    hv = has_stream(p, "video")
    ha = has_stream(p, "audio")
    d = probe_duration(p)
    return hv, ha, d


# ---------------------------------------------------------------------------
# slug + topic title
# ---------------------------------------------------------------------------

def sanitize_topic(raw: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_\-]+", "-", (raw or "").strip().lower())
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "topic"


def default_topic_title(slug: str) -> str:
    s = slug.replace("_", " ").replace("-", " ")
    if not s:
        return "The Topic"
    return s[:1].upper() + s[1:]


# ---------------------------------------------------------------------------
# step 1 — script
# ---------------------------------------------------------------------------

def _word_count(text: str) -> int:
    return sum(1 for w in re.split(r"\s+", text.strip()) if w)


def _cjk_char_count(text: str) -> int:
    """Count CJK unified ideographs (rough 汉字 count for length checks)."""
    return len(re.findall(r"[\u4e00-\u9fff]", text or ""))


def _build_script_prompt(topic_title: str, sections: tuple[str, ...], min_per: int) -> str:
    section_lines = "\n".join(f"## {s}" for s in sections)
    return (
        f"Write a {TARGET_MINUTES}-minute narrated documentary script titled: "
        f'"{topic_title}".\n\n'
        f"Target {TARGET_WORDS - 200} to {TARGET_WORDS + 300} words TOTAL; do not undershoot.\n"
        f"The spoken pacing is roughly {WORDS_PER_MIN} words per minute for English narration.\n\n"
        "Required structure — use EXACTLY these markdown headings, in this order:\n"
        f"{section_lines}\n\n"
        "Content rules:\n"
        f"- Each section should be at least {min_per} words.\n"
        "- 3 to 5 narration-ready paragraphs per section.\n"
        "- No bullet or numbered lists; no stage directions in brackets; no 'Host:' labels.\n"
        "- Serious, fact-grounded, documentary style about power: institutions, history, and "
        "consequences. Tie every section to the main subject in the title.\n"
        "- 'Hook' opens with a strong, cinematic tease. 'Conclusion' closes with measured reflection.\n"
        f"- 'Present Day' covers current role and public perception of: {topic_title}.\n"
    )


def _http_chat(
    env: dict[str, str], system: str, user: str, *, temperature: float = 0.65
) -> str | None:
    """OpenAI- or OpenAI-compatible chat.completions. Supports DeepSeek via key / base URL."""
    d_key = (env.get("DEEPSEEK_API_KEY") or "").strip()
    o_key = (env.get("OPENAI_API_KEY") or "").strip()
    base = (env.get("OPENAI_BASE_URL") or "").strip().rstrip("/")
    model = (env.get("OPENAI_MODEL") or "gpt-4o").strip()

    if d_key and not o_key:
        b = (base or "https://api.deepseek.com").rstrip("/")
        if not b.endswith("/v1"):
            b = f"{b}/v1"
        if not (env.get("OPENAI_MODEL") or "").strip():
            model = "deepseek-chat"
        api_key = d_key
    else:
        if not o_key:
            log("WARN: no OPENAI_API_KEY / DEEPSEEK_API_KEY — will use local fallback script")
            return None
        api_key = o_key
        b = (base or "https://api.openai.com").rstrip("/")
        if not b.endswith("/v1"):
            b = f"{b}/v1"
    url = f"{b}/chat/completions"
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
    }
    raw = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=raw,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=200) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.HTTPError, urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        log(f"WARN: LLM request failed: {e!r}")
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("message") or {}).get("content") or ""


def _expand_short_script(
    env: dict[str, str], short_text: str, topic_title: str, sections: tuple[str, ...]
) -> str | None:
    cur = _word_count(short_text)
    if cur >= MIN_WORDS_FLOOR:
        return None
    system = "You are a documentary script editor. Return only the full expanded script, no preface."
    user = (
        f"The following narration is too short ({cur} words) for a {TARGET_MINUTES}-minute read. "
        f"Expand it to at least {TARGET_WORDS} words, keeping the same `##` headings in order. "
        f"Topic: {topic_title!r}.\n\n{short_text}"
    )
    return _http_chat(env, system, user)


def _missing_section_stubs(sections: tuple[str, ...], text: str) -> str:
    t = text
    for s in sections:
        if f"## {s}".lower() not in t.lower():
            t += f"\n\n## {s}\n\n(Placeholder: expand this section manually for: {s}.)\n"
    return t


def _fallback_script(topic_title: str, sections: tuple[str, ...]) -> str:
    out: list[str] = []
    for s in sections:
        out.append(f"## {s}\n")
        for _k in range(3):
            out.append(
                f"This paragraph is part of {s} for the story of {topic_title}. "
                "In production, this block would be replaced by a fully researched, "
                "citation-backed narration once network LLM and stock APIs are available. "
                "The sentence rhythm is tuned for a calm documentary read.\n"
            )
        out.append("\n")
    return "\n".join(out).strip() + "\n"


def _build_short_cn_prompt(
    topic_title: str,
    sections: tuple[str, ...],
    *,
    duration_seconds: int,
    min_cjk: int,
    max_cjk: int,
) -> str:
    section_lines = "\n".join(f"## {s}" for s in sections)
    return (
        f"为主题「{topic_title}」写一段用于配音的中文纪录片旁白，总时长目标约 {duration_seconds} 秒。\n\n"
        f"硬性要求：全文汉字（中日韩统一表意文字）数量必须在 {min_cjk} 到 {max_cjk} 之间，"
        "宁可略少也不要超过上限；不要写英文正文（保留下方英文标题行即可）。\n\n"
        "结构：必须按顺序使用以下 Markdown 二级标题（标题行本身用英文，方便工程解析）：\n"
        f"{section_lines}\n\n"
        "每个标题下写一到两段中文旁白，语气冷静、信息密度高，适合严肃纪录片；不要列表、不要括号舞台指示、不要「主持人：」。\n"
        "内容须紧扣主题标题中的实体与制度/权力叙事，避免阴谋论口吻。\n"
    )


def _expand_short_cn(
    env: dict[str, str],
    text: str,
    topic_title: str,
    sections: tuple[str, ...],
    min_cjk: int,
    max_cjk: int,
) -> str | None:
    n = _cjk_char_count(text)
    if n >= min_cjk:
        return None
    system = "你是中文纪录片脚本编辑。只输出修订后的完整正文，不要前言或后记。"
    user = (
        f"下列旁白汉字仅约 {n} 个，不足 {min_cjk}。请在保留完全相同的六个「## …」英文标题行的前提下，"
        f"把各段中文扩写到全文共 {min_cjk}–{max_cjk} 个汉字；主题仍是「{topic_title}」。\n\n"
        f"{text}"
    )
    return _http_chat(env, system, user, temperature=0.55)


def _fallback_script_short_cn(topic_title: str, sections: tuple[str, ...]) -> str:
    """Deterministic ~180–220 汉字 fallback when LLM unavailable."""
    body = (
        f"## {sections[0]}\n"
        f"今夜镜头对准「{topic_title}」：地标之下，是一套把意志变成文件的权力机器。\n\n"
        f"## {sections[1]}\n"
        "历史把它推上舞台：预算、人事与议程在此交汇，制度起点清晰可见。\n\n"
        f"## {sections[2]}\n"
        "层级与流程把权力拆成动作与签字；所谓拍板，多是链条末端被看见的一下。\n\n"
        f"## {sections[3]}\n"
        "危机照亮结构里易弯的环节，也迫使规则修补；听证与报道把摩擦拉到日光下。\n\n"
        f"## {sections[4]}\n"
        "今日舆论与预算仍在同一张棋盘上牵制；工具在变，问责与透明度的追问仍在。\n\n"
        f"## {sections[5]}\n"
        "收束：看清权力不为猎奇，而为理解我们共同承担的风险、责任、边界与后果。\n"
    )
    return body.strip() + "\n"


def generate_script_short_cn(
    env: dict[str, str],
    topic_title: str,
    sections: tuple[str, ...],
    duration_seconds: int,
) -> str:
    """~180–220 汉字、约 ``duration_seconds`` 秒口播；六段结构不变。"""
    min_cjk, max_cjk = 180, 220
    system = "你是资深中文纪录片撰稿人，擅长高密度旁白与口语化书面语。"
    user = _build_short_cn_prompt(
        topic_title, sections, duration_seconds=duration_seconds, min_cjk=min_cjk, max_cjk=max_cjk
    )
    text = _http_chat(env, system, user, temperature=0.6)
    if not text or _cjk_char_count(text) < 80:
        log("WARN: short-CN LLM empty/too short; using local Chinese fallback")
        return _fallback_script_short_cn(topic_title, sections)
    log(f"step1: short-CN LLM first draft cjk_chars={_cjk_char_count(text)}")
    if _cjk_char_count(text) < min_cjk:
        exp = _expand_short_cn(env, text, topic_title, sections, min_cjk, max_cjk)
        if exp and _cjk_char_count(exp) > _cjk_char_count(text):
            text = exp
            log(f"step1: short-CN after expansion cjk_chars={_cjk_char_count(text)}")
    if _cjk_char_count(text) > max_cjk + 30:
        log(f"WARN: short-CN draft over {max_cjk} 汉字; keeping LLM output as-is (manual trim optional)")
    if _cjk_char_count(text) < 150:
        log("WARN: short-CN still thin; merging deterministic fallback tail")
        fb = _fallback_script_short_cn(topic_title, sections)
        text = (text.strip() + "\n\n" + fb).strip() + "\n"
    text = _missing_section_stubs(sections, text)
    return text.strip() + "\n"


def generate_script(
    env: dict[str, str],
    topic_title: str,
    sections: tuple[str, ...] = POWER_SECTIONS,
    *,
    duration_seconds: int | None = None,
) -> str:
    if duration_seconds is not None and duration_seconds > 0:
        return generate_script_short_cn(env, topic_title, sections, duration_seconds)
    min_per = max(200, TARGET_WORDS // (len(sections) + 2))
    system = "You are a lead writer for a serious documentary YouTube channel."
    user = _build_script_prompt(topic_title, sections, min_per)
    text = _http_chat(env, system, user)
    if not text or _word_count(text) < 200:
        log("WARN: LLM empty/short; using local fallback")
        return _fallback_script(topic_title, sections)
    log(f"step1: LLM first draft words={_word_count(text)}")
    if _word_count(text) < MIN_WORDS_FLOOR:
        exp = _expand_short_script(env, text, topic_title, sections)
        if exp and _word_count(exp) > _word_count(text):
            text = exp
            log(f"step1: after expansion words={_word_count(text)}")
    if _word_count(text) < MIN_WORDS_FLOOR - 200:
        log("WARN: still under target; padding with additional fallback paragraphs is skipped")
    text = _missing_section_stubs(sections, text)
    return text.strip() + "\n"


# ---------------------------------------------------------------------------
# step 2 — TTS
# ---------------------------------------------------------------------------

def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        joiner = "\n\n" if cur else ""
        if len(cur) + len(joiner) + len(para) <= max_chars:
            cur = cur + joiner + para
            continue
        if cur:
            chunks.append(cur)
        cur = ""
        if len(para) <= max_chars:
            cur = para
            continue
        buf = ""
        sents = re.split(r"(?<=[.!?])\s+", para)
        for s in sents:
            j = " " if buf else ""
            if len(buf) + len(j) + len(s) <= max_chars:
                buf = buf + j + s
            else:
                if buf:
                    chunks.append(buf)
                buf = s
        if buf:
            cur = buf
    if cur:
        chunks.append(cur)
    return chunks or [""]


def _strip_markdown(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if s.startswith("#"):
            continue
        s = re.sub(r"[*_`]+", "", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _ffmpeg_silence_wav(out: Path, sec: float) -> bool:
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r={AR}:cl=stereo",
            "-t",
            f"{max(1.0, sec):.1f}",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
    )
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 64


def _local_say_wav(out: Path, text: str) -> bool:
    """macOS `say` -> aiff, then to wav. Never raises."""
    out.parent.mkdir(parents=True, exist_ok=True)
    safe = (text or "")[:2000]
    try:
        safe = safe.encode("utf-8", errors="replace").decode("utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        log(f"WARN: say text normalize: {e!r}")
        safe = (text or "")[:500]
    aiff = out.with_suffix(".aiff")
    aiff.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = run_cap(["say", "-o", str(aiff), safe])
    except UnicodeEncodeError as e:
        log(f"WARN: say UnicodeEncodeError: {e!r}")
        return False
    except Exception as e:  # noqa: BLE001
        log(f"WARN: say subprocess failed: {e!r}")
        return False
    if r.returncode != 0 or not aiff.is_file() or aiff.stat().st_size <= 0:
        return False
    try:
        r2 = run_cap(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                str(aiff),
                "-ar",
                str(AR),
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(out),
            ],
        )
    except Exception as e:  # noqa: BLE001
        log(f"WARN: say->wav ffmpeg: {e!r}")
        aiff.unlink(missing_ok=True)
        return False
    aiff.unlink(missing_ok=True)
    return r2.returncode == 0 and out.is_file() and out.stat().st_size > 64


def _voice_wav_ok(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 64
    except OSError:
        return False


def _mask_voice_id(voice_id: str) -> str:
    """Log-safe: first 4 + last 4; IDs <= 8 chars fully masked."""
    v = (voice_id or "").strip()
    if not v:
        return ""
    if len(v) <= 8:
        return "****"
    return f"{v[:4]}…{v[-4:]}"


def _force_silence_voice_wav(out_wav: Path) -> None:
    """Last resort: always leave a readable WAV (never raises)."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    for dur in (600.0, 120.0, 60.0, 30.0):
        try:
            if _ffmpeg_silence_wav(out_wav, dur) and _voice_wav_ok(out_wav):
                log(f"WARN: voice.wav filled with {dur:.0f}s silence (last resort)")
                return
        except Exception as e:  # noqa: BLE001
            log(f"WARN: silence ffmpeg dur={dur}: {e!r}")
    try:
        out_wav.write_bytes(b"")
    except OSError:
        pass


def synthesize_voice(
    env: dict[str, str],
    script_text: str,
    out_wav: Path,
    work: Path,
    *,
    allow_local_tts: bool = False,
) -> None:
    """
    Build ``out_wav`` (48 kHz PCM). ElevenLabs (needs ``requests``) → optional macOS ``say``
    (only with ``--allow-local-tts``) → silence.

    If ``ELEVENLABS_*`` is set but ``requests`` is missing, logs an explicit install WARN and
    does **not** fall back to ``say`` unless ``allow_local_tts`` is true.

    Never raises; always ends with a usable ``voice.wav`` (may be silence).
    """
    spoken = _strip_markdown(script_text)
    api_key = (env.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (env.get("ELEVENLABS_VOICE_ID") or "").strip()
    model_id = (env.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5") or "eleven_turbo_v2_5").strip()
    masked_vid = _mask_voice_id(voice_id)
    tts_provider = "silence"

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)

    requests = None
    try:
        import requests as _rq

        requests = _rq
    except ImportError:
        if api_key and voice_id:
            log(
                "WARN: ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID are set, but the Python "
                "package `requests` is not installed — ElevenLabs HTTP cannot run. "
                "Install with: pip install requests   (or: uv pip install requests). "
                "To use macOS `say` instead, re-run with --allow-local-tts; otherwise narration "
                "will fall back to silence."
            )
        elif api_key or voice_id:
            log(
                "WARN: partial ElevenLabs configuration and `requests` is not installed; "
                "install requests or use --allow-local-tts for macOS say."
            )

    if requests is not None and api_key and voice_id:
        try:
            chunks = _split_into_chunks(spoken, TTS_CHUNK_CHARS) or [spoken]
            url = (
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
                "?output_format=mp3_44100_128"
            )
            chunks_dir = work / "tts_chunks"
            shutil.rmtree(chunks_dir, ignore_errors=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)
            chunk_paths: list[Path] = []
            for i, chunk_text in enumerate(chunks, start=1):
                out_mp3 = chunks_dir / f"chunk_{i:02d}.mp3"
                out_mp3.parent.mkdir(parents=True, exist_ok=True)
                ok_chunk = False
                for _attempt in range(2):
                    r = None
                    try:
                        r = requests.post(
                            url,
                            headers={
                                "xi-api-key": api_key,
                                "Content-Type": "application/json",
                                "Accept": "audio/mpeg",
                            },
                            json={
                                "text": chunk_text,
                                "model_id": model_id,
                                "voice_settings": {
                                    "stability": 0.5,
                                    "similarity_boost": 0.75,
                                },
                            },
                            timeout=300,
                        )
                    except UnicodeEncodeError as e:
                        log(f"WARN: ElevenLabs chunk {i} UnicodeEncodeError: {e!r}")
                        break
                    except requests.exceptions.RequestException as e:
                        log(f"WARN: ElevenLabs chunk {i} RequestException: {e!r}")
                        time.sleep(1.0 + _attempt)
                        continue
                    except Exception as e:  # noqa: BLE001
                        log(f"WARN: ElevenLabs chunk {i} error: {type(e).__name__}: {e!r}")
                        time.sleep(1.0)
                        continue
                    try:
                        if (
                            r is not None
                            and r.status_code == 200
                            and len(getattr(r, "content", b"") or b"") > 1024
                        ):
                            out_mp3.write_bytes(r.content)
                            chunk_paths.append(out_mp3)
                            ok_chunk = True
                            break
                    except UnicodeEncodeError as e:
                        log(f"WARN: ElevenLabs chunk {i} write UnicodeEncodeError: {e!r}")
                        break
                    except OSError as e:
                        log(f"WARN: ElevenLabs chunk {i} write OSError: {e!r}")
                        break
                    except Exception as e:  # noqa: BLE001
                        log(f"WARN: ElevenLabs chunk {i} write: {type(e).__name__}: {e!r}")
                        break
                    time.sleep(1.0)
                if not ok_chunk:
                    log(f"WARN: ElevenLabs chunk {i}/{len(chunks)} missing or invalid")

            if chunk_paths:
                voice_mp3 = work / "voice_merged.mp3"
                voice_mp3.parent.mkdir(parents=True, exist_ok=True)
                ins: list[str] = []
                for p in chunk_paths:
                    ins += ["-i", str(p)]
                n = len(chunk_paths)
                try:
                    r0 = run_cap(
                        [
                            "ffmpeg",
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-y",
                            *ins,
                            "-filter_complex",
                            f"concat=n={n}:v=0:a=1[a]",
                            "-map",
                            "[a]",
                            "-c:a",
                            "libmp3lame",
                            "-q:a",
                            "2",
                            str(voice_mp3),
                        ],
                    )
                    if r0.returncode == 0 and voice_mp3.is_file():
                        r1 = run_cap(
                            [
                                "ffmpeg",
                                "-hide_banner",
                                "-y",
                                "-i",
                                str(voice_mp3),
                                "-ar",
                                str(AR),
                                "-ac",
                                "2",
                                "-c:a",
                                "pcm_s16le",
                                str(out_wav),
                            ],
                        )
                        if r1.returncode == 0 and _voice_wav_ok(out_wav):
                            tts_provider = "elevenlabs"
                            log("step2: voice.wav from ElevenLabs (concat)")
                except UnicodeEncodeError as e:
                    log(f"WARN: ElevenLabs concat UnicodeEncodeError: {e!r}")
                except Exception as e:  # noqa: BLE001
                    log(f"WARN: ElevenLabs concat ffmpeg: {type(e).__name__}: {e!r}")
            else:
                log("WARN: ElevenLabs produced no valid chunks; falling back")
        except UnicodeEncodeError as e:
            log(f"WARN: ElevenLabs path UnicodeEncodeError: {e!r}")
        except Exception as e:  # noqa: BLE001
            log(f"WARN: ElevenLabs pipeline error: {type(e).__name__}: {e!r}")

    if _voice_wav_ok(out_wav):
        log(
            f"log: tts_provider={tts_provider} "
            f"elevenlabs_voice_id={masked_vid or '(unset)'}"
        )
        return

    if allow_local_tts:
        try:
            if _local_say_wav(out_wav, spoken):
                tts_provider = "macos_say"
                log("step2: voice.wav from macOS `say` (--allow-local-tts)")
        except Exception as e:  # noqa: BLE001
            log(f"WARN: macOS say fallback: {type(e).__name__}: {e!r}")
    elif api_key and voice_id and requests is None:
        pass  # already warned: no say without flag
    elif not (api_key and voice_id):
        log(
            "WARN: ELEVENLABS_API_KEY / ELEVENLABS_VOICE_ID not fully set; "
            "use --allow-local-tts for macOS `say`, else silence."
        )

    if _voice_wav_ok(out_wav):
        log(
            f"log: tts_provider={tts_provider} "
            f"elevenlabs_voice_id={masked_vid or '(unset)'}"
        )
        return

    log("WARN: TTS fallbacks exhausted; writing 10 min silent voice.wav")
    _force_silence_voice_wav(out_wav)

    if not _voice_wav_ok(out_wav):
        log("WARN: silence generation failed; retrying minimal silence")
        _force_silence_voice_wav(out_wav)
    if not _voice_wav_ok(out_wav):
        log("WARN: voice.wav still invalid after all fallbacks; last ffmpeg pass")
        try:
            _ffmpeg_silence_wav(out_wav, 30.0)
        except Exception as e:  # noqa: BLE001
            log(f"WARN: final silence attempt: {e!r}")
    tts_provider = "silence"
    log(
        f"log: tts_provider={tts_provider} "
        f"elevenlabs_voice_id={masked_vid or '(unset)'}"
    )


# ---------------------------------------------------------------------------
# step 3 — Pexels -> Pixabay -> local -> dummy (no DVIDS)
# ---------------------------------------------------------------------------


def _clean_query(q: str) -> str:
    return " ".join((q or "").lower().replace("-", " ").split()).strip()


def _smart_queries(base: str, slug: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []

    def add(raw: str) -> None:
        s = _clean_query(raw)
        if len(s) < 2 or s in seen:
            return
        seen.add(s)
        out.append(s)

    add(base)
    add(slug.replace("_", " ").replace("-", " "))
    words = _clean_query(base).split()
    if len(words) >= 3:
        add(" ".join(words[:3]))
    if len(words) >= 2:
        add(" ".join(words[-3:]))
    for fb in ("city skyline at dusk", "government building exterior", "news documentary b roll"):
        if len(out) >= 8:
            break
        add(fb)
    return out[:8]


def _download_to_file(url: str, dest: Path) -> bool:
    try:
        import requests
    except ImportError:
        return False
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        r = requests.get(
            url,
            headers={"User-Agent": "StateVerge-build_power_episode/1.0"},
            timeout=20,
            stream=True,
        )
        if r.status_code != 200:
            return False
        with dest.open("wb") as f:
            for chunk in r.iter_content(256 * 1024):
                if chunk:
                    f.write(chunk)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception:  # noqa: BLE001
        return False


def _pexels_h(v: dict[str, Any]) -> int:
    best = 0
    for f in v.get("video_files") or []:
        if isinstance(f, dict):
            try:
                best = max(best, int(f.get("height") or 0))
            except (TypeError, ValueError):
                pass
    return best


def _fetch_pexels(
    queries: list[str], need: int, key: str, out_dir: Path, start: int
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    idx = start
    for q in queries:
        if len(rows) >= need or not key.strip():
            break
        try:
            u = f"https://api.pexels.com/videos/search?query={quote_plus(q)}&per_page=15"
            import requests
        except ImportError:
            return rows, idx
        r = requests.get(
            u,
            headers={"Authorization": key, "User-Agent": "StateVerge/1.0"},
            timeout=20,
        )
        if r.status_code != 200:
            continue
        data = r.json()
        vids = sorted(
            [v for v in (data.get("videos") or []) if isinstance(v, dict)],
            key=_pexels_h,
            reverse=True,
        )
        for v in vids:
            if len(rows) >= need:
                break
            best_url, best_h = None, -1
            for f in v.get("video_files") or []:
                if not isinstance(f, dict):
                    continue
                try:
                    h = int(f.get("height") or 0)
                except (TypeError, ValueError):
                    h = 0
                link = f.get("link")
                if link and h > best_h:
                    best_h, best_url = h, str(link)
            if not best_url:
                continue
            dest = out_dir / f"pexels_{idx:03d}.mp4"
            if _download_to_file(best_url, dest):
                rows.append({"path": dest, "source": "pexels"})
                idx += 1
    return rows, idx


def _pixabay_area(hit: dict[str, Any]) -> int:
    v = hit.get("videos")
    if not isinstance(v, dict):
        return 0
    for key in ("large", "medium", "small"):
        e = v.get(key)
        if isinstance(e, dict):
            try:
                return int(e.get("width") or 0) * int(e.get("height") or 0)
            except (TypeError, ValueError):
                continue
    return 0


def _fetch_pixabay(
    queries: list[str], need: int, key: str, out_dir: Path, start: int
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    idx = start
    for q in queries:
        if len(rows) >= need or not key.strip():
            break
        try:
            import requests
        except ImportError:
            return rows, idx
        p = {"key": key, "q": q, "per_page": 20}
        u = f"https://pixabay.com/api/videos/?{urllib.parse.urlencode(p)}"
        r = requests.get(u, headers={"User-Agent": "StateVerge/1.0"}, timeout=20)
        if r.status_code != 200:
            continue
        data = r.json()
        hits = sorted(
            [h for h in (data.get("hits") or []) if isinstance(h, dict)],
            key=_pixabay_area,
            reverse=True,
        )
        for h in hits:
            if len(rows) >= need:
                break
            vids = h.get("videos") or {}
            med = None
            if isinstance(vids, dict):
                med = vids.get("large") or vids.get("medium") or vids.get("small")
            url = med.get("url") if isinstance(med, dict) else None
            if not url:
                continue
            dest = out_dir / f"pixabay_{idx:03d}.mp4"
            if _download_to_file(str(url), dest):
                rows.append({"path": dest, "source": "pixabay"})
                idx += 1
    return rows, idx


def _copy_local_videos(slug: str, need: int, out_dir: Path, start: int) -> tuple[list[dict[str, Any]], int]:
    root = ROOT / "assets" / "local_videos" / slug
    rows: list[dict[str, Any]] = []
    idx = start
    if not root.is_dir():
        return rows, idx
    files = [p for p in sorted(root.iterdir()) if p.suffix.lower() in VIDEO_EXTS and p.is_file()][: need]
    for p in files:
        if len(rows) >= need:
            break
        dest = out_dir / f"local_{idx:03d}{p.suffix}"
        try:
            shutil.copy2(p, dest)
        except OSError:
            continue
        rows.append({"path": dest, "source": "local"})
        idx += 1
    return rows, idx


def _make_dummy(out: Path) -> bool:
    """Silent black 1080p clip with silent stereo aux (same pattern as stock video tools)."""
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#101018:s={W}x{H}:d=1.2",
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d=1.2",
            "-shortest",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
    )
    return r.returncode == 0 and out.is_file() and out.stat().st_size > 64


def acquire_topic_visuals(slug: str, query: str, dest_dir: Path, limit: int = 24) -> int:
    """
    Mirror ``video_source.py`` style acquisition into ``dest_dir``:
    Pexels -> Pixabay -> local -> dummy. DVIDS is never used (USE_DVIDS=false by design).
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        env = load_env()
    except OSError:  # noqa: BLE001
        env = dict(os.environ)
    pex = (env.get("PEXELS_API_KEY") or "").strip()
    pix = (env.get("PIXABAY_API_KEY") or "").strip()
    smart = _smart_queries(query, slug)
    log(
        f"step3: smart_queries n={len(smart)} preview={smart[:4]!r} pexels_key={'set' if pex else 'no'}"
    )

    rows: list[dict[str, Any]] = []
    next_i = 1
    # 1) Pexels
    a, next_i = _fetch_pexels(smart, limit, pex, dest_dir, next_i)
    rows.extend(a)
    # 2) Pixabay
    need2 = max(0, limit - len(rows))
    b, next_i = _fetch_pixabay(smart, need2, pix, dest_dir, next_i)
    rows.extend(b)
    # 3) local
    need3 = max(0, limit - len(rows))
    c, _ = _copy_local_videos(slug, need3, dest_dir, next_i)
    rows.extend(c)
    # 4) dummy
    d_dummy = 1
    while len(rows) < limit and d_dummy < 32:
        p = dest_dir / f"dummy_{d_dummy:03d}.mp4"
        d_dummy += 1
        if _make_dummy(p):
            rows.append({"path": p, "source": "dummy"})
        else:
            break

    mf = dest_dir / "acquire_manifest.json"
    try:
        mf.write_text(
            json.dumps(
                {
                    "slug": slug,
                    "query": query,
                    "count": len(rows),
                    "videos": [{"source": r["source"], "path": str(r["path"])} for r in rows],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    log(f"step3: saved {len(rows)} clips to {dest_dir} (incl. dummy as needed); manifest {mf.name}")
    return len(rows)


# optional: if repo adds ``src.utils.video_source``, we log that the embedded order matches user request
def _log_video_source_note() -> None:
    p = ROOT / "src" / "utils" / "video_source.py"
    if p.is_file():
        log(
            "info: `src/utils/video_source.py` exists; embedded fetch still uses Pexels->Pixabay->local (no DVIDS)"
        )


# ---------------------------------------------------------------------------
# step 4 — video
# ---------------------------------------------------------------------------

def collect_clips(visuals: Path) -> list[Path]:
    if not visuals.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(visuals.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in VIDEO_EXTS:
            if p.name == "acquire_manifest.json":
                continue
            out.append(p)
    return out


def encode_segment(src: Path, want_dur: float, dst_ts: Path) -> bool:
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={FPS},format=yuv420p,setsar=1"
    )
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-fflags",
            "+discardcorrupt+genpts",
            "-i",
            str(src),
            "-an",
            "-t",
            f"{max(0.1, want_dur):.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(FPS),
            "-bsf:v",
            "h264_mp4toannexb",
            "-f",
            "mpegts",
            str(dst_ts),
        ],
    )
    if r.returncode != 0 or not dst_ts.is_file() or dst_ts.stat().st_size < 2048:
        return False
    return True


def build_silent_video(usable: list[tuple[Path, float]], target: float, work: Path) -> tuple[Path | None, int]:
    work.mkdir(parents=True, exist_ok=True)
    if not usable:
        log("WARN: no usable B-roll; encoding long dummy as single source")
        d = work / "fallback_all_dummy.mp4"
        if not _make_dummy(d):
            return None, 0
        dd = max(0.1, min(300.0, target))
        usable = [(d, dd)]

    seg_paths: list[Path] = []
    accumulated = 0.0
    seg_index = 0
    for cycle in range(MAX_CYCLES):
        if accumulated >= target - 0.04:
            break
        for src, d_src in usable:
            if accumulated >= target - 0.04:
                break
            remaining = max(0.0, target - accumulated)
            slot = min(SEG_MAX, max(SEG_MIN, min(d_src, SEG_MAX)))
            slot = min(slot, d_src)
            want = min(slot, remaining)
            if want < 0.45:
                accumulated = target
                break
            ts = work / f"seg_{seg_index:04d}.ts"
            if not encode_segment(src, want, ts):
                log(f"WARN: segment encode skip src={src.name}")
                continue
            actual = probe_duration(ts)
            if actual <= 0:
                actual = want
            seg_paths.append(ts)
            accumulated += actual
            seg_index += 1
            if seg_index >= MAX_SEGMENTS:
                break
    if not seg_paths:
        log("WARN: could not encode any TS; trying one black slide")
        ts0 = work / "seg_0000.ts"
        d0 = work / "dum.mp4"
        if _make_dummy(d0) and encode_segment(d0, min(10.0, target), ts0):
            seg_paths = [ts0]

    n_ts = len(seg_paths)
    if not seg_paths:
        return None, 0
    list_path = (work / "concat_list.txt").resolve()
    list_path.write_text("".join(f"file '{p.as_posix()}'\n" for p in seg_paths), encoding="utf-8")
    silent = work / "silent_video.mp4"
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(silent),
        ],
    )
    if r.returncode != 0 or probe_duration(silent) <= 0:
        log("WARN: concat copy failed, retry with concat: protocol")
        u = "concat:" + "|".join(str(p.resolve()) for p in seg_paths)
        r2 = run_cap(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-i",
                u,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(silent),
            ],
        )
        if r2.returncode != 0 or probe_duration(silent) <= 0:
            return None, n_ts
    return silent, n_ts


def rebuild_silent_from_ts_reencode(work: Path, silent_out: Path) -> bool:
    """
    Re-mux all ``seg_*.ts`` with **libx264** (no stream copy) so ``silent_out`` is
    guaranteed to carry a normal H.264 video bitstream when TS→MP4 copy produced none.
    """
    segs = sorted(work.glob("seg_*.ts"), key=lambda x: x.name)
    if not segs:
        log("WARN: rebuild_silent_from_ts_reencode: no seg_*.ts under work dir")
        return False
    silent_out.parent.mkdir(parents=True, exist_ok=True)
    lst = work / "concat_reencode_list.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in segs), encoding="utf-8")
    r = run_cap(
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
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(silent_out),
        ],
    )
    ok = r.returncode == 0 and silent_out.is_file() and has_stream(silent_out, "video")
    if not ok:
        log(f"WARN: TS re-encode silent failed stderr_tail={(r.stderr or '')[-400:]}")
    return ok


def make_silent_lavfi_h264(out: Path, duration_sec: float) -> bool:
    """Solid-color 1080p30 H.264 silent clip of ``duration_sec`` (emergency video)."""
    d = max(1.0, min(float(duration_sec), 7200.0))
    out.parent.mkdir(parents=True, exist_ok=True)
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=#101018:s={W}x{H}:r={FPS}:d={d}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out),
        ],
    )
    return r.returncode == 0 and out.is_file() and has_stream(out, "video")


def mux_final(silent: Path, voice: Path, out: Path) -> bool:
    """Mux silent video + ``voice`` (no BGM). Required ffmpeg mapping per project spec."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(voice),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ],
    )
    return r.returncode == 0 and out.is_file()


def mux_final_reencode_video(silent: Path, voice: Path, out: Path) -> bool:
    """Fallback: re-encode video with libx264 while passing through AAC from WAV mux path."""
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(silent),
            "-i",
            str(voice),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(out),
        ],
    )
    return r.returncode == 0 and out.is_file()


def _trim_wav_to_seconds(src: Path, dst: Path, seconds: float) -> bool:
    """Trim WAV to at most ``seconds`` (re-encode PCM for reliable cut)."""
    if seconds <= 0.1:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            str(src),
            "-t",
            f"{seconds:.3f}",
            "-ar",
            str(AR),
            "-ac",
            "2",
            "-c:a",
            "pcm_s16le",
            str(dst),
        ],
    )
    return r.returncode == 0 and dst.is_file() and dst.stat().st_size > 64


def write_preview_jpg_from_video(video: Path, preview_jpg: Path, ss_sec: float = 1.0) -> bool:
    """One frame JPEG for playback / black-frame diagnostics."""
    if not video.is_file():
        return False
    preview_jpg.parent.mkdir(parents=True, exist_ok=True)
    r = run_cap(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{ss_sec:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            str(preview_jpg),
        ],
    )
    return r.returncode == 0 and preview_jpg.is_file()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build 10 min power-episode for any topic")
    ap.add_argument("--topic", required=True, help="slug, e.g. pentagon")
    ap.add_argument(
        "--topic-title",
        default="",
        help="Human title for the script. Default: title-case slug",
    )
    ap.add_argument(
        "--duration-seconds",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Test mode: Chinese narration ~180–220 汉字, ~N s TTS target; "
            "voice.wav may be trimmed to N s; final mux uses -shortest (no BGM)."
        ),
    )
    ap.add_argument("--script-only", action="store_true", help="Only write narration_script.txt")
    ap.add_argument(
        "--allow-local-tts",
        action="store_true",
        help=(
            "Allow macOS `say` when ElevenLabs cannot run (e.g. `requests` missing) or after "
            "ElevenLabs failure; without this flag, narration falls back to silence instead of say."
        ),
    )
    args = ap.parse_args(argv)

    _log_video_source_note()

    topic = sanitize_topic(args.topic)
    title = (args.topic_title or "").strip() or default_topic_title(topic)
    topic_dir = ROOT / "topics" / topic
    brief_txt = topic_dir / "narration_script.txt"
    voice_wav = topic_dir / "voice.wav"
    visuals = topic_dir / "visuals"
    out_dir = topic_dir / "output"
    out_mp4 = out_dir / "final.mp4"
    work = out_dir / "_work"

    for d in (topic_dir, visuals, out_dir, work):
        d.mkdir(parents=True, exist_ok=True)

    env = load_env()
    if (env.get("USE_DVIDS", "") or "").lower() in ("1", "true", "yes"):
        log("info: USE_DVIDS is set in environment but this tool never calls DVIDS")

    # 1) script
    dsec = args.duration_seconds
    if dsec is not None and dsec > 0:
        log(f"step1: short test mode duration_seconds={dsec} (Chinese ~180–220 汉字, six sections)")
    else:
        log("step1: generate narration (6 fixed sections, long-form)")
    try:
        script_text = generate_script(
            env, title, POWER_SECTIONS, duration_seconds=dsec
        )
        brief_txt.write_text(script_text, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        log(f"WARN: script step exception: {e!r}; using fallback")
        if dsec is not None and dsec > 0:
            script_text = _fallback_script_short_cn(title, POWER_SECTIONS)
        else:
            script_text = _fallback_script(title, POWER_SECTIONS)
        brief_txt.write_text(script_text, encoding="utf-8")
    n_chars = len(script_text)
    wcount = _word_count(script_text)
    cjk = _cjk_char_count(script_text)
    log(
        f"log: script length chars={n_chars} words={wcount} cjk_chars={cjk} path={brief_txt}"
    )

    if args.script_only:
        log("done (--script-only)")
        return 0

    # 2) TTS
    tts_work = out_dir / "_tts_work"
    log("step2: synthesize voice")
    synthesize_voice(env, script_text, voice_wav, tts_work, allow_local_tts=args.allow_local_tts)
    if not _voice_wav_ok(voice_wav):
        log("WARN: voice.wav still missing after synthesize_voice; forcing silence")
        _force_silence_voice_wav(voice_wav)

    vd = probe_duration(voice_wav)
    if vd <= 0:
        vd = float(dsec) if (dsec is not None and dsec > 0) else 60.0
    # Cap narration to requested test length so picture pass matches ~N s
    if dsec is not None and dsec > 0 and vd > float(dsec) + 0.25:
        tmp = voice_wav.with_suffix(".trim_tmp.wav")
        log(f"WARN: voice {vd:.2f}s > --duration-seconds {dsec}; trimming to {dsec}s")
        if _trim_wav_to_seconds(voice_wav, tmp, float(dsec)):
            try:
                voice_wav.unlink(missing_ok=True)
                shutil.move(str(tmp), str(voice_wav))
            except OSError:
                try:
                    voice_wav.write_bytes(tmp.read_bytes())
                finally:
                    tmp.unlink(missing_ok=True)
            vd = probe_duration(voice_wav)
            if vd <= 0:
                vd = float(dsec)
    log(f"log: voice duration sec={vd:.2f} path={voice_wav}")

    # 3) B-roll
    try:
        acquire_topic_visuals(topic, title, visuals, limit=28)
    except Exception as e:  # noqa: BLE001
        log(f"WARN: acquire_topic_visuals: {e!r}")
    clips = collect_clips(visuals)
    log(f"log: raw clip files in visuals/ count={len(clips)}")

    usable: list[tuple[Path, float]] = []
    for p in clips:
        if not has_stream(p, "video"):
            continue
        d = probe_duration(p)
        if d <= 0.3:
            continue
        usable.append((p, d))
    if not usable and visuals.is_dir():
        log("WARN: no decoded clips; forcing dummy in visuals for encode")
        dp = visuals / "dummy_force_001.mp4"
        if _make_dummy(dp):
            usable = [(dp, 2.0)]

    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    silent, n_ts = build_silent_video(usable, vd, work)
    n_seg = n_ts
    if silent is None:
        log("WARN: silent build failed; writing a minimal 5s test pattern")
        silent = work / "silent_h264.mp4"
        run_cap(
            [
                "ffmpeg",
                "-hide_banner",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=s={W}x{H}:d=5",
                "-pix_fmt",
                "yuv420p",
                "-c:v",
                "libx264",
                str(silent),
            ],
        )
    if (
        silent
        and probe_duration(silent) > 0
        and probe_duration(silent) < vd * 0.5
        and usable
    ):
        log("WARN: silent very short; retrying fill")
        sil2, n2 = build_silent_video(usable, vd, work)
        if sil2 and probe_duration(sil2) and probe_duration(sil2) > 0:
            silent = sil2
            n_seg = n2

    log(f"log: number of TS segment files (encode passes) = {n_seg}")

    # ------------------------------------------------------------------
    # 4b) ffprobe silent_video — must have video before mux
    # ------------------------------------------------------------------
    silent_path = silent
    silent_has_v = bool(silent_path and silent_path.is_file() and has_stream(silent_path, "video"))
    log(f"log: silent_video has_video={'true' if silent_has_v else 'false'} path={silent_path}")
    if silent_path and silent_path.is_file() and not silent_has_v:
        log("WARN: silent_video.mp4 has no video stream; re-encoding TS concat (no mux yet)")
        re_out = work / "silent_video_reencoded.mp4"
        if rebuild_silent_from_ts_reencode(work, re_out):
            silent_path = re_out
            silent = re_out
            silent_has_v = has_stream(silent_path, "video")
            log(f"log: silent_video after TS re-encode has_video={'true' if silent_has_v else 'false'}")
        if not silent_has_v:
            log("WARN: TS re-encode did not yield video; emergency lavfi H.264 silent clip")
            lav = work / "silent_lavfi_emergency.mp4"
            if make_silent_lavfi_h264(lav, max(vd, 5.0)):
                silent_path = lav
                silent = lav
                silent_has_v = has_stream(silent_path, "video")
                log(f"log: silent_video after lavfi has_video={'true' if silent_has_v else 'false'}")
    if not silent_path or not silent_path.is_file() or not silent_has_v:
        log("WARN: cannot obtain valid silent video; aborting mux (no final.mp4 copy)")
        return 1

    # ------------------------------------------------------------------
    # 5) final mux (no BGM): copy video, AAC audio, -shortest
    # ------------------------------------------------------------------
    if not mux_final(silent_path, voice_wav, out_mp4):
        log("WARN: mux_final (copy) failed; trying video re-encode mux")
        mux_final_reencode_video(silent_path, voice_wav, out_mp4)

    fv, fa, fdur = probe_final_streams(out_mp4)
    log(
        f"log: final has_video={'true' if fv else 'false'} has_audio={'true' if fa else 'false'} "
        f"duration_sec={fdur:.2f}"
    )
    if (not fv) or (not fa) or fdur <= 0:
        log("WARN: final.mp4 missing stream or zero duration; fallback libx264 mux")
        mux_final_reencode_video(silent_path, voice_wav, out_mp4)
        fv, fa, fdur = probe_final_streams(out_mp4)
        log(
            f"log: final (after reencode) has_video={'true' if fv else 'false'} "
            f"has_audio={'true' if fa else 'false'} duration_sec={fdur:.2f}"
        )

    if fv:
        preview_jpg = out_dir / "preview.jpg"
        if not clips:
            log("log: visuals[1..3]=(none — no clip files under visuals/)")
        else:
            for i, vp in enumerate(clips[:3], start=1):
                log(f"log: visuals[{i}]={vp.resolve()}")
        silent_canon = (work / "silent_video.mp4").resolve()
        log(f"log: silent_video.mp4={silent_canon}")
        log(f"log: silent_muxed_into_final={silent_path.resolve()}")
        if write_preview_jpg_from_video(out_mp4, preview_jpg):
            log(f"log: preview_frame={preview_jpg.resolve()}")
        else:
            log("WARN: could not write preview.jpg from final.mp4 (ffmpeg failed?)")

    log(
        f"log: final duration sec={fdur:.2f} (voice was {vd:.2f}) | "
        f"b_roll_files={len(clips)} usable_for_encode={len(usable)} | ts_segments={n_seg} | out={out_mp4}"
    )
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
