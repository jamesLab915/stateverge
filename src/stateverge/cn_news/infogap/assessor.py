"""AI assessment + Chinese draft (OpenAI, same convention as the rest of StateVerge).

One call per shortlisted signal returns the track, three sub-scores and a
draft in the Stateverge structure:

    发生了什么 → 为什么现在 → 美国人在怎么用 → 中国用户为什么应该注意 → 有没有赚钱机会 → 限制/风险

The model may only use the supplied material. Without OPENAI_API_KEY (or on
any error) the heuristic scores are used and the draft fields are left as
【待编辑】, which the publish guard refuses to post.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .models import Assessment, Signal, Track
from .scoring import heuristic_assessment

API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = "gpt-4o-mini"
PLACEHOLDER = "【待编辑】"

SYSTEM_PROMPT = """你是 Stateverge 中文的编辑。Stateverge 的定位:把英文互联网里真正有价值的信息,
提前翻译成中文世界能理解的机会、风险和趋势。读者是中文用户(中国大陆、海外华人)。

只根据用户提供的材料写作:
- 不得编造数字、价格、公司、引语或事实;材料没有的信息写"原文未提及"。
- 不要写"某公司发布了某功能"这种无信息差的句子,要写这对中文用户意味着什么。
- 不得使用"稳赚、躺赚、暴富、保证收益、无风险、必赚"之类的承诺;赚钱机会必须同时写清门槛和风险。
- 中国大陆用户能否使用(网络、支付、地区限制)如果材料没说,就写"需自行确认"。

评分(0-100):
- practical_value:对中文用户的实际用处(能用、能学、能赚钱、能避坑)
- discussion:引发讨论/争议的潜力
- visual:是否有适合做图/视频的素材(演示、截图、数据、实物)

track 只能是:AI_TECH(AI/科技), MONEY(美国赚钱/小生意), US_LIFE(美国普通人生活), INDUSTRY(产业/商业趋势), OTHER。

只输出 JSON:
{"track": "...", "practical_value": 0, "discussion": 0, "visual": 0,
 "headline_zh": "一句话标题,突出中文用户没注意到的变化",
 "what": "发生了什么", "why_now": "为什么现在出现", "how_used": "美国人在怎么用",
 "why_cn": "中国用户为什么应该注意", "opportunity": "有没有机会(没有就直说)", "risks": "限制/风险(必填)",
 "x_line": "适合 X 的一句话(60字以内)"}"""

Post = Callable[[str, dict, bytes], bytes]
_DRAFT_FIELDS = ("headline_zh", "what", "why_now", "how_used", "why_cn", "opportunity", "risks", "x_line")


def _http(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=90) as resp:
        return resp.read()


def _clamp(v) -> float:
    try:
        return max(0.0, min(100.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def placeholder_assessment(signal: Signal) -> Assessment:
    a = heuristic_assessment(signal)
    for f in _DRAFT_FIELDS:
        setattr(a, f, PLACEHOLDER)
    a.headline_zh = f"{PLACEHOLDER} {signal.title}"
    return a


class LLMAssessor:
    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.model = model or os.environ.get("INFOGAP_MODEL") or DEFAULT_MODEL
        self.post = post or _http
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def assess(self, signal: Signal, extra_context: str = "") -> Assessment:
        if not self.available:
            return placeholder_assessment(signal)
        material = (
            f"来源:{signal.source}\n标题:{signal.title}\n链接:{signal.url}\n"
            f"讨论:{signal.discussion_url or '无'}(热度 {signal.points},评论 {signal.comments})\n"
            f"标签:{', '.join(signal.tags) or '无'}\n摘要/正文:{signal.summary or '无'}\n{extra_context}"
        )
        body = json.dumps({
            "model": self.model,
            "temperature": 0.3,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": material}],
        }).encode()
        try:
            raw = json.loads(self.post(API_URL, {"Authorization": f"Bearer {self.api_key}",
                                                 "Content-Type": "application/json"}, body))
            d = json.loads(raw["choices"][0]["message"]["content"])
            track = Track(d.get("track", "OTHER")) if d.get("track") in Track.__members__ else Track.OTHER
            a = Assessment(
                track=track,
                practical_value=_clamp(d.get("practical_value")),
                discussion=_clamp(d.get("discussion")),
                visual=_clamp(d.get("visual")),
                by_llm=True,
            )
            for f in _DRAFT_FIELDS:
                setattr(a, f, str(d.get(f) or PLACEHOLDER).strip())
            return a
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as e:
            self.last_error = type(e).__name__
            return placeholder_assessment(signal)
