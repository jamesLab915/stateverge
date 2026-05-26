"""
ElevenLabs TTS service for the StateVerge Studio /create endpoint.

Reads from `.env`:
    ELEVENLABS_API_KEY              (required)
    ELEVENLABS_VOICE_ID             (required — refuses to pick a default)
    ELEVENLABS_MODEL_ID             (default: eleven_multilingual_v2)
    ELEVENLABS_STABILITY            (default: 0.5)
    ELEVENLABS_SIMILARITY_BOOST     (default: 0.8)
    ELEVENLABS_STYLE                (default: 0.0)
    ELEVENLABS_USE_SPEAKER_BOOST    (default: true)

Reads:
    topics/<slug>/brief/narration_script.txt

Writes (after archiving any existing files into audio/archive/<ts>/):
    topics/<slug>/audio/chunks/chunk_NNN.mp3
    topics/<slug>/audio/voice.mp3
    topics/<slug>/audio/voice.wav        (48 kHz stereo PCM)

Heavy lifting (text chunking, ElevenLabs HTTP call, ffmpeg concat & transcode,
ffprobe verification) is delegated to scripts/generate_eleven_tts_for_topic.py
to avoid two divergent implementations of the same pipeline.
"""

from __future__ import annotations

import importlib.util
import os
import re
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .paths import archive_existing, safe_subpath, safe_topic_dir


