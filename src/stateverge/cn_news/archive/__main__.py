"""CLI: ``PYTHONPATH=src python3 -m stateverge.cn_news.archive <command>``.

    init-db [--db PATH]                          create data/political_archive.db
    history PERSON TOPIC [--years N] [--db PATH] print 今天翻旧账 for PERSON on TOPIC
    import FILE.json [--dry-run]                 import hand-curated statements (see
                                                 docs/archive_import_example.json)
    collect PERSON TOPIC [--years N]             search GovInfo (official transcripts) and
                                                 store hits as CANDIDATE claims
    cloud-run [--root data/archive] [--llm] [--stats]
                                                 process collect/publish/stats queues (GitHub Actions)
    x-video EARLIER_ID LATER_ID --source-earlier A.mp4 --source-later B.mp4 [review flags] [--post]
                                                 render the Section 15 video (ffmpeg) and post it with the thread
    x-stats [--out data/archive/x_stats.json]    account snapshot: followers, 7/90-day impressions
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

from . import clip_builder, envfile, importer, publish_guard, video_render, x_publisher, x_stats
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
    cr.add_argument("--stats", action="store_true", help="always take an account snapshot")
    xv = sub.add_parser("x-video")
    xv.add_argument("earlier_id")
    xv.add_argument("later_id")
    xv.add_argument("--source-earlier", required=True)
    xv.add_argument("--source-later", required=True)
    xv.add_argument("--out", default="")
    xv.add_argument("--reviewer", default="")
    xv.add_argument("--context-reviewed", action="store_true")
    xv.add_argument("--opinion-checked", action="store_true")
    xv.add_argument("--corrections-checked", action="store_true")
    xv.add_argument("--extend-reason", default="", help="needed when an excerpt is longer than 12s")
    xv.add_argument("--post", action="store_true")
    xs = sub.add_parser("x-stats")
    xs.add_argument("--out", default="data/archive/x_stats.json")
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
            print("\n".join(runner.run(stats=args.stats)) or "nothing pending")
            return 0
        if args.cmd == "x-video":
            return _x_video(db, args)
        if args.cmd == "x-stats":
            client = x_publisher.XClient(x_publisher.XCredentials.from_env())
            print(x_stats.record(x_stats.take_snapshot(client), Path(args.out)))
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


def _x_video(db: ArchiveDB, args: argparse.Namespace) -> int:
    earlier, later = db.get(args.earlier_id), db.get(args.later_id)
    if earlier is None or later is None:
        print("claim not found")
        return 1
    card = card_from_comparison(compare(earlier, later))
    elements = ["HISTORICAL_COMPARISON", "TIMELINE", "CONTEXT_EXPLANATION"]
    try:
        plans = {c.claim_id: clip_builder.plan_clip(c, elements, extend_reason=args.extend_reason)
                 for c in (earlier, later)}
        out = Path(args.out or clip_builder.CLIPS_DIR / f"{earlier.claim_id[:8]}-{later.claim_id[:8]}.mp4")
        video_render.render(card, plans, {earlier.claim_id: Path(args.source_earlier),
                                          later.claim_id: Path(args.source_later)}, out)
    except (clip_builder.ClipRefused, video_render.RenderError) as e:
        print(f"refused: {e}")
        return 2
    print(f"video: {out}")
    attestation = publish_guard.ReviewerAttestation(
        reviewer=args.reviewer,
        opinion_not_stated_as_fact=args.opinion_checked,
        corrections_checked=args.corrections_checked,
        context_reviewed=args.context_reviewed,
    )
    try:
        result = x_publisher.publish_card(card, attestation, clips=list(plans.values()), db=db,
                                          dry_run=not args.post, video=out)
    except x_publisher.XPublishError as e:
        print(f"error: {e}")
        return 1
    print(f"decision: {result.decision}")
    for f in result.failures:
        print(f"  ✗ {f}")
    print(f"posted: {result.url}" if result.tweet_ids else "(preview only — watch the video, then add --post)")
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
