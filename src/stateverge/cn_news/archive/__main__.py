"""CLI: ``PYTHONPATH=src python3 -m stateverge.cn_news.archive <command>``.

    init-db [--db PATH]                          create data/political_archive.db
    history PERSON TOPIC [--years N] [--db PATH] print 今天翻旧账 for PERSON on TOPIC
    import FILE.json [--dry-run]                 import hand-curated statements (see
                                                 docs/archive_import_example.json)
    collect PERSON TOPIC [--years N]             search GovInfo (official transcripts) and
                                                 store hits as CANDIDATE claims
    cloud-run [--root data/archive] [--llm]      process collect/publish queues (GitHub Actions)
    x-post EARLIER_ID LATER_ID --reviewer NAME --context-reviewed
           --opinion-checked --corrections-checked [--post]
                                                 compare two stored claims, run the
                                                 publish guard, preview the X thread;
                                                 --post actually posts (needs X_* env vars)
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from . import envfile, importer, publish_guard, x_publisher
from .claim_search import search_history
from .cloud_runner import CloudRunner
from .govinfo_provider import GovInfoProvider
from .stance_llm import LLMStanceJudge
from .contradiction import compare
from .database import DEFAULT_DB_PATH, ArchiveDB
from .models import NewsEvent
from .script_generator import card_from_comparison, card_from_history, history_text


def main(argv: list[str] | None = None) -> int:
    envfile.load()  # secrets from the git-ignored .env.local / .env, if present
    parser = argparse.ArgumentParser(prog="stateverge.cn_news.archive")
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    hist = sub.add_parser("history")
    hist.add_argument("person")
    hist.add_argument("topic")
    hist.add_argument("--years", type=int, default=10)
    imp = sub.add_parser("import")
    imp.add_argument("file")
    imp.add_argument("--dry-run", action="store_true", help="validate only, write nothing")
    xp = sub.add_parser("x-post")
    xp.add_argument("earlier_id")
    xp.add_argument("later_id")
    xp.add_argument("--reviewer", default="")
    xp.add_argument("--context-reviewed", action="store_true")
    xp.add_argument("--opinion-checked", action="store_true")
    xp.add_argument("--corrections-checked", action="store_true")
    xp.add_argument("--post", action="store_true", help="actually post (default: preview only)")
    xp.add_argument("--llm", action="store_true", help="judge stance with the LLM (OPENAI_API_KEY)")
    col = sub.add_parser("collect")
    col.add_argument("person")
    col.add_argument("topic")
    col.add_argument("--years", type=int, default=10)
    cr = sub.add_parser("cloud-run")
    cr.add_argument("--root", default="data/archive")
    cr.add_argument("--llm", action="store_true", help="judge stance with the LLM (OPENAI_API_KEY)")
    args = parser.parse_args(argv)

    with ArchiveDB(args.db) as db:
        if args.cmd == "init-db":
            print(f"initialised {db.path}")
            return 0
        if args.cmd == "import":
            report = importer.import_file(args.file, db, dry_run=args.dry_run)
            print(report.render() + ("(dry run,未写入)" if args.dry_run else ""))
            return 0 if report.failed == 0 else 1
        if args.cmd == "x-post":
            return _x_post(db, args)
        if args.cmd == "collect":
            return _collect(db, args)
        if args.cmd == "cloud-run":
            runner = CloudRunner(Path(args.root), stance_judge=LLMStanceJudge() if args.llm else None)
            print("\n".join(runner.run()) or "nothing pending")
            return 0
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
    judge = LLMStanceJudge() if args.llm else None
    card = card_from_comparison(compare(earlier, later, stance_judge=judge))
    if judge and judge.last_reason:
        print(f"LLM: {judge.last_reason}")
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


def _collect(db: ArchiveDB, args: argparse.Namespace) -> int:
    event = NewsEvent("cli", args.topic, people=[args.person], topics=[args.topic], event_date=date.today().isoformat())
    claims = search_history(event, db, [GovInfoProvider()], years=args.years)[args.person]
    for c in claims:
        flag = "✓" if c.transcript_verified else "?"
        print(f"{flag} [{c.claim_id}] {c.statement_date} {c.source_name}\n    “{c.statement_text_original}”")
    print(f"\n{len(claims)} 条(新采集的均为 CANDIDATE,需人工审核)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
