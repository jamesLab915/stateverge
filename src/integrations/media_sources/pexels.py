"""Pexels Video API — search and download (Authorization header)."""

from __future__ import annotations

import os
from typing import Any

import requests

from . import LOG_PREFIX, download_stream_to_path, load_env_from_dotenv_file

_PEXELS_VIDEO = "https://api.pexels.com/videos/search"


def _env_key() -> str:
    return (os.environ.get("PEXELS_API_KEY") or "").strip()


def _log_result(query: str, result_count: int) -> None:
    print(
        f"{LOG_PREFIX} query={query!r} source=pexels result_count={result_count}",
        flush=True,
    )


def _is_mp4(vf: Any) -> bool:
    if not isinstance(vf, dict) or not vf.get("link"):
        return False
    ft = (vf.get("file_type") or "").lower()
    return "mp4" in ft or str(vf.get("link", "")).lower().endswith(".mp4")


def search(query: str, per_page: int = 5) -> list[dict[str, Any]]:
    load_env_from_dotenv_file()
    k = _env_key()
    if not k:
        print(
            f"{LOG_PREFIX} warning: missing PEXELS_API_KEY, skipping pexels",
            flush=True,
        )
        _log_result(query, 0)
        return []
    n = max(1, min(80, int(per_page)))
    try:
        r = requests.get(
            _PEXELS_VIDEO,
            params={"query": query, "per_page": n},
            headers={"Authorization": k},
            timeout=30,
        )
        r.raise_for_status()
        data: dict[str, Any] = r.json()
    except Exception as e:  # noqa: BLE001
        print(
            f"{LOG_PREFIX} query={query!r} source=pexels error={e!r}",
            flush=True,
        )
        return []
    out: list[dict[str, Any]] = []
    for v in data.get("videos") or []:
        if v.get("id") is None:
            continue
        mps = [x for x in (v.get("video_files") or []) if _is_mp4(x)]
        pool: list[dict[str, Any]] = mps if mps else [
            x for x in (v.get("video_files") or []) if (x or {}).get("link")
        ]
        best: dict[str, Any] | None = None
        best_px = -1
        for vf in pool:
            w = int((vf or {}).get("width") or 0)
            h = int((vf or {}).get("height") or 0)
            if w * h > best_px:
                best, best_px = vf, w * h
        if not (best and best.get("link")):
            continue
        w0 = int(best.get("width") or 0)
        h0 = int(best.get("height") or 0)
        u0 = v.get("user")
        udict = u0 if isinstance(u0, dict) else {}
        out.append(
            {
                "source": "pexels",
                "kind": "video",
                "title": str(
                    udict.get("name") or f"pexels-video-{v.get('id', '')}"
                )[:200],
                "url": str(v.get("url") or "https://www.pexels.com/"),
                "download_url": str(best["link"]),
                "width": w0,
                "height": h0,
                "duration": int(v.get("duration") or 0),
                "author": str(udict.get("name") or udict.get("url") or ""),
                "license": "Pexels License (free to use, see pexels.com/license)",
                "raw": v,
            }
        )
    _log_result(query, len(out))
    return out


def download(url: str, output_path: str, *, force: bool = False) -> str:
    return download_stream_to_path(
        url, output_path, "pexels", force=force, timeout=30
    )
