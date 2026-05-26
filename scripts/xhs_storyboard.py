"""
xhs_storyboard.py — turn a scraped XHS note folder into a video brief.

Given a folder produced by ``xhs_grab.py`` (i.e. ``assets/xhs/<note_id>/``
containing ``caption.txt``, ``img_*.jpg``, optional ``video_*.mp4``, and
``_meta.json``), this script generates:

    narration_zh.txt       1-minute Chinese narration (~280-310 chars)
    runway_prompts.json    6 prompts of 10 seconds each, each pinned to
                           one of the downloaded images, ready to feed
                           Runway Gen-3/Gen-4 image-to-video.
    storyboard.md          Human-readable bundle of narration + prompts.

旁白节奏默认对齐「0–3 秒钩子 → … → 45–60 秒收束」五段节拍（见 ``timing_beats_ref``），
Runway 仍为 6×10 秒；六个旁白片段按 0–10 / … / 50–60 秒切段。

It uses OpenAI (chat.completions, model from env ``OPENAI_MODEL`` or
``gpt-4o-mini``) when ``OPENAI_API_KEY`` is set; otherwise it falls back to
a deterministic local template so the pipeline still completes.

When ``FMP_API_KEY`` is set and a US ticker appears in the title/body/caption
(e.g. ``$TSLA``, ``NASDAQ: AAPL``), the script pulls annual YoY revenue / net
income and margin snapshots via ``src/finance/analyzer.py``, writes
``_fmp_snapshot.json``, and folds numbers into the LLM prompt and fallback spine.

Usage
-----

    # one note
    ./.venv/bin/python scripts/xhs_storyboard.py assets/xhs/<note_id>

    # every note that doesn't yet have a storyboard
    ./.venv/bin/python scripts/xhs_storyboard.py --all

    # force regeneration even if files exist
    ./.venv/bin/python scripts/xhs_storyboard.py --all --force
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XHS_ROOT = REPO_ROOT / "assets" / "xhs"

LOG_PREFIX = "[xhs_storyboard]"

NARRATION_TARGET_CHARS = 295   # ~60 sec at ~5 chars/sec Chinese narration
NARRATION_MIN_CHARS = 260
NARRATION_MAX_CHARS = 330

NUM_SCENES = 6
SCENE_SECONDS = 10

# Reference beat sheet for ~60s VO (wall-clock when read aloud). Runway stays 6×10s;
# scene narration fragments should map ~0–10s … ~50–60s cumulatively.
TIMING_BEATS_REF = [
    {
        "range_label": "0–3 秒",
        "t_start_sec": 0,
        "t_end_sec": 3,
        "focus": "钩子",
        "must_cover": "这家公司，可能出问题了",
    },
    {
        "range_label": "3–15 秒",
        "t_start_sec": 3,
        "t_end_sec": 15,
        "focus": "财报事实",
        "must_cover": "最新财报显示，收入下降X%，利润下滑更快（X 从正文提炼）",
    },
    {
        "range_label": "15–30 秒",
        "t_start_sec": 15,
        "t_end_sec": 30,
        "focus": "盈利能力",
        "must_cover": "这意味着它的盈利能力正在恶化",
    },
    {
        "range_label": "30–45 秒",
        "t_start_sec": 30,
        "t_end_sec": 45,
        "focus": "核心业务",
        "must_cover": "更大的问题是，它的核心业务正在放缓",
    },
    {
        "range_label": "45–60 秒",
        "t_start_sec": 45,
        "t_end_sec": 60,
        "focus": "短期压力",
        "must_cover": "短期来看，这家公司压力不小",
    },
]


def _timing_beats_md_table() -> str:
    rows = ["| 时间段 | 节拍 | 必须点到 |", "|---|---|---|"]
    for b in TIMING_BEATS_REF:
        rows.append(
            f"| {b['range_label']} | {b['focus']} | {b['must_cover']} |"
        )
    return "\n".join(rows)


# --- Optional FMP (US ticker → YoY / margins) --------------------------------

_TICKER_DENY = frozenset({
    "THE", "AND", "FOR", "ARE", "WAS", "ITS", "NOT", "BUT", "YOU", "ALL", "CAN",
    "NEW", "TOP", "BIG", "USA", "CEO", "IPO", "ETF", "GDP", "CPI", "FOMC", "HOW",
    "WHY", "WAY", "DAY", "ONE", "TWO", "OUR", "OUT", "LOW", "NOW", "MAY", "GET",
    "HAS", "HAD", "HIS", "HER", "YES", "OFF", "OWN", "USE", "TRY", "LET", "PUT",
    "END", "LOT", "FEW", "ANY", "SAID", "MAN", "TOO", "VERY", "WHEN", "WITH",
    "FROM", "THIS", "THAT", "WHAT", "THESE", "THOSE", "THERE", "THEIR", "WOULD",
    "COULD", "SHOULD", "MIGHT", "EVERY", "OTHER", "ABOUT", "AFTER", "BEFORE",
    "AGAIN", "UNDER", "ABOVE", "BELOW", "WHILE", "UNTIL", "SINCE", "HTTP",
    "HTTPS", "HTML", "JSON", "API", "AWS", "PDF", "PNG", "JPG", "GIF", "CFO",
    "CTO", "IRS", "SEC", "FDA", "WHO",
})

_RE_TICKER_DOLLAR = re.compile(r"\$([A-Za-z]{1,5})\b")
_RE_TICKER_EXCHANGE = re.compile(
    r"(?:NASDAQ|NYSE|NYSEARCA|AMEX)\s*[:：]\s*([A-Za-z]{1,5})\b",
    re.I,
)
_RE_TICKER_DOT_SUFFIX = re.compile(r"\b([A-Za-z]{1,5})\.(?:US|NYSE|NASDAQ)\b", re.I)
_RE_TICKER_PARENS = re.compile(r"[（(]\s*([A-Za-z]{1,5})\s*[）)]")
_RE_TICKER_STANDALONE = re.compile(r"\b([A-Z]{3,5})\b")


def _guess_us_symbol(blob: str) -> str | None:
    """Pick a likely US ticker from title/body/caption (best-effort)."""
    if not blob or not blob.strip():
        return None
    for rx in (
        _RE_TICKER_DOLLAR,
        _RE_TICKER_EXCHANGE,
        _RE_TICKER_DOT_SUFFIX,
        _RE_TICKER_PARENS,
    ):
        m = rx.search(blob)
        if m:
            sym = m.group(1).strip().upper()
            if len(sym) >= 1:
                return sym
    for m in _RE_TICKER_STANDALONE.finditer(blob.upper()):
        sym = m.group(1)
        if sym not in _TICKER_DENY:
            return sym
    return None


def _fetch_fmp_snapshot(ctx: dict, env: dict[str, str]) -> dict | None:
    """
    If FMP_API_KEY is set and a ticker is guessed, attach YoY revenue / NI + margins.
    """
    key = (env.get("FMP_API_KEY") or "").strip()
    if not key:
        return None
    blob = "\n".join([
        ctx.get("title") or "",
        ctx.get("desc") or "",
        ctx.get("caption") or "",
    ])
    sym = _guess_us_symbol(blob)
    if not sym:
        return None
    root = str(REPO_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from src.integrations.fmp_client import FMPClient
        from src.finance.analyzer import compute_metrics, fetch_fmp_bundle
    except ImportError as exc:
        log(f"  WARN cannot import FMP modules ({exc}); skip snapshot")
        return None
    try:
        client = FMPClient(api_key=key)
        bundle = fetch_fmp_bundle(client, sym)
        metrics = compute_metrics(bundle)
    except Exception as exc:  # noqa: BLE001 — defensive network/API path
        log(f"  WARN FMP fetch failed for {sym}: {exc}")
        return None
    prof = (bundle.get("profile") or [{}])[0]
    if isinstance(prof, dict):
        name = (prof.get("companyName") or prof.get("symbol") or sym).strip()
    else:
        name = sym
    snap = {
        "symbol": bundle.get("symbol") or sym,
        "company_name": name,
        "revenue_yoy": metrics.get("revenue_yoy"),
        "net_income_yoy": metrics.get("net_income_yoy"),
        "gross_margin": metrics.get("gross_margin"),
        "net_margin": metrics.get("net_margin"),
        "market_cap": metrics.get("market_cap"),
        "latest_fiscal_year": metrics.get("latest_fiscal_year"),
        "revenue_latest": metrics.get("revenue_latest"),
        "net_income_latest": metrics.get("net_income_latest"),
        "fetched_at": bundle.get("fetched_at"),
        "source": bundle.get("source") or "financialmodelingprep.com",
    }
    log(f"  + FMP snapshot  {snap['symbol']}  ({snap['company_name']})")
    return snap


def _format_fmp_prompt_block(fmp: dict | None) -> str:
    """Human-readable block for the LLM system prompt."""
    if not fmp:
        return ""
    lines = [
        "【FMP 实盘参考（若与正文冲突，以正文为准；数字写入旁白须口语化）】",
        f"- 标的：{fmp.get('company_name') or '—'} ({fmp.get('symbol') or '—'})",
    ]
    fy = fmp.get("latest_fiscal_year")
    if fy:
        lines.append(f"- 最近财年：{fy}")
    ry = fmp.get("revenue_yoy")
    ny = fmp.get("net_income_yoy")
    if ry is not None:
        lines.append(f"- 营收同比（YoY）：{ry:+.2f}%")
    if ny is not None:
        lines.append(f"- 净利润同比（YoY）：{ny:+.2f}%")
    gm = fmp.get("gross_margin")
    nm = fmp.get("net_margin")
    if gm is not None:
        lines.append(f"- 毛利率（最新财年）：{gm:.2f}%")
    if nm is not None:
        lines.append(f"- 净利率（最新财年）：{nm:.2f}%")
    lines.append("")
    return "\n".join(lines)


def _fallback_revenue_profit_clauses(desc: str, fmp: dict | None) -> tuple[str, str]:
    """
    Build two fragments for the earnings sentence: revenue clause, profit clause.
    """
    if fmp and fmp.get("revenue_yoy") is not None:
        ry = float(fmp["revenue_yoy"])
        if ry < -0.005:
            rev = f"收入同比下滑约{abs(ry):.1f}%"
        elif ry > 0.005:
            rev = f"营收同比增长约{ry:.1f}%"
        else:
            rev = "营收同比基本持平"
    else:
        rev = f"收入下降{_extract_revenue_pct_clause(desc)}"

    if fmp and fmp.get("net_income_yoy") is not None and fmp.get("revenue_yoy") is not None:
        ni = float(fmp["net_income_yoy"])
        ry = float(fmp["revenue_yoy"])
        if ni < ry - 1.0:
            prof = "利润下滑更快"
        elif ni > ry + 1.0:
            prof = "利润修复跑赢营收"
        else:
            prof = "利润走势与营收相近"
    else:
        prof = "利润下滑更快"

    return rev, prof


def _extract_revenue_pct_clause(desc: str) -> str:
    """Best-effort: pull '收入下降X%' style clause from note text."""
    if not desc.strip():
        return "若干个百分点"
    # First percentage in a revenue / sales context
    m = re.search(
        r"(?:收入|营收|销售额)[^。%\n]{0,24}?(\d+(?:\.\d+)?)\s*%",
        desc,
    )
    if m:
        return f"{m.group(1)}%"
    # Any first percentage in text (often revenue)
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*%", desc)
    if m2:
        return f"{m2.group(1)}%"
    return "若干个百分点"


def log(msg: str) -> None:
    print(f"{LOG_PREFIX} {msg}", flush=True)


# ---------------------------------------------------------------------------
# Env / project helpers (kept dependency-free)
# ---------------------------------------------------------------------------


def read_project_env() -> dict[str, str]:
    """Merge process env with values from REPO_ROOT/.env (without overriding)."""
    env = dict(os.environ)
    p = REPO_ROOT / ".env"
    if not p.is_file():
        return env
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        env.setdefault(key.strip(), value.strip().strip("'\""))
    return env


# ---------------------------------------------------------------------------
# Note folder discovery
# ---------------------------------------------------------------------------


def is_note_folder(path: Path) -> bool:
    if not path.is_dir():
        return False
    if (path / "_meta.json").is_file():
        return True
    if any(path.glob("img_*.*")) or any(path.glob("video_*.*")):
        return True
    return False


def list_note_folders(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if is_note_folder(p))


def pick_scene_images(folder: Path, n: int) -> list[Path]:
    """Pick exactly n images for the n scenes, evenly spaced (cycling if few)."""
    images = sorted(
        list(folder.glob("img_*.jpg"))
        + list(folder.glob("img_*.jpeg"))
        + list(folder.glob("img_*.png"))
        + list(folder.glob("img_*.webp"))
    )
    if not images:
        return []
    if len(images) >= n:
        # Evenly-spaced sample so we don't pile up on the first n images.
        step = len(images) / n
        return [images[int(i * step)] for i in range(n)]
    out: list[Path] = []
    for i in range(n):
        out.append(images[i % len(images)])
    return out


def load_note_context(folder: Path) -> dict:
    meta_path = folder / "_meta.json"
    meta: dict = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            meta = {}

    caption_path = folder / "caption.txt"
    caption = caption_path.read_text(encoding="utf-8") if caption_path.is_file() else ""

    title = (meta.get("title") or "").strip()
    desc = (meta.get("desc") or "").strip()
    tags = meta.get("tags") or []
    author = (meta.get("author") or "").strip()

    if not (title or desc) and caption:
        # Recover title/desc from caption.txt if _meta.json is sparse.
        lines = caption.splitlines()
        if lines and lines[0].startswith("# "):
            title = title or lines[0][2:].strip()
            desc = desc or "\n".join(lines[1:]).strip()
        else:
            desc = desc or caption.strip()

    return {
        "folder": folder,
        "note_id": folder.name,
        "title": title,
        "desc": desc,
        "tags": tags,
        "author": author,
        "caption": caption,
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# OpenAI call
# ---------------------------------------------------------------------------


def _chat_openai(prompt: str, env: dict[str, str]) -> str | None:
    key = env.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    payload = {
        "model": env.get("OPENAI_MODEL", "gpt-4o-mini"),
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a bilingual finance / news short-video director. "
                    "Every script MUST follow three pillars in order: "
                    "(1) what happened on the main storyline (company/market event); "
                    "(2) why things changed (causal explanation); "
                    "(3) contrast — who is more exposed / who faces greater risk. "
                    "You write tight Chinese narration and clean English Runway prompts. "
                    "Always respond with a single JSON object matching the schema, "
                    "no markdown fences."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.7,
        "response_format": {"type": "json_object"},
    }
    req = Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(req, timeout=120) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        log(f"openai=fallback reason={exc}")
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    return (choices[0].get("message", {}).get("content") or "").strip() or None


def _build_llm_prompt(ctx: dict, scene_image_names: list[str]) -> str:
    title = ctx["title"] or "(无标题)"
    desc = ctx["desc"] or "(无正文)"
    tags = ctx["tags"] or []
    fmp_block = _format_fmp_prompt_block(ctx.get("fmp"))
    scenes_lines = "\n".join(
        f"  scene {i + 1}: {name}" for i, name in enumerate(scene_image_names)
    )
    tags_str = ", ".join(tags) if tags else "无"
    beats_tbl = _timing_beats_md_table()
    return f"""下面是一篇小红书笔记的素材。请基于它生成一个 60 秒短视频脚本。

