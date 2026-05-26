#!/usr/bin/env python3
"""Interactive CLI: natural language → production plan JSON (v1, plan only)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from ai_director.build_production_plan import build_production_plan, save_production_plan  # noqa: E402
from ai_director.production_plan_schema import validate_plan  # noqa: E402


def _print_plan(plan: dict) -> None:
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="StateVerge Director AI v1 — production plan from NL")
    ap.add_argument("--prompt", type=str, default="", help="Non-interactive brief (CN/EN)")
    ap.add_argument("--use-llm", action="store_true", help="Optional LLM merge (requires OpenAI key)")
    ap.add_argument("--no-save", action="store_true", help="Print only; do not write JSON file")
    args = ap.parse_args(argv)

    prompts: list[str] = []
    if str(args.prompt or "").strip():
        prompts.append(str(args.prompt).strip())
    else:
        print("StateVerge Director AI v1 — enter a brief (empty line to quit).")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                break
            prompts.append(line)

    if not prompts:
        print("DIRECTOR_AI_V1_READY=true")
        return 0

    last_path = None
    for text in prompts:
        plan = build_production_plan(text, use_llm=bool(args.use_llm))
        _print_plan(plan)
        errors = validate_plan(plan)
        if errors:
            print(f"# validation_warnings: {errors}", file=sys.stderr)
        if not args.no_save:
            last_path = save_production_plan(plan)
            print(f"# saved: {last_path}", file=sys.stderr)

    if last_path:
        print(f"DIRECTOR_AI_V1_READY=true plan={last_path}")
    else:
        print("DIRECTOR_AI_V1_READY=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
