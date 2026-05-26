#!/usr/bin/env python3
"""StateVerge Watermark System v1 — SV minimal ambient cinema lockup.

Generates PNG/SVG assets, safe-area references, FFmpeg/DaVinci docs, and preview mockups.
Run: .venv_audio/bin/python3 scripts/generate_stateverge_watermark_sv_v1.py
"""
from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_REPO = Path(__file__).resolve().parent.parent
_ROOT = _REPO / "assets" / "branding" / "stateverge_watermark_sv"
_CHANNEL_S_MASTER = _ROOT / "source" / "s_monogram_channel_master.png"
_FONT_SB = _REPO / "assets/fonts/Montserrat/Montserrat-SemiBold.ttf"
_FONT_LT = _REPO / "assets/fonts/Montserrat/Montserrat-Light.ttf"

# Brand palette
RED = "#E50914"
WHITE = "#FFFFFF"
GRAY = "#B3B3B3"
BLACK = "#0A0A0A"

PRIMARY = "STATEVERGE"
SUBTITLE = "AMBIENT CINEMA"
MONOGRAM = "SV"

SAFE_MARGIN = 40
DEFAULT_OPACITY = 0.82  # 82% — within 75–88% spec


@dataclass(frozen=True)
class SizeSpec:
    name: str
    w: int
    h: int


SIZES = {
    "1080p": SizeSpec("1080p", 320, 80),
    "4k": SizeSpec("4k", 640, 160),
    "shorts": SizeSpec("shorts", 220, 60),
}


def _hex_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


def _hex_rgba(h: str, alpha: float) -> tuple[int, int, int, int]:
    r, g, b = _hex_rgb(h)
    return r, g, b, max(0, min(255, int(round(alpha * 255))))


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), size=size)


def _scale_metrics(base_w: int, base_h: int, w: int, h: int) -> dict[str, float]:
    sx = w / base_w
    sy = h / base_h
    s = min(sx, sy)
    return {"s": s, "sx": sx, "sy": sy}


def _draw_sv_monogram(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    *,
    s_color: str,
    v_color: str,
    s: float,
) -> None:
    x0, y0, x1, y1 = box
    # Tight SV — no decorative glow; geometric spacing like HBO/Netflix idents.
    f_sv = _load_font(_FONT_SB, max(10, int(round(22 * s))))
    text = MONOGRAM
    bbox = draw.textbbox((0, 0), text, font=f_sv)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    tx = x0 + (x1 - x0 - tw) // 2 - bbox[0]
    ty = y0 + (y1 - y0 - th) // 2 - bbox[1]
    # Draw S and V with separate colors
    s_w = draw.textlength("S", font=f_sv)
    draw.text((tx, ty), "S", font=f_sv, fill=s_color)
    draw.text((tx + s_w - 1, ty), "V", font=f_sv, fill=v_color)


def render_watermark(
    size: SizeSpec,
    *,
    variant: str,
) -> Image.Image:
    """variant: full_color | monochrome | reversed | transparent"""
    w, h = size.w, size.h
    m = _scale_metrics(320, 80, w, h)
    s = m["s"]

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = int(round(8 * s))
    box_s = int(round(52 * s))
    bx0, by0 = pad, (h - box_s) // 2
    bx1, by1 = bx0 + box_s, by0 + box_s
    radius = max(2, int(round(4 * s)))

    # Variant colors
    if variant == "full_color":
        box_fill = _hex_rgba(BLACK, 0.92)
        accent = RED
        s_col, v_col = RED, WHITE
        title_col, sub_col = WHITE, GRAY
        accent_on_box = True
    elif variant == "monochrome":
        box_fill = _hex_rgba(BLACK, 0.88)
        accent = GRAY
        s_col, v_col = WHITE, GRAY
        title_col, sub_col = WHITE, GRAY
        accent_on_box = False
    elif variant == "reversed":
        box_fill = _hex_rgba(WHITE, 0.12)
        accent = BLACK
        s_col, v_col = BLACK, _hex_rgb("#333333")
        title_col, sub_col = BLACK, "#444444"
        accent_on_box = False
    elif variant == "transparent":
        box_fill = (0, 0, 0, 0)
        accent = RED
        s_col, v_col = RED, WHITE
        title_col, sub_col = WHITE, GRAY
        accent_on_box = True
    else:
        raise ValueError(variant)

    if box_fill[3] > 0:
        draw.rounded_rectangle((bx0, by0, bx1, by1), radius=radius, fill=box_fill)
    if accent_on_box:
        line_w = max(1, int(round(2 * s)))
        draw.rectangle((bx0, by0, bx0 + line_w, by1), fill=_hex_rgba(RED, 0.55))

    _draw_sv_monogram(draw, (bx0, by0, bx1, by1), s_color=s_col, v_color=v_col, s=s)

    tx = bx1 + int(round(10 * s))
    f_title = _load_font(_FONT_SB, max(8, int(round(20 * s))))
    f_sub = _load_font(_FONT_LT, max(7, int(round(11 * s))))
    ty_title = by0 + int(round(6 * s))
    ty_sub = ty_title + int(round(24 * s))
    draw.text((tx, ty_title), PRIMARY, font=f_title, fill=title_col)
    draw.text((tx, ty_sub), SUBTITLE, font=f_sub, fill=sub_col)

    if variant == "transparent":
        return img

    return img