def _load_eleven_module(repo_root: Path):
    """
    Load scripts/generate_eleven_tts_for_topic.py without forcing it to live
    in a package.
    """
    src = repo_root / "scripts" / "generate_eleven_tts_for_topic.py"
    if not src.is_file():
        raise FileNotFoundError(
            f"missing scripts/generate_eleven_tts_for_topic.py at {src}"
        )
    spec = importlib.util.spec_from_file_location(
        "_sv_eleven_tts_script", src
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Result shape returned to the dashboard route
# ---------------------------------------------------------------------------


@dataclass
class TTSResult:
    ok: bool
    topic: str
    voice_mp3_path: str = ""
    voice_wav_path: str = ""
    chunks: int = 0
    duration_sec: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    archive_dir: str = ""
    voice_id_tail: str = ""
    model_id: str = ""
    duration_ms: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_voice(
    *,
    slug: str,
    repo_root: Path,
    max_chars: int = 2500,
) -> TTSResult:
    t0 = time.monotonic()

    api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if not api_key:
        return TTSResult(ok=False, topic=slug,
                         error="ELEVENLABS_API_KEY missing in .env")
    if not voice_id:
        return TTSResult(
            ok=False, topic=slug,
            error=("ELEVENLABS_VOICE_ID missing in .env "
                   "(refusing to auto-pick a voice)"),
        )

    try:
        topic_dir = safe_topic_dir(slug, repo_root)
    except ValueError as e:
        return TTSResult(ok=False, topic=slug, error=str(e))

    brief_dir = safe_subpath(topic_dir, "brief")
    script_path = safe_subpath(brief_dir, "narration_script.txt")
    if not script_path.is_file():
        return TTSResult(
            ok=False, topic=slug,
            error=(f"narration not found at "
                   f"{script_path.relative_to(repo_root)}; "
                   f"generate the script first"),
        )

    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        return TTSResult(ok=False, topic=slug, error="narration is empty")

    audio_dir = safe_subpath(topic_dir, "audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    chunks_dir = safe_subpath(audio_dir, "chunks")
    chunks_dir.mkdir(parents=True, exist_ok=True)
    voice_mp3 = safe_subpath(audio_dir, "voice.mp3")
    voice_wav = safe_subpath(audio_dir, "voice.wav")

    # archive existing voice.mp3 / voice.wav before regeneration
    archive_dir = archive_existing(
        [voice_mp3, voice_wav],
        archive_root=audio_dir / "archive",
    )

    try:
        eleven = _load_eleven_module(repo_root)
    except (FileNotFoundError, RuntimeError) as e:
        return TTSResult(ok=False, topic=slug, error=str(e))

    model_id = (
        (os.environ.get("ELEVENLABS_MODEL_ID") or "").strip()
        or eleven.DEFAULT_MODEL_ID
    )
    voice_settings = eleven.voice_settings_from_env()

    # ---- chunk + render ----
    try:
        chunks = eleven.chunk_text(text, max_chars=int(max_chars))
    except Exception as e:  # noqa: BLE001
        return TTSResult(ok=False, topic=slug,
                         error=f"chunk_text failed: {e}")

    # wipe any stale chunks from a previous run (the ones we want are already
    # archived in audio/archive/<ts>/ if they were ever a finished voice.*)
    for old in chunks_dir.glob("chunk_*.mp3"):
        try:
            old.unlink()
        except OSError:
            pass

    chunk_paths: list[Path] = []
    for i, c in enumerate(chunks, start=1):
        out = chunks_dir / f"chunk_{i:03d}.mp3"
        try:
            eleven.request_chunk_mp3(
                c,
                api_key=api_key,
                voice_id=voice_id,
                model_id=model_id,
                voice_settings=voice_settings,
                out_path=out,
            )
        except RuntimeError as e:
            return TTSResult(
                ok=False, topic=slug,
                voice_id_tail=_tail(voice_id),
                model_id=model_id,
                error=f"chunk {i:03d}/{len(chunks)} failed: {e}",
            )
        chunk_paths.append(out)
        time.sleep(0.2)

    # ---- concat mp3 ----
    try:
        eleven.ffmpeg_concat_mp3(chunk_paths, voice_mp3)
    except (RuntimeError, ValueError) as e:
        return TTSResult(ok=False, topic=slug,
                         voice_id_tail=_tail(voice_id),
                         model_id=model_id,
                         error=f"ffmpeg concat: {e}")

    # ---- mp3 -> wav (48 kHz stereo) ----
    try:
        eleven.ffmpeg_mp3_to_wav(voice_mp3, voice_wav)
    except RuntimeError as e:
        return TTSResult(ok=False, topic=slug,
                         voice_id_tail=_tail(voice_id),
                         model_id=model_id,
                         error=f"ffmpeg mp3->wav: {e}")

    # ---- ffprobe verify the wav ----
    try:
        info = eleven.ffprobe_info(voice_wav)
    except RuntimeError as e:
        return TTSResult(ok=False, topic=slug,
                         voice_id_tail=_tail(voice_id),
                         model_id=model_id,
                         error=f"ffprobe: {e}")

    return TTSResult(
        ok=True,
        topic=slug,
        voice_mp3_path=str(voice_mp3.relative_to(repo_root)),
        voice_wav_path=str(voice_wav.relative_to(repo_root)),
        chunks=len(chunk_paths),
        duration_sec=float(info.get("duration_sec") or 0.0),
        sample_rate=int(info.get("sample_rate") or 0),
        channels=int(info.get("channels") or 0),
        archive_dir=(
            str(archive_dir.relative_to(repo_root)) if archive_dir else ""
        ),
        voice_id_tail=_tail(voice_id),
        model_id=model_id,
        duration_ms=int((time.monotonic() - t0) * 1000),
    )


# ---------------------------------------------------------------------------
# Read-only helpers (used by the page render to show readiness)
# ---------------------------------------------------------------------------


def env_status() -> dict[str, Any]:
    api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    model_id = (os.environ.get("ELEVENLABS_MODEL_ID") or "").strip() \
        or "eleven_multilingual_v2"
    return {
        "configured": bool(api_key) and bool(voice_id),
        "key_present": bool(api_key),
        "voice_present": bool(voice_id),
        "model_id": model_id,
        "voice_id_tail": _tail(voice_id),
    }


def _tail(s: str) -> str:
    s = (s or "").strip()
    if len(s) >= 4:
        return "…" + s[-4:]
    return ""


# convenience for tests / repl
_PARA_RE = re.compile(r"\n\s*\n+")
