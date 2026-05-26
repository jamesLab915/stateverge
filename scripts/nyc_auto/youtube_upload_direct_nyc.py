#!/usr/bin/env python3
"""Upload one video file to the **NYC long-channel** OAuth token (no manual publish_pack).

Creates a minimal staging package under ``~/StateVerge/data/youtube_nyc_direct_upload_staging/``,
writes ``selected_video_path.txt``, then calls ``upload_from_package_directory``.

- Uses official long token resolution (``youtube_token_paths``): CLI ``--token`` overrides;
  else ``data/youtube/token.json``, with legacy ``.secrets/youtube/token.json`` fallback + warning.
- Does **not** delete your source video. Staging dir is removed after **successful** upload.

Examples::

    python3 scripts/nyc_auto/youtube_upload_direct_nyc.py \\
      --video /path/to/clip.mp4 --title "My NYC clip" --privacy unlisted

    python3 scripts/nyc_auto/youtube_upload_direct_nyc.py \\
      --video /path/to/clip.mp4 --dry-run
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from channel_guard import assert_long_upload_context  # noqa: E402
from youtube_token_paths import resolve_client_secrets, resolve_long_form_upload_token  # noqa: E402
from youtube_upload import upload_from_package_directory  # noqa: E402

STAGING_ROOT = Path.home() / "StateVerge" / "data" / "youtube_nyc_direct_upload_staging"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True, help="Absolute or ~/ path to MP4/MOV.")
    ap.add_argument("--title", default="", help="YouTube title (default: file stem).")
    ap.add_argument("--description", default="", help="Optional; also written to description.txt in staging.")
    ap.add_argument(
        "--tags",
        default="NYC, New York, StateVerge",
        help="Comma-separated tags (default NYC trio).",
    )
    ap.add_argument("--privacy", choices=("private", "unlisted", "public"), default="unlisted")
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--token", type=Path, default=None)
    ap.add_argument("--client-secrets", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-reupload", action="store_true")
    args = ap.parse_args()
    if args.privacy == "public" and not args.allow_public:
        print("ERROR: public requires --allow-public (auto-publish queues never use public).", file=sys.stderr)
        return 3

    video = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"ERROR: not a file: {video}", file=sys.stderr)
        return 2

    token_path, warns = resolve_long_form_upload_token(args.token)
    guard = assert_long_upload_context(video, token_path)
    if guard:
        print(json.dumps(guard, indent=2, ensure_ascii=False), file=sys.stderr)
        return 6
    sec = resolve_client_secrets(args.client_secrets)
    token_used = str(token_path)
    sec_used = str(sec)

    print("=== youtube_upload_direct_nyc ===")
    print(f"video={video}")
    print(f"token_path_used={token_used}")
    print(f"client_secrets_path_used={sec_used}")
    print(f"privacy={args.privacy} dry_run={args.dry_run}")
    for w in warns:
        print(f"WARNING: {w}")

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    pkg = STAGING_ROOT / f"nyc_direct_{uuid.uuid4().hex[:12]}"
    pkg.mkdir(parents=True, exist_ok=True)

    tit = (args.title or "").strip() or video.stem
    desc = (args.description or "").strip()

    (pkg / "selected_video_path.txt").write_text(str(video) + "\n", encoding="utf-8")
    if desc:
        (pkg / "description.txt").write_text(desc, encoding="utf-8")
    tags_line = (args.tags or "").strip()
    if tags_line:
        (pkg / "tags.txt").write_text(tags_line, encoding="utf-8")

    res = upload_from_package_directory(
        pkg,
        privacy=args.privacy,
        allow_public=args.allow_public,
        title=tit,
        description=desc or None,
        tags_str=args.tags,
        dry_run=args.dry_run,
        token_path=token_path,
        client_secrets=sec,
        force_reupload=args.force_reupload,
        allow_test_assets=False,
        token_path_used=token_used,
        client_secrets_path_used=sec_used,
    )

    if res.ok:
        if res.status == "success" and res.video_id:
            payload = {
                "ok": True,
                "video_id": res.video_id,
                "url": f"https://www.youtube.com/watch?v={res.video_id}",
                "title": res.title,
                "privacy": res.privacy,
                "token_path_used": token_used,
            }
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            print(f"OK: https://www.youtube.com/watch?v={res.video_id}")
            shutil.rmtree(pkg, ignore_errors=True)
        elif res.status == "dry_run":
            print("OK: dry-run complete (no upload).")
            print(f"(staging kept for inspection: {pkg})")
        return 0

    err_payload = {
        "ok": False,
        "status": res.status,
        "error": res.error,
        "token_path_used": token_used,
    }
    err_path = pkg / "upload_error.json"
    err_path.write_text(json.dumps(err_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(err_payload, indent=2, ensure_ascii=False), file=sys.stderr)
    print(f"Staging kept: {pkg}", file=sys.stderr)
    if res.status == "rejected_privacy":
        return 3
    if res.status == "skipped_duplicate":
        return 4
    if res.status == "blocked_test_asset":
        return 5
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
