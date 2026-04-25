"""
Reusable auto-selection of Envato assets from ``<repo>/assets/envato/``.

Used when :file:`packaging_manifest.json` omits paths (manifest still wins on conflict).
"""

from __future__ import annotations

import json
import random
import re
from pathlib import Path
from typing import Any, Optional

from .brief_loader import ProductionBrief, load_brief
from .paths import topic_production_paths

# Match sort_envato layout (flat buckets under envato)
DIR_MUSIC = "music"
DIR_SFX = "sfx"
DIR_TITLE = "title_openers"
# Future: could overlay lower-third *video*; today packaging uses drawtext only.
DIR_LOWER = "lower_thirds"

MUSIC_EXTS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aiff", ".aif", ".flac", ".ogg"}
)
SFX_EXTS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".aiff", ".aif"})
OPENER_EXTS: frozenset[str] = frozenset({".mp4", ".mov"})

LOWER_THIRD_DEFAULT_S = 5.0


def envato_root(root: Path) -> Path:
    return Path(root).resolve() / "assets" / "envato"


def _is_media(p: Path, allowed: frozenset[str]) -> bool:
    return p.is_file() and p.suffix.lower() in allowed and not p.name.startswith(".")


def _list_dir(root: Path, sub: str, exts: frozenset[str]) -> list[Path]:
    d = envato_root(root) / sub
    if not d.is_dir():
        return []
    return sorted(
        f for f in d.iterdir() if _is_media(f, exts)
    )


def _mood_string(production_brief: Optional[ProductionBrief], path: Path) -> str:
    """
    ``production_brief`` may set ``envato.music_mood``; the JSON may also
    set a top-level ``"mood"`` key.
    """
    s = ""
    p = Path(path)
    if p.is_file():
        with p.open("r", encoding="utf-8") as fh:
            raw: Any = json.load(fh)
        if isinstance(raw, dict) and isinstance(raw.get("mood"), str):
            t = raw["mood"].strip()
            if t:
                s = t
    if s:
        return s
    if production_brief is not None:
        return str(production_brief.envato.music_mood or "")
    if path.is_file():
        b2 = load_brief(path)
        if b2 is not None:
            return str(b2.envato.music_mood or "")
    return ""


def _filename_matches_mood(filename: str, mood: str) -> bool:
    m = (mood or "").strip().lower()
    if not m:
        return True
    tokens = [w for w in re.split(r"[^\w]+", m) if len(w) >= 2]
    if not tokens:
        return True
    low = filename.lower()
    return any(t in low for t in tokens)


def pick_random_music(
    root: Path,
    *,
    production_brief: Optional[ProductionBrief],
    production_brief_path: Path,
    rng: random.Random,
) -> Optional[Path]:
    """One file from ``music/``; if *mood* is set, prefer files whose name matches."""
    cands = _list_dir(root, DIR_MUSIC, MUSIC_EXTS)
    if not cands:
        return None
    mood = _mood_string(production_brief, production_brief_path)
    msub = [p for p in cands if _filename_matches_mood(p.name, mood)]
    src = msub or cands
    return rng.choice(src)


def pick_random_sfx(
    root: Path,
    *,
    rng: random.Random,
) -> Optional[Path]:
    fs = _list_dir(root, DIR_SFX, SFX_EXTS)
    return rng.choice(fs) if fs else None


def pick_random_title_opener(
    root: Path,
    *,
    rng: random.Random,
) -> Optional[Path]:
    fs = _list_dir(root, DIR_TITLE, OPENER_EXTS)
    return rng.choice(fs) if fs else None


def _chapter_sfx_timestamps(
    video_duration: float, brief: Optional[ProductionBrief]
) -> list[float]:
    d = max(0.1, float(video_duration or 0.0))
    if not brief or not brief.chapters:
        return [min(2.0, d * 0.05), d * 0.4, d * 0.72]

    t = 0.0
    times: list[float] = []
    for ch in brief.chapters:
        t0 = t + min(1.2, d * 0.02)
        t0 = min(t0, max(0.0, d - 0.5))
        if t0 < d:
            times.append(t0)
        t += float(getattr(ch, "duration_sec", 120) or 0)
    out = [x for x in times if x < d * 0.99]
    if not out:
        return [d * 0.15, d * 0.55, d * 0.88]
    return out[:6]


