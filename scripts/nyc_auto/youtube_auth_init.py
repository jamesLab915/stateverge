#!/usr/bin/env python3
"""One-time YouTube OAuth setup: save refresh token (default: long-form ``data/youtube/token.json``).

For **Real NYC Shorts**, pass ``--token ~/StateVerge/data/youtube/token_shorts.json`` so uploads never share the long channel OAuth file.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

_NYC_AUTO = Path(__file__).resolve().parent
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

from stateverge_paths import CODE_ROOT  # noqa: E402
from youtube_scopes import SCOPES  # noqa: E402

DEFAULT_SECRETS = CODE_ROOT / ".secrets" / "youtube" / "client_secrets.json"
DEFAULT_TOKEN = CODE_ROOT / "data" / "youtube" / "token.json"
CONTROL_CENTER_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"


def _write_auth_reports(
    *,
    token_path: Path,
    client_secrets: Path,
    credentials_valid: bool,
    channel_title: str,
    channel_id: str,
    scopes: list[str],
    status: str,
    error: str = "",
) -> None:
    CONTROL_CENTER_LOGS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "timestamp": ts,
        "status": status,
        "token_path": str(token_path),
        "client_secrets_path": str(client_secrets),
        "credentials_valid": credentials_valid,
        "channel_title": channel_title,
        "channel_id": channel_id,
        "scopes": scopes,
        "error": error,
    }
    jpath = CONTROL_CENTER_LOGS / "youtube_auth_status_long_v1.json"
    mpath = CONTROL_CENTER_LOGS / "youtube_auth_status_long_v1.md"
    try:
        jpath.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = [
            "# YouTube long-channel auth status (v1)",
            "",
            f"- **timestamp**: {ts}",
            f"- **status**: {status}",
            f"- **token_path**: `{token_path}`",
            f"- **client_secrets_path**: `{client_secrets}`",
            f"- **credentials_valid**: {credentials_valid}",
            f"- **channel_title**: {channel_title or '(none)'}",
            f"- **channel_id**: {channel_id or '(none)'}",
            f"- **scopes**: `{', '.join(scopes)}`",
        ]
        if error:
            lines.append(f"- **error**: {error}")
        mpath.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"WARN: could not write auth report: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Initialize YouTube OAuth token (scopes: youtube.upload + youtube.readonly)."
    )
    parser.add_argument(
        "--client-secrets",
        type=Path,
        default=DEFAULT_SECRETS,
        help="OAuth client JSON path (default: .secrets/youtube/client_secrets.json)",
    )
    parser.add_argument(
        "--token",
        type=Path,
        default=DEFAULT_TOKEN,
        help="Where to save authorized user token (Shorts: data/youtube/token_shorts.json)",
    )
    args = parser.parse_args()

    secrets = args.client_secrets.expanduser().resolve()
    token_out = args.token.expanduser().resolve()

    if not secrets.is_file():
        print(
            "ERROR: client secrets not found:\n"
            f"  {secrets}\n\n"
            "Create an OAuth Client (Desktop app) in Google Cloud Console, enable YouTube Data API v3,\n"
            "download the JSON, and save it as:\n"
            f"  {DEFAULT_SECRETS}\n"
            "(see .secrets/youtube/client_secrets.example.json for shape.)",
            file=sys.stderr,
        )
        _write_auth_reports(
            token_path=token_out,
            client_secrets=secrets,
            credentials_valid=False,
            channel_title="",
            channel_id="",
            scopes=list(SCOPES),
            status="client_secrets_missing",
            error="client_secrets_not_found",
        )
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print(
            "ERROR: missing dependencies. Install with:\n"
            "  python3 -m pip install -r requirements-youtube.txt",
            file=sys.stderr,
        )
        return 1

    token_out.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent")
    token_out.write_text(creds.to_json(), encoding="utf-8")
    print(f"OK: token saved to {token_out}")

    ch_title = ""
    ch_id = ""
    creds_valid = False
    err_msg = ""
    status = "post_check_failed"

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        loaded = Credentials.from_authorized_user_file(str(token_out), SCOPES)
        if loaded.expired and loaded.refresh_token:
            loaded.refresh(Request())
        creds_valid = bool(loaded.valid)
        if creds_valid:
            print("OK: credentials loaded and validate successfully")
            youtube = build("youtube", "v3", credentials=loaded, cache_discovery=False)
            resp = youtube.channels().list(part="snippet", mine=True).execute()
            items = resp.get("items") or []
            if items:
                it0 = items[0]
                ch_id = str(it0.get("id") or "")
                sn = it0.get("snippet") or {}
                ch_title = str(sn.get("title") or "")
                print(f"OK: channel_title={ch_title}")
                print(f"OK: channel_id={ch_id}")
                status = "ok"
            else:
                status = "no_channel_items"
                err_msg = "channels.list returned no items"
                print(f"WARN: {err_msg}")
        else:
            print("WARN: credentials saved but not marked valid; try upload dry-run next.")
            status = "credentials_invalid"
    except Exception as exc:  # noqa: BLE001
        err_msg = repr(exc)
        print(f"WARN: post-check failed (token file still saved): {exc}")
        status = "post_check_exception"

    _write_auth_reports(
        token_path=token_out,
        client_secrets=secrets,
        credentials_valid=creds_valid,
        channel_title=ch_title,
        channel_id=ch_id,
        scopes=list(SCOPES),
        status=status,
        error=err_msg,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
