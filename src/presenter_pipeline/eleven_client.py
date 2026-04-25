"""
ElevenLabs Text-to-Speech: request ``mp3_44100_128``, then convert to 48kHz stereo WAV via ffmpeg.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import requests

from . import duration
from .config import ElevenSettings
from .logutil import log_presenter

LOG = logging.getLogger("presenter.eleven")

ELEVEN_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"

# ElevenLabs query param (see API docs for text-to-speech)
OUTPUT_FORMAT = "mp3_44100_128"


def _voice_settings(settings: ElevenSettings) -> dict[str, Any]:
    return {
        "stability": settings.stability,
        "similarity_boost": settings.similarity_boost,
        "style": settings.style,
        "use_speaker_boost": settings.use_speaker_boost,
    }


def text_to_speech(
    text: str,
    settings: ElevenSettings,
    out_wav: Path,
) -> bool:
    """
    Generate voice via ElevenLabs (MP3), write ``out_wav`` as 48kHz stereo WAV.
    Returns True on success, False on failure.
    """
    if not (settings.voice_id or "").strip():
        log_presenter(
            LOG, "--", "--", "eleven_tts", "missing ELEVENLABS_VOICE_ID", level=logging.ERROR
        )
        return False
    url = f"{ELEVEN_TTS_BASE}/{settings.voice_id}"
    query = {"output_format": OUTPUT_FORMAT}
    headers = {
        "xi-api-key": settings.api_key,
        "Content-Type": "application/json",
        "Accept": "audio/*",
    }
    body: dict[str, Any] = {
        "text": text,
        "model_id": settings.model_id,
        "voice_settings": _voice_settings(settings),
    }
    try:
        r = requests.post(url, headers=headers, params=query, json=body, timeout=300)
    except requests.RequestException as e:
        log_presenter(
            LOG, "--", "--", "eleven_tts", f"request_error: {e}", level=logging.ERROR
        )
        return False
    if r.status_code not in (200, 201):
        log_presenter(
            LOG,
            "--",
            "--",
            "eleven_tts",
            f"HTTP {r.status_code}: {(r.text or '')[:500]}",
            level=logging.ERROR,
        )
        return False
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    data = r.content
    with tempfile.TemporaryDirectory() as tdir:
        td = Path(tdir)
        mp3_path = td / "from_elevenlabs.mp3"
        mp3_path.write_bytes(data)
        args = [
            "ffmpeg",
            "-y",
            "-i",
            str(mp3_path),
            "-ar",
            "48000",
            "-ac",
            "2",
            str(out_wav),
        ]
        code, _o, e = duration.run_cmd(args)
        if code != 0:
            log_presenter(
                LOG,
                "--",
                "--",
                "ffmpeg_mp3_to_wav",
                (e or "")[:2000],
                level=logging.ERROR,
            )
            return False
    return True