def build_auto_sfx_events(
    root: Path,
    video_duration: float,
    production_brief: Optional[ProductionBrief],
    rng: random.Random,
) -> list[dict[str, Any]]:
    """
    Random SFX from ``sfx/`` at time points (chapter-based when possible).
    """
    tms = _chapter_sfx_timestamps(video_duration, production_brief)
    if not tms:
        return []
    sfxs = _list_dir(root, DIR_SFX, SFX_EXTS)
    if not sfxs:
        return []
    out: list[dict[str, Any]] = []
    for ts in tms:
        f = rng.choice(sfxs)
        rel = f.relative_to(Path(root).resolve())
        out.append({"time_sec": float(ts), "file": str(rel).replace("\\", "/")})
    return out


def make_drawtext_lower_thirds(
    production_brief: Optional[ProductionBrief], *, topic_title: str = ""
) -> list[dict[str, Any]]:
    """When no (or no usable) lower-third *entries*, use ffmpeg drawtext defaults."""
    title = topic_title
    if production_brief is not None and getattr(production_brief, "title", ""):
        title = str(production_brief.title)
    st = "Global Systems"
    if production_brief is not None:
        st = (production_brief.style or st)[:80]
    return [
        {
            "time_sec": 0.0,
            "duration_sec": LOWER_THIRD_DEFAULT_S,
            "text": title or "STATEVERGE",
            "subtext": st,
        }
    ]


def resolve_packaging_sources(
    root: Path,
    topic: str,
    manifest: dict[str, Any],
    *,
    main_duration: float,
) -> dict[str, Any]:
    """
    Apply **manifest > auto** for all packaging inputs.

    Returns a dict shaped like a manifest, with real paths to existing files
    and resolved ``lower_thirds`` / ``sfx`` entries.
    """
    root = Path(root).resolve()
    t = topic_production_paths(root, topic)
    bpath: Path = t["production_brief"]
    b = load_brief(bpath) if bpath.is_file() else None
    rng = random.Random(hash(bpath.resolve().as_posix()) & (2**32 - 1) ^ (hash(topic) & (2**31 - 1)))

    def m_str(key: str) -> str:
        v = manifest.get(key, "")
        return v if isinstance(v, str) else str(v or "")

    m = dict(manifest)

    # — title opener
    t_op = m_str("title_opener")
    p_op = t_op.strip() and (t_op if Path(t_op).is_absolute() else root / t_op)
    p_op = Path(p_op).resolve() if t_op else Path()
    if t_op and p_op.is_file():
        m["title_opener"] = t_op
    else:
        auto = pick_random_title_opener(root, rng=rng)
        m["title_opener"] = (
            str(auto.relative_to(root)).replace("\\", "/") if auto is not None else ""
        )

    # — BGM
    t_mu = m_str("background_music")
    p_mu = t_mu.strip() and (t_mu if Path(t_mu).is_absolute() else root / t_mu)
    p_mu = Path(p_mu).resolve() if t_mu else Path()
    if t_mu and p_mu.is_file():
        m["background_music"] = t_mu
    else:
        auto = pick_random_music(
            root,
            production_brief=b,
            production_brief_path=bpath,
            rng=rng,
        )
        m["background_music"] = (
            str(auto.relative_to(root)).replace("\\", "/")
            if auto is not None
            else ""
        )

    # — SFX: manifest list wins if *any* entry files exist
    raw_sfx = manifest.get("sfx")
    merged_sfx: list[dict[str, Any]] = []
    if isinstance(raw_sfx, list) and raw_sfx:
        for it in raw_sfx:
            if not isinstance(it, dict):
                continue
            fkey = it.get("file", "")
            fp = (
                Path(fkey)
                if fkey and Path(fkey).is_absolute()
                else (root / str(fkey))
            ).resolve()
            if fkey and fp.is_file():
                d = dict(it)
                try:
                    d["file"] = str(
                        fp.relative_to(root)
                    ).replace("\\", "/")
                except ValueError:
                    d["file"] = str(fkey)
                merged_sfx.append(d)
    if not merged_sfx:
        merged_sfx = build_auto_sfx_events(
            root, main_duration, b, rng
        )
    m["sfx"] = merged_sfx

    # — Lower thirds: manifest drawtext list wins if non-empty
    lt = manifest.get("lower_thirds")
    if isinstance(lt, list) and lt and any(
        isinstance(x, dict) for x in lt
    ):
        m["lower_thirds"] = [x for x in lt if isinstance(x, dict)]
    else:
        m["lower_thirds"] = make_drawtext_lower_thirds(
            b, topic_title=topic.replace("-", " ").title()
        )

    m["transitions"] = bool(
        manifest["transitions"] if "transitions" in manifest else True
    )
    return m
