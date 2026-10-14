"""Rules every 国内要闻 post must pass before it can be scheduled or posted."""

from __future__ import annotations

from ..archive.x_publisher import weighted_length

ALLOWED = ("利率货币", "房地产", "宏观数据", "科技AI", "政策民生", "资本市场", "安全事故")

# Political / high-dispute topics are out of scope for this channel.
POLITICAL = (
    "习近平", "总书记", "中共", "共产党", "政治局", "人大", "政协", "台湾", "台独", "香港", "新疆", "西藏",
    "维吾尔", "人权", "抗议", "示威", "游行", "维权", "上访", "六四", "法轮功", "达赖", "解放军", "军演",
    "外交部", "制裁", "间谍", "反腐", "落马", "双开", "审查", "翻墙", "防火长城", "异见",
)
SENSATIONAL = ("震惊", "重磅", "突发!", "炸裂", "崩盘", "暴雷", "史诗级", "全网疯传", "惊天")
ADVICE = ("建议买入", "抄底", "必涨", "必跌", "稳赚", "梭哈")
FOOTER = "(AI 辅助整理,以原文为准)"


def check(event: dict, headlines: dict[int, dict], text: str) -> list[str]:
    problems = []
    if event.get("category") not in ALLOWED:
        problems.append(f"类别不在范围内:{event.get('category')}")
    ids = [i for i in event.get("source_ids", []) if i in headlines]
    outlets = {headlines[i]["outlet"] for i in ids}
    if len(outlets) < 2:
        problems.append(f"独立来源不足 2 家(只有 {len(outlets)} 家)")
    blob = text + " " + " ".join(headlines[i]["title"] for i in ids)
    pol = sorted({w for w in POLITICAL if w in blob})
    if pol:
        problems.append(f"涉及政治类话题,不发:{pol}")
    if event.get("political"):
        problems.append("编辑判断为政治类,不发")
    hype = sorted({w for w in SENSATIONAL + ADVICE if w in text})
    if hype:
        problems.append(f"夸张或投资建议用语:{hype}")
    if FOOTER not in text:
        problems.append("缺少「AI 辅助整理」标注")
    if weighted_length(text) > 280 * 3:
        problems.append("太长(超过 3 条推文)")
    return problems
