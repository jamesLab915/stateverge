"""Public YouTube channel snapshot (no API key): channel page + RSS feed."""

from __future__ import annotations

import json
import re
import urllib.request
import xml.etree.ElementTree as ET

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
NS = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/",
      "yt": "http://www.youtube.com/xml/schemas/2015"}


def _get(url: str, get=None) -> str:
    if get:
        return get(url)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9",
                                               "Cookie": "CONSENT=YES+1"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _first(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1) if m else ""


def snapshot(handle: str, get=None) -> dict:
    handle = handle if handle.startswith("@") else f"@{handle}"
    page = _get(f"https://www.youtube.com/{handle}", get)
    cid = _first(r'"externalId":"(UC[\w-]{22})"', page) or _first(r'"channelId":"(UC[\w-]{22})"', page)
    out = {"handle": handle, "channel_id": cid,
           "title": _first(r'<meta property="og:title" content="([^"]*)"', page),
           "subscribers": _first(r'"subscriberCountText":\{[^}]*?"content":"([^"]+)"', page)
           or _first(r'([\d.,]+[KM]? subscribers)', page),
           "video_count": _first(r'([\d,]+ videos)', page), "recent": []}
    if not cid:
        out["error"] = "channel id not found (page blocked or handle wrong)"
        return out
    feed = ET.fromstring(_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}", get))
    for e in feed.findall("a:entry", NS):
        stats = e.find("media:group/media:community/media:statistics", NS)
        out["recent"].append({
            "title": e.findtext("a:title", "", NS),
            "published": e.findtext("a:published", "", NS)[:10],
            "views": int(stats.get("views", 0)) if stats is not None else None,
            "shorts": "/shorts/" in (e.find("a:link", NS).get("href", "") if e.find("a:link", NS) is not None else ""),
        })
    return out


def report(handles: list[str], get=None) -> list[dict]:
    res = []
    for h in handles:
        try:
            res.append(snapshot(h, get))
        except Exception as e:  # noqa: BLE001 - one bad channel must not stop the rest
            res.append({"handle": h, "error": f"{type(e).__name__}: {e}"[:300]})
    return res


if __name__ == "__main__":
    import sys
    print(json.dumps(report(sys.argv[1:]), ensure_ascii=False, indent=2))
