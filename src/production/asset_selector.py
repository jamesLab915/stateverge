"""
Select Envato assets from ``<project>/assets/envato/`` for packaging (pathlib, stdlib only).

``pick_*`` accept an optional ``root`` (StateVerge project root). If omitted, the parent of
``src/`` is used. Logs::

    [asset_selector] type=music file=...
    [asset_selector] type=sfx file=...
    [asset_selector] type=title file=...
"""

from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Optional

_DEFAULT_ROOT: Path = Path(__file__).resolve().parent.parent.parent

MUSIC_EXTS: frozenset[str] = frozenset(
    {".mp3", ".wav", ".m4a", ".aiff", ".aif", ".flac", ".ogg"}
)
SFX_EXTS: frozenset[str] = frozenset({".mp3", ".wav", ".m4a", ".aiff", ".aif"})
OPENER_EXTS: frozenset[str] = frozenset({".mp4", ".mov"})
LOWER_EXTS: frozenset[str] = frozenset(
    {".mogrt", ".mov", ".mp4", ".aep", ".prproj"}
)

_MOOD_FILTER_WORDS: tuple[str, ...] = (
    "dark",
    "tension",
    "epic",
    "cinematic",
    "ambient",
)

_rng = random.Random()

_LOG_PREFIX = "[asset_selector]"


def _log(kind: str, path: Path) -> None:
    print(
        f"{_LOG_PREFIX} type={kind} file={path!s}",
        flush=True,
    )


def _root(r: Path | None) -> Path:
    return (r or _DEFAULT_ROOT).resolve()


def _list_files(d: Path, exts: frozenset[str]) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(
        f
        for f in d.iterdir()
        if f.is_file() and f.suffix.lower() in exts and not f.name.startswith(".")
    )


def _pick_from(pool: list[Path], kind: str) -> Path:
    if not pool:
        raise FileNotFoundError(f"no file in pool for {kind!r} (see assets/envato/)")
    c = _rng.choice(pool)
    _log(kind, c)
    return c


def pick_music(
    mood: str | None,
    root: Optional[Path] = None,
) -> Path:
    """
    Scan ``assets/envato/music/``.

    If *mood* is set, restrict to files whose name contains at least one of
    *dark* / *tension* / *epic* / *cinematic* / *ambient*, then prefer:

    * dark / tension in mood → filenames with *dark* or *tension*
    * epic / cinematic in mood (and not already handled as dark) → *epic* or *cinematic*
    * calm, or *ambient* as a mood word → *ambient* in filename

    If nothing matches, fall back to a random file from the filter pool, then from all.
    """
    r = _root(root)
    mdir = r / "assets" / "envato" / "music"
    all_p = _list_files(mdir, MUSIC_EXTS)
    if not all_p:
        raise FileNotFoundError(
            f"no audio in {mdir}"
        )
    m = (mood or "").strip().lower()
    if not m:
        return _pick_from(all_p, "music")

    filtered = [
        p
        for p in all_p
        if any(w in p.name.lower() for w in _MOOD_FILTER_WORDS)
    ]
    pool: list[Path] = filtered if filtered else all_p

    prefer_dark = "dark" in m or "tension" in m
    prefer_epic = "epic" in m or "cinematic" in m
    prefer_calm = "calm" in m

    sub: list[Path] = []
    if prefer_dark:
        sub = [p for p in pool if "dark" in p.name.lower() or "tension" in p.name.lower()]
    elif prefer_epic:
        sub = [p for p in pool if "epic" in p.name.lower() or "cinematic" in p.name.lower()]
    elif prefer_calm:
        sub = [p for p in pool if "ambient" in p.name.lower()]
    if sub:
        return _pick_from(sub, "music")
    if pool is not all_p:
        return _pick_from(pool, "music")
    return _pick_from(all_p, "music")


def pick_sfx(
    event_type: str,
    root: Optional[Path] = None,
) -> Path:
    """
    Scan ``assets/envato/sfx/`` for *event_type*:

    * *impact* → *impact* / *hit* / *boom*
    * *whoosh* → *whoosh* / *swoosh*
    * *riser* → *riser* / *build*
    * otherwise → any file
    """
    r = _root(root)
    sdir = r / "assets" / "envato" / "sfx"
    all_p = _list_files(sdir, SFX_EXTS)
    if not all_p:
        raise FileNotFoundError(
            f"no SFX in {sdir}"
        )
    et = (event_type or "impact").strip().lower()
    m: dict[str, tuple[str, ...]] = {
        "impact": ("impact", "hit", "boom"),
        "whoosh": ("whoosh", "swoosh"),
        "riser": ("riser", "build"),
    }
    if et in m:
        kws = m[et]
        sub = [p for p in all_p if any(kw in p.name.lower() for kw in kws)]
        if sub:
            return _pick_from(sub, "sfx")
    return _pick_from(all_p, "sfx")


def pick_title_opener(
    root: Optional[Path] = None,
) -> Path:
    """Random file under ``assets/envato/title_openers/`` (``.mp4`` / ``.mov``)."""
    r = _root(root)
    tdir = r / "assets" / "envato" / "title_openers"
    all_p = _list_files(tdir, OPENER_EXTS)
    return _pick_from(all_p, "title")


def pick_lower_third(
    root: Optional[Path] = None,
) -> Path | None:
    """
    If ``assets/envato/lower_thirds/`` has a file, return one (random) path; else ``None``.
    """
    r = _root(root)
    d = r / "assets" / "envato" / "lower_thirds"
    if not d.is_dir():
        return None
    all_p = [f for f in d.iterdir() if f.is_file() and not f.name.startswith(".")]
    all_p = [f for f in all_p if f.suffix.lower() in LOWER_EXTS]
    if not all_p:
        return None
    c = _rng.choice(all_p)
    _log("lower", c)
    return c


def _rel(
    p: Path,
    root: Path,
) -> str:
    p = p.resolve()
    r = root.resolve()
    return str(p.relative_to(r)).replace("\\", "/")


def build_default_sfx_timeline(
    root: Optional[Path],
    video_duration: float,
) -> list[dict[str, Any]]:
    """
    Auto SFX: 5s → *impact*; 60, 120, … → *whoosh*; one *riser* in the last 10 seconds.

    *video_duration* is the final packaged timeline length (e.g. after title opener), in seconds.
    """
    r = _root(root)
    d = max(0.01, float(video_duration))
    ev: list[tuple[float, str]] = []
    if d >= 4.0:
        ev.append((5.0 if d >= 5.0 else min(1.0, d * 0.2), "impact"))
    t = 60.0
    while t < d - 0.1:
        ev.append((t, "whoosh"))
        t += 60.0
    # riser: inside [d-10, d]
    t_lo = max(0.0, d - 10.0)
    t_r = min(d - 0.2, t_lo + 5.0)
    for _, _k in list(ev):
        if abs(t_r - _) < 0.3:
            t_r = _ + 0.35
    t_r = min(t_r, d - 0.1)
    if t_r < t_lo:
        t_r = t_lo
    if t_r >= 0.0 and t_r < d and t_r + 0.1 <= d:
        ev.append((t_r, "riser"))
    ev.sort(key=lambda x: (x[0], x[1] != "riser"))
    out: list[dict[str, Any]] = []
    for ts, k in ev:
        if ts < 0.0 or ts >= d - 0.01:
            continue
        p = pick_sfx(k, root)
        out.append(
            {
                "time_sec": float(round(min(ts, d - 0.1), 2)),
                "file": _rel(p, r),
            }
        )
    return out
