#!/usr/bin/env python3
"""Cut + upload one Short to **StateVerge NYC** (long OAuth) from ``SV_CACHE/air`` portrait pool.

Schedule via ``com.stateverge.nyc_long_shorts.autopublish`` (18:00 / 21:00 / 00:00 / 02:00 local).
Does not use ``token_shorts.json`` or Real NYC Shorts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_NYC_AUTO = Path(__file__).resolve().parent
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

from channel_guard import validate_long_channel_token  # noqa: E402

try:
    from utils.shorts_paths import ensure_shorts_dirs, shorts_materials_dir, long_channel_token_path  # noqa: E402
except Exception:

    def ensure_shorts_dirs(*, verbose: bool = False) -> dict[str, Path]:  # type: ignore[misc]
        return {}

    def shorts_materials_dir(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_CACHE/air")

    def long_channel_token_path() -> Path:  # type: ignore[misc]
        return Path.home() / "StateVerge/data/youtube/token.json"


_WORKER = _SCRIPTS / "jobs" / "shorts_cut_upload_job.py"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--upload", action="store_true", help="Encode + upload (default without: dry-run encode only).")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--privacy-status", choices=("private", "unlisted"), default="unlisted")
    ap.add_argument("--duration-seconds", type=float, default=30.0)
    ap.add_argument("--asset-mode", choices=("auto", "video_only", "image_only"), default="video_only")
    ap.add_argument("--audio-mode", default="original", help="Default: keep source audio (no Suno/Envato BGM).")
    ap.add_argument("--music-category", default="rnb", help="Default: stateverge_suno/rnb on SV_TRANSFER.")
    ap.add_argument("--job-id", default="")
    args = ap.parse_args()

    ensure_shorts_dirs(verbose=False)
    mat = shorts_materials_dir(verbose=False)
    tok = long_channel_token_path()
    ok_tok, tok_reason = validate_long_channel_token(tok)
    if not ok_tok:
        print(
            json.dumps(
                {
                    "status": "blocked",
                    "block_reason": "channel_guard_failed",
                    "channel_guard_detail": tok_reason,
                    "channel": "NYC_LONG",
                },
                indent=2,
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 7

    print("=== auto_publish_queue_nyc_long_shorts ===")
    print(f"materials_dir={mat}")
    print(f"token_path={tok}")
    print(f"upload={bool(args.upload)} dry_run={bool(args.dry_run)} privacy={args.privacy_status}")

    if not _WORKER.is_file():
        print(f"ERROR: worker missing: {_WORKER}", file=sys.stderr)
        return 2

    job_id = (args.job_id or "").strip() or uuid.uuid4().hex[:16]
    py = sys.executable or "python3"
    cmd: list[str] = [
        py,
        str(_WORKER),
        "--job-id",
        job_id,
        "--upload-channel",
        "nyc_long",
        "--duration-seconds",
        str(float(args.duration_seconds)),
        "--audio-mode",
        str(args.audio_mode),
        "--music-category",
        str(args.music_category),
        "--asset-mode",
        str(args.asset_mode),
    ]
    if args.upload:
        cmd.append("--upload")
        cmd.extend(["--privacy-status", str(args.privacy_status)])
    else:
        cmd.append("--dry-run")

    print(json.dumps({"worker_cmd": cmd}, ensure_ascii=False))
    r = subprocess.run(cmd, cwd=str(_SCRIPTS.parent))
    return int(r.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
