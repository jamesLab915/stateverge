"""
Thin top-level CLI wrapper around the real per-domain modules.

This module **does not** implement pipeline steps itself. It only routes flags
to the current, real entry points:

    --only-main   →  src.mix_engine.build_video.run_build
                     (writes ``topics/<topic>/output/final_mix.mp4`` from
                     ``topics/<topic>/mix/timeline.json``)

    --only-final  →  src.production.packaging_engine.apply_packaging
                     (the same code path as ``python -m src.production.cli
                     --topic <slug> --package``; writes
                     ``topics/<topic>/output/final_packaged.mp4``)

Usage::

    python -m src.run_pipeline --topic hidden-rules --only-main
    python -m src.run_pipeline --topic hidden-rules --only-final

Notes:
- All paths are anchored at ``$STATEVERGE_ROOT`` (default ``~/StateVerge``)
  and follow the ``topics/<topic>/output/`` convention.
- This wrapper deliberately calls **only** the currently real modules listed
  in the task spec:

    * ``src.production.cli``           (via its real backend ``packaging_engine``)
    * ``src.presenter_pipeline.cli``   (reserved for future flags)
    * ``src.mix_engine.build_video``

  No deprecated / removed helpers are referenced.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("run_pipeline")


def _root() -> Path:
    """Resolve the StateVerge project root (env override, else ``~/StateVerge``)."""
    return Path(
        os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge")
    ).resolve()


def _load_env(root: Path) -> None:
    """Best-effort .env loader (no hard dependency on python-dotenv)."""
    p = root / ".env"
    if not p.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    try:
        load_dotenv(p, override=False)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# --only-main : delegate to mix_engine.build_video
# ---------------------------------------------------------------------------

def _run_only_main(root: Path, topic: str) -> int:
    """
    Build ``topics/<topic>/output/final_mix.mp4`` from the mix timeline.

    Pre-checks ``topics/<topic>/mix/timeline.json`` exists; otherwise prints
    a clear, actionable error and returns non-zero (no traceback noise).
    """
    from src.mix_engine.build_video import run_build
    from src.mix_engine.timeline_loader import topic_mix_paths

    paths = topic_mix_paths(root, topic)
    timeline = paths["timeline"]
    out = paths["final_mix"]

    if not timeline.is_file():
        msg = (
            f"[run_pipeline] --only-main: missing mix timeline.\n"
            f"  expected: {timeline}\n"
            f"  topic root: {paths['root']}\n"
            f"  hint: create topics/{topic}/mix/timeline.json (JSON array of clips,\n"
            f"        see src/mix_engine/timeline_loader.py for the schema), then re-run."
        )
        print(msg, file=sys.stderr, flush=True)
        return 2

    print(
        f"[run_pipeline] topic={topic} action=only_main | "
        f"timeline={timeline} | out={out}",
        flush=True,
    )
    try:
        final = run_build(root, topic, timeline=timeline, out=out)
    except FileNotFoundError as e:
        print(f"[run_pipeline] error: {e}", file=sys.stderr, flush=True)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"[run_pipeline] error: {e}", file=sys.stderr, flush=True)
        return 1
    print(f"[run_pipeline] topic={topic} action=only_main_done | output={final}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# --only-final : delegate to production.packaging_engine (same as `--package`)
# ---------------------------------------------------------------------------

def _run_only_final(root: Path, topic: str) -> int:
    """
    Run packaging (lower-thirds, music-duck, SFX) → ``final_packaged.mp4``.
    Equivalent to ``python -m src.production.cli --topic <slug> --package``.
    """
    from src.production.packaging_engine import apply_packaging
    from src.production.paths import topic_production_paths

    t = topic_production_paths(root, topic)
    t["output"].mkdir(parents=True, exist_ok=True)
    t["brief"].mkdir(parents=True, exist_ok=True)

    print(
        f"[run_pipeline] topic={topic} action=only_final | "
        f"input={t['final_with_presenter']} | out={t['final_packaged']}",
        flush=True,
    )
    try:
        out = apply_packaging(root, topic)
    except FileNotFoundError as e:
        print(f"[run_pipeline] error: {e}", file=sys.stderr, flush=True)
        return 2
    except Exception as e:  # noqa: BLE001
        print(f"[run_pipeline] error: {e}", file=sys.stderr, flush=True)
        return 1
    print(f"[run_pipeline] topic={topic} action=only_final_done | output={out}", flush=True)
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(
        prog="python -m src.run_pipeline",
        description=(
            "Thin wrapper around StateVerge real CLIs. "
            "Use --only-main (mix_engine.build_video) or "
            "--only-final (production --package)."
        ),
    )
    p.add_argument("--topic", required=True, help="Topic slug (topics/<slug>/)")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--only-main",
        action="store_true",
        dest="only_main",
        help=(
            "Run mix_engine.build_video → topics/<topic>/output/final_mix.mp4 "
            "(requires topics/<topic>/mix/timeline.json)"
        ),
    )
    g.add_argument(
        "--only-final",
        action="store_true",
        dest="only_final",
        help=(
            "Run production --package → topics/<topic>/output/final_packaged.mp4 "
            "(requires topics/<topic>/output/final_with_presenter.mp4)"
        ),
    )
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    root = _root()
    _load_env(root)

    topic = args.topic.strip()
    if not topic:
        print("[run_pipeline] error: --topic must be non-empty", file=sys.stderr)
        return 2

    if args.only_main:
        return _run_only_main(root, topic)
    if args.only_final:
        return _run_only_final(root, topic)
    p.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
