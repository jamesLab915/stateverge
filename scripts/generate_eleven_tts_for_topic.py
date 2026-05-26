"""
Generate ElevenLabs Chinese narration for a StateVerge topic.

Reads:
    topics/<topic>/brief/narration_script.txt
    .env (ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID required;
          ELEVENLABS_MODEL_ID, ELEVENLABS_STABILITY, ELEVENLABS_SIMILARITY_BOOST,
          ELEVENLABS_STYLE, ELEVENLABS_USE_SPEAKER_BOOST optional)

Writes:
    topics/<topic>/audio/chunks/chunk_NNN.mp3   (one per text chunk)
    topics/<topic>/audio/voice.mp3              (concatenated final mp3)
    topics/<topic>/audio/voice.wav              (48kHz stereo wav)

Old voice.mp3 / voice.wav are moved to topics/<topic>/audio/archive/<ts>/
before regeneration.

Usage:
    python scripts/generate_eleven_tts_for_topic.py --topic ai_future_cn
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
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

LOG_PREFIX = "[eleven_tts]"
ELEVEN_TTS_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_MAX_CHARS = 2500
DEFAULT_MODEL_ID = "eleven_multilingual_v2"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_dotenv(root: Path) -> None:
    """Load .env from repo root. Uses python-dotenv if present, else manual parse."""
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


def env_float(name: str, default: float) -> float:
    v = env_str(name)
    if not v:
        return default
    try:
        return float(v)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    v = env_str(name).lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "y", "on")


def voice_settings_from_env() -> dict[str, Any]:
    return {
        "stability": env_float("ELEVENLABS_STABILITY", 0.5),
        "similarity_boost": env_float("ELEVENLABS_SIMILARITY_BOOST", 0.8),
        "style": env_float("ELEVENLABS_STYLE", 0.0),
        "use_speaker_boost": env_bool("ELEVENLABS_USE_SPEAKER_BOOST", True),
    }


def chunk_text(text: str, max_chars: int) -> list[str]:
    """
    Pack paragraphs (split on blank lines) into chunks ≤ max_chars.
    A paragraph longer than max_chars is split at Chinese/Latin sentence ends
    (。！？!?.) without breaking inside a sentence when possible.
    """
    paras = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    def split_long_para(p: str) -> list[str]:
        if len(p) <= max_chars:
            return [p]
        sents = re.split(r"(?<=[。！？!?\.])", p)
        out: list[str] = []
        cur = ""
        for s in sents:
            if not s.strip():
                continue
            if len(cur) + len(s) > max_chars:
                if cur:
                    out.append(cur)
                if len(s) > max_chars:
                    for i in range(0, len(s), max_chars):
                        out.append(s[i : i + max_chars])
                    cur = ""
                else:
                    cur = s
            else:
                cur += s
        if cur:
            out.append(cur)
        return out

    for p in paras:
        for piece in split_long_para(p):
            sep = "\n\n" if current else ""
            if len(current) + len(sep) + len(piece) > max_chars:
                flush()
                current = piece
            else:
                current += sep + piece
    flush()
    return chunks


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def archive_existing_voice(audio_dir: Path) -> Path | None:
    targets = [audio_dir / "voice.mp3", audio_dir / "voice.wav"]
    found = [t for t in targets if t.is_file()]
    if not found:
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive = ensure_dir(audio_dir / "archive" / ts)
    for t in found:
        dst = archive / t.name
        shutil.move(str(t), str(dst))
        log(f"archived {t.name} -> audio/archive/{ts}/{t.name}")
    return archive


def request_chunk_mp3(
    text: str,
    *,
    api_key: str,
    voice_id: str,
    model_id: str,
    voice_settings: dict[str, Any],
    out_path: Path,
    timeout: int = 300,
    retries: int = 2,
) -> None:
    url = f"{ELEVEN_TTS_BASE}/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/*",
    }
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": voice_settings,
    }
    params = {"output_format": OUTPUT_FORMAT}
    last_err: str = ""
    for attempt in range(1, retries + 2):
        try:
            r = requests.post(
                url, headers=headers, params=params, json=body, timeout=timeout
            )
        except requests.RequestException as e:
            last_err = f"network: {e}"
            if attempt <= retries:
                time.sleep(1.5 * attempt)
                continue
            raise RuntimeError(last_err) from e
        if r.status_code == 200:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_bytes(r.content)
            return
        snippet = (r.text or "")[:400]
        last_err = f"HTTP {r.status_code}: {snippet}"
        if r.status_code in (429, 500, 502, 503, 504) and attempt <= retries:
            time.sleep(2.0 * attempt)
            continue
        raise RuntimeError(last_err)
    raise RuntimeError(last_err or "unknown error")


def ffmpeg_concat_mp3(chunk_paths: list[Path], out_mp3: Path) -> None:
    """
    Concatenate same-encoded mp3 chunks using ffmpeg concat demuxer + -c copy.
    All ElevenLabs chunks share mp3_44100_128, so stream copy is safe.
    """
    if not chunk_paths:
        raise ValueError("no chunks to concat")
    if len(chunk_paths) == 1:
        shutil.copy2(str(chunk_paths[0]), str(out_mp3))
        return
    list_file = out_mp3.parent / "_concat_list.txt"
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in chunk_paths)
        + "\n",
        encoding="utf-8",
    )
    try:
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0",
            "-i", str(list_file),
            "-c", "copy",
            str(out_mp3),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                f"ffmpeg concat failed: {(r.stderr or '')[:600]}"
            )
    finally:
        try:
            list_file.unlink()
        except OSError:
            pass


def ffmpeg_mp3_to_wav(in_mp3: Path, out_wav: Path) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(in_mp3),
        "-ar", "48000",
        "-ac", "2",
        str(out_wav),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg mp3->wav failed: {(r.stderr or '')[:600]}")


def ffprobe_info(path: Path) -> dict[str, Any]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries",
        "format=duration,size:stream=codec_name,sample_rate,channels",
        "-of", "json",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {(r.stderr or '')[:400]}")
    j = json.loads(r.stdout or "{}")
    fmt = j.get("format") or {}
    streams = j.get("streams") or []
    s0 = streams[0] if streams else {}
    return {
        "path": str(path),
        "duration_sec": float(fmt.get("duration") or 0.0),
        "size_bytes": int(fmt.get("size") or 0),
        "codec_name": s0.get("codec_name", ""),
        "sample_rate": int(s0.get("sample_rate") or 0),
        "channels": int(s0.get("channels") or 0),
    }


def fmt_duration(secs: float) -> str:
    s = int(round(secs))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{ss:02d}"
    return f"{m}:{ss:02d}"


def run(args: argparse.Namespace) -> int:
    root = repo_root()
    load_dotenv(root)

    api_key = env_str("ELEVENLABS_API_KEY")
    voice_id = env_str("ELEVENLABS_VOICE_ID")
    model_id = env_str("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID)
    if not api_key:
        print(
            f"{LOG_PREFIX} error: ELEVENLABS_API_KEY missing in {root}/.env",
            file=sys.stderr,
        )
        return 1
    if not voice_id:
        print(
            f"{LOG_PREFIX} error: ELEVENLABS_VOICE_ID missing in {root}/.env "
            f"(set it explicitly; refusing to pick a default voice).",
            file=sys.stderr,
        )
        return 1

    topic = args.topic
    tdir = root / "topics" / topic
    script_path = tdir / "brief" / "narration_script.txt"
    audio_dir = ensure_dir(tdir / "audio")
    chunks_dir = ensure_dir(audio_dir / "chunks")
    voice_mp3 = audio_dir / "voice.mp3"
    voice_wav = audio_dir / "voice.wav"

    if not script_path.is_file():
        print(
            f"{LOG_PREFIX} error: narration not found at {script_path}",
            file=sys.stderr,
        )
        return 1

    text = script_path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"{LOG_PREFIX} error: narration is empty", file=sys.stderr)
        return 1

    chunks = chunk_text(text, max_chars=int(args.max_chars))
    log(
        f"topic={topic} chars={len(text)} paragraphs="
        f"{sum(1 for p in re.split(r'\n\s*\n+', text) if p.strip())} "
        f"chunks={len(chunks)} max_chars={args.max_chars}"
    )
    log(f"voice_id={voice_id} model_id={model_id}")

    archive_existing_voice(audio_dir)

    if not args.reuse_chunks:
        for old in chunks_dir.glob("chunk_*.mp3"):
            try:
                old.unlink()
            except OSError:
                pass

    chunk_paths: list[Path] = []
    voice_settings = voice_settings_from_env()
    for i, c in enumerate(chunks, start=1):
        out = chunks_dir / f"chunk_{i:03d}.mp3"
        if args.reuse_chunks and out.is_file() and out.stat().st_size > 0:
            log(f"chunk {i:03d}/{len(chunks)} reuse_existing chars={len(c)} file={out.name}")
            chunk_paths.append(out)
            continue
        log(f"chunk {i:03d}/{len(chunks)} chars={len(c)} -> POST elevenlabs ...")
        try:
            request_chunk_mp3(
                c,
                api_key=api_key,
                voice_id=voice_id,
                model_id=model_id,
                voice_settings=voice_settings,
                out_path=out,
            )
        except RuntimeError as e:
            print(
                f"{LOG_PREFIX} error: chunk {i:03d} failed: {e}",
                file=sys.stderr,
            )
            return 2
        size_kb = out.stat().st_size / 1024
        log(f"chunk {i:03d}/{len(chunks)} ok size={size_kb:.1f} KiB file={out.name}")
        chunk_paths.append(out)
        time.sleep(0.2)

    log(f"concat {len(chunk_paths)} chunks -> {voice_mp3.name}")
    try:
        ffmpeg_concat_mp3(chunk_paths, voice_mp3)
    except RuntimeError as e:
        print(f"{LOG_PREFIX} error: {e}", file=sys.stderr)
        return 3

    log(f"transcode -> {voice_wav.name} (48000Hz stereo)")
    try:
        ffmpeg_mp3_to_wav(voice_mp3, voice_wav)
    except RuntimeError as e:
        print(f"{LOG_PREFIX} error: {e}", file=sys.stderr)
        return 4

    log("ffprobe verification:")
    for p in (voice_mp3, voice_wav):
        try:
            info = ffprobe_info(p)
        except RuntimeError as e:
            print(f"{LOG_PREFIX} ffprobe error: {e}", file=sys.stderr)
            return 5
        rel = p.relative_to(root)
        size_mb = info["size_bytes"] / (1024 * 1024)
        log(
            f"  {rel}  duration={fmt_duration(info['duration_sec'])} "
            f"({info['duration_sec']:.2f}s)  codec={info['codec_name']}  "
            f"sample_rate={info['sample_rate']}Hz  channels={info['channels']}  "
            f"size={size_mb:.2f} MiB"
        )

    log("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Generate ElevenLabs narration audio for a StateVerge topic; "
            "auto-chunks long text and concatenates back to one mp3 + wav."
        )
    )
    ap.add_argument("--topic", required=True, help="Topic slug under topics/")
    ap.add_argument(
        "--max-chars",
        type=int,
        default=DEFAULT_MAX_CHARS,
        help=f"Max characters per ElevenLabs request (default {DEFAULT_MAX_CHARS}).",
    )
    ap.add_argument(
        "--reuse-chunks",
        action="store_true",
        help="Skip chunk_*.mp3 that already exist (resume after failure).",
    )
    ns = ap.parse_args(argv)
    return run(ns)


if __name__ == "__main__":
    raise SystemExit(main())
