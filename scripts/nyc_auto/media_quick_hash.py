"""Chunked file fingerprinting for upload dedupe (no full-file memory load)."""
from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK = 16 * 1024 * 1024  # 16 MiB
_FULL_READ_THRESHOLD = 48 * 1024 * 1024  # 48 MiB


def triple_chunk_sha256(path: Path) -> str:
    """SHA256 of sampled bytes: first 16MB + middle 16MB + last 16MB; full file if size < 48MB.

    Reads are streamed in fixed-size blocks to avoid loading large files into memory.
    """
    h = hashlib.sha256()
    try:
        size = path.stat().st_size
    except OSError:
        h.update(str(path).encode("utf-8", errors="replace"))
        return h.hexdigest()

    if size <= 0:
        h.update(b"empty")
        return h.hexdigest()

    def _update_range(fh, start: int, length: int) -> None:
        fh.seek(start)
        remain = min(length, max(0, size - start))
        block = 1024 * 1024
        while remain > 0:
            n = min(block, remain)
            b = fh.read(n)
            if not b:
                break
            h.update(b)
            remain -= len(b)

    try:
        with path.open("rb") as fh:
            if size < _FULL_READ_THRESHOLD:
                remain = size
                block = 1024 * 1024
                while remain > 0:
                    n = min(block, remain)
                    b = fh.read(n)
                    if not b:
                        break
                    h.update(b)
                    remain -= len(b)
            else:
                _update_range(fh, 0, _CHUNK)
                mid_start = max(0, (size - _CHUNK) // 2)
                _update_range(fh, mid_start, _CHUNK)
                tail_start = max(0, size - _CHUNK)
                _update_range(fh, tail_start, _CHUNK)
    except OSError:
        h.update(str(path).encode("utf-8", errors="replace"))

    return h.hexdigest()


def content_key_v2(*, size_bytes: int, duration_sec: float, quick_hash: str, basename_normalized: str) -> str:
    body = f"{size_bytes}|{round(float(duration_sec))}|{quick_hash}|{basename_normalized}"
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def normalize_basename(name: str) -> str:
    return name.strip().lower()
