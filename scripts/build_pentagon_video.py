#!/usr/bin/env python3
"""
End-to-end one-shot build for: "The Pentagon Explained".

Pipeline (all artifacts under ``topics/<topic>/``):
    1. OpenAI -> ``brief/narration_script.txt`` (6 sections, ~10 min target).
    2. ElevenLabs TTS -> ``voice/voice.mp3`` then ``voice/voice.wav``
       (chunked per section, concatenated via ffmpeg).
    3. ``src.integrations.media_sources.selector`` -> ``assets/raw/*.mp4``
       (Pexels + Pixabay only; no DVIDS).
    4. Per-clip normalize to 6-10s MPEG-TS (1080p30 / yuv420p / H.264, no audio)
       under ``output/_work/seg_NNNN.ts``.
    5. Concat TS to ``output/_work/silent_video.mp4`` (TS -> mp4 ``-c copy``,
       which avoids the ffmpeg 8.1 ``h264_mp4toannexb`` regression triggered
       by mp4-as-input concat).
    6. Final mux: silent_video + voice.wav -> ``output/final.mp4``
       (no background music). Length is locked to ``voice.wav`` via ``-t``.
    7. Validate with ffprobe.

Usage:
    .venv/bin/python scripts/build_pentagon_video.py
    .venv/bin/python scripts/build_pentagon_video.py --topic pentagon \
        --topic-title "The Pentagon Explained"
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
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

ROOT = Path(
    os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
).resolve()

W, H, FPS, AR = 1920, 1080, 30, 48000
SEG_MIN, SEG_MAX = 6.0, 10.0  # per-clip play length window
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}

# rough TTS pacing - eleven_multilingual_v2 with our voice settings
# was empirically observed at ~185 wpm. Tune if you swap voices/models.
WORDS_PER_MIN = 185
TARGET_MINUTES = 10
TARGET_WORDS = WORDS_PER_MIN * TARGET_MINUTES  # ~1850
# Hard floor: if the generated script falls below this, re-ask OpenAI to expand.
MIN_WORDS_FLOOR = int(TARGET_WORDS * 0.92)  # ~1700

# ElevenLabs request size cap (chars per call). The free/starter tiers cap at
# ~5000; we stay well under it and chunk by sentences.
TTS_CHUNK_CHARS = 2400

# safety net
MAX_SEGMENTS = 600
MAX_CYCLES = 200


# ---------------------------------------------------------------------------
# logging & subprocess
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(f"[pentagon] {msg}", flush=True)


def fail(msg: str, code: int = 2) -> "Optional[None]":
    log(f"FAIL: {msg}")
    sys.exit(code)


def run_cap(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False, **kw)


def run_stream(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=False, **kw)


# ---------------------------------------------------------------------------
# .env loader (does not depend on python-dotenv)
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
        env.setdefault(k.strip(), v.strip().strip("'\""))
    return env


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def probe_duration(p: Path) -> float:
    r = run_cap([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1",
        str(p),
    ])
    s = (r.stdout or "").strip()
    if r.returncode != 0 or not s:
        return -1.0
    try:
        return float(s)
    except ValueError:
        return -1.0


def has_stream(p: Path, kind: str) -> bool:
    r = run_cap([
        "ffprobe", "-v", "error",
        "-select_streams", f"{kind[0]}:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=nw=1:nk=1",
        str(p),
    ])
    return r.returncode == 0 and (r.stdout or "").strip() == kind


# ---------------------------------------------------------------------------
# step 1: script generation (OpenAI)
# ---------------------------------------------------------------------------

PENTAGON_SECTIONS = (
    "Introduction",
    "Origins and Construction",
    "Architecture and Scale",
    "Inside the Pentagon",
    "Power and Influence",
    "Conclusion",
)


def _openai_chat(env: dict[str, str], prompt: str, model_override: str = "") -> str:
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        fail("OPENAI_API_KEY not set", 10)
    env_model = env.get("OPENAI_MODEL", "").strip()
    # ``mini`` family chronically undershoots requested length on long-form
    # prose; force a fuller model for documentary scripts.
    if model_override:
        model = model_override
    elif env_model and "mini" not in env_model.lower():
        model = env_model
    else:
        model = "gpt-4o"
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You write long-form, factual, cinematic documentary "
                    "narration for narrated YouTube videos. Use serious, "
                    "globally aware tone. Produce narration-ready paragraphs, "
                    "no bullet lists, no stage directions, no speaker labels."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
    }
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=180) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        # surface body for HTTPError to aid debugging
        body = ""
        if isinstance(e, HTTPError):
            try:
                body = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                body = ""
        fail(f"OpenAI request failed: {e!r} body={body}", 11)
    choices = data.get("choices") or []
    if not choices:
        fail(f"OpenAI returned no choices: {str(data)[:300]}", 11)
    out = (choices[0].get("message", {}).get("content") or "").strip()
    if not out:
        fail("OpenAI returned empty content", 11)
    return out


def _word_count(text: str) -> int:
    # crude but stable word counter (whitespace split, drop empties)
    return sum(1 for w in re.split(r"\s+", text.strip()) if w)


def _build_script_prompt(
    topic_title: str, sections: tuple[str, ...], min_per_section: int
) -> str:
    section_lines = "\n".join(f"## {s}" for s in sections)
    return (
        f"Write a {TARGET_MINUTES}-minute narrated documentary script titled: "
        f"\"{topic_title}\".\n\n"
        f"Target {TARGET_WORDS - 100} to {TARGET_WORDS + 200} words TOTAL "
        f"(this is roughly {WORDS_PER_MIN} words per minute when read aloud "
        f"by a brisk documentary narrator). Do NOT undershoot; "
        f"the script MUST run at least {TARGET_MINUTES} minutes when narrated.\n\n"
        "Required structure - use EXACTLY these markdown headings, in order:\n"
        f"{section_lines}\n\n"
        "Per-section rules:\n"
        f"- Each section MUST be at least {min_per_section} words.\n"
        "- Write 3 to 5 narration-ready paragraphs per section.\n"
        "- No bullet lists, numbered lists, brackets, or stage directions.\n"
        "- No 'Section 1:' prefixes; the markdown heading is the only label.\n"
        "- Keep sentences medium-length and fluent for TTS.\n"
        "- Stick to verifiable history, architecture, organization, and "
        "geopolitical role of the Pentagon (the U.S. Department of Defense "
        "headquarters in Arlington, Virginia). Avoid speculation and "
        "conspiracy framing.\n"
        "- Open with a strong cinematic hook in the Introduction.\n"
        "- End with a measured, reflective Conclusion.\n"
        "- Include concrete dates, organizational facts, and architectural "
        "details (sides, rings, floors, total floor space, the Pentagon's "
        "construction history under Brigadier General Brehon Somervell, the "
        "September 11 2001 attack, etc.) to give the narration substance.\n"
    )


def _expand_short_script(env: dict[str, str], short_text: str, sections: tuple[str, ...]) -> str:
    cur_words = _word_count(short_text)
    needed = TARGET_WORDS - cur_words
    section_lines = ", ".join(sections)
    prompt = (
        "The following narration script is too short for a "
        f"{TARGET_MINUTES}-minute video. It currently has {cur_words} words; "
        f"it must be expanded to AT LEAST {TARGET_WORDS} words by adding "
        f"approximately {max(needed, 200)} more words of substantive content "
        "evenly distributed across the existing sections. Keep the same "
        f"section headings ({section_lines}) and the same overall narrative "
        "arc; deepen each section with additional concrete history, "
        "architecture, organizational detail, and geopolitical context. "
        "Return the FULL expanded script, in the same markdown heading "
        "format. Do not add new sections, do not add bullet lists, do not "
        "add stage directions.\n\n"
        "--- BEGIN ORIGINAL SCRIPT ---\n"
        f"{short_text}\n"
        "--- END ORIGINAL SCRIPT ---\n"
    )
    return _openai_chat(env, prompt)


def generate_pentagon_script(
    env: dict[str, str], topic_title: str, sections: tuple[str, ...]
) -> str:
    min_per_section = max(250, TARGET_WORDS // (len(sections) + 1))
    text = _openai_chat(env, _build_script_prompt(topic_title, sections, min_per_section))
    wc = _word_count(text)
    log(f"step1: initial draft words={wc} target>={TARGET_WORDS}")
    # one expansion pass if we undershoot the floor
    if wc < MIN_WORDS_FLOOR:
        log(f"step1: undershoot ({wc}<{MIN_WORDS_FLOOR}); requesting expansion")
        expanded = _expand_short_script(env, text, sections)
        ewc = _word_count(expanded)
        log(f"step1: expanded draft words={ewc}")
        if ewc > wc:
            text = expanded
            wc = ewc
    # ensure all six headings exist; if any missing, append a stub
    missing = [s for s in sections if f"## {s}".lower() not in text.lower()]
    if missing:
        log(f"WARN OpenAI output missing sections={missing}; appending stubs")
        for s in missing:
            text += (
                f"\n\n## {s}\n\n"
                f"This section on {s.lower()} could not be generated cleanly. "
                "Manual editing recommended.\n"
            )
    return text.strip() + "\n"


# ---------------------------------------------------------------------------
# step 2: ElevenLabs TTS
# ---------------------------------------------------------------------------

def _split_into_chunks(text: str, max_chars: int) -> list[str]:
    """Split prose into chunks <= max_chars, preferring sentence boundaries."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    cur = ""
    for para in paragraphs:
        # if adding this para keeps us under cap, append
        joiner = "\n\n" if cur else ""
        if len(cur) + len(joiner) + len(para) <= max_chars:
            cur = cur + joiner + para
            continue
        # flush current and start new with the paragraph
        if cur:
            chunks.append(cur)
            cur = ""
        # if single paragraph too big, split by sentences
        if len(para) <= max_chars:
            cur = para
            continue
        sents = re.split(r"(?<=[.!?])\s+", para)
        buf = ""
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
    return chunks


