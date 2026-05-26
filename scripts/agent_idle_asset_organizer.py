#!/usr/bin/env python3
"""Idle-time safe asset organization (indexes, drafts, no raw delete)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL = Path.home() / "StateVerge_Control_Center"
SV_TRANSFER = Path("/Volumes/SV_TRANSFER")
SV_CACHE = Path("/Volumes/SV_CACHE")
OUT_JSON_CACHE = SV_CACHE / "logs" / "idle_asset_organizer_latest.json"
OUT_MD_CC = CONTROL / "logs" / "idle_asset_organizer_latest.md"


def _venv_py() -> str:
    p = STATEVERGE / ".venv_audio" / "bin" / "python3"
    return str(p) if p.is_file() else sys.executable


def _disk_free_pct(path: Path) -> float:
    try:
        import shutil

        u = shutil.disk_usage(path)
        return 100.0 * float(u.free) / float(u.total or 1)
    except OSError:
        return 0.0


def _cpu_busy() -> bool:
    try:
        import psutil  # type: ignore

        return float(psutil.cpu_percent(interval=0.25)) >= 70.0
    except Exception:
        try:
            load1, _, _ = os.getloadavg()
            return load1 >= float(os.cpu_count() or 4) * 0.7
        except OSError:
            return False


def _proc(pattern: str) -> int:
    try:
        r = subprocess.run(
            ["/bin/ps", "-ax", "-o", "command="],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        n = 0
        for line in (r.stdout or "").splitlines():
            if pattern in line and "grep" not in line:
                n += 1
        return n
    except (OSError, subprocess.TimeoutExpired):
        return 0


def idle_ok(*, max_runtime_minutes: int, warnings: list[str]) -> bool:
    if _proc("youtube_upload.py") > 0:
        warnings.append("skip_active_youtube_upload_process")
        return False
    if _proc("ffmpeg") > 3:
        warnings.append("skip_many_ffmpeg")
        return False
    if _cpu_busy():
        warnings.append("skip_cpu_high")
        return False
    for vol, name in ((SV_TRANSFER, "SV_TRANSFER"), (SV_CACHE, "SV_CACHE")):
        if not vol.exists():
            warnings.append(f"missing_volume:{name}")
            return False
        if _disk_free_pct(vol) < 10.0:
            warnings.append(f"skip_disk_low:{name}")
            return False
        if not os.access(vol, os.W_OK):
            warnings.append(f"skip_volume_not_writable:{name}")
            return False
    safe_flag = CONTROL / "logs" / "STATEVERGE_SAFE_MODE.flag"
    if safe_flag.is_file():
        warnings.append("skip_safe_mode_flag")
        return False
    _ = max_runtime_minutes
    return True


def cmd_diagnose() -> dict[str, Any]:
    w: list[str] = []
    ok = idle_ok(max_runtime_minutes=60, warnings=w)
    return {"idle_ok": ok, "warnings": w, "sv_transfer": SV_TRANSFER.is_dir(), "sv_cache": SV_CACHE.is_dir()}


def cmd_run_once(args: argparse.Namespace) -> dict[str, Any]:
    warnings: list[str] = []
    t0 = time.monotonic()
    if args.safe_only and not idle_ok(max_runtime_minutes=args.max_runtime_minutes, warnings=warnings):
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "skipped": True,
            "warnings": warnings,
            "files_touched": 0,
        }
        _write_outputs(payload)
        return payload

    if args.delete_allowed or args.destructive_move_allowed:
        warnings.append("unsafe_flags_ignored_delete_and_destructive_move_still_disabled")
    touched = 0
    index_script = STATEVERGE / "scripts" / "index_media_library.py"
    if index_script.is_file() and args.allow_media_index:
        r = subprocess.run(
            [_venv_py(), str(index_script)],
            cwd=str(STATEVERGE),
            capture_output=True,
            text=True,
            timeout=max(60, args.max_runtime_minutes * 40),
            check=False,
        )
        touched += 1
        if r.returncode != 0:
            warnings.append(f"index_media_library_rc_{r.returncode}")

    media_root = SV_TRANSFER / "media_index"
    try:
        media_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "note": "idle_asset_organizer manifest only; originals untouched",
            "safe_only": bool(args.safe_only),
        }
        (media_root / "idle_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        touched += 1
    except OSError as exc:
        warnings.append(f"manifest_write:{exc!r}")

    review_gallery = SV_TRANSFER / "asset_review" / "idle_gallery_stub.json"
    if args.allow_classify:
        try:
            review_gallery.parent.mkdir(parents=True, exist_ok=True)
            review_gallery.write_text(json.dumps({"items": [], "source": "idle_organizer"}, indent=2), encoding="utf-8")
            touched += 1
        except OSError as exc:
            warnings.append(f"gallery_stub:{exc!r}")

    elapsed = round(time.monotonic() - t0, 2)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "skipped": False,
        "warnings": warnings,
        "files_touched": touched,
        "elapsed_sec": elapsed,
        "max_runtime_minutes": args.max_runtime_minutes,
    }
    _write_outputs(payload)
    return payload


def _write_outputs(payload: dict[str, Any]) -> None:
    try:
        OUT_JSON_CACHE.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON_CACHE.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        alt = CONTROL / "logs" / "idle_asset_organizer_latest.json"
        alt.parent.mkdir(parents=True, exist_ok=True)
        alt.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    md = CONTROL / "logs" / "idle_asset_organizer_latest.md"
    try:
        md.parent.mkdir(parents=True, exist_ok=True)
        md.write_text(
            "\n".join(
                [
                    "# Idle asset organizer",
                    "",
                    f"- generated_at: `{payload.get('generated_at')}`",
                    f"- skipped: {payload.get('skipped')}",
                    f"- files_touched: {payload.get('files_touched')}",
                    "",
                    "## warnings",
                    "",
                    "```json",
                    json.dumps(payload.get("warnings") or [], indent=2),
                    "```",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def cmd_report() -> dict[str, Any]:
    p = OUT_JSON_CACHE if OUT_JSON_CACHE.is_file() else CONTROL / "logs" / "idle_asset_organizer_latest.json"
    if not p.is_file():
        return {"latest": None}
    try:
        return {"latest": json.loads(p.read_text(encoding="utf-8"))}
    except (OSError, json.JSONDecodeError):
        return {"latest": None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("run-once", "diagnose", "report"), default="run-once")
    ap.add_argument("--max-runtime-minutes", type=int, default=60)
    ap.add_argument("--safe-only", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument(
        "--delete-allowed",
        action="store_true",
        help="DANGER: allow delete operations (default: never delete).",
    )
    ap.add_argument(
        "--destructive-move-allowed",
        action="store_true",
        help="DANGER: allow destructive moves of originals (default: off).",
    )
    ap.add_argument("--allow-symlink", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-copy-index", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-thumbnail", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-media-index", action=argparse.BooleanOptionalAction, default=True)
    ap.add_argument("--allow-classify", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()
    if args.mode == "diagnose":
        out = cmd_diagnose()
        print(json.dumps(out, indent=2))
        return 0
    if args.mode == "report":
        print(json.dumps(cmd_report(), indent=2))
        return 0
    cmd_run_once(args)
    print(json.dumps({"ok": True}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
