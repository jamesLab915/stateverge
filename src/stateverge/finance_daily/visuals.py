"""1920×1080 frames drawn with Pillow: title, market board, chart, headline, end card.

Colours follow the Chinese convention 红涨绿跌 (red = up, green = down), always
with ▲/▼ and a sign so nobody has to guess.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .market import Quote
from .news import Headline

W, H = 1920, 1080
BG_TOP, BG_BOTTOM = (10, 16, 30), (17, 27, 48)
WHITE, GRAY, DIM = (240, 242, 247), (160, 168, 184), (90, 100, 120)
RED, UP, DOWN = (229, 9, 20), (232, 64, 64), (38, 186, 120)
SHOW = "Stateverge 美国财经日报"

FONT_CANDIDATES = [
    os.environ.get("FINANCE_FONT", ""),
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    raise FileNotFoundError("no CJK font found; install fonts-noto-cjk or set FINANCE_FONT")


def _canvas() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True)))
    return img, d


def _chrome(d: ImageDraw.ImageDraw, day: str) -> None:
    d.rectangle([0, 0, W, 86], fill=(8, 12, 22))
    d.rectangle([0, 0, 12, 86], fill=RED)
    d.text((48, 20), SHOW, font=font(40), fill=WHITE)
    d.text((W - 48, 26), f"{day} · 美东收盘", font=font(32), fill=GRAY, anchor="ra")


def _fmt(q: Quote) -> tuple[str, str, tuple]:
    value = f"{q.close:,.2f}{'%' if q.unit == '%' else ''}"
    arrow = "▲" if q.change >= 0 else "▼"
    move = f"{q.change * 100:+.0f}个基点" if q.unit == "%" else f"{q.change_pct:+.2f}%"  # yields move in bp
    return value, f"{arrow} {move}", UP if q.change >= 0 else DOWN


def title_card(day: str, title: str, out: Path) -> Path:
    img, d = _canvas()
    _chrome(d, day)
    d.rectangle([140, 360, 156, 640], fill=RED)
    d.text((200, 360), "今日美国财经", font=font(64), fill=GRAY)
    y = 460
    for line in textwrap.wrap(title, 18)[:2]:
        d.text((200, y), line, font=font(88), fill=WHITE)
        y += 110
    d.text((200, 900), "AI 虚拟主播播报 · 内容由 AI 辅助生成 · 不构成投资建议", font=font(30), fill=DIM)
    img.save(out)
    return out


def board(day: str, quotes: list[Quote], out: Path) -> Path:
    img, d = _canvas()
    _chrome(d, day)
    d.text((96, 140), "收盘行情", font=font(56), fill=WHITE)
    cols, cw, ch = 2, 840, 140
    for i, q in enumerate(quotes[:8]):
        x, y = 96 + (i % cols) * (cw + 48), 230 + (i // cols) * (ch + 16)
        d.rounded_rectangle([x, y, x + cw, y + ch], 18, fill=(22, 33, 56))
        value, pct, color = _fmt(q)
        d.text((x + 36, y + 18), q.name, font=font(36), fill=GRAY)
        d.text((x + 36, y + 66), value, font=font(52), fill=WHITE)
        d.text((x + cw - 36, y + 74), pct, font=font(44), fill=color, anchor="ra")
    img.save(out)
    return out


def chart(day: str, q: Quote, out: Path) -> Path:
    img, d = _canvas()
    _chrome(d, day)
    value, pct, color = _fmt(q)
    d.text((96, 130), q.name, font=font(64), fill=WHITE)
    d.text((96, 220), value, font=font(80), fill=WHITE)
    d.text((96 + d.textlength(value, font=font(80)) + 40, 240), pct, font=font(56), fill=color)
    d.text((W - 96, 150), "近一个月 · 日收盘", font=font(32), fill=GRAY, anchor="ra")
    s = q.series or [q.prev_close, q.close]
    x0, x1, y0, y1 = 120, W - 120, 380, H - 140
    lo, hi = min(s), max(s)
    pad = (hi - lo) * 0.1 or abs(hi) * 0.01 or 1
    lo, hi = lo - pad, hi + pad
    pts = [(x0 + (x1 - x0) * i / max(len(s) - 1, 1), y1 - (y1 - y0) * (v - lo) / (hi - lo)) for i, v in enumerate(s)]
    for k in range(5):
        gy = y0 + (y1 - y0) * k / 4
        d.line([(x0, gy), (x1, gy)], fill=(36, 48, 72), width=2)
        d.text((x1 + 10, gy), f"{hi - (hi - lo) * k / 4:,.2f}", font=font(24), fill=DIM, anchor="lm")
    trend = UP if s[-1] >= s[0] else DOWN
    fill = Image.new("RGBA", (W, H))
    ImageDraw.Draw(fill).polygon([(x0, y1), *pts, (x1, y1)], fill=(*trend, 46))
    img.paste(fill, (0, 0), fill)
    d.line(pts, fill=trend, width=6, joint="curve")
    lx, ly = pts[-1]
    d.ellipse([lx - 12, ly - 12, lx + 12, ly + 12], fill=trend)
    d.text((x0, y1 + 24), f"数据:{q.source or 'market data'} · 截至 {q.as_of}", font=font(26), fill=DIM)
    img.save(out)
    return out


def headline(day: str, h: Headline, out: Path) -> Path:
    img, d = _canvas()
    _chrome(d, day)
    d.rounded_rectangle([96, 180, W - 96, H - 180], 24, fill=(22, 33, 56))
    d.rounded_rectangle([140, 230, 140 + d.textlength(h.source, font=font(34)) + 48, 290], 12, fill=RED)
    d.text((164, 236), h.source, font=font(34), fill=WHITE)
    y = 350
    for line in textwrap.wrap(h.title, 46)[:4]:
        d.text((140, y), line, font=font(56), fill=WHITE)
        y += 76
    d.text((140, H - 250), "英文原文标题 · 中文解读见字幕", font=font(28), fill=DIM)
    img.save(out)
    return out


def end_card(day: str, out: Path) -> Path:
    img, d = _canvas()
    _chrome(d, day)
    d.text((W // 2, 420), "感谢收看", font=font(96), fill=WHITE, anchor="mm")
    d.text((W // 2, 540), "每个交易日更新 · 订阅不错过", font=font(44), fill=GRAY, anchor="mm")
    d.text((W // 2, 820), "本节目由 AI 虚拟主播播报,内容由 AI 辅助整理,仅供信息参考,不构成任何投资建议。",
           font=font(30), fill=DIM, anchor="mm")
    d.text((W // 2, 870), "行情与新闻以原始来源为准。", font=font(30), fill=DIM, anchor="mm")
    img.save(out)
    return out


def lower_third(out: Path) -> Path:
    """Transparent overlay for anchor shots: AI label is always visible on the presenter."""
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    top = H - 330  # above the subtitle band
    d.rectangle([0, top, 760, top + 100], fill=(8, 12, 22, 225))
    d.rectangle([0, top, 14, top + 100], fill=RED)
    d.text((48, top + 12), SHOW, font=font(40), fill=WHITE)
    d.text((48, top + 62), "AI 虚拟主播 · 非真人", font=font(28), fill=GRAY)
    img.save(out)
    return out
