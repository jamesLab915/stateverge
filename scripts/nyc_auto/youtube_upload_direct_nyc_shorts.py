#!/usr/bin/env python3
"""Upload one **Short-format** video to **StateVerge NYC** (long-channel OAuth token).

Use this when you want a vertical Short on @statevergenyc, not on @realnycshorts.
For the dedicated Shorts channel, use ``youtube_upload_direct_shorts.py`` + ``token_shorts.json``.

Creates a minimal staging package under ``~/StateVerge/data/youtube_nyc_long_short_upload_staging/``,
writes ``selected_video_path.txt``, then calls ``upload_from_package_directory``.

Examples::

    python3 scripts/nyc_auto/youtube_upload_direct_nyc_shorts.py \\
      --video /Volumes/SV_TRANSFER/ready_to_upload/shorts_clips/clip.mp4 \\
      --title "NYC Harbor Evening #Shorts" --privacy unlisted

    python3 scripts/nyc_auto/youtube_upload_direct_nyc_shorts.py \\
      --video /path/to/vertical.mp4 --dry-run
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

from channel_guard import assert_long_channel_short_upload_context  # noqa: E402
from youtube_token_paths import resolve_client_secrets, resolve_long_form_upload_token  # noqa: E402
from youtube_upload import upload_from_package_directory  # noqa: E402

STAGING_ROOT = Path.home() / "StateVerge" / "data" / "youtube_nyc_long_short_upload_staging"

DEFAULT_TAGS = "Shorts,NYC,New York,StateVerge,StateVerge NYC"


def _ensure_shorts_description(desc: str) -> str:
    d = (desc or "").strip()
    if not d:
        return "#Shorts\n\nNYC vertical clip by StateVerge NYC."
    if "#shorts" not in d.lower():
        return f"{d}\n\n#Shorts"
    return d


def _ensure_shorts_title(title: str, video: Path) -> str:
    t = (title or "").strip() or video.stem
    if "#shorts" not in t.lower() and "shorts" not in t.lower():
        if len(t) + 9 <= 100:
            t = f"{t} #Shorts"
    return t[:100]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--video", type=Path, required=True, help="MP4/MOV/M4V (typically 9:16).")
    ap.add_argument("--title", default="", help="YouTube title (default: stem + #Shorts).")
    ap.add_argument("--description", default="", help="Description (#Shorts appended if missing).")
    ap.add_argument("--tags", default=DEFAULT_TAGS, help="Comma-separated tags.")
    ap.add_argument("--privacy", choices=("private", "unlisted", "public"), default="unlisted")
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--token", type=Path, default=None, help="Override long token (default: data/youtube/token.json).")
    ap.add_argument("--client-secrets", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force-reupload", action="store_true")
    args = ap.parse_args()

    if args.privacy == "public" and not args.allow_public:
        print("ERROR: public requires --allow-public.", file=sys.stderr)
        return 3

    video = args.video.expanduser().resolve()
    if not video.is_file():
        print(f"ERROR: not a file: {video}", file=sys.stderr)
        return 2
    if video.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        print(f"ERROR: unsupported extension: {video.suffix}", file=sys.stderr)
        return 2

    token_path, warns = resolve_long_form_upload_token(args.token)
    guard = assert_long_channel_short_upload_context(video, token_path)
    if guard:
        print(json.dumps(guard, indent=2, ensure_ascii=False), file=sys.stderr)
        return 6
    sec = resolve_client_secrets(args.client_secrets)
    token_used = str(token_path)
    sec_used = str(sec)

    tit = _ensure_shorts_title(args.title, video)
    desc = _ensure_shorts_description(args.description)

    print("=== youtube_upload_direct_nyc_shorts ===")
    print("channel=StateVerge NYC (long token)")
    print(f"video={video}")
    print(f"token_path_used={token_used}")
    print(f"client_secrets_path_used={sec_used}")
    print(f"privacy={args.privacy} dry_run={args.dry_run}")
    for w in warns:
        print(f"WARNING: {w}")

    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
    pkg = STAGING_ROOT / f"nyc_long_short_{uuid.uuid4().hex[:12]}"
    pkg.mkdir(parents=True, exist_ok=True)

    (pkg / "selected_video_path.txt").write_text(str(video) + "\n", encoding="utf-8")
    (pkg / "description.txt").write_text(desc, encoding="utf-8")
    tags_line = (args.tags or "").strip()
    if tags_line:
        (pkg / "tags.txt").write_text(tags_line, encoding="utf-8")
    meta = {
        "video_type": "short",
        "upload_surface": "long_channel_short",
        "channel": "NYC_LONG",
        "snippet": {"title": tit, "description": desc, "tags": [t.strip() for t in tags_line.split(",") if t.strip()]},
    }
    (pkg / "youtube_metadata.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    res = upload_from_package_directory(
        pkg,
        privacy=args.privacy,
        allow_public=args.allow_public,
        title=tit,
        description=desc,
        tags_str=tags_line or None,
        dry_run=args.dry_run,
        token_path=token_path,
        client_secrets=sec,
        force_reupload=args.force_reupload,
        allow_test_assets=False,
        token_path_used=token_used,
        client_secrets_path_used=sec_used,
        channel_type="short",
    )

    if res.ok:
        if res.status == "success" and res.video_id:
            payload = {
                "ok": True,
                "video_id": res.video_id,
                "url": f"https://www.youtube.com/watch?v={res.video_id}",
                "title": res.title,
                "privacy": res.privacy,
                "channel": "StateVerge NYC",
                "upload_surface": "long_channel_short",
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
        "upload_surface": "long_channel_short",
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
