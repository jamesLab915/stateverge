#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path.home() / "StateVerge"
DEFAULT_TOKEN = ROOT / "data" / "youtube" / "token_shorts.json"
DEFAULT_STAGING_ROOT = ROOT / "data" / "youtube_shorts_direct_upload_staging"

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from channel_guard import assert_shorts_upload_context  # noqa: E402


def die(msg: str, code: int = 1):
    print(msg, file=sys.stderr)
    raise SystemExit(code)


def load_creds(token_path: Path) -> Credentials:
    if not token_path.exists():
        die(f"TOKEN_NOT_FOUND: {token_path}")
    data = json.loads(token_path.read_text())
    creds = Credentials.from_authorized_user_info(data)
    if not creds or not creds.valid:
        die(f"TOKEN_INVALID_OR_EXPIRED: {token_path}")
    return creds


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    privacy: str,
    token_path: Path,
    dry_run: bool,
):
    if not video_path.exists():
        die(f"VIDEO_NOT_FOUND: {video_path}")
    if video_path.suffix.lower() not in {".mp4", ".mov", ".m4v"}:
        die(f"UNSUPPORTED_VIDEO_FILE: {video_path}")

    guard = assert_shorts_upload_context(video_path, token_path)
    if guard:
        print(json.dumps(guard, ensure_ascii=False, indent=2), file=sys.stderr)
        die("CHANNEL_GUARD_BLOCKED", 6)

    staging_dir = DEFAULT_STAGING_ROOT / f"{int(time.time())}_{video_path.stem}"
    staging_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["Shorts", "NYC", "New York City", "StateVerge"],
            "categoryId": "22",
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    (staging_dir / "selected_video_path.txt").write_text(str(video_path) + "\n")
    (staging_dir / "youtube_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2)
    )

    if dry_run:
        result = {
            "dry_run": True,
            "uploaded": False,
            "video": str(video_path),
            "title": title,
            "privacy": privacy,
            "token_path": str(token_path),
            "staging_dir": str(staging_dir),
        }
        (staging_dir / "_dry_run_result.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return result

    creds = load_creds(token_path)
    youtube = build("youtube", "v3", credentials=creds)

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        chunksize=-1,
        resumable=True,
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=metadata,
        media_body=media,
    )

    print(f"UPLOAD_START video={video_path}")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"UPLOAD_PROGRESS {int(status.progress() * 100)}%")

    video_id = response.get("id", "")
    result = {
        "uploaded": bool(video_id),
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        "video": str(video_path),
        "title": title,
        "privacy": privacy,
        "token_path": str(token_path),
        "staging_dir": str(staging_dir),
        "raw_response": response,
    }

    (staging_dir / "_upload_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2)
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--title", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="unlisted", choices=["private", "unlisted", "public"])
    ap.add_argument("--allow-public", action="store_true")
    ap.add_argument("--token", default=str(DEFAULT_TOKEN))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.privacy == "public" and not args.allow_public:
        die("PUBLIC_UPLOAD_BLOCKED: add --allow-public if you really want public")

    video = Path(args.video).expanduser().resolve()
    title = args.title.strip() or video.stem
    description = args.description.strip() or "#Shorts\n\nNYC street footage by StateVerge."

    token_path = Path(args.token).expanduser().resolve()
    blocked = assert_shorts_upload_context(video, token_path)
    if blocked:
        die(json.dumps(blocked, ensure_ascii=False), 6)

    upload_video(
        video_path=video,
        title=title,
        description=description,
        privacy=args.privacy,
        token_path=token_path,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