def _strip_markdown(text: str) -> str:
    """Strip section markers and other markdown so TTS reads only prose."""
    out_lines: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out_lines.append("")
            continue
        if s.startswith("#"):
            # skip headings entirely; they are visual structure only
            continue
        # remove inline emphasis markers
        s = re.sub(r"[*_`]+", "", s)
        out_lines.append(s)
    cleaned = "\n".join(out_lines)
    # collapse triple+ newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def synthesize_voice(
    env: dict[str, str], script_text: str, voice_dir: Path
) -> Path:
    api_key = env.get("ELEVENLABS_API_KEY", "").strip()
    voice_id = env.get("ELEVENLABS_VOICE_ID", "").strip()
    model_id = env.get("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5").strip() or "eleven_turbo_v2_5"
    if not api_key:
        fail("ELEVENLABS_API_KEY not set", 20)
    if not voice_id:
        fail("ELEVENLABS_VOICE_ID not set", 20)

    def _f(name: str, default: float) -> float:
        v = (env.get(name, "") or "").strip()
        try:
            return float(v) if v else default
        except ValueError:
            return default

    def _b(name: str, default: bool) -> bool:
        v = (env.get(name, "") or "").strip().lower()
        if not v:
            return default
        return v in ("1", "true", "yes", "on")

    voice_settings = {
        "stability": _f("ELEVENLABS_STABILITY", 0.5),
        "similarity_boost": _f("ELEVENLABS_SIMILARITY_BOOST", 0.75),
        "style": _f("ELEVENLABS_STYLE", 0.0),
        "use_speaker_boost": _b("ELEVENLABS_USE_SPEAKER_BOOST", True),
    }

    voice_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = voice_dir / "chunks"
    if chunks_dir.exists():
        shutil.rmtree(chunks_dir, ignore_errors=True)
    chunks_dir.mkdir(parents=True, exist_ok=True)

    spoken_text = _strip_markdown(script_text)
    chunks = _split_into_chunks(spoken_text, TTS_CHUNK_CHARS)
    if not chunks:
        fail("script produced no TTS chunks", 21)

    log(f"TTS: total_chars={len(spoken_text)} chunks={len(chunks)} model={model_id} voice={voice_id[:8]}...")

    try:
        import requests
    except ImportError as e:
        fail(f"requests not available in interpreter: {e}", 22)
        return Path()  # unreachable

    url = (
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        "?output_format=mp3_44100_128"
    )
    chunk_paths: list[Path] = []
    for i, chunk_text in enumerate(chunks, start=1):
        out_mp3 = chunks_dir / f"chunk_{i:02d}.mp3"
        body = {
            "text": chunk_text,
            "model_id": model_id,
            "voice_settings": voice_settings,
        }
        log(f"TTS chunk {i}/{len(chunks)} chars={len(chunk_text)} -> {out_mp3.name}")
        attempt = 0
        last_err = ""
        while attempt < 3:
            attempt += 1
            try:
                r = requests.post(
                    url,
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json=body,
                    timeout=300,
                )
            except Exception as e:  # noqa: BLE001
                last_err = repr(e)
                log(f"WARN TTS network error attempt={attempt}: {last_err[:200]}")
                time.sleep(2 * attempt)
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code} {r.text[:300]}"
                log(f"WARN TTS HTTP error attempt={attempt}: {last_err}")
                # 429 rate limit -> backoff longer
                time.sleep(4 if r.status_code == 429 else 2 * attempt)
                continue
            out_mp3.write_bytes(r.content)
            if out_mp3.stat().st_size < 1024:
                last_err = f"chunk too small: {out_mp3.stat().st_size}B"
                log(f"WARN {last_err}; retrying")
                time.sleep(2)
                continue
            break
        else:
            fail(f"TTS chunk {i} failed after retries: {last_err}", 23)
        chunk_paths.append(out_mp3)
        time.sleep(0.5)

    # concat all mp3 chunks via ffmpeg filter_complex (safe across mp3 frames)
    voice_mp3 = voice_dir / "voice.mp3"
    inputs: list[str] = []
    for p in chunk_paths:
        inputs += ["-i", str(p)]
    n = len(chunk_paths)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        *inputs,
        "-filter_complex", f"concat=n={n}:v=0:a=1[a]",
        "-map", "[a]",
        "-c:a", "libmp3lame", "-q:a", "2",
        str(voice_mp3),
    ]
    r = run_cap(cmd)
    if r.returncode != 0 or not voice_mp3.is_file():
        fail(f"voice mp3 concat failed: {(r.stderr or '')[-400:]}", 24)

    # convert to wav (48kHz stereo, PCM s16)
    voice_wav = voice_dir / "voice.wav"
    cmd2 = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(voice_mp3),
        "-ar", str(AR), "-ac", "2",
        "-c:a", "pcm_s16le",
        str(voice_wav),
    ]
    r2 = run_cap(cmd2)
    if r2.returncode != 0 or not voice_wav.is_file():
        fail(f"mp3 -> wav conversion failed: {(r2.stderr or '')[-400:]}", 25)

    return voice_wav


