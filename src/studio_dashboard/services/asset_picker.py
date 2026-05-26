"""
Asset picker service — list every video clip the operator can pick from for a
given topic, and save the resulting selection.

Sources scanned (in this order):

  1. assets/envato/                       (global Envato library)
  2. assets/runway/                       (global Runway library)
  3. assets/pexels/                       (global Pexels stock)
  4. assets/pixabay/                      (global Pixabay stock)
  5. topics/<slug>/assets/raw/            (auto-fetched per topic)
  6. topics/<slug>/video/                 (one-click pipeline output)
  7. topics/<slug>/envato/                (curated topic Envato cuts)

`list_assets()` returns one entry per video file:

  {
    "source":       "envato",
    "label":        "Envato",
    "rel_path":     "assets/envato/.../foo.mp4",
    "abs_path":     "/Users/.../foo.mp4",
    "filename":     "foo.mp4",
    "size_bytes":   12345678,
    "mtime":        1714330000.0,
    "duration_sec": 15.0,
    "width":        1920,
    "height":       1080,
    "fps":          29.97,
  }

Probe results are cached in `~/StateVerge/.cache/asset_probe.json` keyed by
`abs_path::mtime::size` so that re-opening the picker is near-instant. Files
that have changed since the cache entry was written are re-probed.

`save_selection()` writes `topics/<slug>/mix/selected_assets.json` with the
canonical structure documented in `load_selection()`. Any existing
`selected_assets.json` is first moved to
`topics/<slug>/mix/archive/<YYYYMMDD_HHMMSS>/`.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..scanner import VIDEO_EXTS  # reuse the canonical video extension set
from .paths import archive_existing, safe_subpath, safe_topic_dir


# ---------------------------------------------------------------------------
# Source configuration
# ---------------------------------------------------------------------------


@dataclass
class SourceSpec:
    id: str          # short id used in the URL filter
    label: str       # display label
    rel: str         # path template; "{slug}" for per-topic sources
    scope: str       # "global" or "topic"


SOURCES: list[SourceSpec] = [
    SourceSpec("envato",       "Envato",         "assets/envato",                "global"),
    SourceSpec("runway",       "Runway",         "assets/runway",                "global"),
    SourceSpec("pexels",       "Pexels",         "assets/pexels",                "global"),
    SourceSpec("pixabay",      "Pixabay",        "assets/pixabay",               "global"),
    SourceSpec("topic_raw",    "Topic / raw",    "topics/{slug}/assets/raw",     "topic"),
    SourceSpec("topic_video",  "Topic / video",  "topics/{slug}/video",          "topic"),
    SourceSpec("topic_envato", "Topic / envato", "topics/{slug}/envato",         "topic"),
]


# Cap per source. Large libraries are sorted by mtime desc and truncated; the
# operator gets the most recently added clips first. Bumping this needs to be
# weighed against ffprobe cost (~30-80 ms per clip on first pass).
DEFAULT_MAX_PER_SOURCE = 250


# ---------------------------------------------------------------------------
# Probe cache
# ---------------------------------------------------------------------------


def _cache_path(repo_root: Path) -> Path:
    return repo_root / ".cache" / "asset_probe.json"


def _load_cache(repo_root: Path) -> dict[str, dict]:
    p = _cache_path(repo_root)
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(repo_root: Path, cache: dict[str, dict]) -> None:
    p = _cache_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _cache_key(abs_path: str, st_mtime: float, st_size: int) -> str:
    # mtime + size invalidates if the file changed
    return f"{abs_path}::{int(st_mtime)}::{st_size}"


# ---------------------------------------------------------------------------
# ffprobe wrapper
# ---------------------------------------------------------------------------


def _ffprobe_video(path: Path) -> dict[str, Any]:
    """
    Cheap probe: duration + first video stream resolution + fps. Returns a
    dict with `duration_sec`, `width`, `height`, `fps`. On any error returns
    zeros so the row still renders.
    """
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,r_frame_rate:format=duration",
        "-of", "json",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    if r.returncode != 0:
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    try:
        j = json.loads(r.stdout or "{}")
    except json.JSONDecodeError:
        return {"duration_sec": 0.0, "width": 0, "height": 0, "fps": 0.0}
    fmt = j.get("format") or {}
    streams = j.get("streams") or []
    s0 = streams[0] if streams else {}
    fps = 0.0
    rfr = s0.get("r_frame_rate")
    if isinstance(rfr, str) and "/" in rfr:
        try:
            num, den = rfr.split("/")
            num_f, den_f = float(num), float(den)
            if den_f > 0:
                fps = round(num_f / den_f, 3)
        except ValueError:
            pass
    try:
        dur = float(fmt.get("duration") or 0.0)
    except (TypeError, ValueError):
        dur = 0.0
    return {
        "duration_sec": dur,
        "width": int(s0.get("width") or 0),
        "height": int(s0.get("height") or 0),
        "fps": fps,
    }


# ---------------------------------------------------------------------------
# Source scan
# ---------------------------------------------------------------------------


def _resolve_source_root(spec: SourceSpec, slug: str, repo_root: Path) -> Path:
    rel = spec.rel.format(slug=slug)
    return (repo_root / rel).resolve()


def _iter_videos(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    try:
        for p in root.rglob("*"):
            try:
                if not p.is_file():
                    continue
            except OSError:
                continue
            if p.suffix.lower() in VIDEO_EXTS:
                yield p
    except OSError:
        return


def _scan_source(
    spec: SourceSpec,
    slug: str,
    repo_root: Path,
    cache: dict[str, dict],
    cache_dirty: list[bool],
    max_per_source: int,
) -> tuple[bool, list[dict]]:
    """
    Returns (root_exists, items). Items are sorted by mtime desc, capped at
    max_per_source.
    """
    root = _resolve_source_root(spec, slug, repo_root)
    if not root.is_dir():
        return False, []

    # Confirm the resolved root is still inside the repo (defence against
    # symlink shenanigans inside assets/).
    if not root.is_relative_to(repo_root):
        return False, []

    # Gather (mtime, path, size) tuples first, then truncate before probing.
    candidates: list[tuple[float, Path, int]] = []
    for p in _iter_videos(root):
        try:
            st = p.stat()
        except OSError:
            continue
        candidates.append((st.st_mtime, p, st.st_size))

    candidates.sort(key=lambda t: t[0], reverse=True)
    candidates = candidates[:max_per_source]

    items: list[dict] = []
    for mtime, p, size in candidates:
        abs_path = str(p)
        key = _cache_key(abs_path, mtime, size)
        cached = cache.get(key)
        if cached:
            probe = cached
        else:
            probe = _ffprobe_video(p)
            cache[key] = probe
            cache_dirty[0] = True
        try:
            rel_path = str(p.relative_to(repo_root))
        except ValueError:
            rel_path = abs_path  # e.g. symlink target outside repo

        items.append({
            "source": spec.id,
            "label": spec.label,
            "rel_path": rel_path,
            "abs_path": abs_path,
            "filename": p.name,
            "size_bytes": int(size),
            "mtime": float(mtime),
            "duration_sec": float(probe.get("duration_sec") or 0.0),
            "width": int(probe.get("width") or 0),
            "height": int(probe.get("height") or 0),
            "fps": float(probe.get("fps") or 0.0),
        })
    return True, items


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_assets(
    *,
    slug: str,
    repo_root: Path,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
) -> dict[str, Any]:
    """
    Returns:
        {
          "topic":   "<slug>",
          "sources": [{"id", "label", "scope", "rel_root", "exists", "count"}],
          "items":   [<asset>, ...],
          "totals":  {"sources": int, "items": int, "duration_sec": float,
                      "elapsed_ms": int, "max_per_source": int,
                      "cache_path": "..."},
        }
    """
    t0 = time.monotonic()
    # validate slug + ensure topic dir exists (we may need to create mix/)
    topic_dir = safe_topic_dir(slug, repo_root, create=False)
    if not topic_dir.is_dir():
        # The list endpoint is read-only; if the topic dir doesn't exist that's
        # not necessarily an error — global sources still apply — but per-topic
        # sources will simply be empty.
        topic_dir = repo_root / "topics" / slug

    cache = _load_cache(repo_root)
    cache_dirty = [False]

    source_summary: list[dict] = []
    items: list[dict] = []
    for spec in SOURCES:
        rel_root = spec.rel.format(slug=slug)
        exists, src_items = _scan_source(
            spec, slug, repo_root, cache, cache_dirty, max_per_source
        )
        items.extend(src_items)
        source_summary.append({
            "id": spec.id,
            "label": spec.label,
            "scope": spec.scope,
            "rel_root": rel_root,
            "exists": exists,
            "count": len(src_items),
        })

    if cache_dirty[0]:
        try:
            _save_cache(repo_root, cache)
        except OSError:
            pass

    total_dur = sum(float(it["duration_sec"]) for it in items)
    return {
        "topic": slug,
        "sources": source_summary,
        "items": items,
        "totals": {
            "sources": sum(1 for s in source_summary if s["exists"]),
            "items": len(items),
            "duration_sec": round(total_dur, 2),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "max_per_source": max_per_source,
            "cache_path": str(_cache_path(repo_root).relative_to(repo_root)),
        },
    }


# ---------------------------------------------------------------------------
# Save / load selection
# ---------------------------------------------------------------------------


@dataclass
class SaveResult:
    ok: bool
    topic: str
    selection_path: str = ""
    items: int = 0
    duration_sec: float = 0.0
    archive_dir: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def save_selection(
    *,
    slug: str,
    items: list[dict],
    repo_root: Path,
    options: dict[str, Any] | None = None,
) -> SaveResult:
    """
    Validates each incoming item:
      - abs_path must resolve inside repo_root
      - file must exist and be a video extension
      - duration / size are re-probed if missing (defence against client-side
        spoofing of weird durations)
    Writes topics/<slug>/mix/selected_assets.json. Old file → archive/<ts>/.
    """
    try:
        topic_dir = safe_topic_dir(slug, repo_root)
    except ValueError as e:
        return SaveResult(ok=False, topic=slug, error=str(e))

    mix_dir = safe_subpath(topic_dir, "mix")
    mix_dir.mkdir(parents=True, exist_ok=True)
    sel_path = safe_subpath(mix_dir, "selected_assets.json")

    cleaned: list[dict] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        abs_path = (raw.get("abs_path") or "").strip()
        if not abs_path:
            continue
        try:
            p = Path(abs_path).resolve()
        except OSError:
            continue
        # Must be inside the repo
        try:
            if not p.is_relative_to(repo_root):
                continue
        except (TypeError, ValueError):
            continue
        if not p.is_file():
            continue
        if p.suffix.lower() not in VIDEO_EXTS:
            continue

        try:
            st = p.stat()
        except OSError:
            continue

        # Re-probe missing or zero duration so the renderer can trust the JSON
        if (raw.get("duration_sec") or 0) <= 0 or not raw.get("width"):
            probe = _ffprobe_video(p)
        else:
            probe = {
                "duration_sec": float(raw.get("duration_sec") or 0.0),
                "width": int(raw.get("width") or 0),
                "height": int(raw.get("height") or 0),
                "fps": float(raw.get("fps") or 0.0),
            }

        cleaned.append({
            "source": str(raw.get("source") or "unknown"),
            "rel_path": str(p.relative_to(repo_root)),
            "abs_path": str(p),
            "filename": p.name,
            "size_bytes": int(st.st_size),
            "duration_sec": float(probe.get("duration_sec") or 0.0),
            "width": int(probe.get("width") or 0),
            "height": int(probe.get("height") or 0),
            "fps": float(probe.get("fps") or 0.0),
        })

    if not cleaned:
        return SaveResult(
            ok=False, topic=slug,
            error="no valid assets in selection",
        )

    # Coerce options
    opts = options or {}
    payload = {
        "topic": slug,
        "selected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "options": {
            "per_clip_sec": float(opts.get("per_clip_sec") or 6.0),
            "shuffle": bool(opts.get("shuffle") or False),
        },
        "items": cleaned,
    }

    archive_dir = archive_existing(
        [sel_path],
        archive_root=mix_dir / "archive",
    )
    sel_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    return SaveResult(
        ok=True,
        topic=slug,
        selection_path=str(sel_path.relative_to(repo_root)),
        items=len(cleaned),
        duration_sec=sum(it["duration_sec"] for it in cleaned),
        archive_dir=(
            str(archive_dir.relative_to(repo_root)) if archive_dir else ""
        ),
    )


def load_selection(slug: str, repo_root: Path) -> dict[str, Any] | None:
    try:
        topic_dir = safe_topic_dir(slug, repo_root, create=False)
    except ValueError:
        return None
    sel_path = safe_subpath(topic_dir, "mix", "selected_assets.json")
    if not sel_path.is_file():
        return None
    try:
        return json.loads(sel_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
