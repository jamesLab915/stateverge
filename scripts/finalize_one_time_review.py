#!/usr/bin/env python3
"""
Finalize ONE_TIME_REVIEW: read manual selected/rejected symlink folders,
merge with review_index.jsonl, write 05_INDEX approved / rejected lists.

Without --apply: print counts only.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SV_CODE_RE = re.compile(r"^(SV-\d{8})__")


def load_review_index(path: Path) -> dict[str, dict]:
    by_code: dict[str, dict] = {}
    if not path.is_file():
        return by_code
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        code = row.get("asset_code")
        if isinstance(code, str):
            by_code[code] = row
    return by_code


def collect_codes(folder: Path) -> set[str]:
    """Scan folder/video and folder/image for SV-xxxxxxxx__* symlinks or files."""
    found: set[str] = set()
    for sub in ("video", "image"):
        d = folder / sub
        if not d.is_dir():
            continue
        try:
            for p in d.iterdir():
                if p.name.startswith("._"):
                    continue
                m = SV_CODE_RE.match(p.name)
                if m:
                    found.add(m.group(1))
        except OSError:
            continue
    return found


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    _SCRIPTS = Path(__file__).resolve().parent
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    from stateverge_paths import SSD_ROOT

    ap = argparse.ArgumentParser(description="Finalize ONE_TIME_REVIEW selections.")
    ap.add_argument("--root", type=Path, default=SSD_ROOT)
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write approved_assets.* and rejected_assets.jsonl (default: dry-run counts)",
    )
    ns = ap.parse_args()
    root = ns.root.expanduser().resolve()
    review_base = root / "02_PROJECTS" / "ONE_TIME_REVIEW"
    index_path = review_base / "review_index.jsonl"
    selected_root = review_base / "selected"
    rejected_root = review_base / "rejected"
    index_dir = root / "05_INDEX"

    by_code = load_review_index(index_path)
    total_index = len(by_code)

    sel_codes = collect_codes(selected_root)
    rej_codes = collect_codes(rejected_root)

    approved_set = set(sel_codes)
    rejected_only = set(rej_codes) - approved_set

    known = set(by_code.keys())
    orphan_sel = sel_codes - known
    orphan_rej = rej_codes - known

    pending_codes = known - sel_codes - rej_codes

    print(
        f"review_index entries: {total_index}\n"
        f"selected (symlinks): {len(sel_codes)} unique SV codes\n"
        f"rejected (symlinks, excl. overlap): {len(rejected_only)} unique SV codes\n"
        f"pending (in index, not in selected/rejected): {len(pending_codes)}\n"
    )
    if orphan_sel:
        print(f"WARN orphan selected (not in index): {sorted(orphan_sel)[:20]}" + (" ..." if len(orphan_sel) > 20 else ""))
    if orphan_rej:
        print(f"WARN orphan rejected (not in index): {sorted(orphan_rej)[:20]}" + (" ..." if len(orphan_rej) > 20 else ""))

    if not ns.apply:
        print("\nDry-run only. Pass --apply to write 05_INDEX outputs.")
        return 0

    if not index_path.is_file():
        print(f"MISSING_REVIEW_INDEX: {index_path}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    approved_rows: list[dict] = []
    for code in sorted(approved_set):
        src = by_code.get(code)
        if not src:
            continue
        row = {
            "asset_code": code,
            "asset_id": src.get("asset_id", ""),
            "media_type": src.get("media_type", ""),
            "original_path": src.get("original_path", ""),
            "review_link_path": src.get("review_link_path", ""),
            "approved_at": now,
            "shoot_date": src.get("shoot_date", ""),
            "time_bucket": src.get("time_bucket", ""),
            "location_slug": src.get("location_slug", ""),
            "device_tier": src.get("device_tier", ""),
            "duration_seconds": src.get("duration_seconds", 0),
            "orientation": src.get("orientation", ""),
            "status": "approved",
        }
        approved_rows.append(row)

    rejected_rows: list[dict] = []
    for code in sorted(rejected_only):
        src = by_code.get(code)
        if not src:
            continue
        rejected_rows.append(
            {
                "asset_code": code,
                "asset_id": src.get("asset_id", ""),
                "media_type": src.get("media_type", ""),
                "original_path": src.get("original_path", ""),
                "review_link_path": src.get("review_link_path", ""),
                "rejected_at": now,
                "shoot_date": src.get("shoot_date", ""),
                "time_bucket": src.get("time_bucket", ""),
                "location_slug": src.get("location_slug", ""),
                "device_tier": src.get("device_tier", ""),
                "duration_seconds": src.get("duration_seconds", 0),
                "orientation": src.get("orientation", ""),
                "status": "rejected",
            }
        )

    appr_jsonl = index_dir / "approved_assets.jsonl"
    appr_csv = index_dir / "approved_assets.csv"
    rej_jsonl = index_dir / "rejected_assets.jsonl"

    fields_appr = [
        "asset_code",
        "asset_id",
        "media_type",
        "original_path",
        "review_link_path",
        "approved_at",
        "shoot_date",
        "time_bucket",
        "location_slug",
        "device_tier",
        "duration_seconds",
        "orientation",
        "status",
    ]

    write_jsonl(appr_jsonl, approved_rows)
    write_csv(appr_csv, fields_appr, approved_rows)
    write_jsonl(rej_jsonl, rejected_rows)

    print(
        f"\nWrote:\n  {appr_jsonl}\n  {appr_csv}\n  {rej_jsonl}\n"
        f"approved_rows={len(approved_rows)} rejected_rows={len(rejected_rows)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
