"""
StateVerge unified media sources (Pexels, Pixabay, DVIDS).

Shared logging helpers and a stream downloader used by ``pexels`` / ``pixabay`` / ``dvids``.
Do not import production / presenter_pipeline / mix_engine from here.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

LOG_PREFIX = "[media_source]"


def default_repo_root() -> Path:
    """``StateVerge/`` (parent of ``src/``)."""
    return Path(__file__).resolve().parents[3]


def load_env_from_dotenv_file(root: Path | None = None) -> None:
    """Load ``.env`` from repo root. Prefer ``python-dotenv``; else parse key=value lines."""
    r = (root or default_repo_root()).resolve()
    p = r / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(p, override=False)
    except ImportError:
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


def safe_base_filename(stem: str, max_len: int = 72) -> str:
    """URL-safeish single path segment: no spaces or OS-forbidden characters."""
    s = re.sub(r'[<>:"/\\|?*\s\u0000-\u001f]+', "_", stem, flags=re.UNICODE)
    s = re.sub(r"_{2,}", "_", s).strip("._-")
    if not s:
        s = "file"
    if len(s) > max_len:
        s = s[:max_len]
    return s


def download_stream_to_path(
    url: str,
    output_path: str,
    source: str,
    *,
    force: bool = False,
    timeout: int = 30,
) -> str:
    """
    Stream download with requests. Skips if file exists, size>0, unless *force*.

    Logs ``[media_source] skip existing`` / ``[media_source] download``.
    """
    import requests  # local import to keep package import light

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    pstr = str(out)
    if out.is_file() and out.stat().st_size > 0 and not force:
        print(
            f"{LOG_PREFIX} skip existing file={pstr}",
            flush=True,
        )
        return pstr
    if not (url and url.strip().lower().startswith("http")):
        print(
            f"{LOG_PREFIX} download skipped (invalid url) source={source}",
            flush=True,
        )
        return pstr
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with out.open("wb") as f:
                for chunk in r.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        f.write(chunk)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            f"{LOG_PREFIX} download failed source={source!r} file={pstr!r}: {e!r}"
        ) from e
    print(
        f"{LOG_PREFIX} download source={source} file={pstr}",
        flush=True,
    )
    return pstr