# ---------------------------------------------------------------------------
# step 3: footage selector (Pexels + Pixabay)
# ---------------------------------------------------------------------------

def fetch_footage(topic: str) -> Path:
    """Run the existing media_sources selector. Returns assets/raw dir."""
    cmd = [
        sys.executable, "-m", "src.integrations.media_sources.selector",
        "--topic", topic,
        "--source", "pexels,pixabay",
        "--per-page", "5",
        "--max-queries", "10",
        "--max-per-segment", "8",
    ]
    log(f"selector: {' '.join(cmd)}")
    r = run_stream(cmd, cwd=str(ROOT))
    if r.returncode != 0:
        fail(f"selector exited non-zero: {r.returncode}", 30)
    raw_dir = ROOT / "topics" / topic / "assets" / "raw"
    if not raw_dir.is_dir():
        fail(f"selector did not create raw dir: {raw_dir}", 31)
    return raw_dir


def collect_footage(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(raw_dir.iterdir(), key=lambda x: x.name.lower()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.suffix.lower() in VIDEO_EXTS:
            out.append(p)
    return out


# ---------------------------------------------------------------------------
# step 4: encode normalized TS segments
# ---------------------------------------------------------------------------

def encode_segment(src: Path, want_dur: float, dst_ts: Path) -> bool:
    vf = (
        f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
        f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={FPS},format=yuv420p,setsar=1"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-fflags", "+discardcorrupt+genpts",
        "-i", str(src),
        "-an",
        "-t", f"{want_dur:.3f}",
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-g", str(FPS),
        "-bsf:v", "h264_mp4toannexb",
        "-f", "mpegts",
        str(dst_ts),
    ]
    r = run_cap(cmd)
    if r.returncode != 0:
        log(f"WARN encode failed for {src.name}: {(r.stderr or '')[-300:]}")
        return False
    if not dst_ts.is_file() or dst_ts.stat().st_size < 4096:
        log(f"WARN encode produced empty TS for {src.name}")
        return False
    return True


def build_silent_video(
    sources: list[tuple[Path, float]], target: float, work: Path
) -> Path:
    work.mkdir(parents=True, exist_ok=True)
    seg_paths: list[Path] = []
    accumulated = 0.0
    seg_index = 0

    for cycle in range(MAX_CYCLES):
        if accumulated >= target - 0.05:
            break
        for src, d_src in sources:
            if accumulated >= target - 0.05:
                break
            remaining = max(0.0, target - accumulated)
            slot = min(SEG_MAX, max(SEG_MIN, min(d_src, SEG_MAX)))
            slot = min(slot, d_src)
            want = min(slot, remaining)
            if want < 0.5:
                accumulated = target
                break
            ts = work / f"seg_{seg_index:04d}.ts"
            if not encode_segment(src, want, ts):
                continue
            actual = probe_duration(ts)
            if actual <= 0:
                actual = want
            seg_paths.append(ts)
            accumulated += actual
            seg_index += 1
            log(
                f"segment {seg_index:03d} cycle={cycle+1} "
                f"src={src.name[:60]} want={want:.2f}s got={actual:.2f}s "
                f"cum={accumulated:.2f}/{target:.2f}s"
            )
            if seg_index >= MAX_SEGMENTS:
                log("WARN hit MAX_SEGMENTS; stopping")
                break

    if not seg_paths:
        fail("no segments could be encoded", 40)

    list_path = (work / "tmp_concat.txt").resolve()
    list_path.write_text(
        "".join(f"file '{p.resolve().as_posix()}'\n" for p in seg_paths),
        encoding="utf-8",
    )
    silent = work / "silent_video.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_path),
        "-c", "copy",
        "-movflags", "+faststart",
        str(silent),
    ]
    r = run_cap(cmd)
    if r.returncode != 0 or probe_duration(silent) <= 0:
        log("WARN concat-demuxer path failed; falling back to concat: protocol")
        url = "concat:" + "|".join(str(p.resolve()) for p in seg_paths)
        cmd2 = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", url,
            "-c", "copy",
            "-movflags", "+faststart",
            str(silent),
        ]
        r2 = run_cap(cmd2)
        if r2.returncode != 0 or probe_duration(silent) <= 0:
            fail(f"silent video assembly failed: {(r2.stderr or '')[-400:]}", 41)
    return silent


