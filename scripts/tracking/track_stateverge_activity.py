#!/usr/bin/env python3
"""
Scan selected project trees, diff against a JSON snapshot, append events to CSV.
Stdlib only. Does not read file contents (paths + metadata only).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TRACK = REPO_ROOT / "docs" / "tracking"
EVENTS_CSV = TRACK / "auto_tracking_events.csv"
LOGS_TRACKING = REPO_ROOT / "logs" / "tracking"
SNAPSHOT_JSON = LOGS_TRACKING / "file_snapshot.json"

WATCH_PREFIXES = ("src", "scripts", "docs", "topics", "assets", "output")

SKIP_DIR_NAMES = frozenset(
    {".git", "node_modules", ".next", ".venv", "__pycache__"}
)

MEDIA_SUFFIXES = frozenset(
    {".mp4", ".mov", ".wav", ".mp3", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
)

# Avoid logging noise / recursion from tracker outputs
IGNORE_EXACT_RELPATH = {
    "docs/tracking/auto_tracking_events.csv",
    "logs/tracking/file_snapshot.json",
}


def _is_ignored_generated_path(relp: str) -> bool:
    """Exclude auto-generated monthly reports (avoids feedback when running generate_monthly_report)."""
    return relp.startswith("docs/tracking/monthly_reports/")


def _watched_relpath(relp: str) -> bool:
    for prefix in WATCH_PREFIXES:
        if relp == prefix or relp.startswith(prefix + "/"):
            return True
    return False


def _relp_excluded_for_scan(relp: str) -> bool:
    """Path rules for inclusion (no filesystem stat); must match _iter_tracked_files."""
    if not _watched_relpath(relp):
        return True
    parts = list(Path(relp).parts)
    if any(part in SKIP_DIR_NAMES for part in parts):
        return True
    if _is_under_output_tmp(parts):
        return True
    if relp in IGNORE_EXACT_RELPATH:
        return True
    if _is_ignored_generated_path(relp):
        return True
    if _is_secretish_path(relp):
        return True
    if Path(relp).suffix.lower() in MEDIA_SUFFIXES:
        return True
    return False


CSV_HEADER = [
    "Timestamp",
    "Event Type",
    "Source",
    "File Path",
    "Summary",
    "Project Area",
    "Evidence Value",
    "IRS Relevance",
    "EB1_NIW_Relevance",
    "Notes",
]


@dataclass(frozen=True)
class FileMeta:
    mtime: float
    size: int


def _is_under_output_tmp(parts: list[str]) -> bool:
    """output/<any>/tmp/..."""
    if len(parts) < 3 or parts[0] != "output":
        return False
    return "tmp" in parts[1:]


def _is_secretish_path(relp: str) -> bool:
    lower = relp.lower()
    base = Path(relp).name.lower()
    if base == ".env" or base.endswith(".env") or re.search(
        r"(^|/)\.env($|\.)", relp
    ):
        return True
    if "id_rsa" in lower or "id_ed25519" in lower or "id_ecdsa" in lower:
        return True
    if base.endswith(".pem") or base.endswith(".p12") or base.endswith(".key"):
        return True
    if base in ("credentials.json", "serviceaccount.json", ".netrc"):
        return True
    if "secrets" in lower and "/secrets/" in f"/{lower}/":
        return True
    return False


def _iter_tracked_files() -> Iterator[tuple[str, FileMeta]]:
    """Yield (posix relative path, meta) for non-ignored files under watch roots."""
    for prefix in WATCH_PREFIXES:
        root = REPO_ROOT / prefix
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if p.is_dir():
                continue
            try:
                rel = p.relative_to(REPO_ROOT)
            except ValueError:
                continue
            relp = rel.as_posix()
            if _relp_excluded_for_scan(relp):
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            yield (relp, FileMeta(mtime=float(st.st_mtime), size=int(st.st_size)))


def _load_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_JSON.is_file():
        return None
    try:
        raw = SNAPSHOT_JSON.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    files = data.get("files")
    if not isinstance(files, dict):
        return None
    return data


def _project_area(relp: str) -> str:
    if relp.startswith("src/production/") or relp == "src/production":
        return "Production pipeline"
    if relp.startswith("src/presenter_pipeline/") or relp == "src/presenter_pipeline":
        return "Presenter pipeline"
    if relp.startswith("src/integrations/") or relp == "src/integrations":
        return "Integrations"
    if relp.startswith("src/"):
        return "Source code"
    if relp.startswith("docs/") or relp == "docs":
        return "Documentation"
    if relp.startswith("scripts/") or relp == "scripts":
        return "Automation scripts"
    if relp.startswith("topics/") or relp == "topics":
        return "Topic research / production assets"
    if relp.startswith("assets/") or relp == "assets":
        return "Asset management"
    if relp.startswith("output/") or relp == "output":
        return "Generated outputs"
    return "StateVerge project"


def _evidence_tier(relp: str) -> str:
    if relp.startswith(("src/", "docs/", "scripts/")) or relp in (
        "src",
        "docs",
        "scripts",
    ):
        return "High"
    if relp.startswith(("topics/", "assets/", "output/")) or relp in (
        "topics",
        "assets",
        "output",
    ):
        return "Medium"
    return "Medium"


def _event_summary(event: str, relp: str) -> str:
    """Short automatic English description; path-based, no file contents."""
    p = relp
    if p.startswith("src/production/") or p == "src/production":
        layer = "production pipeline"
    elif p.startswith("src/presenter_pipeline/") or p == "src/presenter_pipeline":
        layer = "presenter pipeline"
    elif p.startswith("src/integrations/") or p == "src/integrations":
        layer = "integrations"
    elif p.startswith("src/"):
        layer = "source tree"
    elif p.startswith("docs/") or p == "docs":
        layer = "documentation"
    elif p.startswith("scripts/") or p == "scripts":
        layer = "automation / tooling"
    elif p.startswith("topics/") or p == "topics":
        layer = "topic / production research assets"
    elif p.startswith("assets/") or p == "assets":
        layer = "asset management"
    elif p.startswith("output/") or p == "output":
        layer = "generated outputs"
    else:
        layer = "StateVerge project"
    if event == "created":
        return f"New file in {layer}"
    if event == "deleted":
        return f"Deleted file in {layer}"
    return f"Modified file in {layer}"


def _diff_snapshot(
    old_files: dict[str, dict[str, Any]] | None, current: dict[str, FileMeta]
) -> list[tuple[str, str, str, str, str, str, str, str, str, str]]:
    """
    Return list of CSV rows (values only, no header) for new events.
    """
    rows: list[tuple[str, str, str, str, str, str, str, str, str, str]] = []
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    src = "file_scan"
    irs = "software/business development support"
    eb1 = "sustained technical development evidence"

    old_keys = set(old_files.keys()) if old_files else set()
    new_keys = set(current.keys())

    for path in sorted(new_keys - old_keys):
        ev = "created"
        area = _project_area(path)
        ev_val = _evidence_tier(path)
        rows.append(
            (
                ts,
                ev,
                src,
                path,
                _event_summary(ev, path),
                area,
                ev_val,
                irs,
                eb1,
                "",
            )
        )

    for path in sorted(old_keys - new_keys):
        ev = "deleted"
        area = _project_area(path)
        ev_val = _evidence_tier(path)
        rows.append(
            (
                ts,
                ev,
                src,
                path,
                _event_summary(ev, path),
                area,
                ev_val,
                irs,
                eb1,
                "",
            )
        )

    for path in sorted(new_keys & old_keys):
        if not old_files:
            continue
        o = old_files.get(path) or {}
        try:
            om = float(o.get("mtime", 0))
            osz = int(o.get("size", 0))
        except (TypeError, ValueError):
            om, osz = 0.0, 0
        cur = current[path]
        if cur.mtime != om or cur.size != osz:
            ev = "modified"
            area = _project_area(path)
            ev_val = _evidence_tier(path)
            rows.append(
                (
                    ts,
                    ev,
                    src,
                    path,
                    _event_summary(ev, path),
                    area,
                    ev_val,
                    irs,
                    eb1,
                    "",
                )
            )
    return rows


def _ensure_csv() -> None:
    TRACK.mkdir(parents=True, exist_ok=True)
    if not EVENTS_CSV.is_file():
        with EVENTS_CSV.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(CSV_HEADER)


def _append_events(rows: list[tuple]) -> int:
    if not rows:
        return 0
    _ensure_csv()
    with EVENTS_CSV.open("a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
    return len(rows)


def _write_snapshot(current: dict[str, FileMeta]) -> None:
    LOGS_TRACKING.mkdir(parents=True, exist_ok=True)
    out = {
        "version": 1,
        "updated_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "files": {k: {"mtime": v.mtime, "size": v.size} for k, v in current.items()},
    }
    SNAPSHOT_JSON.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _run_scan(*, from_git_hook: bool) -> int:
    _ensure_csv()
    current_list = list(_iter_tracked_files())
    current: dict[str, FileMeta] = {p: m for p, m in current_list}
    old_data = _load_snapshot()
    old_files: dict[str, dict[str, Any]] | None = None
    if old_data and isinstance(old_data.get("files"), dict):
        old_files = old_data["files"]  # type: ignore[assignment]
    if old_files:
        old_files = {
            k: v
            for k, v in old_files.items()
            if not _relp_excluded_for_scan(k)
        }

    is_first = old_data is None
    rows: list[tuple] = []
    if not is_first:
        rows = _diff_snapshot(
            {k: (v if isinstance(v, dict) else {}) for k, v in (old_files or {}).items()},
            current,
        )
    n = _append_events(rows)
    _write_snapshot(current)
    label = "git-hook" if from_git_hook else "manual"
    print(
        f"[tracking] action=auto_track mode={label} files_tracked={len(current)} new_events={n} snapshot={SNAPSHOT_JSON}"
    )
    if is_first:
        print(
            "[tracking] info=first_snapshot baseline only (no file delta events; future runs will record changes).",
        )
    return 0


def _print_summary() -> int:
    if not EVENTS_CSV.is_file():
        print("[tracking] no_events_file path=%s" % EVENTS_CSV, file=sys.stderr)
        return 0
    with EVENTS_CSV.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        print("[tracking] events=0 (empty CSV except header).")
        return 0
    last = rows[-20:]
    print("[tracking] last %d event(s) from auto_tracking_events.csv" % len(last))
    for r in last:
        t = r.get("Timestamp", "")
        et = r.get("Event Type", "")
        fp = r.get("File Path", "")
        su = (r.get("Summary") or "")[:80]
        print(f"  {t}  [{et}]  {fp}")
        if su:
            print(f"      {su}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="StateVerge file-tree auto tracking (stdlib only)."
    )
    ap.add_argument(
        "--mode",
        choices=("manual", "git-hook"),
        default="manual",
        help="How the scan was invoked (both run the same file scan + CSV append).",
    )
    g = ap.add_mutually_exclusive_group()
    g.add_argument(
        "--summary",
        action="store_true",
        help="Print last 20 events from auto_tracking_events.csv and exit.",
    )
    args = ap.parse_args()

    if args.summary:
        return _print_summary()
    from_hook = args.mode == "git-hook"
    return _run_scan(from_git_hook=from_hook)


if __name__ == "__main__":
    sys.exit(main())
