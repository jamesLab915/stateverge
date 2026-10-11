"""Chinese episode script from market data + headlines (OpenAI), with a guard.

The model returns JSON segments. Exactly three are ``anchor`` lines (the
on-camera presenter, ≤ ~5 s each: open, middle, close); the rest are
voice-over over a market board, a chart or a headline card.

The guard never lets a number through that is not in the supplied data, and
blocks advice-like wording. The model gets one retry with the guard's
complaints; whatever still fails is recorded on the episode.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable

from .market import Quote
from .news import Headline

API_URL = "https://api.openai.com/v1/chat/completions"
Post = Callable[[str, dict, bytes], bytes]

ANCHOR_MAX_CHARS = 26  # ≈ 5 s of Mandarin
MIN_CHARS, MAX_CHARS = 650, 1150  # ≈ 3–5 min of narration
ADVICE_WORDS = ("稳赚", "保证收益", "必涨", "必跌", "建议买入", "建议卖出", "推荐买入", "抄底", "梭哈", "无风险", "翻倍")

SYSTEM_PROMPT = f"""你是「Stateverge 美国财经日报」的中文撰稿人。观众是关心美国经济、美股、利率、汇率的华人。
只使用用户提供的行情数据和新闻标题/摘要,不得编造任何数字、公司、人物或引语。
提到的每个数字(点位、涨跌幅、价格、收益率)必须与数据一致,涨跌幅保留两位小数。
新闻里的英文数字可以换算单位(如 billion → 亿),但不要自己估算新数字。
不给投资建议,不预测涨跌,不使用「{'、'.join(ADVICE_WORDS)}」等字眼。语气专业、平实、口语化。

结构(segments 按播放顺序):
1. anchor:开场一句(不超过 {ANCHOR_MAX_CHARS} 个字),例如问候并点出今天最重要的一件事
2. vo + board:三大指数收盘概况
3. 2–4 段 vo + chart 或 headline:今天最重要的 2–4 件事,讲清发生了什么、为什么重要、对华人(在美工作/投资/汇款)意味着什么
4. anchor:中间一句过渡(不超过 {ANCHOR_MAX_CHARS} 个字)
5. 1–2 段 vo:利率、美元、原油、黄金或比特币中值得一提的变化
6. anchor:结尾一句(不超过 {ANCHOR_MAX_CHARS} 个字),只做告别,不要说「订阅」以外的号召

anchor 恰好 3 段(不多不少)。每段 vo 写 120–220 字,讲透背景和影响;全部 text 合计 {MIN_CHARS}–{MAX_CHARS} 个字
(少于 {MIN_CHARS} 字视为不合格)。