**叙事骨架：** ① 发生了什么（事实）；② 为什么变了（解释）；③ 短期风险与压力（收束）。

**口播时间轴（约 60 秒整段朗读时必须贴合下列节拍；百分比优先采用下文「FMP 实盘参考」（若有），否则从正文提炼；再缺失则用「若干」「明显」「更快」等克制表述）：**

{beats_tbl}

{fmp_block}**与 6 个 Runway 分镜（每镜 10 秒）的关系：**
`narration_zh` 写成一整段连贯口播（可加句号，不要 markdown）；六个 `scenes[i].narration_zh` 必须按时间顺序切成 **6 段**，每段约对应朗读 **10 秒**，依次为 **0–10、10–20 … 50–60 秒**，语义上覆盖上表五段节拍（节拍可跨 scene）。

【标题】{title}
【正文】{desc}
【标签】{tags_str}

已下载的配图（按顺序，将一一对应 6 个分镜，每镜 10 秒）：
{scenes_lines}

**分镜内容与时间轴一致（示意）：**
- Scene 1–2：0–3 秒钩子 + 3–15 秒财报事实；
- Scene 3–4：15–45 秒盈利恶化 + 主业放缓；
- Scene 5–6：45–60 秒短期压力收束。

请严格输出一个 JSON 对象，schema 如下：
{{
  "narration_zh": "string，约 280-310 个汉字的连贯口播旁白，口语化、有信息密度；不要使用 markdown，不要列点。",
  "scenes": [
    {{
      "index": 1,
      "duration_sec": 10,
      "image_ref": "scene 对应的图片文件名，原样回传",
      "narration_zh": "本镜对应的旁白片段（汉字，从 narration_zh 中切出）",
      "prompt": "Runway Gen-3/4 image-to-video 用的英文运动+视觉提示词，1-2 句；画面须呼应本镜对应时间段（财报冲击 / 盈利恶化 / 主业放缓 / 短期压力）；不要写文字、字幕、水印。",
      "camera": "简短英文相机指令，如 slow dolly in / static / handheld pan left",
      "negative_prompt": "标准 Runway 负面词，例如 text, watermark, distorted hands, low quality, jitter"
    }}
    // 共 {NUM_SCENES} 个 scene，index 从 1 到 {NUM_SCENES}，每个 duration_sec=10
  ]
}}

