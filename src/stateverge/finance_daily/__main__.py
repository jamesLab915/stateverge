"""CLI: ``PYTHONPATH=src python3 -m stateverge.finance_daily cloud-run [--root data/finance] [--work finance_build]``."""

from __future__ import annotations

import argparse
from pathlib import Path

from .runner import FinanceRunner


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="stateverge.finance_daily")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cr = sub.add_parser("cloud-run")
    cr.add_argument("--root", default="data/finance")
    cr.add_argument("--work", default="finance_build")
    args = ap.parse_args(argv)
    print("\n".join(FinanceRunner(Path(args.root), Path(args.work)).run()) or "nothing pending")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
