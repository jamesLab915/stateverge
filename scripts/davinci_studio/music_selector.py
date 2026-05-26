#!/usr/bin/env python3
"""Pick background music for DaVinci Folder Studio v1."""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

from paths import DRIVING_MUSIC_SUBDIRS, ENVATO_MUSIC_ROOT, NYC_LONG_MUSIC_ROOT

_AUDIO_EXTS = frozenset({".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"})
_MAX_WALK_DEPTH = 6
_MAX_FILES = 4000


def _walk_audio(root: Path) -> list[Path]:
    out: list[Path] = []
    if not root.is_dir():
        return out
    root_r = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        try:
            depth = len(Path(dirpath).relative_to(root_r).parts)
        except ValueError:
            depth = 0
        if depth > _MAX_WALK_DEPTH:
            dirnames[:] = []
            continue
        for fn in filenames:
            if len(out) >= _MAX_FILES:
                return out
            if Path(fn).suffix.lower() in _AUDIO_EXTS:
                p = Path(dirpath) / fn
                if p.is_file() and not p.name.startswith("._"):
                    out.append(p)
    return out


def pick_nyc_long_track(seed: str | None = None) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if not NYC_LONG_MUSIC_ROOT.is_dir():
        warnings.append("nyc_long_music_root_missing")
        return None, warnings
    pool: list[Path] = []
    for sub in DRIVING_MUSIC_SUBDIRS:
        d = NYC_LONG_MUSIC_ROOT / sub
        pool.extend(_walk_audio(d))
    if not pool:
        warnings.append("nyc_long_music_empty_in_allowed_subdirs")
        return None, warnings
    rng = random.Random(seed or "davinci_studio_v1")
    pick = rng.choice(pool)
    warnings.append(f"nyc_long_pick:{pick.name}")
    return pick, warnings


def pick_envato_track(seed: str | None = None) -> tuple[Path | None, list[str]]:
    warnings: list[str] = []
    if not ENVATO_MUSIC_ROOT.is_dir():
        warnings.append("envato_music_root_missing")
        return None, warnings
    files = _walk_audio(ENVATO_MUSIC_ROOT)
    if not files:
        warnings.append("envato_music_empty")
        return None, warnings
    rng = random.Random((seed or "envato") + "_v1")
    pick = rng.choice(files)
    warnings.append(f"envato_pick:{pick.name}")
    return pick, warnings


def _pick_suno_driving_track(seed: str | None = None) -> tuple[Path | None, list[str]]:
    """Suno inbox/category pick for driving calm; fail-open to nyc_long."""
    warnings: list[str] = []
    try:
        _repo_scripts = Path(__file__).resolve().parent.parent
        if str(_repo_scripts) not in sys.path:
            sys.path.insert(0, str(_repo_scripts))
        from music_selector import (  # noqa: WPS433
            load_suno_categories_config,
            pick_suno_track_for_categories,
            resolve_suno_categories_for_scene,
        )

        cfg = load_suno_categories_config()
        cats = resolve_suno_categories_for_scene(theme="driving", blob=str(seed or "davinci_folder"))
        if cats:
            track, cat, sw = pick_suno_track_for_categories(cats, cfg=cfg, theme="driving")
            warnings.extend(sw)
            if track and track.is_file():
                warnings.append(f"suno_pick:{cat}")
                return track, warnings
        warnings.append("suno_no_match_fallback_nyc_long")
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"suno_pick_failed:{exc!r}")
    return pick_nyc_long_track(seed)


def pick_music(mode: str, *, seed: str | None = None, add_music: bool = False) -> tuple[Path | None, list[str]]:
    from audio_modes import canonical_mode, is_ferry_mode

    m = canonical_mode(mode or "")
    if is_ferry_mode(m) and add_music:
        return pick_nyc_long_track((seed or "ferry") + "_ambient")
    if m == "driving_music_first":
        return _pick_suno_driving_track(seed)
    if m == "add_envato_music":
        return pick_envato_track(seed)
    return None, ["music_not_required_for_mode"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", required=True)
    ap.add_argument("--seed", default="")
    args = ap.parse_args()
    track, warnings = pick_music(args.mode, seed=args.seed or None)
    print(
        json.dumps(
            {
                "track": str(track) if track else None,
                "warnings": warnings,
            },
            indent=2,
        )
    )
    return 0 if track else 1


if __name__ == "__main__":
    raise SystemExit(main())
