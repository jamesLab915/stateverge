#!/usr/bin/env python3
"""One-shot: long autopublish emergency + rejected output entry + optional quarantine move (no delete)."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from utils.storage_paths import get_sv_transfer  # noqa: E402
except Exception:  # noqa: BLE001

    def get_sv_transfer(*, verbose: bool = False) -> Path:  # type: ignore[misc]
        return Path("/Volumes/SV_TRANSFER")


BLOCK = "bad_audio_and_mixed_source_policy_review_required"
REJECT_PATH = Path(
    "/Volumes/SV_TRANSFER/ready_to_upload/nyc_long_clips/nyc_long_master_clean_real_sound_20260513T102702Z.mp4"
)


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    xfer = get_sv_transfer(verbose=False)
    pack = xfer / "publish_pack" / "nyc_long_uploads"
    pack.mkdir(parents=True, exist_ok=True)
    emergency = pack / "long_autopublish_emergency.json"
    rej_file = pack / "long_rejected_outputs.json"
    emergency.write_text(
        json.dumps(
            {
                "LONG_AUTOPUBLISH_DISABLED": True,
                "SAFE_TO_ENABLE_LONG_UPLOAD": False,
                "block_reason": BLOCK,
                "note": "Emergency: bad_audio_and_mixed_source_policy_review_required",
                "updated_at": _utc(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    entry = {
        "resolved_path": str(REJECT_PATH.resolve()) if REJECT_PATH.is_file() else str(REJECT_PATH),
        "path": str(REJECT_PATH),
        "reasons": [
            "audio_damaged_by_cleanup",
            "mixed_walking_and_driving",
            "mixed_aspect_9_16_and_16_9",
            "requires_manual_review",
        ],
        "rejected_at": _utc(),
        "requires_manual_review": True,
    }
    data: dict[str, Any] = {"version": 1, "updated_at": _utc(), "entries": []}
    if rej_file.is_file():
        try:
            old = json.loads(rej_file.read_text(encoding="utf-8", errors="replace"))
            if isinstance(old, dict) and isinstance(old.get("entries"), list):
                data["entries"] = list(old["entries"])
        except (OSError, json.JSONDecodeError):
            pass
    keys = {str(e.get("resolved_path") or e.get("path") or "") for e in data["entries"]}
    if entry["resolved_path"] not in keys and str(REJECT_PATH) not in keys:
        data["entries"].append(entry)
    rej_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    quarantine = xfer / "ready_to_upload" / "_rejected_long_outputs"
    if REJECT_PATH.is_file():
        quarantine.mkdir(parents=True, exist_ok=True)
        dst = quarantine / REJECT_PATH.name
        if not dst.is_file():
            try:
                shutil.move(str(REJECT_PATH), str(dst))
                entry["quarantine_path"] = str(dst)
            except OSError as exc:
                entry["quarantine_error"] = repr(exc)
        for i, ex in enumerate(data["entries"]):
            if str(ex.get("path") or "") == str(REJECT_PATH) or str(ex.get("resolved_path") or "") == entry.get(
                "resolved_path"
            ):
                merged = {**ex, **{k: v for k, v in entry.items() if v is not None}}
                data["entries"][i] = merged
                break
        rej_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps({"ok": True, "emergency": str(emergency), "rejected_json": str(rej_file)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
