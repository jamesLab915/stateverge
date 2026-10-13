"""Original explainer graphics (1920×1080). No Fed seal, logo or official artwork."""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw

from ..finance_daily.visuals import BG_BOTTOM, BG_TOP, DIM, GRAY, RED, WHITE, font

W, H = 1920, 1080
SHOW = "STATEVERGE MARKETS"
PANEL = (22, 33, 56)
ACCENT = (90, 160, 255)
AMBER = (245, 176, 65)


def _canvas(series: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(BG_TOP, BG_BOTTOM, strict=True)))
    d.rectangle([0, 0, W, 80], fill=(8, 12, 22))
    d.rectangle([0, 0, 12, 80], fill=RED)
    d.text((44, 20), SHOW, font=font(34), fill=WHITE)
    d.text((W - 44, 24), series, font=font(28), fill=GRAY, anchor="ra")
    return img, d


def _wrap(d: ImageDraw.ImageDraw, text: str, size: int, width_px: int) -> list[str]:
    f = font(size)
    out: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if d.textlength(trial, font=f) <= width_px:
                line = trial
            else:
                out.append(line)
                line = word
        out.append(line)
    return out


def _heading(d, text: str, y: int = 130) -> None:
    d.text((110, y), text, font=font(64), fill=WHITE)
    d.rectangle([110, y + 92, 230, y + 98], fill=RED)


def _arrow(d, x0, y0, x1, y1, color=GRAY, width=6) -> None:
    d.line([(x0, y0), (x1, y1)], fill=color, width=width)
    import math
    a = math.atan2(y1 - y0, x1 - x0)
    for s in (-0.45, 0.45):
        d.line([(x1, y1), (x1 - 26 * math.cos(a + s), y1 - 26 * math.sin(a + s))], fill=color, width=width)


