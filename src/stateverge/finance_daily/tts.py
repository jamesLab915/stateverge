"""OpenAI text-to-speech: one mp3 per segment."""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Callable

API_URL = "https://api.openai.com/v1/audio/speech"
Post = Callable[[str, dict, bytes], bytes]

INSTRUCTIONS = "用标准普通话播报,专业财经新闻主播的语气,沉稳、清晰,语速适中偏快,不夸张。"


def _http(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


class Speaker:
    def __init__(self, api_key: str | None = None, voice: str | None = None, model: str | None = None,
                 post: Post | None = None) -> None:
        self.api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        self.voice = voice or os.environ.get("FINANCE_VOICE") or "onyx"
        self.model = model or os.environ.get("FINANCE_TTS_MODEL") or "gpt-4o-mini-tts"
        self.post = post or _http

    def speak(self, text: str, out: Path) -> Path:
        body = json.dumps({"model": self.model, "voice": self.voice, "input": text,
                           "instructions": INSTRUCTIONS, "response_format": "mp3"}).encode()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(self.post(API_URL, {"Authorization": f"Bearer {self.api_key}",
                                            "Content-Type": "application/json"}, body))
        return out
