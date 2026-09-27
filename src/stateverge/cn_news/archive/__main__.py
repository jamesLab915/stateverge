"""CLI: ``PYTHONPATH=src python3 -m stateverge.cn_news.archive <command>``.

    init-db [--db PATH]                          create data/political_archive.db
    history PERSON TOPIC [--years N] [--db PATH] print 今天翻旧账 for PERSON on TOPIC
    x-post EARLIER_ID LATER_ID --reviewer NAME --context-reviewed
           --opinion-checked --corrections-checked [--post]
                                                 compare two stored claims, run the
                                                 publish guard, preview the X thread;
                                                 --post actually posts (needs X_* env vars)
"""

from __future__ import annotations

import argparse
from datetime import date

from . import publish_guard, x_publisher
from .claim_search import search_history
from .contradiction import compare
from .database import DEFAULT_DB_PATH, ArchiveDB
from .models import NewsEvent
from .script_generator import card_from_comparison, card_from_history, history_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="stateverge.cn_news.archive")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    hist = sub.add_parser("history")
    hist.add_argument("person")
    hist.add_argument("topic")
    hist.add_argument("--years", type=int, default=10)
    xp = sub.add_parser("x-post")
    xp.add_argument("earlier_id")
    xp.add_argument("later_id")
    xp.add_argument("--reviewer", default="")
    xp.add_argument("--context-reviewed", action="store_true")
    xp.add_argument("--opinion-checked", action="store_true")
    xp.add_argument("--corrections-checked", action="store_true")
    xp.add_argument("--post", action="store_true", help="actually post (default: preview only)")
    args = parser.parse_args(argv)

    with ArchiveDB(args.db) as db:
        if args.cmd == "init-db":
            print(f"initialised {db.path}")
            return 0
        if args.cmd == "x-post":
            return _x_post(db, args)
        event = NewsEvent("cli", args.topic, people=[args.person], topics=[args.topic], event_date=date.today().isoformat())
        claims = search_history(event, db, years=args.years)[args.person]
        if not claims:
            print(f"no archived statements for {args.person} on {args.topic}")
            return 1
        print(history_text(card_from_history(args.person, args.topic, claims)))
    return 0


def _x_post(db: ArchiveDB, args: argparse.Namespace) -> int:
    earlier, later = db.get(args.earlier_id), db.get(args.later_id)
    if earlier is None or later is None:
        print("claim not found")
        return 1
    card = card_from_comparison(compare(earlier, later))
    attestation = publish_guard.ReviewerAttestation(
        reviewer=args.reviewer,
        opinion_not_stated_as_fact=args.opinion_checked,
        corrections_checked=args.corrections_checked,
        context_reviewed=args.context_reviewed,
    )
    try:
        result = x_publisher.publish_card(card, attestation, db=db, dry_run=not args.post)
    except x_publisher.XPublishError as e:
        print(f"error: {e}")
        return 1
    print(f"status: {card.status.value if card.status else '-'}  decision: {result.decision}")
    for f in result.failures:
        print(f"  ✗ {f}")
    for i, part in enumerate(result.parts, 1):
        print(f"\n--- post {i} ({x_publisher.weighted_length(part)}/280) ---\n{part}")
    if result.tweet_ids:
        print(f"\nposted: {result.url}")
    elif result.decision == publish_guard.ALLOW_PUBLISH:
        print("\n(preview only — add --post to publish)")
    return 0 if result.decision == publish_guard.ALLOW_PUBLISH else 2


if __name__ == "__main__":
    raise SystemExit(main())
