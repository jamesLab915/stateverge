#!/usr/bin/env python3
"""Set YouTube channel branding watermark on the official NYC Long channel (API, not burned into video)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_NYC = Path(__file__).resolve().parent
for p in (_SCRIPTS, _NYC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from channel_guard import validate_long_channel_token  # noqa: E402
from youtube_scopes import SCOPES  # noqa: E402
from youtube_token_paths import (  # noqa: E402
    OFFICIAL_LONG_TOKEN_PATH,
    OFFICIAL_SHORTS_TOKEN_PATH,
    resolve_client_secrets,
    resolve_long_form_upload_token,
)
from youtube_upload import _load_credentials  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent.parent
_BRAND_DIR = _REPO / "assets" / "branding"
_SV_ROOT = _BRAND_DIR / "stateverge_watermark_sv"
_DEFAULT_SQUARE = _SV_ROOT / "transparent_png" / "stateverge_channel_watermark_800.png"
_FALLBACK_SQUARE = _SV_ROOT / "full_color" / "stateverge_channel_watermark_800.png"
_LEGACY_SQUARE = _BRAND_DIR / "stateverge_ambient_cinema_channel_watermark_800.png"
_WIDE_WM = _SV_ROOT / "transparent_png" / "sv_watermark_transparent.png"
_LEGACY_WIDE = _BRAND_DIR / "stateverge_ambient_cinema_watermark_320x80.png"


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_square_watermark_png(dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_file():
        return dst
    for candidate in (_DEFAULT_SQUARE, _FALLBACK_SQUARE, _LEGACY_SQUARE):
        if candidate.is_file():
            return candidate
    src = _WIDE_WM if _WIDE_WM.is_file() else _LEGACY_WIDE
    if not src.is_file():
        gen = _REPO / "scripts" / "generate_stateverge_watermark_sv_v1.py"
        if gen.is_file():
            subprocess.check_call([sys.executable, str(gen)])
            if _DEFAULT_SQUARE.is_file():
                return _DEFAULT_SQUARE
        raise FileNotFoundError(f"no watermark source image: {src}")
    vf = "scale=520:-1,pad=800:800:(ow-iw)/2:(oh-ih)/2:color=black@0.0,format=rgba"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vf",
        vf,
        "-frames:v",
        "1",
        "-update",
        "1",
        str(dst),
    ]
    subprocess.check_call(cmd)
    return dst


def _watermark_metadata(*, position: str, channel_id: str) -> dict:
    corner = {
        "top_left": "topLeft",
        "top_right": "topRight",
        "bottom_left": "bottomLeft",
        "bottom_right": "bottomRight",
    }.get(position.strip().lower(), "topLeft")
    # Entire video: from start, long duration (Studio: "Entire video").
    return {
        "position": {"type": "corner", "cornerPosition": corner},
        "timing": {
            "type": "offsetFromStart",
            "offsetMs": 0,
            "durationMs": 24 * 60 * 60 * 1000,
        },
        "targetChannelId": channel_id,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", type=Path, default=_DEFAULT_SQUARE, help="Square PNG/JPEG (>=150x150)")
    ap.add_argument(
        "--position",
        choices=("top_left", "top_right", "bottom_left", "bottom_right"),
        default="top_left",
        help="Default top_left. Channel image: 800×800 hollow PNG, red S centered.",
    )
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    token_path, tw = resolve_long_form_upload_token(None)
    for w in tw:
        print(f"WARNING:{w}", file=sys.stderr)
    try:
        if token_path.resolve() == OFFICIAL_SHORTS_TOKEN_PATH.resolve():
            print("ERROR: must use long token.json, not token_shorts.json", file=sys.stderr)
            return 4
    except OSError:
        pass

    ok, reason = validate_long_channel_token(token_path)
    if not ok:
        print(f"ERROR:channel_guard:{reason}", file=sys.stderr)
        return 4

    try:
        from googleapiclient.discovery import build
        from googleapiclient.errors import HttpError
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        print("ERROR: pip install -r requirements-youtube.txt", file=sys.stderr)
        return 1

    sec = resolve_client_secrets(None)
    creds = _load_credentials(token_path, sec)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    ch = youtube.channels().list(part="snippet", mine=True).execute()
    items = ch.get("items") or []
    if not items:
        print("ERROR:no_channel_for_token", file=sys.stderr)
        return 1
    channel_id = str(items[0].get("id") or "")
    title = str((items[0].get("snippet") or {}).get("title") or "")

    img = _ensure_square_watermark_png(args.image.expanduser().resolve())
    meta = _watermark_metadata(position=args.position, channel_id=channel_id)

    report = {
        "updated_at": _utc(),
        "channel_id": channel_id,
        "channel_title": title,
        "token_path": str(token_path),
        "image_path": str(img),
        "watermark_metadata": meta,
        "position": args.position,
        "dry_run": bool(args.dry_run),
    }

    print(f"CHANNEL_ID={channel_id}")
    print(f"CHANNEL_TITLE={title}")
    print(f"WATERMARK_IMAGE={img}")
    print(f"WATERMARK_POSITION={args.position}")

    if args.dry_run:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("WATERMARK_DRY_RUN=true")
        return 0

    try:
        # Clear stale watermark first (helps Studio refresh).
        try:
            youtube.watermarks().unset(channelId=channel_id).execute()
        except HttpError as unset_exc:
            if int(getattr(unset_exc.resp, "status", 0) or 0) not in (204, 404):
                print(f"WARNING:watermark_unset:{unset_exc}", file=sys.stderr)

        media = MediaFileUpload(str(img), mimetype="image/png", resumable=True)
        youtube.watermarks().set(channelId=channel_id, media_body=media, body=meta).execute()
    except HttpError as exc:
        # watermarks.set returns HTTP 204 No Content on success; client may surface it as HttpError.
        if int(getattr(exc.resp, "status", 0) or 0) == 204:
            pass
        else:
            print(f"ERROR:youtube_api:{exc}", file=sys.stderr)
            try:
                body = json.loads(exc.content.decode("utf-8", errors="replace"))
                print(json.dumps(body, indent=2, ensure_ascii=False), file=sys.stderr)
            except Exception:  # noqa: BLE001
                pass
            return 1

    report["status"] = "ok"
    out_dir = Path.home() / "StateVerge_Control_Center" / "logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "youtube_long_channel_watermark_sv_v1.json"
    out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("YOUTUBE_CHANNEL_WATERMARK_SET=true")
    print(f"REPORT_PATH={out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