只输出 JSON:
{{"title": "YouTube 标题(30 字内,写清今天最大的变化,不夸张)",
  "description": "YouTube 简介(3–5 句)",
  "segments": [
    {{"kind": "anchor", "text": "..."}},
    {{"kind": "vo", "text": "...", "visual": {{"type": "board"}}}},
    {{"kind": "vo", "text": "...", "visual": {{"type": "chart", "symbol": "^GSPC"}}}},
    {{"kind": "vo", "text": "...", "visual": {{"type": "headline", "index": 0}}}}
  ]}}"""


@dataclass
class Segment:
    kind: str  # "anchor" | "vo"
    text: str
    visual: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "visual": self.visual}


@dataclass
class Script:
    title: str
    description: str
    segments: list[Segment]
    problems: list[str] = field(default_factory=list)
    by_llm: bool = True

    def to_dict(self) -> dict:
        return {"title": self.title, "description": self.description, "problems": self.problems,
                "by_llm": self.by_llm, "segments": [s.to_dict() for s in self.segments]}

    @classmethod
    def from_dict(cls, d: dict) -> Script:
        segs = [Segment(s.get("kind", "vo"), str(s.get("text", "")).strip(), s.get("visual") or {})
                for s in d.get("segments", [])]
        return cls(str(d.get("title", "")).strip(), str(d.get("description", "")).strip(), segs,
                   list(d.get("problems", [])), bool(d.get("by_llm", True)))


def material(quotes: list[Quote], heads: list[Headline]) -> str:
    lines = ["【收盘行情】"]
    for q in quotes:
        lines.append(f"{q.symbol} {q.name}: 收于 {q.close:,.2f}{q.unit},前收 {q.prev_close:,.2f},"
                     f"涨跌 {q.change:+,.2f}({q.change_pct:+.2f}%),日期 {q.as_of}"
                     + (f",即 {q.change * 100:+.0f} 个基点(收益率请用基点描述变化)" if q.unit == "%" else ""))
    lines.append("\n【过去24小时新闻】")
    for i, h in enumerate(heads):
        lines.append(f"[{i}] {h.source}: {h.title}\n    {h.summary[:300]}")
    return "\n".join(lines)


# ---------- guard ----------

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _variants(x: float) -> set[str]:
    out = set()
    for v in (x, abs(x)):
        for d in (0, 1, 2):
            s = f"{v:.{d}f}"
            out.update({s, f"{v:,.{d}f}"})
            if d:
                out.add(s.rstrip("0").rstrip("."))
    return out


def allowed_numbers(quotes: list[Quote], heads: list[Headline]) -> set[str]:
    ok: set[str] = set()
    for q in quotes:
        for v in (q.close, q.prev_close, q.change, q.change_pct):
            ok |= _variants(v)
        ok.update(_NUM.findall(q.name))  # 标普500, 10年期
        if q.unit == "%":
            ok |= _variants(q.change * 100)  # yield change in basis points
    for h in heads:
        ok.update(n.replace(",", "") for n in _NUM.findall(f"{h.title} {h.summary}"))
    return {n.replace(",", "") for n in ok}


def _cjk_len(text: str) -> int:
    return len(re.sub(r"[\s，。、！？：；,.!?:;「」\"'()（）]", "", text))


def check(script: Script, quotes: list[Quote], heads: list[Headline]) -> list[str]:
    problems = []
    anchors = [s for s in script.segments if s.kind == "anchor"]
    if len(anchors) != 3:
        problems.append(f"anchor 段应为 3 段,实际 {len(anchors)} 段")
    for a in anchors:
        if _cjk_len(a.text) > ANCHOR_MAX_CHARS:
            problems.append(f"anchor 段超过 {ANCHOR_MAX_CHARS} 字:{a.text}")
    total = sum(_cjk_len(s.text) for s in script.segments)
    if not MIN_CHARS <= total <= MAX_CHARS:
        problems.append(f"总字数 {total},应在 {MIN_CHARS}–{MAX_CHARS} 之间")
    symbols = {q.symbol for q in quotes}
    for s in script.segments:
        v = s.visual or {}
        if v.get("type") == "chart" and v.get("symbol") not in symbols:
            problems.append(f"图表代码不存在:{v.get('symbol')}")
        if v.get("type") == "headline" and not (isinstance(v.get("index"), int) and 0 <= v["index"] < len(heads)):
            problems.append(f"新闻编号不存在:{v.get('index')}")
    ok = allowed_numbers(quotes, heads)
    text = " ".join(s.text for s in script.segments) + " " + script.title
    for n in _NUM.findall(text):
        bare = n.replace(",", "")
        if bare in ok or (bare.isdigit() and (int(bare) <= 31 or 1990 <= int(bare) <= 2100)):
            continue
        problems.append(f"数字无法在数据中核对:{n}")
    bad = sorted({w for w in ADVICE_WORDS if w in text})
    if bad:
        problems.append(f"含投资建议/夸大用语:{bad}")
    if not script.title:
        problems.append("缺少标题")
    return problems


def normalize(script: Script) -> Script:
    """Keep exactly the first, a middle and the last anchor line; extra ones become voice-over."""
    idx = [i for i, s in enumerate(script.segments) if s.kind == "anchor"]
    if len(idx) > 3:
        keep = {idx[0], idx[len(idx) // 2], idx[-1]}
        for i in idx:
            if i not in keep:
                script.segments[i].kind = "vo"
                script.segments[i].visual = script.segments[i].visual or {"type": "board"}
    return script


# ---------- writer ----------

def _http(url: str, headers: dict, body: bytes) -> bytes:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


class ScriptWriter:
    def __init__(self, api_key: str | None = None, model: str | None = None, post: Post | None = None) -> None:
        self.api_key = (api_key or os.environ.get("OPENAI_API_KEY", "")).strip()
        self.model = model or os.environ.get("FINANCE_MODEL") or "gpt-4o-mini"
        self.post = post or _http
        self.retries = 2

    def _ask(self, messages: list[dict]) -> Script:
        body = json.dumps({"model": self.model, "temperature": 0.4, "response_format": {"type": "json_object"},
                           "messages": messages}).encode()
        raw = json.loads(self.post(API_URL, {"Authorization": f"Bearer {self.api_key}",
                                             "Content-Type": "application/json"}, body))
        return Script.from_dict(json.loads(raw["choices"][0]["message"]["content"]))

    def write(self, quotes: list[Quote], heads: list[Headline]) -> Script:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY missing")
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": material(quotes, heads)}]
        try:
            script = normalize(self._ask(messages))
            for _ in range(self.retries):
                problems = check(script, quotes, heads)
                if not problems:
                    break
                messages += [{"role": "assistant", "content": json.dumps(script.to_dict(), ensure_ascii=False)},
                             {"role": "user", "content": "请修正以下问题后重新输出完整 JSON:\n- " + "\n- ".join(problems)}]
                script = normalize(self._ask(messages))
            script.problems = check(script, quotes, heads)
            return script
        except (urllib.error.URLError, TimeoutError, KeyError, IndexError, TypeError, ValueError) as e:
            raise RuntimeError(f"script generation failed: {type(e).__name__}: {e}") from e
