#!/usr/bin/env python3
"""Quarantine ready_to_upload videos that fail ffprobe (no video / ffprobe error). Never deletes; default dry-run."""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
DEFAULT_DIAGNOSE_JSON = CONTROL_LOGS / "ready_pool_streams_diagnose.json"

try:
    from utils.storage_paths import get_sv_transfer, get_transfer_ready_to_upload  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")

    def get_transfer_ready_to_upload(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER/ready_to_upload")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _under_ready_upload(p: Path, ready_upload: Path) -> bool:
    try:
        rp = p.resolve()
        ru = ready_upload.resolve()
    except OSError:
        return False
    s = str(rp).replace("\\", "/")
    low = s.lower()
    if "/00_inbox/iphone" in low:
        return False
    if "/sv_cache/inbox" in low:
        return False
    for parent in rp.parents:
        if parent == ru:
            return True
    return rp == ru


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--diagnose-json",
        type=Path,
        default=DEFAULT_DIAGNOSE_JSON,
        help="Output from diagnose_ready_pool_streams.py",
    )
    ap.add_argument("--dry-run", action="store_true", help="Explicit dry-run (default when --apply is omitted).")
    ap.add_argument("--apply", action="store_true", help="Perform moves (requires explicit opt-in).")
    args = ap.parse_args()
    dry_run = not bool(args.apply)

    xfer = get_sv_transfer(verbose=False)
    ready = get_transfer_ready_to_upload(verbose=False)
    ready_upload = xfer / "ready_to_upload"
    dest_root = ready_upload / "_invalid_probe"

    if not args.diagnose_json.is_file():
        print(json.dumps({"ok": False, "error": "diagnose_json_missing", "path": str(args.diagnose_json)}))
        return 2

    try:
        data = json.loads(args.diagnose_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": f"read_json_failed:{exc!r}"}))
        return 3

    files = data.get("files")
    if not isinstance(files, list):
        print(json.dumps({"ok": False, "error": "bad_diagnose_json_no_files_array"}))
        return 4

    ts = _utc_stamp()
    quarantine_dir = dest_root / ts
    manifest: dict[str, Any] = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "quarantine_dir": str(quarantine_dir),
        "source_diagnose_json": str(args.diagnose_json),
        "planned_moves": [],
        "skipped": [],
    }
    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for row in files:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("rejected_reason") or "")
        if reason not in ("no_video_stream", "ffprobe_failed"):
            continue
        src = Path(str(row.get("path") or ""))
        if not src.is_file():
            skipped.append({"path": str(src), "reason": "not_a_file"})
            continue
        if not _under_ready_upload(src, ready_upload):
            skipped.append({"path": str(src), "reason": "not_under_sv_transfer_ready_to_upload"})
            continue
        low = str(src).replace("\\", "/").lower()
        if "/00_inbox/iphone" in low or "/sv_cache/inbox" in low:
            skipped.append({"path": str(src), "reason": "inbox_excluded"})
            continue
        try:
            ru_res = ready_upload.resolve()
        except OSError:
            ru_res = ready_upload
        rel = src.relative_to(ru_res)
        planned.append(
            {
                "src": str(src),
                "dst": str(quarantine_dir / rel),
                "rejected_reason": reason,
                "size_bytes": row.get("size_bytes"),
            }
        )

    manifest["planned_moves"] = planned
    manifest["skipped"] = skipped

    if dry_run:
        print(
            json.dumps(
                {"ok": True, "dry_run": True, "would_move_count": len(planned), "manifest_preview": manifest},
                indent=2,
            )
        )
        return 0

    if not xfer.is_dir() or not ready_upload.is_dir():
        print(json.dumps({"ok": False, "error": "sv_transfer_or_ready_to_upload_not_mounted"}))
        return 5

    quarantine_dir.mkdir(parents=True, exist_ok=True)
    done: list[dict[str, Any]] = []
    for item in planned:
        src = Path(item["src"])
        dst = Path(item["dst"])
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            done.append({"src": str(src), "dst": str(dst)})
        except OSError as exc:
            skipped.append({"path": str(src), "reason": f"move_failed:{exc!r}"})

    manifest["completed_moves"] = done
    manifest["skipped_after_apply"] = skipped
    man_path = quarantine_dir / "quarantine_manifest.json"
    man_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "dry_run": False, "moved": len(done), "manifest": str(man_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
