"""Runway avatar videos: the presenter speaks the given audio (lip-sync).

One-time setup creates a *custom avatar* from a reference portrait
(``POST /v1/avatars``); every anchor segment is then rendered with
``POST /v1/avatar_videos`` (model ``gwm1_avatars``, speech = our TTS audio)
and polled through ``GET /v1/tasks/{id}``.

The portrait is a fictional presenter generated once with OpenAI images —
never a real person — and saved in data/finance/ so the face stays the same.
"""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

BASE = "https://api.dev.runwayml.com"
VERSION = "2024-11-06"
Request = Callable[[str, str, dict, bytes | None], bytes]

ANCHOR_PORTRAIT_PROMPT = (
    "Photorealistic medium close-up portrait of a fictional East Asian financial news anchor in her early 30s, "
    "seated at a modern TV news desk, facing the camera directly, neutral friendly expression, mouth closed, "
    "navy blazer over a white blouse, shoulder-length dark hair, natural makeup, soft broadcast studio lighting, "
    "blurred dark-blue newsroom background with subtle market screens, 16:9 framing with head and shoulders centred, "
    "sharp focus on the face, real skin texture. Not a real or famous person. No text, no logos, no watermark."
)


class RunwayError(RuntimeError):
    pass


def _http(method: str, url: str, headers: dict, body: bytes | None) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def data_uri(path: Path, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


class RunwayClient:
    def __init__(self, api_key: str | None = None, request: Request | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.api_key = (api_key or os.environ.get("RUNWAYML_API_SECRET", "")).strip()
        self.request = request or _http
        self.sleep = sleep

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _call(self, method: str, path: str, payload: dict | None = None) -> dict:
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Runway-Version": VERSION,
                   "Content-Type": "application/json"}
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            raw = self.request(method, BASE + path, headers, body)
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode(errors="replace")
            raise RunwayError(f"Runway {method} {path} → HTTP {e.code} {detail}") from e
        return json.loads(raw) if raw else {}

    # --- avatar (one-time) ---
    def create_avatar(self, name: str, portrait: Path, voice_preset: str = "victoria") -> str:
        d = self._call("POST", "/v1/avatars", {
            "name": name,
            "personality": "A calm, professional Chinese-language financial news presenter.",
            "referenceImage": data_uri(portrait, "image/jpeg" if portrait.suffix.lower() in (".jpg", ".jpeg") else "image/png"),
            "voice": {"type": "runway-live-preset", "presetId": voice_preset},
        })
        return d["id"]

    def wait_avatar(self, avatar_id: str, timeout_s: int = 900) -> dict:
        deadline = time.time() + timeout_s
        while True:
            d = self._call("GET", f"/v1/avatars/{avatar_id}")
            if d.get("status") == "READY":
                return d
            if d.get("status") == "FAILED":
                raise RunwayError(f"avatar failed: {d.get('failureReason') or d}")
            if time.time() > deadline:
                raise RunwayError("avatar not ready in time")
            self.sleep(10)

    # --- per segment ---
    def avatar_video(self, avatar_id: str, audio: Path, timeout_s: int = 900) -> str:
        task = self._call("POST", "/v1/avatar_videos", {
            "model": "gwm1_avatars",
            "avatar": {"type": "custom", "avatarId": avatar_id},
            "speech": {"type": "audio", "audio": data_uri(audio, "audio/mpeg")},
        })
        return self.wait_task(task["id"], timeout_s)[0]

    def wait_task(self, task_id: str, timeout_s: int = 900) -> list[str]:
        deadline = time.time() + timeout_s
        while True:
            t = self._call("GET", f"/v1/tasks/{task_id}")
            status = t.get("status")
            if status == "SUCCEEDED":
                return list(t.get("output") or [])
            if status in ("FAILED", "CANCELLED"):
                raise RunwayError(f"task {status}: {t.get('failure') or t.get('failureCode') or ''}")
            if time.time() > deadline:
                raise RunwayError(f"task {task_id} timed out ({status})")
            self.sleep(5)


def generate_portrait(out: Path, api_key: str | None = None, post: Callable | None = None) -> Path:
    """Fictional presenter portrait via OpenAI images, saved as JPEG (< 5 MB for Runway)."""
    from PIL import Image

    key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
    body = json.dumps({"model": os.environ.get("FINANCE_IMAGE_MODEL", "gpt-image-1"),
                       "prompt": ANCHOR_PORTRAIT_PROMPT, "size": "1536x1024", "n": 1}).encode()
    send = post or (lambda url, h, b: _http("POST", url, h, b))
    raw = json.loads(send("https://api.openai.com/v1/images/generations",
                          {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}, body))
    img = Image.open(io.BytesIO(base64.b64decode(raw["data"][0]["b64_json"]))).convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, "JPEG", quality=92)
    return out