def _save_png(img: Image.Image, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, "PNG", optimize=True)


def _write_svg(path: Path) -> None:
    # 320×80 canonical SVG — scalable for 4K/Shorts in NLE/FFmpeg.
    svg = f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" width="320" height="80" viewBox="0 0 320 80" fill="none">
  <rect x="8" y="14" width="52" height="52" rx="4" fill="{BLACK}" fill-opacity="0.92"/>
  <rect x="8" y="14" width="2" height="52" fill="{RED}" fill-opacity="0.55"/>
  <text x="22" y="50" font-family="Montserrat, Arial, sans-serif" font-weight="600" font-size="22" fill="{RED}">S</text>
  <text x="36" y="50" font-family="Montserrat, Arial, sans-serif" font-weight="600" font-size="22" fill="{WHITE}">V</text>
  <text x="70" y="36" font-family="Montserrat, Arial, sans-serif" font-weight="600" font-size="20" fill="{WHITE}" letter-spacing="0.06em">{PRIMARY}</text>
  <text x="70" y="56" font-family="Montserrat, Arial, sans-serif" font-weight="300" font-size="11" fill="{GRAY}" letter-spacing="0.14em">{SUBTITLE}</text>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8")


def _svg_to_png(svg_path: Path, out_path: Path, w: int, h: int) -> bool:
    """Optional rsvg/cairosvg conversion; returns False if unavailable."""
    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(url=str(svg_path), write_to=str(out_path), output_width=w, output_height=h)
        return True
    except Exception:
        pass
    for cmd in (
        ["rsvg-convert", "-w", str(w), "-h", str(h), "-o", str(out_path), str(svg_path)],
        ["qlmanage", "-t", "-s", str(max(w, h)), "-o", str(out_path.parent), str(svg_path)],
    ):
        import subprocess

        try:
            subprocess.run(cmd, check=True, capture_output=True)
            if out_path.is_file():
                return True
        except Exception:
            continue
    return False


def _make_safe_area_reference() -> Image.Image:
    w, h = 1920, 1080
    img = Image.new("RGBA", (w, h), _hex_rgba("#12141a", 1.0))
    draw = ImageDraw.Draw(img)
    m = SAFE_MARGIN
    draw.rectangle((m, m, w - m, h - m), outline=_hex_rgba(GRAY, 0.35), width=2)
    # 12% max width guide
    max_wm_w = int(w * 0.12)
    draw.rectangle((w - m - max_wm_w, h - m - 80, w - m, h - m), outline=_hex_rgba(RED, 0.5), width=1)
    wm = render_watermark(SIZES["1080p"], variant="transparent")
    wm = wm.resize((320, 80), Image.Resampling.LANCZOS)
    img.paste(wm, (w - m - 320, h - m - 80), wm)
    # top-left alternate
    img.paste(wm, (m, m), wm)
    f = _load_font(_FONT_LT, 18)
    lines = [
        "StateVerge SV Watermark — Safe Area (1080p reference)",
        f"Margin: {SAFE_MARGIN}px  |  Max logo width: ≤12% frame ({max_wm_w}px)",
        "Default: bottom-right  |  Alternate: top-left",
        f"Opacity: 75%–88% (default {int(DEFAULT_OPACITY * 100)}%)",
    ]
    y = m + 100
    for line in lines:
        draw.text((m, y), line, font=f, fill=GRAY)
        y += 26
    return img


