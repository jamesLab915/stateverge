"""
Production CLI: brief, OpenAI scripts, LTX plan, packaging (ffmpeg).

Run from repo root::

    export PYTHONPATH="$PWD"
    python -m src.production.cli --topic my-ep --generate-brief
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import brief_loader, ltx_planner, packaging_engine, script_generator
from .paths import topic_production_paths


def _root() -> Path:
    return Path(os.environ.get("STATEVERGE_ROOT", Path.home() / "StateVerge"))


def _load_env() -> None:
    p = _root() / ".env"
    if p.is_file():
        try:
            from dotenv import load_dotenv
            load_dotenv(p, override=False)
        except Exception:
            pass


def main() -> int:
    _load_env()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    p = argparse.ArgumentParser(description="StateVerge production (brief, scripts, LTX plan, pack)")
    p.add_argument("--topic", required=True, help="Topic slug (topics/<slug>)")
    p.add_argument(
        "--generate-brief", action="store_true", help="Create topics/<t>/brief/production_brief.json if missing"
    )
    p.add_argument(
        "--generate-scripts",
        action="store_true",
        help="OpenAI → script/narration_script.txt and presenter_script.json",
    )
    p.add_argument(
        "--generate-ltx-plan", action="store_true", help="Write brief/ltx_scene_plan.json"
    )
    p.add_argument(
        "--package", action="store_true", help="ffmpeg: music duck + SFX + lower-thirds → final_packaged.mp4"
    )
    a = p.parse_args()
    if not any(
        [
            a.generate_brief,
            a.generate_scripts,
            a.generate_ltx_plan,
            a.package,
        ]
    ):
        p.print_help()
        return 1
    root = _root()
    t = topic_production_paths(root, a.topic)
    t["root"].mkdir(parents=True, exist_ok=True)
    t["brief"].mkdir(parents=True, exist_ok=True)
    t["script"].mkdir(parents=True, exist_ok=True)
    t["output"].mkdir(parents=True, exist_ok=True)
    if a.generate_brief:
        path, b, cr = brief_loader.load_or_create_default(
            root, a.topic, title=a.topic.replace("-", " ").title()
        )
        if cr:
            print(f"Created brief: {path}")
        else:
            print(f"Brief already exists: {path}")
    if a.generate_scripts:
        n, ps = script_generator.generate_scripts(root, a.topic, brief=None)
        print(f"Wrote:\n  {n}\n  {ps}")
    if a.generate_ltx_plan:
        tpaths = topic_production_paths(root, a.topic)
        bp = tpaths["production_brief"]
        np = tpaths["narration_script"]
        print(f"正在读取 production_brief: {bp}", flush=True)
        print(f"正在读取 narration_script: {np}", flush=True)
        try:
            res = ltx_planner.generate_ltx_scene_plan(root, a.topic)
        except FileNotFoundError as e:
            print(f"错误: {e}", file=sys.stderr, flush=True)
            return 1
        except ValueError as e:
            print(f"错误: {e}", file=sys.stderr, flush=True)
            return 1
        out = res.output_path
        print(f"生成了 {res.scene_count} 个 scenes", flush=True)
        print(f"输出文件: {out}", flush=True)
        print(f"Wrote LTX scene plan: {out}", flush=True)
        print(f"Scene count: {res.scene_count}", flush=True)
    if a.package:
        o = packaging_engine.apply_packaging(root, a.topic)
        print(f"Wrote: {o}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
