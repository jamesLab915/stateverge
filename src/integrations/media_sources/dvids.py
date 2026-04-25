"""DVIDS Search API (api.dvidshub.net) — search and /asset for direct files."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

import requests

from . import LOG_PREFIX, download_stream_to_path, load_env_from_dotenv_file

_BASE = "https://api.dvidshub.net"


def _key() -> str:
    return (os.environ.get("DVIDS_API_KEY") or "").strip()


def _log_result(query: str, result_count: int) -> None:
    print(
        f"{LOG_PREFIX} query={query!r} source=dvids result_count={result_count}",
        flush=True,
    )


def _first_file_url(obj: Any) -> Optional[str]:
    if isinstance(obj, str) and obj.startswith("http"):
        s = obj.lower()
        if any(
            s.endswith(x) for x in (".mp4", ".m3u8", ".m4v", ".jpg", ".jpeg", ".png", ".webm")
        ) or "cloudfront" in s:
            return obj
    if isinstance(obj, dict):
        for v in obj.values():
            u = _first_file_url(v)
            if u:
                return u
    if isinstance(obj, list):
        for it in obj:
            u = _first_file_url(it)
            if u:
                return u
    return None


def _parse_asset(data: dict[str, Any]) -> tuple[Optional[str], int, int]:
    root = data.get("results")
    if isinstance(root, list) and root:
        asset: Any = root[0]
    else:
        asset = data.get("result") or data
    w, h = 0, 0
    u: str | None = None
    if isinstance(asset, dict):
        w = int(asset.get("width") or asset.get("video_width") or 0)
        h = int(asset.get("height") or asset.get("video_height") or 0)
        u = _first_file_url(asset)
        if not u and str(asset.get("hls_url") or "").startswith("http"):
            u = str(asset.get("hls_url"))
    if not u and isinstance(data, dict):
        blob = json.dumps(data, ensure_ascii=False)
        m = re.findall(
            r"https?://[^\s\"'<>\\]+\.(?:mp4|m3u8|jpe?g|png|webm)(?:\?[^\"'\\s]+)?",
            blob,
            re.I,
        )
        if m:
            u = m[0]
    if u and not u.startswith("http"):
        u = None
    return u, w, h


def _asset_fetch(
    api_key: str, asset_id: str
) -> tuple[Optional[str], int, int, dict[str, Any]]:
    try:
        r = requests.get(
            f"{_BASE}/asset",
            params={"id": asset_id, "api_key": api_key, "format": "json"},
            timeout=30,
        )
        if r.status_code in (400, 403, 404):
            return None, 0, 0, {}
        r.raise_for_status()
        d: dict[str, Any] = r.json()
    except Exception:  # noqa: BLE001
        return None, 0, 0, {}
    u, w, h = _parse_asset(d)
    if not u and isinstance(d, dict):
        u = _first_file_url(d)
    return u, w, h, d if isinstance(d, dict) else {}


def _search_raw(
    query: str, per_page: int, type_: str, api_key: str
) -> list[dict[str, Any]]:
    m = min(50, max(1, int(per_page)))
    params: dict[str, Any] = {
        "q": query,
        "type": type_,
        "max_results": m,
        "format": "json",
    }
    if api_key:
        params["api_key"] = api_key
    try:
        r = requests.get(f"{_BASE}/search", params=params, timeout=30)
    except Exception as e:  # noqa: BLE001
        print(
            f"{LOG_PREFIX} query={query!r} source=dvids network_error={e!r}",
            flush=True,
        )
        return []
    if r.status_code == 403 and not api_key:
        print(
            f"{LOG_PREFIX} DVIDS: HTTP 403 without key —"
            f" get a public API key at https://api.dvidshub.net/ and set DVIDS_API_KEY",
            flush=True,
        )
        return []
    if r.status_code == 403 and api_key:
        print(
            f"{LOG_PREFIX} DVIDS: HTTP 403 (check DVIDS_API_KEY / domain allowlist)",
            flush=True,
        )
        return []
    if r.status_code != 200:
        print(
            f"{LOG_PREFIX} DVIDS: HTTP {r.status_code} (truncated): {r.text[:180]!r}",
            flush=True,
        )
        return []
    d: dict[str, Any] = r.json()
    return list(d.get("results") or [])


def _row_to_dict(
    row: dict[str, Any], query: str, api_key: str, asset_cap: int, asset_n: list[int]
) -> dict[str, Any] | None:
    typ = str(row.get("type") or "")
    if typ not in ("video", "image"):
        return None
    page = str(row.get("url") or "")
    title = (str(row.get("title") or row.get("short_description") or query)[:500])
    w, h = int(row.get("width") or 0), int(row.get("height") or 0)
    duration = int(row.get("duration") or 0)
    author = str(row.get("credit") or row.get("unit_name") or row.get("branch") or "DVIDS")
    durl: str = ""
    aid = row.get("id")

    if typ == "image":
        durl = str(row.get("thumbnail") or "")
        if not durl or not durl.startswith("http"):
            durl = ""
        if api_key and aid and asset_n[0] < asset_cap and str(aid).startswith("image:"):
            asset_n[0] += 1
            file_u, w2, h2, _a = _asset_fetch(api_key, str(aid))
            if file_u and file_u.startswith("http"):
                durl = file_u
            w = w2 or w
            h = h2 or h
        w = w or int(row.get("thumb_width") or 0)
        h = h or int(row.get("thumb_height") or 0)
    else:
        if api_key and aid and asset_n[0] < asset_cap and str(aid).startswith("video:"):
            asset_n[0] += 1
            file_u, w2, h2, _a = _asset_fetch(api_key, str(aid))
            if file_u and file_u.startswith("http"):
                durl = file_u
            w, h = w2 or w, h2 or h
    kind = "video" if typ == "video" else "image"
    return {
        "source": "dvids",
        "kind": kind,
        "title": title,
        "url": page,
        "download_url": durl,
        "width": w,
        "height": h,
        "duration": duration,
        "author": author,
        "license": "DVIDS — U.S. DoD media; verify field usage in manifest / ToS",
        "raw": row,
    }


def search(query: str, per_page: int = 5) -> list[dict[str, Any]]:
    load_env_from_dotenv_file()
    k = _key()
    m = max(1, int(per_page))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    asset_cap = 14
    asset_n = [0]
    for typ in ("video", "image"):
        for row in _search_raw(query, m, typ, k):
            if (row or {}).get("type") != typ:
                continue
            d = _row_to_dict(row, query, k, asset_cap, asset_n)
            if not d:
                continue
            durl = str(d.get("download_url") or "")
            page = str(d.get("url") or "")
            if not durl and not page:
                continue
            kdup = durl or page
            if kdup in seen:
                continue
            seen.add(kdup)
            out.append(d)
    _log_result(query, len(out))
    return out


def download(url: str, output_path: str, *, force: bool = False) -> str:
    u = (url or "").strip()
    if not u or not u.lower().startswith("http"):
        print(
            f"{LOG_PREFIX} download source=dvids skipped: empty or page-only (no file URL)",
            flush=True,
        )
        return output_path
    return download_stream_to_path(u, output_path, "dvids", force=force, timeout=30)
