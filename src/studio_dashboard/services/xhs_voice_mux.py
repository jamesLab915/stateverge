"""
XHS narration -> ElevenLabs TTS -> mux onto stitched video.

Reads from .env:
    ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID, ELEVENLABS_STABILITY, ELEVENLABS_SIMILARITY_BOOST,
    ELEVENLABS_STYLE, ELEVENLABS_USE_SPEAKER_BOOST

Reads:
    assets/xhs/<note_id>/narration_zh.txt

Writes (under the same note folder):
    narration_zh.mp3                    1-min Chinese narration (concat of chunks)
    final_with_voice_<sourcestem>.mp4   Source video + narration audio (when mux_into is set)

Heavy lifting (text chunking, ElevenLabs HTTP call, ffmpeg concat) is delegated
to scripts/generate_eleven_tts_for_topic.py — same module the existing
/api/create/voice endpoint uses, so we don't fork TTS logic.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


FFMPEG_BIN = os.environ.get("FFMPEG_BIN") or shutil.which("ffmpeg") or "ffmpeg"


def _load_eleven_module(repo_root: Path):
    """Same trick the /create voice endpoint uses; keeps a single TTS impl."""
    src = repo_root / "scripts" / "generate_eleven_tts_for_topic.py"
    if not src.is_file():
        raise FileNotFoundError(f"missing {src}")
    spec = importlib.util.spec_from_file_location("_sv_eleven_xhs", src)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {src}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------


@dataclass
class VoiceMuxResult:
    ok: bool
    note_id: str
    narration_chars: int = 0
    chunks: int = 0
    voice_mp3_rel: str = ""
    voice_duration_sec: float = 0.0
    voice_id_tail: str = ""
    model_id: str = ""

    muxed_video_rel: str = ""
    muxed_into_source: str = ""
    muxed_duration_sec: float = 0.0

    duration_ms: int = 0
    log_tail: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "note_id": self.note_id,
            "narration_chars": self.narration_chars,
            "chunks": self.chunks,
            "voice_mp3_rel": self.voice_mp3_rel,
            "voice_duration_sec": round(self.voice_duration_sec, 2),
            "voice_id_tail": self.voice_id_tail,
            "model_id": self.model_id,
            "muxed_video_rel": self.muxed_video_rel,
            "muxed_into_source": self.muxed_into_source,
            "muxed_duration_sec": round(self.muxed_duration_sec, 2),
            "duration_ms": self.duration_ms,
            "log_tail": self.log_tail[-40:],
            "error": self.error,
        }


def _tail(s: str) -> str:
    s = (s or "").strip()
    return ("…" + s[-4:]) if len(s) >= 4 else ""


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_voice_and_optional_mux(
    *,
    repo_root: Path,
    note_id: str,
    mux_into: str | None = None,
    output_name: str | None = None,
    chunk_max_chars: int = 2500,
) -> VoiceMuxResult:
    """TTS narration_zh.txt; if mux_into is given, mux it onto that video."""
    t0 = time.monotonic()
    result = VoiceMuxResult(ok=False, note_id=note_id)

    api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    if not api_key:
        result.error = "ELEVENLABS_API_KEY missing in .env"
        return result
    if not voice_id:
        result.error = "ELEVENLABS_VOICE_ID missing in .env (refusing to auto-pick)"
        return result
    result.voice_id_tail = _tail(voice_id)

    note_dir = repo_root / "assets" / "xhs" / note_id
    if not note_dir.is_dir():
        result.error = f"note folder not found: {note_dir}"
        return result

    narration_path = note_dir / "narration_zh.txt"
    if not narration_path.is_file():
        result.error = (
            "narration_zh.txt not found; run xhs_storyboard.py for this note first"
        )
        return result
    text = narration_path.read_text(encoding="utf-8").strip()
    if not text:
        result.error = "narration_zh.txt is empty"
        return result
    result.narration_chars = len(text)

    try:
        eleven = _load_eleven_module(repo_root)
    except (FileNotFoundError, RuntimeError) as exc:
        result.error = str(exc)
        return result

    model_id = (
        (os.environ.get("ELEVENLABS_MODEL_ID") or "").strip()
        or eleven.DEFAULT_MODEL_ID
    )
    result.model_id = model_id
    voice_settings = eleven.voice_settings_from_env()

    # ---- Render TTS chunks ---------------------------------------------------
    chunks_dir = note_dir / "_voice_chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    for old in chunks_dir.glob("chunk_*.mp3"):
        try:
            old.unlink()
        except OSError:
            pass

    try:
        chunks = eleven.chunk_text(text, max_chars=int(chunk_max_chars))
    except Exception as exc:  # noqa: BLE001
        result.error = f"chunk_text failed: {exc}"
        return result

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
        except RuntimeError as exc:
            result.error = f"chunk {i:03d}/{len(chunks)} failed: {exc}"
            result.duration_ms = int((time.monotonic() - t0) * 1000)
            return result
        chunk_paths.append(out)
        time.sleep(0.15)

    voice_mp3 = note_dir / "narration_zh.mp3"
    try:
        eleven.ffmpeg_concat_mp3(chunk_paths, voice_mp3)
    except (RuntimeError, ValueError) as exc:
        result.error = f"ffmpeg concat: {exc}"
        result.duration_ms = int((time.monotonic() - t0) * 1000)
        return result

    try:
        info = eleven.ffprobe_info(voice_mp3)
        result.voice_duration_sec = float(info.get("duration_sec") or 0.0)
    except RuntimeError:
        result.voice_duration_sec = 0.0

    result.chunks = len(chunk_paths)
    result.voice_mp3_rel = str(voice_mp3.relative_to(repo_root)).replace(os.sep, "/")
    result.ok = True

    # ---- Optional mux onto a target video ------------------------------------
    if mux_into:
        src = (note_dir / mux_into).resolve()
        try:
            src.relative_to(note_dir.resolve())
        except ValueError:
            result.error = "mux_into must be a filename inside the note folder"
            result.ok = False
            return result
        if not src.is_file():
            result.error = f"mux source video not found: {src.name}"
            result.ok = False
            return result

        out_name = (output_name or "").strip()
        if not out_name:
            stem = src.stem
            out_name = f"final_with_voice_{stem}.mp4"
        if not out_name.lower().endswith((".mp4", ".mov", ".mkv")):
            out_name += ".mp4"
        muxed = note_dir / out_name
        result.muxed_into_source = src.name

        ok, log_tail, dur = _mux_audio_onto_video(src, voice_mp3, muxed)
        result.log_tail = log_tail
        if not ok:
            result.error = "ffmpeg mux failed; see log_tail"
            result.ok = False
        else:
            result.muxed_video_rel = str(muxed.relative_to(repo_root)).replace(os.sep, "/")
            result.muxed_duration_sec = dur

    result.duration_ms = int((time.monotonic() - t0) * 1000)
    return result


# ---------------------------------------------------------------------------
# ffmpeg mux
# ---------------------------------------------------------------------------


def _mux_audio_onto_video(
    video: Path,
    audio: Path,
    out_path: Path,
) -> tuple[bool, list[str], float]:
    """Replace the video's audio track with `audio`, keeping video as-is.

    Strategy:
      - Stream-copy the video (no re-encode -> fast, lossless)
      - Re-encode audio to AAC 128k (Runway clips often have mismatched audio)
      - `-shortest` so the file ends at the shorter of (video, narration)

    If the video is shorter than the narration, this clips the narration tail.
    If the narration is shorter, the video tail will play silent — that's the
    common case for a 60s scene-cut with a ~50-55s narration, and it sounds
    natural (the visual closes; the voice has already wrapped).
    """
    cmd = [
        FFMPEG_BIN, "-y", "-loglevel", "error",
        "-i", str(video),
        "-i", str(audio),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, [f"ffmpeg invocation failed: {exc}"], 0.0
    log_tail = (proc.stderr or "").strip().splitlines()[-30:]
    if proc.returncode != 0 or not out_path.is_file():
        return False, log_tail, 0.0
    # Probe final duration.
    dur = _probe_duration(out_path)
    return True, log_tail, dur


def _probe_duration(path: Path) -> float:
    try:
        proc = subprocess.run(
            [
                shutil.which("ffprobe") or "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True, text=True, timeout=10,
        )
        return float((proc.stdout or "0").strip() or 0)
    except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Helpers used by the dashboard /xhs page render
# ---------------------------------------------------------------------------


def env_status() -> dict:
    api_key = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
    voice_id = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
    model_id = (
        (os.environ.get("ELEVENLABS_MODEL_ID") or "").strip()
        or "eleven_multilingual_v2"
    )
    return {
        "configured": bool(api_key) and bool(voice_id),
        "key_present": bool(api_key),
        "voice_present": bool(voice_id),
        "model_id": model_id,
        "voice_id_tail": _tail(voice_id),
    }
