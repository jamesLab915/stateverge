#!/usr/bin/env python3
"""Orchestrate NYC_AUTO V1 stages (ingest, shorts, long music, YouTube package)."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import OUT_PACKAGES, list_projects_recent, log_lines, nyc_daily_log  # noqa: E402

PY = sys.executable


def run_mod(rel: str, argv: list[str], log_name: str) -> int:
    script = _SCRIPT_DIR / rel
    log_lines(log_name, [f"RUN {script.name} {' '.join(argv)}"], nyc_daily_log(log_name))
    try:
        r = subprocess.run([PY, str(script)] + argv, timeout=4 * 3600)
        return int(r.returncode or 0)
    except subprocess.TimeoutExpired:
        log_lines(log_name, [f"TIMEOUT {rel}"], nyc_daily_log(log_name))
        return 124
    except OSError as exc:
        log_lines(log_name, [f"OS_ERROR {rel}: {exc}"], nyc_daily_log(log_name))
        return 1


def resolve_ids(project_ids: list[str] | None, use_all: bool) -> list[str]:
    if project_ids:
        return list(dict.fromkeys(project_ids))
    if use_all:
        return list_projects_recent()
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="NYC_AUTO V1 pipeline wrapper")
    ap.add_argument("--ingest", action="store_true")
    ap.add_argument("--make-shorts", action="store_true")
    ap.add_argument("--make-long", action="store_true")
    ap.add_argument("--package", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--project-id", action="append", dest="project_ids", default=None)
    ap.add_argument("--music", default=None)
    ap.add_argument("--cut-start", type=float, default=0.0)
    ap.add_argument("--target-minutes", type=float, default=None)
    ap.add_argument("--short-count", type=int, default=5)
    ap.add_argument("--short-duration", type=float, default=30.0)
    ap.add_argument("--pkg-type", choices=("shorts", "long"), default="shorts")
    ap.add_argument("--title-topic", default=None)
    ap.add_argument("--youtube-upload", action="store_true")
    ap.add_argument("--privacy", default="private", choices=("private", "unlisted", "public"))
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--playlist-id", default=None)
    ap.add_argument("--dry-run-upload", action="store_true")
    ap.add_argument("--allow-test-assets", action="store_true")
    args = ap.parse_args()

    log_name = "nyc_pipeline"
    log_lines(log_name, ["========== pipeline start =========="], nyc_daily_log(log_name))
    rc = 0

    if args.ingest:
        rc = max(rc, run_mod("nyc_ingest_long_videos.py", [], log_name))

    targets = resolve_ids(args.project_ids, args.all)

    if args.make_shorts:
        shorts_args = [
            "--count",
            str(args.short_count),
            "--duration",
            str(args.short_duration),
        ]
        if args.all:
            shorts_args.append("--all")
            rc = max(rc, run_mod("nyc_generate_shorts_candidates.py", shorts_args, log_name))
        elif args.project_ids:
            for pid in targets:
                rc = max(
                    rc,
                    run_mod(
                        "nyc_generate_shorts_candidates.py",
                        shorts_args + ["--project-id", pid],
                        log_name,
                    ),
                )
        else:
            log_lines(
                log_name,
                ["SKIP make-shorts: need --project-id or --all"],
                nyc_daily_log(log_name),
            )
            rc = max(rc, 1)

    if args.make_long:
        if not targets:
            log_lines(
                log_name,
                ["SKIP make-long: need --project-id or --all"],
                nyc_daily_log(log_name),
            )
            rc = max(rc, 1)
        else:
            for pid in targets:
                lng = [
                    "--project-id",
                    pid,
                    "--cut-start",
                    str(args.cut_start),
                ]
                if args.music:
                    lng += ["--music", args.music]
                if args.target_minutes is not None:
                    lng += ["--target-minutes", str(args.target_minutes)]
                rc = max(rc, run_mod("nyc_make_long_music_version.py", lng, log_name))

    if args.package:
        if not targets:
            log_lines(
                log_name,
                ["SKIP package: need --project-id or --all"],
                nyc_daily_log(log_name),
            )
            rc = max(rc, 1)
        else:
            for pid in targets:
                pkg = ["--project-id", pid, "--type", args.pkg_type]
                if args.title_topic:
                    pkg += ["--title-topic", args.title_topic]
                rc = max(rc, run_mod("nyc_generate_youtube_package.py", pkg, log_name))

    if args.youtube_upload:
        if args.privacy == "public" and not args.allow_public:
            log_lines(
                log_name,
                ["ERROR: --privacy public requires --allow-public"],
                nyc_daily_log(log_name),
            )
            rc = max(rc, 3)
        elif not targets:
            log_lines(
                log_name,
                ["ERROR: --youtube-upload needs --project-id or --all"],
                nyc_daily_log(log_name),
            )
            rc = max(rc, 1)
        else:
            try:
                from youtube_upload import upload_from_package_directory
            except ImportError as exc:
                log_lines(
                    log_name,
                    [f"ERROR: YouTube deps missing ({exc}); pip install -r requirements-youtube.txt"],
                    nyc_daily_log(log_name),
                )
                rc = max(rc, 1)
            else:
                for pid in targets:
                    pkg_dir = OUT_PACKAGES / pid
                    if not pkg_dir.is_dir() or not (pkg_dir / "selected_video_path.txt").is_file():
                        log_lines(
                            log_name,
                            [f"SKIP youtube: missing package or selected_video_path for {pid}"],
                            nyc_daily_log(log_name),
                        )
                        rc = max(rc, 1)
                        continue
                    res = upload_from_package_directory(
                        pkg_dir,
                        privacy=args.privacy,
                        allow_public=args.allow_public,
                        dry_run=args.dry_run_upload,
                        playlist_id=args.playlist_id,
                        allow_test_assets=args.allow_test_assets,
                    )
                    if res.status == "rejected_privacy":
                        rc = max(rc, 3)
                    elif res.status == "blocked_test_asset":
                        log_lines(
                            log_name,
                            [f"SKIP youtube: BLOCKED_TEST_OR_PLACEHOLDER_ASSET {pid}"],
                            nyc_daily_log(log_name),
                        )
                    elif res.status == "error":
                        rc = max(rc, 1)

    log_lines(log_name, [f"========== pipeline end rc={rc} =========="], nyc_daily_log(log_name))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())