# ---------------------------------------------------------------------------
# step 6: final mux
# ---------------------------------------------------------------------------

def mux_final(silent: Path, voice: Path, out: Path, target: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(silent),
        "-i", str(voice),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2",
        "-t", f"{target:.3f}",
        "-movflags", "+faststart",
        str(out),
    ]
    r = run_cap(cmd)
    if r.returncode != 0 or not out.is_file():
        fail(f"final mux failed: {(r.stderr or '')[-500:]}", 50)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build The Pentagon Explained video")
    ap.add_argument("--topic", default="pentagon", help="topic slug under topics/")
    ap.add_argument(
        "--topic-title",
        default="The Pentagon Explained",
        help="full title used in OpenAI prompt",
    )
    ap.add_argument(
        "--skip-script",
        action="store_true",
        help="reuse existing brief/narration_script.txt",
    )
    ap.add_argument(
        "--skip-tts",
        action="store_true",
        help="reuse existing voice/voice.wav",
    )
    ap.add_argument(
        "--skip-footage",
        action="store_true",
        help="reuse existing assets/raw/ contents",
    )
    args = ap.parse_args(argv)

    topic = args.topic
    topic_dir = ROOT / "topics" / topic
    brief_dir = topic_dir / "brief"
    voice_dir = topic_dir / "voice"
    out_dir = topic_dir / "output"
    work_dir = out_dir / "_work"
    out_file = out_dir / "final.mp4"

    for d in (brief_dir, voice_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    env = load_env()

    # ------------------------------------------------------------------
    # step 1 - script
    # ------------------------------------------------------------------
    script_path = brief_dir / "narration_script.txt"
    if args.skip_script and script_path.is_file():
        log(f"step1: reuse existing script {script_path}")
        script_text = script_path.read_text(encoding="utf-8")
    else:
        log(f"step1: generating ~{TARGET_MINUTES}-min script via OpenAI: {args.topic_title!r}")
        script_text = generate_pentagon_script(env, args.topic_title, PENTAGON_SECTIONS)
        script_path.write_text(script_text, encoding="utf-8")
        wc = len(script_text.split())
        log(f"step1: wrote {script_path} words={wc}")

    # ------------------------------------------------------------------
    # step 2 - TTS
    # ------------------------------------------------------------------
    voice_wav = voice_dir / "voice.wav"
    if args.skip_tts and voice_wav.is_file():
        log(f"step2: reuse existing voice {voice_wav}")
    else:
        log(f"step2: synthesizing voice via ElevenLabs -> {voice_wav}")
        voice_wav = synthesize_voice(env, script_text, voice_dir)

    voice_dur = probe_duration(voice_wav)
    if voice_dur <= 0:
        fail(f"could not probe voice duration: {voice_wav}", 26)
    log(f"voice={voice_wav}")
    log(f"voice_duration={voice_dur:.2f}s ({voice_dur/60:.2f} min)")

    # ------------------------------------------------------------------
    # step 3 - fetch footage
    # ------------------------------------------------------------------
    raw_dir = topic_dir / "assets" / "raw"
    if args.skip_footage and raw_dir.is_dir() and any(raw_dir.iterdir()):
        log(f"step3: reuse existing raw footage in {raw_dir}")
    else:
        log("step3: fetching footage via media_sources.selector")
        raw_dir = fetch_footage(topic)

    cands = collect_footage(raw_dir)
    log(f"footage_candidates={len(cands)}")
    usable: list[tuple[Path, float]] = []
    for p in cands:
        if not has_stream(p, "video"):
            log(f"skip (no video stream): {p.name}")
            continue
        d = probe_duration(p)
        if d <= 0.5:
            log(f"skip (unusable duration={d:.2f}): {p.name}")
            continue
        usable.append((p, d))
    log(f"footage_usable={len(usable)}")
    if not usable:
        fail("no usable footage downloaded; check Pexels/Pixabay API keys", 32)

    # ------------------------------------------------------------------
    # step 4+5 - encode segments + assemble silent video
    # ------------------------------------------------------------------
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    silent = build_silent_video(usable, voice_dur, work_dir)
    silent_d = probe_duration(silent)
    log(f"silent_video={silent} duration={silent_d:.2f}s")

    # ------------------------------------------------------------------
    # step 6 - final mux (no music)
    # ------------------------------------------------------------------
    mux_final(silent, voice_wav, out_file, voice_dur)

    # ------------------------------------------------------------------
    # step 7 - validate
    # ------------------------------------------------------------------
    final_d = probe_duration(out_file)
    has_v = has_stream(out_file, "video")
    has_a = has_stream(out_file, "audio")
    log(f"output={out_file}")
    log(f"final_duration={final_d:.2f}s ({final_d/60:.2f} min)")
    log(f"video_stream={'yes' if has_v else 'NO'}")
    log(f"audio_stream={'yes' if has_a else 'NO'}")

    if not (out_file.is_file() and final_d > 0 and has_v and has_a):
        fail("output validation failed", 60)
    if abs(final_d - voice_dur) > 1.5:
        log(f"WARN final_duration {final_d:.2f}s drifts from voice {voice_dur:.2f}s by >1.5s")

    log(f"OK file_size={out_file.stat().st_size} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