def _make_style_guide() -> Image.Image:
    w, h = 1600, 2200
    img = Image.new("RGBA", (w, h), _hex_rgba(BLACK, 1.0))
    draw = ImageDraw.Draw(img)
    f_h = _load_font(_FONT_SB, 36)
    f_b = _load_font(_FONT_LT, 16)
    draw.text((60, 50), "StateVerge Watermark System v1", font=f_h, fill=WHITE)
    draw.text((60, 100), "SV · Ambient Cinema · Netflix / TVB / HBO aesthetic", font=f_b, fill=GRAY)

    y = 160
    swatches = [(RED, "Primary Red #E50914"), (WHITE, "White #FFFFFF"), (GRAY, "Gray #B3B3B3"), (BLACK, "Black #0A0A0A")]
    x = 60
    for col, label in swatches:
        draw.rectangle((x, y, x + 80, y + 80), fill=col, outline=GRAY if col == BLACK else None)
        draw.text((x, y + 92), label, font=f_b, fill=GRAY)
        x += 200

    y = 320
    variants = [
        ("full_color", "Full color (default on dark footage)"),
        ("monochrome", "Monochrome"),
        ("reversed", "Reversed (light backgrounds)"),
        ("transparent", "Transparent PNG (burn-in / overlay)"),
    ]
    for key, label in variants:
        wm = render_watermark(SIZES["1080p"], variant=key)
        if key == "reversed":
            panel = Image.new("RGBA", (400, 120), _hex_rgba("#E8E8E8", 1.0))
            panel.paste(wm, (40, 20), wm)
            img.paste(panel, (60, y))
        else:
            img.paste(wm, (60, y), wm)
        draw.text((400, y + 28), label, font=f_b, fill=WHITE)
        y += 130

    y += 20
    draw.text((60, y), "Sizes", font=_load_font(_FONT_SB, 24), fill=WHITE)
    y += 40
    for spec in SIZES.values():
        wm = render_watermark(spec, variant="full_color")
        img.paste(wm, (60, y), wm)
        draw.text((60 + spec.w + 24, y + spec.h // 2 - 10), f"{spec.name}: {spec.w}×{spec.h}px", font=f_b, fill=GRAY)
        y += spec.h + 24

    y += 10
    rules = textwrap.dedent(
        """
        Do: minimal SV monogram · wide tracking · bottom-right default · 75–88% opacity
        Don't: glow · thick shadow · gamer style · metal gradients · oversized logo (>12% width)
        Fonts: Montserrat SemiBold (STATEVERGE) · Montserrat Light (AMBIENT CINEMA)
        Icon: SV only — not SSV, STATEV, or SVG file branding confusion
        """
    ).strip()
    for i, line in enumerate(rules.split("\n")):
        draw.text((60, y + i * 22), line.strip(), font=f_b, fill=GRAY)

    return img


def _gradient_bg(w: int, h: int, stops: list[tuple[float, str]]) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    parsed = [(t, _hex_rgb(c)) for t, c in stops]

    def lerp(a: int, b: int, t: float) -> int:
        return int(a + (b - a) * t)

    for y in range(h):
        t = y / max(1, h - 1)
        for i in range(len(parsed) - 1):
            t0, c0 = parsed[i]
            t1, c1 = parsed[i + 1]
            if t0 <= t <= t1:
                u = (t - t0) / max(1e-6, t1 - t0)
                col = tuple(lerp(c0[j], c1[j], u) for j in range(3))
                break
        else:
            col = parsed[-1][1]
        for x in range(w):
            px[x, y] = col
    return img.convert("RGBA")


def _add_city_silhouette(base: Image.Image, *, warm: bool = False) -> Image.Image:
    w, h = base.size
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    rng = [0.12, 0.18, 0.25, 0.32, 0.4, 0.48, 0.55, 0.62, 0.7, 0.78, 0.85]
    for i, cx in enumerate(rng):
        bw = int(w * (0.04 + (i % 5) * 0.012))
        bh = int(h * (0.12 + (i % 7) * 0.045))
        col = _hex_rgba("#1a2030" if not warm else "#2a1810", 0.55 + (i % 3) * 0.1)
        draw.rectangle((int(cx * w) - bw // 2, h - bh, int(cx * w) + bw // 2, h), fill=col)
        # sparse window lights
        if i % 2 == 0:
            for wy in range(h - bh + 12, h - 20, 22):
                draw.rectangle((int(cx * w) - 4, wy, int(cx * w) + 4, wy + 6), fill=_hex_rgba("#FFE8C8" if warm else "#A8C8FF", 0.35))
    return Image.alpha_composite(base, overlay)


def _make_mockup(name: str, stops: list[tuple[float, str]], *, warm: bool = False, ferry: bool = False) -> Image.Image:
    w, h = 1920, 1080
    bg = _gradient_bg(w, h, stops)
    if ferry:
        draw = ImageDraw.Draw(bg)
        # water band + bokeh lights
        for x in range(0, w, 90):
            draw.ellipse((x, h - 180, x + 40, h - 140), fill=_hex_rgba("#FFD080", 0.12))
        draw.rectangle((0, h - 120, w, h), fill=_hex_rgba("#050810", 0.6))
    bg = _add_city_silhouette(bg, warm=warm)
    wm = render_watermark(SIZES["1080p"], variant="transparent")
    # simulate 82% opacity on composite
    alpha = wm.split()[3].point(lambda a: int(a * DEFAULT_OPACITY))
    wm.putalpha(alpha)
    m = SAFE_MARGIN
    bg.paste(wm, (w - m - 320, h - m - 80), wm)
    # label strip (subtle, for internal preview only)
    draw = ImageDraw.Draw(bg)
    f = _load_font(_FONT_LT, 14)
    draw.text((m, h - m - 110), f"Preview · {name}", font=f, fill=_hex_rgba(GRAY, 0.5))
    return bg


def _write_ffmpeg_examples() -> None:
    root = _ROOT
    wm = root / "transparent_png" / "sv_watermark_transparent.png"
    text = textwrap.dedent(
        f"""
        # StateVerge SV Watermark — FFmpeg overlay examples (v1)
        # Asset: {wm}
        # Default position: bottom-right, margin {SAFE_MARGIN}px
        # Opacity: bake into PNG alpha, or use colorchannelmixer (0.75–0.88)

        # --- 1080p long (canonical 320×80) ---
        ffmpeg -i input.mp4 \\
          -i "{wm}" \\
          -filter_complex "[1:v]scale=320:80:flags=lanczos,format=rgba,colorchannelmixer=aa=0.82[wm];[0:v][wm]overlay=W-w-{SAFE_MARGIN}:H-h-{SAFE_MARGIN}:format=auto" \\
          -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \\
          -c:a copy \\
          output_1080p_branded.mp4

        # --- 4K long (640×160) ---
        ffmpeg -i input_4k.mp4 \\
          -i "{root / 'full_color' / 'sv_watermark_4k.png'}" \\
          -filter_complex "[1:v]scale=640:160:flags=lanczos,format=rgba,colorchannelmixer=aa=0.82[wm];[0:v][wm]overlay=W-w-{SAFE_MARGIN}:H-h-{SAFE_MARGIN}:format=auto" \\
          -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \\
          -c:a copy \\
          output_4k_branded.mp4

        # --- Shorts 9:16 (220×60), bottom-right ---
        ffmpeg -i input_shorts.mp4 \\
          -i "{root / 'full_color' / 'sv_watermark_shorts.png'}" \\
          -filter_complex "[1:v]scale=220:60:flags=lanczos,format=rgba,colorchannelmixer=aa=0.80[wm];[0:v][wm]overlay=W-w-32:H-h-32:format=auto" \\
          -c:v libx264 -crf 20 -preset medium -pix_fmt yuv420p \\
          -c:a copy \\
          output_shorts_branded.mp4

        # --- Top-left alternate (Long review / TVB-style corner) ---
        ffmpeg -i input.mp4 -i "{wm}" \\
          -filter_complex "[1:v]scale=320:80,format=rgba,colorchannelmixer=aa=0.82[wm];[0:v][wm]overlay={SAFE_MARGIN}:{SAFE_MARGIN}:format=auto" \\
          -c:v libx264 -crf 18 -preset slow -c:a copy output_topleft.mp4

        # --- macOS VideoToolbox (faster 4K long) ---
        ffmpeg -hwaccel videotoolbox -i input_4k.mp4 -i "{wm}" \\
          -filter_complex "[1:v]scale=640:160,format=rgba,colorchannelmixer=aa=0.82[wm];[0:v][wm]overlay=W-w-{SAFE_MARGIN}:H-h-{SAFE_MARGIN}" \\
          -c:v h264_videotoolbox -b:v 28M -maxrate 32M -bufsize 64M -c:a copy output_4k_vt.mp4

        # --- Simple overlay (user spec; opacity from PNG alpha) ---
        ffmpeg -i input.mp4 \\
          -i "{wm}" \\
          -filter_complex "overlay=W-w-{SAFE_MARGIN}:H-h-{SAFE_MARGIN}:format=auto" \\
          -c:v libx264 -crf 18 -preset slow \\
          -c:a copy \\
          output.mp4

        # Helper script (scaled overlay + optional in-place):
        #   .venv_audio/bin/python3 scripts/apply_stateverge_ambient_watermark.py \\
        #     --input input.mp4 --watermark-png "{wm}" --position bottom_right --opacity 0.82
        """
    ).strip()
    (_ROOT / "ffmpeg" / "ffmpeg_overlay_examples.txt").write_text(text + "\n", encoding="utf-8")


def _key_black_to_alpha(img: Image.Image, *, threshold: int = 28) -> Image.Image:
    """Turn near-black background transparent; keep folded red S geometry."""
    src = img.convert("RGBA")
    out = Image.new("RGBA", src.size, (0, 0, 0, 0))
    sp = src.load()
    op = out.load()
    w, h = src.size
    th = max(0, min(255, int(threshold)))
    for y in range(h):
        for x in range(w):
            r, g, b, _a = sp[x, y]
            if r <= th and g <= th and b <= th:
                continue
            op[x, y] = (r, g, b, 255)
    return out


def _render_channel_square_from_master(
    master: Path,
    *,
    size: int = 800,
    fill_ratio: float = 0.96,
    key_threshold: int = 28,
) -> Image.Image | None:
    if not master.is_file():
        return None
    keyed = _key_black_to_alpha(Image.open(master), threshold=key_threshold)
    bbox = keyed.getbbox()
    if not bbox:
        return None
    cropped = keyed.crop(bbox)
    cw, ch = cropped.size
    target = max(1, int(round(size * fill_ratio)))
    scale = min(target / cw, target / ch)
    nw = max(1, int(round(cw * scale)))
    nh = max(1, int(round(ch * scale)))
    scaled = cropped.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(scaled, ((size - nw) // 2, (size - nh) // 2), scaled)
    return canvas


def render_channel_square_800(*, visible: bool = False) -> Image.Image:
    """YouTube channel watermark — mandatory 800×800 square, PNG with alpha.

    Default (visible=False): hollow/transparent canvas, **red S centered** only.
    Uses `source/s_monogram_channel_master.png` when present (3D ribbon S); else font fallback.
    YouTube scales the square in the viewer corner; no black panel, no wordmark.
    Legacy visible=True: filled panel + SV + STATEVERGE stack (deprecated for channel).
    """
    size = 800
    if not visible:
        from_master = _render_channel_square_from_master(_CHANNEL_S_MASTER)
        if from_master is not None:
            return from_master

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    if not visible:
        f_s = _load_font(_FONT_SB, 340)
        letter = "S"
        bbox = draw.textbbox((0, 0), letter, font=f_s)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (size - tw) // 2 - bbox[0]
        ty = (size - th) // 2 - bbox[1]
        draw.text((tx, ty), letter, font=f_s, fill=RED)
        return canvas

    # Legacy filled layout (optional)
    panel = 760
    margin = 20
    px = margin
    py = margin
    draw.rounded_rectangle(
        (px, py, px + panel, py + panel),
        radius=20,
        fill=_hex_rgba(BLACK, 0.82),
        outline=_hex_rgba(GRAY, 0.28),
        width=1,
    )
    f_sv = _load_font(_FONT_SB, 108)
    f_title = _load_font(_FONT_SB, 52)
    f_sub = _load_font(_FONT_LT, 28)
    cx = px + panel // 2
    y = py + 52
    s_w = draw.textlength("S", font=f_sv)
    draw.text((cx - s_w - 4, y), "S", font=f_sv, fill=RED, anchor="lt")
    draw.text((cx - 4, y), "V", font=f_sv, fill=WHITE, anchor="lt")
    y += 130
    tw = draw.textlength(PRIMARY, font=f_title)
    draw.text((cx - tw / 2, y), PRIMARY, font=f_title, fill=WHITE)
    y += 62
    sw = draw.textlength(SUBTITLE, font=f_sub)
    draw.text((cx - sw / 2, y), SUBTITLE, font=f_sub, fill=GRAY)
    draw.rectangle((px + 14, py + 48, px + 18, py + panel - 48), fill=_hex_rgba(RED, 0.7))
    return canvas


def _write_davinci_md() -> None:
    text = textwrap.dedent(
        """
        # StateVerge SV Watermark — DaVinci Resolve

        ## Import
        1. **Media Pool** → import `transparent_png/sv_watermark_transparent.png` (or size-matched PNG from `full_color/`).
        2. For **4K** timelines use `full_color/sv_watermark_4k.png` (640×160). For **Shorts** use `sv_watermark_shorts.png` (220×60).

        ## Timeline placement
        | Format | PNG | Track |
        |--------|-----|-------|
        | 1080p Long | 320×80 | V2 over picture |
        | 4K Long | 640×160 | V2 |
        | 9:16 Shorts | 220×60 | V2 |

        ## Transform
        - **Position**: Bottom Right (default). Alternate: Top Left for review / compliance cuts.
        - **Margin**: ≥ **40px** from edges (see `safe_area_reference.png`).
        - **Width**: do not exceed **12%** of frame width.
        - **Opacity**: **75%–88%** (recommended **82%**).

        ## Fusion (optional precise margin)
        ```
        // Bottom-right with 40px safe margin on 1920×1080
        X = (InputWidth - DaVinciResolve.GetWatermarkWidth()) - 40
        Y = (InputHeight - DaVinciResolve.GetWatermarkHeight()) - 40
        ```

        ## Color versions
        - **Dark footage** (NYC harbor, ferry night, skyline): `sv_watermark_transparent.png` or `sv_watermark_red.png`
        - **Bright / snow / fog**: `reversed/sv_watermark_reversed.png` or `monochrome/sv_watermark_white.png`
        - **High-contrast B&W grade**: `monochrome/sv_watermark_mono.png`

        ## Export
        - Burn-in on deliverable masters only when platform requires in-file branding.
        - YouTube Long: prefer **channel watermark** (Studio) for 4K; use timeline PNG for Shorts or forced deliverables.

        ## Style
        Ambient cinema — minimal SV lockup, no glow, no gamer outlines. Reference: Netflix, TVB Jade, HBO, Apple TV+, NHK World.
        """
    ).strip()
    (_ROOT / "davinci" / "davinci_usage.md").write_text(text + "\n", encoding="utf-8")


def main() -> int:
    dirs = [
        _ROOT,
        _ROOT / "full_color",
        _ROOT / "monochrome",
        _ROOT / "reversed",
        _ROOT / "transparent_png",
        _ROOT / "svg",
        _ROOT / "ffmpeg",
        _ROOT / "davinci",
        _ROOT / "previews",
        _ROOT / "source",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Canonical 1080p exports at root
    variants_map = {
        "full_color": ("sv_watermark_red.png", "full_color"),
        "monochrome": ("sv_watermark_white.png", "monochrome"),  # white-on-dark mono
        "reversed": ("sv_watermark_black.png", "reversed"),
        "transparent": ("sv_watermark_transparent.png", "transparent_png"),
    }
    sub_names = {
        "full_color": "sv_watermark_red.png",
        "monochrome": "sv_watermark_mono.png",
        "reversed": "sv_watermark_reversed.png",
        "transparent": "sv_watermark_transparent.png",
    }
    for variant, (fname, sub) in variants_map.items():
        img = render_watermark(SIZES["1080p"], variant=variant)
        _save_png(img, _ROOT / fname)
        _save_png(img, _ROOT / sub / sub_names[variant])

    # Size variants (full color + transparent)
    for spec in SIZES.values():
        fc = render_watermark(spec, variant="full_color")
        tr = render_watermark(spec, variant="transparent")
        suffix = "" if spec.name == "1080p" else f"_{spec.name.replace('4k', '4k')}"
        if spec.name == "1080p":
            pass
        elif spec.name == "4k":
            _save_png(fc, _ROOT / "full_color" / "sv_watermark_4k.png")
            _save_png(tr, _ROOT / "transparent_png" / "sv_watermark_4k_transparent.png")
        else:
            _save_png(fc, _ROOT / "full_color" / "sv_watermark_shorts.png")
            _save_png(tr, _ROOT / "transparent_png" / "sv_watermark_shorts_transparent.png")

    # White explicit copy for dark scenes
    white = render_watermark(SIZES["1080p"], variant="monochrome")
    _save_png(white, _ROOT / "monochrome" / "sv_watermark_white.png")
    _save_png(white, _ROOT / "sv_watermark_white.png")

    # SVG
    svg_path = _ROOT / "svg" / "sv_watermark.svg"
    _write_svg(svg_path)
    _write_svg(_ROOT / "sv_watermark.svg")

    # Guides
    _save_png(_make_style_guide(), _ROOT / "style_guide.png")
    _save_png(_make_safe_area_reference(), _ROOT / "safe_area_reference.png")

    # Mockups
    mockups = {
        "preview_cinematic_dark": ([(0, "#080a12"), (0.5, "#121826"), (1, "#1a1020")], False, False),
        "preview_hong_kong_night": ([(0, "#050810"), (0.45, "#0c1830"), (1, "#1a2848")], False, False),
        "preview_ferry_night": ([(0, "#030508"), (0.6, "#0a1420"), (1, "#101820")], False, True),
        "preview_nyc_skyline": ([(0, "#0a0810"), (0.4, "#1a1428"), (1, "#2a1818")], True, False),
        "preview_dark_ambient": ([(0, "#050505"), (1, "#121212")], False, False),
    }
    for name, (stops, warm, ferry) in mockups.items():
        _save_png(_make_mockup(name, stops, warm=warm, ferry=ferry), _ROOT / "previews" / f"{name}.png")

    _write_ffmpeg_examples()
    _write_davinci_md()

    # YouTube Long channel watermark (800×800 square, hollow + red S centered)
    ch_800 = render_channel_square_800(visible=False)
    ch_paths = [
        _ROOT / "transparent_png" / "stateverge_channel_watermark_800.png",
        _ROOT / "full_color" / "stateverge_channel_watermark_800.png",
        _REPO / "assets" / "branding" / "stateverge_ambient_cinema_channel_watermark_800.png",
    ]
    for p in ch_paths:
        _save_png(ch_800, p)

    # Symlink legacy path for existing scripts
    legacy = _REPO / "assets" / "branding" / "stateverge_ambient_cinema_watermark_320x80.png"
    src = _ROOT / "transparent_png" / "sv_watermark_transparent.png"
    if not legacy.is_file() or legacy.stat().st_size < 1000:
        import shutil

        shutil.copy2(src, legacy)

    print(f"WATERMARK_SV_ROOT={_ROOT}")
    print("GENERATED=ok")
    for p in sorted(_ROOT.rglob("*")):
        if p.is_file() and p.suffix in {".png", ".svg", ".txt", ".md"}:
            print(f"  {p.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