硬性要求：
- narration_zh 总字数（不含标点）控制在 {NARRATION_MIN_CHARS}-{NARRATION_MAX_CHARS} 个汉字，目标 {NARRATION_TARGET_CHARS}。
- scenes 数组长度必须正好等于 {NUM_SCENES}。
- 每个 scene 的 image_ref 必须等于上面列出的对应文件名，按顺序。
- prompt / camera / negative_prompt 全部用英文。
- 直接输出 JSON 对象，不要 ``` 代码块、不要解释。
"""


# ---------------------------------------------------------------------------
# Offline fallback
# ---------------------------------------------------------------------------


_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _count_cjk(s: str) -> int:
    return sum(1 for _ in _CJK_RE.finditer(s))


def _fallback_storyboard(ctx: dict, scene_image_names: list[str]) -> dict:
    """Deterministic script following TIMING_BEATS_REF / user's 60s earnings spine."""
    title = ctx["title"] or ctx["note_id"]
    desc = ctx["desc"] or ""
    fmp = ctx.get("fmp") if isinstance(ctx.get("fmp"), dict) else None
    rev_clause, prof_clause = _fallback_revenue_profit_clauses(desc, fmp)

    # Core spine (matches user-provided beat sheet; income line uses best-effort X%).
    narration = (
        "这家公司，可能出问题了。"
        f"最新财报显示，{rev_clause}，{prof_clause}。"
        "这意味着它的盈利能力正在恶化。"
        "更大的问题是，它的核心业务正在放缓。"
        "短期来看，这家公司压力不小。"
    )
    # Light anchor to the note title so it doesn't read completely generic.
    if title and title not in narration:
        narration = f"围绕「{title}」，先看一句判断：{narration}"

    _tail_pad = (
        "读财报要先分清一次性因素与结构性放缓。"
        "主业与现金流比单季 headline 数字更接近真相。"
        "宏观只是在定价风险溢价，质地决定你能不能扛波动。"
        "把叙事拧成一条线：先看事实冲击，再看因果解释，最后看谁在为波动买单。"
    )
    _guard = 0
    while _count_cjk(narration) < NARRATION_MIN_CHARS and _guard < 4:
        narration += _tail_pad
        _guard += 1
    if _count_cjk(narration) < NARRATION_MIN_CHARS:
        narration += "结尾仍需紧盯主业指引与现金流安全边际。"
    if _count_cjk(narration) > NARRATION_MAX_CHARS:
        out_chars: list[str] = []
        cjk = 0
        for ch in narration:
            out_chars.append(ch)
            if _CJK_RE.match(ch):
                cjk += 1
            if cjk >= NARRATION_TARGET_CHARS:
                break
        narration = "".join(out_chars)

    # Split into 6 roughly-equal slices for per-scene VO (~10s each when read).
    parts: list[str] = []
    n = len(narration)
    step = max(1, n // NUM_SCENES)
    for i in range(NUM_SCENES):
        start = i * step
        end = n if i == NUM_SCENES - 1 else (i + 1) * step
        parts.append(narration[start:end].strip())

    cameras = [
        "slow dolly in",
        "handheld pan left",
        "slow push in on charts",
        "static medium shot",
        "split-screen juxtaposition, handheld drift",
        "smooth orbit right",
    ]
    prompts_en = [
        # ~0–10s hook + numbers
        (
            "Cold open on an ominous corporate skyline or empty headquarters lobby, "
            "slow creeping dolly-in, tension building, documentary realism."
        ),
        (
            "Earnings document macro: revenue and profit highlights sliding downward "
            "on layered spreadsheet HUD, subtle handheld anxiety."
        ),
        # ~20–40s profitability / core slowdown
        (
            "Margin compression metaphor: shrinking rings or draining liquidity visuals "
            "over financial charts, muted office daylight."
        ),
        (
            "Core business slowdown: factory throughput or SaaS dashboard latency "
            "ticking slower, rack-focus between KPI tiles."
        ),
        # ~40–60s pressure / outlook
        (
            "Risk tableau: storm clouds over industrial campus or bearish equity curve "
            "reflection on glass, cinematic teal-orange contrast."
        ),
        (
            "Closing beat: solitary executive silhouette against ticker wall, "
            "slow exhale camera orbit, unresolved tension."
        ),
    ]
    moods_suffix = (
        " photoreal, shallow depth of field, 24fps cinematic look."
    )
    scenes: list[dict] = []
    for i, name in enumerate(scene_image_names):
        scenes.append({
            "index": i + 1,
            "duration_sec": SCENE_SECONDS,
            "image_ref": name,
            "narration_zh": parts[i] if i < len(parts) else "",
            "prompt": prompts_en[i % len(prompts_en)].rstrip(".") + moods_suffix,
            "camera": cameras[i % len(cameras)],
            "negative_prompt": (
                "text, watermark, logo, captions, distorted hands, extra fingers, "
                "low quality, jitter, oversaturated"
            ),
        })

    return {"narration_zh": narration, "scenes": scenes, "_source": "fallback"}


# ---------------------------------------------------------------------------
# Generation orchestrator
# ---------------------------------------------------------------------------


def _normalize_storyboard(
    raw: dict,
    scene_image_names: list[str],
) -> dict:
    """Coerce a model response into our exact schema; tolerate small mistakes."""
    narration = (raw.get("narration_zh") or raw.get("narration") or "").strip()
    raw_scenes = raw.get("scenes") or []
    scenes: list[dict] = []
    for i in range(NUM_SCENES):
        src = raw_scenes[i] if i < len(raw_scenes) and isinstance(raw_scenes[i], dict) else {}
        ref = src.get("image_ref") or (
            scene_image_names[i] if i < len(scene_image_names) else ""
        )
        # If model returned a path-y ref, keep just the filename.
        if isinstance(ref, str):
            ref = ref.strip().split("/")[-1]
        scenes.append({
            "index": i + 1,
            "duration_sec": SCENE_SECONDS,
            "image_ref": ref,
            "narration_zh": (src.get("narration_zh") or src.get("voiceover") or "").strip(),
            "prompt": (src.get("prompt") or "").strip(),
            "camera": (src.get("camera") or "").strip(),
            "negative_prompt": (
                src.get("negative_prompt")
                or "text, watermark, logo, distorted hands, low quality, jitter"
            ).strip(),
        })
    return {"narration_zh": narration, "scenes": scenes}


def _render_storyboard_md(ctx: dict, sb: dict) -> str:
    title = ctx["title"] or ctx["note_id"]
    lines: list[str] = []
    lines.append(f"# Storyboard — {title}")
    lines.append("")
    lines.append(f"- note_id: `{ctx['note_id']}`")
    if ctx["author"]:
        lines.append(f"- author: {ctx['author']}")
    if ctx["tags"]:
        lines.append(f"- tags: {' '.join('#' + t for t in ctx['tags'])}")
    src = ctx["meta"].get("src_url") if ctx.get("meta") else ""
    if src:
        lines.append(f"- source: {src}")
    lines.append(f"- target duration: {NUM_SCENES * SCENE_SECONDS}s "
                 f"({NUM_SCENES} scenes × {SCENE_SECONDS}s)")
    fmp = ctx.get("fmp")
    if isinstance(fmp, dict) and fmp.get("symbol"):
        lines.append("")
        lines.append("## FMP 快照（实盘参考）")
        lines.append("")
        lines.append(f"- symbol: `{fmp.get('symbol')}`")
        if fmp.get("company_name"):
            lines.append(f"- company: {fmp['company_name']}")
        if fmp.get("latest_fiscal_year"):
            lines.append(f"- fiscal year: {fmp['latest_fiscal_year']}")
        if fmp.get("revenue_yoy") is not None:
            lines.append(f"- revenue YoY: {fmp['revenue_yoy']}%")
        if fmp.get("net_income_yoy") is not None:
            lines.append(f"- net income YoY: {fmp['net_income_yoy']}%")
        if fmp.get("fetched_at"):
            lines.append(f"- fetched_at: {fmp['fetched_at']}")
    lines.append("")
    lines.append("## 口播时间轴（参考节拍）")
    lines.append("")
    lines.append(_timing_beats_md_table())
    lines.append("")
    lines.append("## 旁白 (1 min, Chinese)")
    lines.append("")
    lines.append(sb.get("narration_zh", "").strip())
    lines.append("")
    lines.append("## Runway prompts")
    lines.append("")
    for sc in sb.get("scenes", []):
        lines.append(f"### Scene {sc['index']} — {sc['duration_sec']}s")
        lines.append(f"- image: `{sc['image_ref']}`")
        lines.append(f"- camera: {sc['camera']}")
        lines.append(f"- prompt: {sc['prompt']}")
        if sc.get("narration_zh"):
            lines.append(f"- 旁白片段: {sc['narration_zh']}")
        lines.append(f"- negative: {sc['negative_prompt']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate_for_folder(folder: Path, force: bool = False) -> dict:
    if not folder.is_dir():
        return {"folder": str(folder), "ok": False, "error": "not a directory"}

    ctx = load_note_context(folder)
    env = read_project_env()
    ctx["fmp"] = _fetch_fmp_snapshot(ctx, env)
    fmp_path = folder / "_fmp_snapshot.json"
    if ctx.get("fmp"):
        fmp_path.write_text(
            json.dumps(ctx["fmp"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif fmp_path.is_file():
        try:
            fmp_path.unlink()
        except OSError:
            pass

    out_narration = folder / "narration_zh.txt"
    out_prompts = folder / "runway_prompts.json"
    out_md = folder / "storyboard.md"

    if not force and out_narration.is_file() and out_prompts.is_file() and out_md.is_file():
        log(f"  skip {folder.name}  (already has storyboard; pass --force to rebuild)")
        return {"folder": str(folder), "ok": True, "skipped": True}

    scene_images = pick_scene_images(folder, NUM_SCENES)
    if not scene_images:
        return {
            "folder": str(folder),
            "ok": False,
            "error": "no img_*.{jpg,png,webp} files in folder",
        }
    scene_image_names = [p.name for p in scene_images]

    sb: dict | None = None
    raw_response = _chat_openai(_build_llm_prompt(ctx, scene_image_names), env)
    if raw_response:
        try:
            parsed = json.loads(raw_response)
            sb = _normalize_storyboard(parsed, scene_image_names)
            sb["_source"] = "openai:" + (env.get("OPENAI_MODEL") or "gpt-4o-mini")
        except json.JSONDecodeError as exc:
            log(f"  WARN openai response was not valid JSON ({exc}); using fallback")
            sb = None

    if sb is None:
        sb = _fallback_storyboard(ctx, scene_image_names)

    if sb is not None:
        n_chars = _count_cjk(sb.get("narration_zh") or "")
        if n_chars < NARRATION_MIN_CHARS:
            log(
                f"  WARN narration too short ({n_chars} CJK chars, "
                f"min {NARRATION_MIN_CHARS}); using fallback spine"
            )
            sb = _fallback_storyboard(ctx, scene_image_names)

    # Final sanity: enforce 6 scenes pinned to the right images.
    while len(sb["scenes"]) < NUM_SCENES:
        i = len(sb["scenes"])
        sb["scenes"].append({
            "index": i + 1,
            "duration_sec": SCENE_SECONDS,
            "image_ref": scene_image_names[i] if i < len(scene_image_names) else "",
            "narration_zh": "",
            "prompt": "",
            "camera": "",
            "negative_prompt": "text, watermark, logo, distorted hands, low quality",
        })
    for i, sc in enumerate(sb["scenes"][:NUM_SCENES]):
        sc["index"] = i + 1
        sc["duration_sec"] = SCENE_SECONDS
        if i < len(scene_image_names) and not sc.get("image_ref"):
            sc["image_ref"] = scene_image_names[i]
    sb["scenes"] = sb["scenes"][:NUM_SCENES]

    out_narration.write_text(sb["narration_zh"].strip() + "\n", encoding="utf-8")

    runway_payload = {
        "note_id": ctx["note_id"],
        "title": ctx["title"],
        "total_duration_sec": NUM_SCENES * SCENE_SECONDS,
        "scene_count": NUM_SCENES,
        "scene_seconds": SCENE_SECONDS,
        "timing_beats_ref": TIMING_BEATS_REF,
        "source": sb.get("_source", "unknown"),
        "scenes": sb["scenes"],
    }
    if ctx.get("fmp"):
        runway_payload["fmp_snapshot"] = ctx["fmp"]
    out_prompts.write_text(
        json.dumps(runway_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    out_md.write_text(_render_storyboard_md(ctx, sb), encoding="utf-8")

    log(
        f"  OK  {folder.name}  "
        f"narration={_count_cjk(sb['narration_zh'])}cjk  "
        f"scenes={len(sb['scenes'])}  "
        f"src={sb.get('_source')}"
    )
    return {
        "folder": str(folder),
        "ok": True,
        "narration_chars": _count_cjk(sb["narration_zh"]),
        "scenes": len(sb["scenes"]),
        "source": sb.get("_source"),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate 1-min narration + 6 Runway prompts for an XHS note folder.",
    )
    ap.add_argument("folders", nargs="*", type=Path,
                    help="one or more note folders (e.g. assets/xhs/<note_id>)")
    ap.add_argument("--all", action="store_true",
                    help=f"process every note under --root (default {DEFAULT_XHS_ROOT.relative_to(REPO_ROOT)})")
    ap.add_argument("--root", type=Path, default=DEFAULT_XHS_ROOT,
                    help="root folder used by --all")
    ap.add_argument("--force", action="store_true",
                    help="regenerate even if narration/prompts already exist")
    ns = ap.parse_args(argv)

    folders: list[Path] = list(ns.folders)
    if ns.all:
        folders.extend(list_note_folders(ns.root))
    if not folders:
        ap.error("nothing to do — pass folder paths or --all")

    seen: set[Path] = set()
    unique: list[Path] = []
    for f in folders:
        rp = f.resolve()
        if rp in seen:
            continue
        seen.add(rp)
        unique.append(f)

    log(f"processing {len(unique)} folder(s)")
    results = [generate_for_folder(f, force=ns.force) for f in unique]

    ok = sum(1 for r in results if r.get("ok"))
    fail = len(results) - ok
    log(f"done  total={len(results)}  ok={ok}  fail={fail}")
    if fail:
        for r in results:
            if not r.get("ok"):
                log(f"  FAIL  {r.get('folder')}  reason={r.get('error')}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
