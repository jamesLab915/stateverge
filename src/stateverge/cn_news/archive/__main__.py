"""CLI: ``PYTHONPATH=src python3 -m stateverge.cn_news.archive <command>``.

    init-db [--db PATH]                          create data/political_archive.db
    history PERSON TOPIC [--years N] [--db PATH] print 今天翻旧账 for PERSON on TOPIC
"""

from __future__ import annotations

import argparse
from datetime import date

from .claim_search import search_history
from .database import DEFAULT_DB_PATH, ArchiveDB
from .models import NewsEvent
from .script_generator import card_from_history, history_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stateverge.cn_news.archive")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    hist = sub.add_parser("history")
    hist.add_argument("person")
    hist.add_argument("topic")
    hist.add_argument("--years", type=int, default=10)
    args = parser.parse_args(argv)

    with ArchiveDB(args.db) as db:
        if args.cmd == "init-db":
            print(f"initialised {db.path}")
            return 0
        event = NewsEvent("cli", args.topic, people=[args.person], topics=[args.topic], event_date=date.today().isoformat())
        claims = search_history(event, db, years=args.years)[args.person]
        if not claims:
            print(f"no archived statements for {args.person} on {args.topic}")
            return 1
        print(history_text(card_from_history(args.person, args.topic, claims)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
