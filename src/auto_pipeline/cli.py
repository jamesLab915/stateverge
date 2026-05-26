"""CLI entry point for the future-tech documentary auto pipeline."""

from __future__ import annotations

import argparse
import sys

from .build_render_plan import build_render_plan
from .generate_heygen_plan import generate_heygen_plan
from .generate_prompts import generate_prompts
from .generate_queries import generate_queries
from .generate_script import generate_script
from .split_sections import split_sections


def run(topic: str) -> None:
    generate_script(topic)
    split_sections(topic)
    generate_prompts(topic)
    generate_queries(topic)
    generate_heygen_plan(topic)
    build_render_plan(topic)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate StateVerge future documentary production files."
    )
    ap.add_argument("--topic", required=True, help="Topic slug, e.g. ai-factory-future")
    args = ap.parse_args(argv)
    run(args.topic)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
