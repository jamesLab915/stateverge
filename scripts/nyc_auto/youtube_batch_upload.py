#!/usr/bin/env python3
"""Batch upload NYC_AUTO packages (calls youtube_upload, no shell)."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import NYC_ROOT, path_matches_test_asset_marker, read_project_source_path
from youtube_token_paths import resolve_client_secrets, resolve_long_form_upload_token
from youtube_upload import (
    load_successful_video_paths,
    read_first_nonempty_line,
    upload_from_package_directory,
)


def default_packages_root() -> Path:
    return NYC_ROOT / "output" / "packages"


def path_matches_type(video_path: Path, kind: str) -> bool:
    s = str(video_path).lower()
    if kind == "shorts":
        return "/shorts/" in s or "_short_" in s
    if kind == "long":
        return "/long/" in s or "long_music" in s
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages-root", type=Path, default=None)
    ap.add_argument("--privacy", default="private", choices=("private", "unlisted", "public"))
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--type", choices=("shorts", "long", "all"), default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--playlist-id", default=None)
    ap.add_argument("--notify-subscribers", action="store_true")
    ap.add_argument("--force-reupload", action="store_true")
    ap.add_argument("--allow-test-assets", action="store_true")
    ap.add_argument("--token", type=Path, default=None, help="OAuth token JSON (use token_shorts.json for Shorts channel).")
    ap.add_argument("--client-secrets", type=Path, default=None, help="OAuth client secrets JSON.")
    args = ap.parse_args()

    token_path, _warn = resolve_long_form_upload_token(args.token)
    client_secrets = resolve_client_secrets(args.client_secrets)
    for w in _warn:
        print(f"WARNING: {w}")

    root = (args.packages_root or default_packages_root()).expanduser().resolve()
    if not root.is_dir():
        print(f"ERROR: packages root not found: {root}", file=sys.stderr)
        return 2

    if args.privacy == "public" and not args.allow_public:
        print("ERROR: --privacy public requires --allow-public", file=sys.stderr)
        return 3

    success_paths = load_successful_video_paths()
    pkgs = sorted([p for p in root.iterdir() if p.is_dir()], key=lambda x: x.name)
    successes = 0
    fails = 0
    skips = 0
    dry_done = 0

    for pkg in pkgs:
        if args.dry_run:
            if dry_done >= max(1, args.limit):
                break
        else:
            if successes >= max(1, args.limit):
                break

        if not (pkg / "selected_video_path.txt").is_file():
            skips += 1
            continue
        line = read_first_nonempty_line(pkg / "selected_video_path.txt")
        if not line:
            skips += 1
            continue
        vpath = Path(line.strip()).expanduser()
        try:
            vkey = str(vpath.resolve())
        except OSError:
            vkey = str(vpath)
        if not args.force_reupload and vkey in success_paths:
            skips += 1
            continue
        kind = "all" if args.type == "all" else args.type
        if not path_matches_type(vpath, kind):
            skips += 1
            continue

        sp = read_project_source_path(pkg.name)
        if not args.allow_test_assets and path_matches_test_asset_marker(
            str(pkg),
            pkg.name,
            vkey,
            sp,
        ):
            print(f"SKIP test/placeholder asset (package {pkg.name})")
            skips += 1
            continue

        print(f"--- package {pkg.name} ---")
        res = upload_from_package_directory(
            pkg,
            privacy=args.privacy,
            allow_public=args.allow_public,
            dry_run=args.dry_run,
            token_path=token_path,
            client_secrets=client_secrets,
            playlist_id=args.playlist_id,
            notify_subscribers=args.notify_subscribers,
            force_reupload=args.force_reupload,
            allow_test_assets=args.allow_test_assets,
        )
        if args.dry_run:
            dry_done += 1
        elif res.status == "success":
            successes += 1
            success_paths.add(vkey)
        elif res.status == "skipped_duplicate":
            skips += 1
        elif res.status == "blocked_test_asset":
            skips += 1
        else:
            fails += 1

    print(
        "=== batch summary "
        f"ok_success={successes} fail={fails} skip={skips} "
        f"dry_run_packages={dry_done if args.dry_run else 0} ==="
    )
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
