"""OpenAI editor: group headlines into events, keep only in-scope ones, draft posts."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .guard import ALLOWED, FOOTER
from .sources import Headline

API_URL = "https://api.openai.com/v1/chat/completions"
Post = Callable[[str, dict, bytes], bytes]

SYSTEM_PROMPT = f"""你是「Stateverge 国内要闻」的编辑,账号在 X 上面向海外华人,用简体中文。
任务:从给你的过去 24 小时中文新闻标题(带编号和媒体名)里,挑出今天最值得关注的 3–5 件事。

范围:只做 {"、".join(ALLOWED)}。
绝对不做:任何政治、领导人、党政人事、军事外交、台港疆藏、维权抗议、反腐、审查类话题;即使沾边也排除。

每件事必须:
- 至少被 2 家不同媒体报道(source_ids 填这些标题编号,来自不同媒体);只有一家报的不要选
- 只用标题里能看到的事实,不编造数字、不推测原因、不预测涨跌、不给投资建议
- 语气平实,不用「震惊、重磅、炸裂、崩盘、暴雷」这类词

输出 JSON:
{{"events": [{{
  "category": "{ALLOWED[0]}",
  "title": "一句话事件标题(25 字内,事实为主)",
  "facts": ["要点1", "要点2"],
  "why": "对海外华人/在国内有家人资产的人意味着什么(40 字内,不夸张)",
  "source_ids": [3, 7],
  "importance": 0-100,
  "political": false
}}]}}
按 importance 从高到低排列。"""


def _http(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def material(heads: list[Headline]) -> str:
    return "\n".join(f"[{h.id}] ({h.category}) {h.outlet}:{h.title}" for h in heads)


class Editor:
    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None) -> None:
        self.api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        self.model = model or os.environ.get("CNBRIEF_MODEL") or "gpt-4o-mini"
        self.post = post or _http

    def events(self, heads: list[Headline]) -> list[dict]:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing")
        body = json.dumps({"model": self.model, "temperature": 0.2, "response_format": {"type": "json_object"},
                           "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                        {"role": "user", "content": material(heads)}]}).encode()
        try:
            raw = json.loads(self.post(API_URL, {"Authorization": f"Bearer {self.api_key}",
                                                 "Content-Type": "application/json"}, body))
            return list(json.loads(raw["choices"][0]["message"]["content"]).get("events", []))
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(f"editor failed: {type(e).__name__}: {e}") from e


def post_parts(event: dict, by_id: dict[int, dict]) -> list[str]:
    """Main post without links (X shows link-free posts to more people); sources go in the reply."""
    from ..archive.x_publisher import split_thread

    links = []
    for i in event.get("source_ids", []):
        h = by_id.get(i)
        if h and h["outlet"] not in [x[0] for x in links]:
            links.append((h["outlet"], h["url"]))
    main = [f"🇨🇳【{event.get('category', '')}】{event.get('title', '').strip()}", ""]
    main += [f"· {f.strip()}" for f in event.get("facts", [])[:3]]
    if event.get("why"):
        main += ["", f"为什么值得关注:{event['why'].strip()}"]
    reply = ["来源:" + " / ".join(o for o, _ in links[:3]), *[u for _, u in links[:2]], "", FOOTER]
    return split_thread("\n".join(main)) + ["\n".join(reply)]
