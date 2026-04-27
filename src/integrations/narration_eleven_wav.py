"""
Read ``topics/<topic>/brief/narration_script.txt`` and synthesize
``topics/<topic>/audio/voice.wav`` via ElevenLabs (chunked HTTP + ffmpeg concat).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

AR = 48_000
TTS_CHUNK_CHARS = 2400

LOG = "narration_eleven_wav"


def _log(msg: str) -> None:
    print(f"[{LOG}] {msg}", flush=True)


def read_brief_narration(root: Path, topic: str) -> str:
    """Read Chinese (or any) script from the canonical brief path, with one fallback."""
    tdir = root / "topics" / topic
    cands = [
        tdir / "brief" / "narration_script.txt",
        tdir / "narration_script.txt",
    ]
    for p in cands:
        if p.is_file() and p.stat().st_size > 0:
            return p.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(
        f"no narration found; tried: " + ", ".join(str(x) for x in cands)
    )


def strip_for_tts(text: str) -> str:
    """Drop obvious markdown/heading noise; keep spoken lines (incl. Chinese)."""
    t = (text or "").strip()
    if len(t) >= 2 and t[0] in "（(" and t[-1] in "）)":
        t = t[1:-1].strip()
    out: list[str] = []
    for line in t.splitlines():
        s = line.strip()
        if not s:
            out.append("")
            continue
        if s.startswith("#"):
            continue
        s = re.sub(r"[*_`]+", "", s)
        out.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


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
        sents = re.split(r"(?<=[.!?。！？])\s+", para)
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


def _run_cap(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


def _voice_wav_ok(p: Path) -> bool:
    try:
        return p.is_file() and p.stat().st_size > 64
    except OSError:
        return False


def synthesize_elevenlabs_to_wav(
    *,
    text: str,
    out_wav: Path,
    work: Path,
    api_key: str,
    voice_id: str,
    model_id: str = "eleven_turbo_v2_5",
) -> bool:
    """
    One ElevenLabs pipeline: chunked POST → merge mp3 → 48kHz stereo PCM ``out_wav``.
    """
    try:
        import requests
    except ImportError:
        _log("ERROR: Python package `requests` is required. Install: pip install requests")
        return False

    spoken = strip_for_tts(text)
    if not spoken:
        _log("ERROR: narration text is empty after strip_for_tts")
        return False

    chunks = _split_into_chunks(spoken, TTS_CHUNK_CHARS) or [spoken]
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128"
    chunk_paths: list[Path] = []
    for i, chunk_text in enumerate(chunks, start=1):
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
            except Exception as e:  # noqa: BLE001
                _log(f"WARN: chunk {i} request: {e!r}")
                time.sleep(1.0)
                continue
            if (
                r is not None
                and r.status_code == 200
                and len(getattr(r, "content", b"") or b"") > 1024
            ):
                p = work / f"chunk_{i:02d}.mp3"
                p.write_bytes(r.content)
                chunk_paths.append(p)
                ok_chunk = True
                break
            time.sleep(0.5)
        if not ok_chunk:
            _log(f"ERROR: ElevenLabs chunk {i}/{len(chunks)} failed (HTTP or empty body)")
            return False

    voice_mp3 = work / "voice_merged.mp3"
    if len(chunk_paths) == 1:
        shutil.copy2(chunk_paths[0], voice_mp3)
    else:
        ins: list[str] = []
        for p in chunk_paths:
            ins += ["-i", str(p)]
        n = len(chunk_paths)
        r0 = _run_cap(
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
        if r0.returncode != 0 or not voice_mp3.is_file():
            _log(f"ERROR: ffmpeg mp3 concat failed: {(r0.stderr or '')[:800]}")
            return False

    r1 = _run_cap(
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
    if r1.returncode != 0 or not _voice_wav_ok(out_wav):
        _log(f"ERROR: mp3 → wav: {(r1.stderr or '')[:800]}")
        return False
    return True


def build_voice_wav_from_brief(
    root: Path,
    topic: str,
    out_wav: Path,
    work: Path,
) -> bool:
    """
    Read ``read_brief_narration`` + :func:`synthesize_elevenlabs_to_wav` using
    ``ELEVENLABS_API_KEY`` / ``ELEVENLABS_VOICE_ID`` / ``ELEVENLABS_MODEL_ID`` from the environment.
    """
    _load_env_dotenv(root)
    key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    model = (os.environ.get("ELEVENLABS_MODEL_ID") or "eleven_turbo_v2_5").strip()
    if not key or not voice_id:
        _log(
            "ERROR: ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID must be set in the environment or .env"
        )
        return False
    try:
        raw = read_brief_narration(root, topic)
    except OSError as e:
        _log(f"ERROR: {e!r}")
        return False
    return synthesize_elevenlabs_to_wav(
        text=raw,
        out_wav=out_wav,
        work=work,
        api_key=key,
        voice_id=voice_id,
        model_id=model,
    )


def _load_env_dotenv(root: Path) -> None:
    p = root / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(p, override=False)
    except Exception:  # noqa: BLE001
        pass
