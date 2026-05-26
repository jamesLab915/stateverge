#!/usr/bin/env python3
"""Patch media index nyc_long_driving_sources.json: set policy_version if missing. No asset moves/deletes."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

POLICY = "nyc_long_channel_source_policy_v1"
DEFAULT_INDEX = Path("/Volumes/SV_TRANSFER/media_index/nyc_long_driving_sources.json")


def _patch_one(path: Path, *, apply: bool) -> dict[str, Any]:
    rep: dict[str, Any] = {"path": str(path), "exists": path.is_file(), "changed": False, "before": {}, "after": {}}
    if not path.is_file():
        rep["error"] = "file_missing"
        return rep
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        rep["error"] = repr(exc)
        return rep
    if not isinstance(data, dict):
        rep["error"] = "root_not_object"
        return rep
    rep["before"]["policy_version"] = data.get("policy_version")
    rep["before"]["long_source_policy_version"] = data.get("long_source_policy_version")
    need_pv = str(data.get("policy_version") or "").strip() == ""
    need_lsv = str(data.get("long_source_policy_version") or "").strip() == ""
    if not need_pv and not need_lsv:
        rep["note"] = "policy_fields_already_present"
        rep["after"] = rep["before"]
        return rep
    if not apply:
        rep["would_set_policy_version"] = POLICY if need_pv else None
        rep["would_set_long_source_policy_version"] = POLICY if need_lsv else None
        rep["changed"] = True
        return rep
    if need_pv:
        data["policy_version"] = POLICY
    if need_lsv:
        data["long_source_policy_version"] = POLICY
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    rep["changed"] = True
    rep["after"]["policy_version"] = data.get("policy_version")
    rep["after"]["long_source_policy_version"] = data.get("long_source_policy_version")
    return rep


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--index-json", type=Path, default=DEFAULT_INDEX)
    ap.add_argument("--apply", action="store_true", help="Write changes (default is dry-run inspect only).")
    args = ap.parse_args()
    r = _patch_one(args.index_json.expanduser(), apply=bool(args.apply))
    print(json.dumps({"ok": "error" not in r, **r}, indent=2, ensure_ascii=False))
    return 0 if "error" not in r else 1


if __name__ == "__main__":
    raise SystemExit(main())
