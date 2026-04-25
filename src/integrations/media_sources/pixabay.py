"""Pixabay API — search videos and images, download.

Videos are requested first; if fewer than *per_page*, images fill the rest.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from . import LOG_PREFIX, download_stream_to_path, load_env_from_dotenv_file

_VIDEOS = "https://pixabay.com/api/videos/"
_IMAGES = "https://pixabay.com/api/"


def _key() -> str:
    return (os.environ.get("PIXABAY_API_KEY") or "").strip()


def _log_result(query: str, result_count: int) -> None:
    print(
        f"{LOG_PREFIX} query={query!r} source=pixabay result_count={result_count}",
        flush=True,
    )


def search(query: str, per_page: int = 5) -> list[dict[str, Any]]:
    load_env_from_dotenv_file()
    k = _key()
    if not k:
        print(
            f"{LOG_PREFIX} warning: missing PIXABAY_API_KEY, skipping pixabay",
            flush=True,
        )
        _log_result(query, 0)
        return []
    n = max(1, min(200, int(per_page)))
    out: list[dict[str, Any]] = []
    try:
        r = requests.get(
            _VIDEOS,
            params={"key": k, "q": query, "per_page": n},
            timeout=30,
        )
        r.raise_for_status()
        d1 = r.json()
        for h in d1.get("hits") or []:
            vdata = h.get("videos") or {}
            best: dict[str, Any] = {}
            best_px = -1
            for _name, blob in vdata.items():
                if not isinstance(blob, dict) or not blob.get("url"):
                    continue
                w = int(blob.get("width") or 0)
                h0 = int(blob.get("height") or 0)
                px = w * h0
                if px > best_px:
                    best, best_px = blob, px
            if not best.get("url"):
                continue
            out.append(
                {
                    "source": "pixabay",
                    "kind": "video",
                    "title": str(h.get("tags") or h.get("id") or "pixabay")[:200],
                    "url": str(h.get("pageURL") or "https://pixabay.com/"),
                    "download_url": str(best["url"]),
                    "width": int(best.get("width") or 0),
                    "height": int(best.get("height") or 0),
                    "duration": int(h.get("duration") or 0),
                    "author": str(h.get("user") or ""),
                    "license": "Pixabay License (see pixabay.com/service/license)",
                    "raw": h,
                }
            )
    except Exception as e:  # noqa: BLE001
        print(
            f"{LOG_PREFIX} query={query!r} source=pixabay videos error={e!r}",
            flush=True,
        )
    need = n - len(out)
    if need > 0:
        try:
            r2 = requests.get(
                _IMAGES,
                params={
                    "key": k,
                    "q": query,
                    "per_page": need,
                    "image_type": "photo",
                },
                timeout=30,
            )
            r2.raise_for_status()
            d2 = r2.json()
            for h in d2.get("hits") or []:
                url = str(
                    h.get("largeImageURL")
                    or h.get("imageURL")
                    or h.get("webformatURL")
                    or ""
                )
                if not url:
                    continue
                out.append(
                    {
                        "source": "pixabay",
                        "kind": "image",
                        "title": str(h.get("tags") or h.get("id") or "pixabay")[:200],
                        "url": str(h.get("pageURL") or "https://pixabay.com/"),
                        "download_url": url,
                        "width": int(h.get("imageWidth") or 0),
                        "height": int(h.get("imageHeight") or 0),
                        "duration": 0,
                        "author": str(h.get("user") or ""),
                        "license": "Pixabay License (see pixabay.com/service/license)",
                        "raw": h,
                    }
                )
        except Exception as e:  # noqa: BLE001
            print(
                f"{LOG_PREFIX} query={query!r} source=pixabay images error={e!r}",
                flush=True,
            )
    out = out[:n]
    _log_result(query, len(out))
    return out


def download(url: str, output_path: str, *, force: bool = False) -> str:
    return download_stream_to_path(
        url, output_path, "pixabay", force=force, timeout=30
    )
