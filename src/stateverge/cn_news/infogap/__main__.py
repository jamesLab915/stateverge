"""CLI: ``PYTHONPATH=src python3 -m stateverge.cn_news.infogap <command>``.

    scan [--hours 24] [--root data/infogap]   scan the English internet, write the review file
    cloud-run [--root data/infogap]           process digest_requests.json (GitHub Actions)
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..archive import envfile
from .assessor import LLMAssessor
from .collectors import default_collectors
from .coverage import default_coverage
from .digest import scan
from .runner import InfoGapRunner, save_scan


def main(argv: list[str] | None = None) -> int:
    envfile.load()
    parser = argparse.ArgumentParser(prog="stateverge.cn_news.infogap")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("scan")
    sc.add_argument("--hours", type=int, default=24)
    sc.add_argument("--root", default="data/infogap")
    cr = sub.add_parser("cloud-run")
    cr.add_argument("--root", default="data/infogap")
    args = parser.parse_args(argv)

    if args.cmd == "scan":
        result = scan(default_collectors(), default_coverage(), LLMAssessor(), hours=args.hours)
        js, md = save_scan(result, Path(args.root))
        print(f"抓取 {result.collected} 条,候选 {len(result.candidates)} 条,入选 {len(result.picks)} 条")
        print(f"审核稿:{md}")
        return 0
    print("\n".join(InfoGapRunner(Path(args.root)).run()) or "nothing pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
