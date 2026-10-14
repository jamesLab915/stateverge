"""``PYTHONPATH=src python3 -m stateverge.cn_news.cnbrief cloud-run [--root data/cnbrief]``"""

from __future__ import annotations

import argparse
from pathlib import Path

from .runner import CnBriefRunner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stateverge.cn_news.cnbrief")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cr = sub.add_parser("cloud-run")
    cr.add_argument("--root", default="data/cnbrief")
    args = ap.parse_args(argv)
    print("\n".join(CnBriefRunner(Path(args.root)).run()) or "nothing pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