def title(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    d.rectangle([140, 380, 158, 700], fill=RED)
    d.text((200, 370), v.get("kicker", "THE FED, EXPLAINED"), font=font(44), fill=GRAY)
    y = 450
    for line in _wrap(d, v["title"], 104, 1560)[:2]:
        d.text((200, y), line, font=font(104), fill=WHITE)
        y += 125
    if v.get("subtitle"):
        d.text((200, y + 20), v["subtitle"], font=font(44), fill=GRAY)
    img.save(out)
    return out


def bullets(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, v["heading"])
    y = 300
    for i, item in enumerate(v["items"]):
        d.ellipse([130, y + 16, 154, y + 40], fill=RED if i == v.get("highlight", -1) else ACCENT)
        for j, line in enumerate(_wrap(d, item, 50, 1560)):
            d.text((190, y + j * 64), line, font=font(50), fill=WHITE)
        y += 64 * len(_wrap(d, item, 50, 1560)) + 44
    if v.get("note"):
        d.text((110, H - 150), v["note"], font=font(30), fill=DIM)
    img.save(out)
    return out


def _box(d, xy, title_text, body="", fill=PANEL, accent=ACCENT, tsize=46, bsize=32) -> None:
    x0, y0, x1, y1 = xy
    d.rounded_rectangle(xy, 22, fill=fill)
    d.rectangle([x0, y0 + 20, x0 + 10, y1 - 20], fill=accent)
    d.text(((x0 + x1) / 2, y0 + 50), title_text, font=font(tsize), fill=WHITE, anchor="mm")
    by = y0 + 120
    for line in _wrap(d, body, bsize, x1 - x0 - 60):
        d.text(((x0 + x1) / 2, by), line, font=font(bsize), fill=GRAY, anchor="mm")
        by += bsize + 12


def org(v: dict, series: str, out: Path) -> Path:
    """Board of Governors + 12 Reserve Banks → FOMC (text from the episode JSON)."""
    img, d = _canvas(series)
    _heading(d, v.get("heading", "Three key parts"))
    b, r, f = v["board"], v["banks"], v["fomc"]
    _box(d, (110, 330, 860, 560), b["title"], b["body"], tsize=52, bsize=38)
    _box(d, (1060, 330, 1810, 560), r["title"], r["body"], tsize=52, bsize=38)
    _box(d, (510, 740, 1410, 960), f["title"], f["body"], accent=RED, tsize=52, bsize=38)
    _arrow(d, 485, 565, 760, 735)
    _arrow(d, 1435, 565, 1160, 735)
    img.save(out)
    return out


def flow(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, v["heading"])
    steps = v["steps"]
    n = len(steps)
    gap, top, bh = 60, 420, 300
    bw = (W - 220 - gap * (n - 1)) // n
    for i, s in enumerate(steps):
        x0 = 110 + i * (bw + gap)
        d.rounded_rectangle([x0, top, x0 + bw, top + bh], 22, fill=PANEL)
        d.text((x0 + 30, top + 26), f"{i + 1}", font=font(44), fill=RED)
        y = top + 100
        for line in _wrap(d, s, 36, bw - 50):
            d.text((x0 + 30, y), line, font=font(36), fill=WHITE)
            y += 48
        if i < n - 1:
            _arrow(d, x0 + bw + 8, top + bh // 2, x0 + bw + gap - 8, top + bh // 2, color=GRAY, width=5)
    if v.get("note"):
        for j, line in enumerate(_wrap(d, v["note"], 32, W - 220)):
            d.text((110, top + bh + 80 + j * 44), line, font=font(32), fill=DIM)
    img.save(out)
    return out


def districts(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, v.get("heading", "12 Reserve Banks"))
    cities = v["cities"]
    for i, c in enumerate(cities):
        col, row = i % 4, i // 4
        x0, y0 = 110 + col * 435, 320 + row * 200
        d.rounded_rectangle([x0, y0, x0 + 400, y0 + 160], 18, fill=PANEL)
        d.text((x0 + 30, y0 + 28), f"{i + 1}", font=font(36), fill=RED)
        d.text((x0 + 30, y0 + 80), c, font=font(42), fill=WHITE)
    if v.get("note"):
        d.text((110, 960), v["note"], font=font(30), fill=DIM)
    img.save(out)
    return out


def compare(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, v["heading"])
    for k, side in enumerate((v["left"], v["right"])):
        x0 = 110 + k * 870
        d.rounded_rectangle([x0, 300, x0 + 830, 980], 22, fill=PANEL)
        d.text((x0 + 40, 330), side["title"], font=font(48), fill=ACCENT if k == 0 else AMBER)
        y = 420
        for item in side["items"]:
            d.ellipse([x0 + 44, y + 16, x0 + 60, y + 32], fill=ACCENT if k == 0 else AMBER)
            for line in _wrap(d, item, 36, 720):
                d.text((x0 + 80, y), line, font=font(36), fill=WHITE)
                y += 50
            y += 22
    img.save(out)
    return out


def example(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, v["heading"])
    d.rounded_rectangle([110, 270, 640, 330], 14, fill=AMBER)
    d.text((375, 300), "HYPOTHETICAL EXAMPLE", font=font(32), fill=(20, 20, 20), anchor="mm")
    y = 390
    for item in v["lines"]:
        for line in _wrap(d, item, 44, W - 260):
            d.text((130, y), line, font=font(44), fill=WHITE)
            y += 60
        y += 26
    d.text((110, H - 120), "Illustrative numbers, not real data or a forecast.", font=font(30), fill=DIM)
    img.save(out)
    return out


def sources(v: dict, series: str, out: Path) -> Path:
    img, d = _canvas(series)
    _heading(d, "Sources")
    y = 300
    for s in v["items"]:
        for line in textwrap.wrap(s, 80):
            d.text((110, y), line, font=font(34), fill=WHITE)
            y += 48
        y += 18
    d.text((110, H - 160), "Educational content, not financial advice. AI-assisted production; presenter is an AI avatar.",
           font=font(30), fill=DIM)
    d.text((110, H - 115), "Not affiliated with or endorsed by the Federal Reserve.", font=font(30), fill=DIM)
    img.save(out)
    return out


def lower_third(out: Path, name: str = "Stateverge Markets") -> Path:
    img = Image.new("RGBA", (W, H))
    d = ImageDraw.Draw(img)
    top = H - 330
    d.rectangle([0, top, 760, top + 100], fill=(8, 12, 22, 225))
    d.rectangle([0, top, 14, top + 100], fill=RED)
    d.text((48, top + 12), name, font=font(40), fill=WHITE)
    d.text((48, top + 62), "AI presenter · not a real person", font=font(28), fill=GRAY)
    img.save(out)
    return out


RENDERERS = {"title": title, "bullets": bullets, "org": org, "flow": flow, "districts": districts,
             "compare": compare, "example": example, "sources": sources}
