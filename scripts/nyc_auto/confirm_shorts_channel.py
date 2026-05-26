#!/usr/bin/env python3
"""Verify OAuth token is authorized for the Shorts channel (Real NYC Shorts).

Uses **only** the provided token path (expected: data/youtube/token_shorts.json).
Never falls back to the long-form channel token.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from pathlib import Path
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from nyc_common import CODE_ROOT  # noqa: E402
from youtube_upload import _load_credentials  # noqa: E402

DEFAULT_TOKEN_SHORTS = CODE_ROOT / "data" / "youtube" / "token_shorts.json"
DEFAULT_SECRETS = CODE_ROOT / ".secrets" / "youtube" / "client_secrets.json"

# Optional exact channel id override (set in environment if Google changes handle).
ENV_SHORTS_CHANNEL_ID = "STATEVERGE_SHORTS_CHANNEL_ID"


@dataclass
class ShortsChannelConfirm:
    ok: bool
    youtube_channel_title: str = ""
    youtube_channel_id: str = ""
    confirm_shorts_channel_ok: bool = False
    block_reason: str = ""
    detail: str = ""


def _channel_title_is_shorts(title: str) -> bool:
    t = (title or "").strip().lower()
    if not t:
        return False
    if "shorts" in t:
        return True
    # Exact known branding (case-insensitive).
    if t == "real nyc shorts":
        return True
    return False


def confirm_shorts_channel(
    *,
    token_path: Path,
    client_secrets: Path,
    expected_channel_id: str | None = None,
) -> ShortsChannelConfirm:
    token_path = token_path.expanduser().resolve()
    client_secrets = client_secrets.expanduser().resolve()
    if not token_path.is_file():
        return ShortsChannelConfirm(
            ok=False,
            block_reason="missing_token_shorts",
            detail=str(token_path),
        )
    exp_id = (expected_channel_id or os.environ.get(ENV_SHORTS_CHANNEL_ID) or "").strip()
    try:
        creds = _load_credentials(token_path, client_secrets)
    except FileNotFoundError as exc:
        return ShortsChannelConfirm(
            ok=False,
            block_reason="missing_client_secrets_or_token",
            detail=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        return ShortsChannelConfirm(
            ok=False,
            block_reason="shorts_oauth_invalid",
            detail=str(exc)[:500],
        )

    try:
        from googleapiclient.discovery import build
    except ImportError:
        return ShortsChannelConfirm(
            ok=False,
            block_reason="missing_google_api_libs",
            detail="pip install -r requirements-youtube.txt",
        )

    title = ""
    cid = ""
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items") or []
        if not items:
            return ShortsChannelConfirm(
                ok=False,
                youtube_channel_title="",
                youtube_channel_id="",
                confirm_shorts_channel_ok=False,
                block_reason="shorts_channel_confirmation_failed",
                detail="channels.list returned no items",
            )
        snip = (items[0].get("snippet") or {}) if isinstance(items[0], dict) else {}
        title = str(snip.get("title") or "")
        cid = str(items[0].get("id") or "")
    except Exception as exc:  # noqa: BLE001
        return ShortsChannelConfirm(
            ok=False,
            block_reason="shorts_channel_confirmation_failed",
            detail=str(exc)[:500],
        )

    id_ok = bool(exp_id and cid == exp_id)
    title_ok = _channel_title_is_shorts(title)
    ok = id_ok or title_ok
    return ShortsChannelConfirm(
        ok=ok,
        youtube_channel_title=title,
        youtube_channel_id=cid,
        confirm_shorts_channel_ok=ok,
        block_reason="" if ok else "shorts_channel_confirmation_failed",
        detail="" if ok else (f"title={title!r} id={cid!r} expected_id={exp_id!r}" if exp_id else f"title={title!r} id={cid!r}"),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="Confirm OAuth channel is Shorts (not long-form).")
    ap.add_argument("--token", type=Path, default=DEFAULT_TOKEN_SHORTS)
    ap.add_argument("--client-secrets", type=Path, default=DEFAULT_SECRETS)
    ap.add_argument("--expected-channel-id", default=None)
    args = ap.parse_args()
    res = confirm_shorts_channel(
        token_path=args.token,
        client_secrets=args.client_secrets,
        expected_channel_id=args.expected_channel_id,
    )
    print(
        "confirm_shorts_channel_ok=%s title=%r id=%r block_reason=%r detail=%r"
        % (
            res.confirm_shorts_channel_ok,
            res.youtube_channel_title,
            res.youtube_channel_id,
            res.block_reason,
            res.detail,
        )
    )
    return 0 if res.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
