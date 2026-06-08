#!/usr/bin/env python3
"""Scheduled Shorts queue runner (Pro-only).

Default is **dry-run** (encode only, no YouTube). Pass ``--upload`` for real Shorts upload
(``token_shorts.json`` + channel confirm in worker). Optional ``--privacy-status`` is forwarded
to the worker (default when uploading: ``unlisted``).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_NYC_AUTO = Path(__file__).resolve().parent
if str(_NYC_AUTO) not in sys.path:
    sys.path.insert(0, str(_NYC_AUTO))

from channel_guard import validate_shorts_channel_token  # noqa: E402

try:
    from utils.shorts_paths import ensure_shorts_dirs, token_shorts_path  # noqa: E402
except Exception:

    def ensure_shorts_dirs(*, verbose: bool = False) -> dict[str, Path]:  # type: ignore[misc]
        return {}

    def token_shorts_path() -> Path:  # type: ignore[misc]
        return Path.home() / "StateVerge/data/youtube/token_shorts.json"


_WORKER = Path(__file__).resolve().parent.parent / "jobs" / "shorts_cut_upload_job.py"


def main() -> int:
    ap = argparse.ArgumentParser(description="Run one Shorts cut/upload job from the compliant pool.")
    ap.add_argument("--dry-run", action="store_true", help="Do not upload to YouTube (default if --upload omitted).")
    ap.add_argument("--upload", action="store_true", help="Real YouTube upload after encode (token_shorts + Shorts channel guard).")
    ap.add_argument(
        "--privacy-status",
        choices=("private", "unlisted"),
        default="unlisted",
        help="YouTube privacy when --upload (default: unlisted; auto-publish never uses public).",
    )
    ap.add_argument(
        "--no-real-sound-gate",
        action="store_true",
        help="Forward to worker: skip Real Sound Cleanup Gate before Shorts upload.",
    )
    ap.add_argument(
        "--use-ai-metadata",
        action="store_true",
        help="Forward to worker: optional OpenAI metadata refine (fail-open).",
    )
    ap.add_argument(
        "--agent-private-upload",
        action="store_true",
        help="Private-only agent upload path; forwarded to worker.",
    )
    ap.add_argument(
        "--review-queue",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Write review queue when using agent-private-upload (default: on).",
    )
    ap.add_argument("--no-public", action="store_true", help="Reject public privacy upstream.")
    ap.add_argument("--duration-seconds", type=float, default=30.0)
    ap.add_argument(
        "--asset-mode",
        choices=("auto", "video_only", "image_only"),
        default="video_only",
        help="Default video_only from STATEVERGE_SHORTS_MATERIALS_DIR (portrait vertical).",
    )
    ap.add_argument(
        "--audio-mode",
        choices=("envato_music", "original", "source_audio", "real_sound", "music", "silent"),
        default="original",
        help="Default: original/source audio (no BGM). Use envato_music/suno_music only when explicitly set.",
    )
    ap.add_argument(
        "--music-category",
        default="rnb",
        help="Default: Suno RNB at /Volumes/SV_TRANSFER/04_AUDIO/music/stateverge_suno/rnb",
    )
    ap.add_argument("--music-volume", type=float, default=0.30)
    ap.add_argument("--original-volume", type=float, default=1.0)
    ap.add_argument("--job-id", default="", help="Optional fixed job id (default: random).")
    ap.add_argument(
        "--max-count",
        type=int,
        default=1,
        help="Parity with long queue CLI; Shorts runner always processes one job per invocation (value ignored).",
    )
    args = ap.parse_args()
    agent_pu = bool(getattr(args, "agent_private_upload", False))

    if agent_pu:
        if args.privacy_status != "private":
            print("ERROR: --agent-private-upload requires --privacy-status private", file=sys.stderr)
            return 3

    ensure_shorts_dirs(verbose=False)

    if args.upload:
        try:
            from always_publish.queue_integration import (  # noqa: WPS433
                blocked_payload,
                preflight_shorts_upload,
                print_blocked,
            )

            pre = preflight_shorts_upload(force=False)
            if not pre.get("allowed"):
                print_blocked(blocked_payload("shorts", pre))
                return 0
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"always_deliver_preflight_failed": repr(exc)}, ensure_ascii=False), flush=True)

    try:
        from always_publish.queue_integration import run_shorts_upload_from_delivery_queue  # noqa: WPS433

        dq_exit = run_shorts_upload_from_delivery_queue(args)
        if dq_exit is not None:
            return int(dq_exit)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"delivery_queue_shorts_path_failed": repr(exc)}, ensure_ascii=False), flush=True)

    if args.upload:
        ok_tok, tok_reason = validate_shorts_channel_token(token_shorts_path())
        if not ok_tok:
            print(
                json.dumps(
                    {
                        "status": "blocked",
                        "block_reason": "channel_guard_failed",
                        "channel_guard_detail": tok_reason,
                        "video_type": "short",
                        "channel": "SHORTS",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 7
        try:
            from doctor_gate_client import (  # type: ignore
                check_real_upload_allowed_by_doctor,
                log_doctor_gate_block_upload,
            )

            allowed, detail, gw = check_real_upload_allowed_by_doctor(dry_run=False)
            if gw:
                print(json.dumps({"doctor_gate_warnings": gw}, ensure_ascii=False), flush=True)
            if not allowed:
                log_doctor_gate_block_upload()
                print(
                    json.dumps(
                        {
                            "status": "blocked",
                            "blocked_by_doctor_gate": True,
                            "block_reason": "doctor_gate_block_upload",
                            "doctor_gate_detail": detail,
                            "selected_file_check": {"status": "deferred_to_worker_selection"},
                            "video_type": "short",
                            "channel": "SHORTS",
                        },
                        indent=2,
                        ensure_ascii=False,
                    )
                )
                return 8
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"doctor_gate_check_failed": repr(exc)}, ensure_ascii=False), flush=True)

    if not _WORKER.is_file():
        print(f"ERROR: worker missing: {_WORKER}", file=sys.stderr)
        return 2

    # DaVinci YouTube audio finish: worker prefers finished_for_youtube via davinci_audio_finish.upload_path.
    try:
        daf_dir = _SCRIPTS / "davinci_audio_finish"
        if daf_dir.is_dir() and str(daf_dir) not in sys.path:
            sys.path.insert(0, str(daf_dir))
        from config import load_config as _daf_load_config  # noqa: WPS433

        daf_cfg = _daf_load_config()
        if daf_cfg.get("enable_davinci_audio_finish") or daf_cfg.get("require_davinci_audio_finish"):
            print(
                json.dumps(
                    {"davinci_audio_finish_config": daf_cfg},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except Exception:
        pass

    job_id = (args.job_id or "").strip() or uuid.uuid4().hex[:16]
    py = sys.executable or "python3"

    cmd: list[str] = [
        py,
        str(_WORKER),
        "--job-id",
        job_id,
        "--duration-seconds",
        str(float(args.duration_seconds)),
        "--audio-mode",
        str(args.audio_mode),
        "--music-category",
        str(args.music_category),
        "--music-volume",
        str(float(args.music_volume)),
        "--original-volume",
        str(float(args.original_volume)),
        "--asset-mode",
        str(args.asset_mode),
    ]
    # Default policy: unless --upload, behave like API dry-run (no YouTube).
    if args.upload:
        cmd.append("--upload")
        cmd.extend(["--privacy-status", str(args.privacy_status)])
        cmd.append("--no-public")
    else:
        cmd.append("--dry-run")
        if agent_pu:
            cmd.extend(["--privacy-status", str(args.privacy_status)])
    if args.no_real_sound_gate:
        cmd.append("--no-real-sound-gate")
    if args.use_ai_metadata:
        cmd.append("--use-ai-metadata")
    if agent_pu:
        cmd.append("--agent-private-upload")
        if args.review_queue:
            cmd.append("--review-queue")
        else:
            cmd.append("--no-review-queue")
    if getattr(args, "no_public", False):
        cmd.append("--no-public")

    try:
        r = subprocess.run(cmd, check=False, capture_output=True, text=True)
        if r.stdout:
            print(r.stdout, end="" if r.stdout.endswith("\n") else "\n")
        if r.stderr:
            print(r.stderr, end="" if r.stderr.endswith("\n") else "\n", file=sys.stderr)
        if args.upload:
            try:
                from always_publish.queue_integration import record_queue_outcome  # noqa: WPS433

                payload: dict[str, Any] = {"status": "uploaded" if r.returncode == 0 else "upload_failed"}
                if r.stdout.strip():
                    try:
                        payload = json.loads(r.stdout)
                    except json.JSONDecodeError:
                        payload["stdout_tail"] = r.stdout[-500:]
                record_queue_outcome(kind="shorts", payload=payload)
            except Exception:
                pass
        return int(r.returncode)
    except OSError as exc:
        print(f"ERROR: failed to spawn worker: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
