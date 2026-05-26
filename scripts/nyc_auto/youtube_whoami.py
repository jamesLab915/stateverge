#!/usr/bin/env python3
"""Print the authenticated YouTube channel (channels.list mine=True). No uploads."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_NYC_AUTO = Path(__file__).resolve().parent
for p in (_SCRIPTS, _NYC_AUTO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from youtube_scopes import SCOPES
from youtube_token_paths import OFFICIAL_LONG_TOKEN_PATH, resolve_long_form_upload_token

DEFAULT_TOKEN = OFFICIAL_LONG_TOKEN_PATH


def _hint_reauth(token_path: Path) -> None:
    print(
        "\nIf you see insufficient scopes, delete the old token and re-authorize "
        "(upload + youtube.readonly):\n"
        f"  rm {token_path}\n"
        "  python3 scripts/nyc_auto/youtube_auth_init.py\n"
        "  python3 scripts/nyc_auto/youtube_whoami.py\n",
        file=sys.stderr,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Show YouTube channel for the saved OAuth token.")
    ap.add_argument("--token", type=Path, default=None, help="OAuth token (default: official long token path + legacy fallback).")
    args = ap.parse_args()
    token_path, warns = resolve_long_form_upload_token(args.token)
    for w in warns:
        print(f"WARNING: {w}", file=sys.stderr)

    if not token_path.is_file():
        print(f"ERROR: token not found: {token_path}", file=sys.stderr)
        print("Run: python3 scripts/nyc_auto/youtube_auth_init.py", file=sys.stderr)
        return 2

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
    except ImportError:
        print(
            "ERROR: install dependencies:\n  python3 -m pip install -r requirements-youtube.txt",
            file=sys.stderr,
        )
        return 1

    try:
        from google.auth.exceptions import RefreshError
    except ImportError:  # pragma: no cover
        RefreshError = Exception  # type: ignore[misc,assignment]
    try:
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
    except RefreshError as exc:
        low = str(exc).lower()
        if "invalid_grant" in low:
            print("YOUTUBE_TOKEN_INVALID_GRANT", file=sys.stderr)
            print(f"token_path_used={token_path}", file=sys.stderr)
        print(f"ERROR: could not load credentials: {exc}", file=sys.stderr)
        _hint_reauth(token_path)
        return 3
    except Exception as exc:  # noqa: BLE001
        low = str(exc).lower()
        if "invalid_grant" in low:
            print("YOUTUBE_TOKEN_INVALID_GRANT", file=sys.stderr)
            print(f"token_path_used={token_path}", file=sys.stderr)
        print(f"ERROR: could not load credentials: {exc}", file=sys.stderr)
        _hint_reauth(token_path)
        return 3

    try:
        youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = youtube.channels().list(part="snippet,contentDetails,statistics", mine=True).execute()
    except HttpError as exc:
        body = ""
        try:
            body = json.dumps(json.loads(exc.content.decode("utf-8", errors="replace")), indent=0)
        except (TypeError, ValueError, AttributeError):
            body = str(exc)
        low = body.lower()
        if exc.resp.status == 403 and (
            "insufficient" in low
            or "insufficientauthentication" in low.replace(" ", "")
            or "access_not_configured" in low
        ):
            print("ERROR: insufficient OAuth scopes for channels.list(mine=True).", file=sys.stderr)
            _hint_reauth(token_path)
        else:
            print(f"ERROR: YouTube API: {exc}", file=sys.stderr)
            if exc.resp.status == 403:
                _hint_reauth(token_path)
        return 4
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        _hint_reauth(token_path)
        return 4

    items = resp.get("items") or []
    print(f"CHANNEL COUNT: {len(items)}")
    if not items:
        print("No channel returned for this account (unexpected for mine=True).")
        return 0

    for item in items:
        cid = item.get("id") or ""
        sn = item.get("snippet") or {}
        st = item.get("statistics") or {}
        title = sn.get("title") or ""
        custom = sn.get("customUrl") or ""
        subs = st.get("subscriberCount")
        if subs is None:
            subs = "hidden"
        print(f"CHANNEL_ID: {cid}")
        print(f"TITLE: {title}")
        print(f"CUSTOM_URL: {custom}")
        print(f"SUBSCRIBERS: {subs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
