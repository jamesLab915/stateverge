#!/usr/bin/env python3
"""Initialize DaVinci Resolve cache/storage dirs under SV_CACHE or repo fallback.

Does not import StateVerge storage_paths; uses fixed primary/fallback roots only.
Stdlib only. Exits 0 even on partial failure; details go to JSON + markdown.

Stdout emits exactly three lines (no other stdout) when the script finishes:
  DAVINCI_STORAGE_READY=...
  UNIFIED_DAVINCI_ROOT=...
  ACTIVE_DAVINCI_ROOT=/Volumes/SV_CACHE/davinci
ACTIVE line is always the canonical SV_CACHE path for Resolve prefs; stderr
carries probes, JSON paths, and effective_write_root when it differs.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SV_CACHE = Path("/Volumes/SV_CACHE")
PRIMARY_DAVINCI = SV_CACHE / "davinci"
FALLBACK_DAVINCI = Path(
    "/Users/ziweizhang/StateVerge/_storage_fallback/sv_cache/davinci"
)
CANONICAL_ACTIVE = "/Volumes/SV_CACHE/davinci"

SUBDIRS: tuple[str, ...] = (
    "projects",
    "cache",
    "proxies",
    "optimized_media",
    "gallery_stills",
    "renders",
    "audio_exports",
    "xml",
    "logs",
)

MAX_LEGACY_BYTES = 500 * 1024 * 1024
MAX_LEGACY_FILES = 2000

CONTROL_CENTER_LOG = Path(
    "/Users/ziweizhang/StateVerge_Control_Center/logs/davinci_storage_setup.md"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _probe_sv_cache_writable() -> tuple[bool, list[str]]:
    """Return (writable, warnings) for /Volumes/SV_CACHE."""
    warnings: list[str] = []
    if not SV_CACHE.exists():
        warnings.append(f"SV_CACHE missing: {SV_CACHE}")
        return False, warnings
    if not SV_CACHE.is_dir():
        warnings.append(f"SV_CACHE not a directory: {SV_CACHE}")
        return False, warnings
    probe_dir = PRIMARY_DAVINCI / "logs"
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        warnings.append(f"Cannot mkdir probe dir {probe_dir}: {exc}")
        return False, warnings
    try:
        with tempfile.NamedTemporaryFile(
            dir=probe_dir, prefix=".sv_write_probe_", delete=True
        ) as fh:
            fh.write(b"ok")
            fh.flush()
    except OSError as exc:
        warnings.append(f"SV_CACHE not writable (temp file probe failed): {exc}")
        return False, warnings
    return True, warnings


def _ensure_dir(path: Path, errors: list[str]) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError as exc:
        errors.append(f"mkdir failed {path}: {exc}")
        return False


def _write_json(path: Path, payload: dict, errors: list[str]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        errors.append(f"JSON write failed {path}: {exc}")


def _dir_size_and_file_count(path: Path) -> tuple[int, int]:
    """Recursive byte size and file count for *path* (directory)."""
    total_bytes = 0
    file_count = 0
    if not path.exists():
        return 0, 0
    try:
        for root, _, files in os.walk(path, topdown=True, followlinks=False):
            for name in files:
                fp = Path(root) / name
                try:
                    total_bytes += fp.stat().st_size
                    file_count += 1
                except OSError:
                    continue
    except OSError:
        pass
    return total_bytes, file_count


def _migrate_legacy_children(
    source: Path,
    dest: Path,
    migration_warnings: list[str],
    legacy_migrated: list[str],
) -> None:
    """Move each child of *source* into *dest*; never delete *source*."""
    try:
        names = sorted(source.iterdir(), key=lambda p: p.name.lower())
    except OSError as exc:
        migration_warnings.append(f"legacy_list_failed:{source}:{exc}")
        return
    for child in names:
        target = dest / child.name
        try:
            if target.exists():
                migration_warnings.append(
                    f"legacy_dest_exists_skip:{child}->{target}"
                )
                continue
            shutil.move(str(child), str(target))
            legacy_migrated.append(f"{child} -> {target}")
        except OSError as exc:
            migration_warnings.append(f"legacy_move_failed:{child}:{exc}")


def _run_primary_legacy_migrations(
    migration_warnings: list[str],
    legacy_detected: list[str],
    legacy_migrated: list[str],
) -> None:
    """Compatibility moves on SV_CACHE only; fail-open; never remove sources."""
    specs: tuple[tuple[Path, Path, str], ...] = (
        (SV_CACHE / "davinci_projects", PRIMARY_DAVINCI / "projects", "davinci_projects"),
        (PRIMARY_DAVINCI / "ProxyMedia", PRIMARY_DAVINCI / "proxies", "davinci/ProxyMedia"),
        (PRIMARY_DAVINCI / "CacheClip", PRIMARY_DAVINCI / "cache", "davinci/CacheClip"),
        (PRIMARY_DAVINCI / "gallery", PRIMARY_DAVINCI / "gallery_stills", "davinci/gallery"),
    )
    for src, dest, label in specs:
        if not src.exists() or not src.is_dir():
            continue
        legacy_detected.append(str(src.resolve()))
        nbytes, nfiles = _dir_size_and_file_count(src)
        if nbytes > MAX_LEGACY_BYTES or nfiles > MAX_LEGACY_FILES:
            migration_warnings.append(
                f"large_legacy_skipped:{label}:bytes={nbytes}:files={nfiles}"
            )
            continue
        _migrate_legacy_children(src, dest, migration_warnings, legacy_migrated)


def _write_markdown(path: Path, report: dict, errors: list[str]) -> None:
    active = report.get("active_root", "")
    eff = report.get("effective_write_root", active)
    fb = report.get("fallback_used", False)
    unified = report.get("unified_root", False)
    created = report.get("created_dirs") or []
    ts = report.get("timestamp", "")
    leg_det = report.get("legacy_paths_detected") or []
    leg_mig = report.get("legacy_paths_migrated") or []
    mig_wr = report.get("migration_warnings") or []
    lines = [
        "# DaVinci Resolve storage (StateVerge)",
        "",
        f"- **Timestamp (UTC)**: {ts}",
        f"- **UNIFIED_DAVINCI_ROOT**: {'true' if unified else 'false'}",
        f"- **Active DaVinci root (JSON active_root)**: `{active}`",
        f"- **Effective write root**: `{eff}`",
        f"- **Fallback used**: {'yes' if fb else 'no'}",
        "",
        "## Legacy paths detected",
        "",
    ]
    for p in leg_det:
        lines.append(f"- `{p}`")
    if not leg_det:
        lines.append("- _(none)_")
    lines.extend(["", "## Legacy paths migrated", ""])
    for p in leg_mig:
        lines.append(f"- `{p}`")
    if not leg_mig:
        lines.append("- _(none)_")
    lines.extend(["", "## Migration warnings", ""])
    for w in mig_wr:
        lines.append(f"- {w}")
    if not mig_wr:
        lines.append("- _(none)_")
    lines.extend(
        [
            "",
            "## Created / ensured directories",
            "",
        ]
    )
    for d in created:
        lines.append(f"- `{d}`")
    if not created:
        lines.append("- _(none recorded)_")
    lines.extend(
        [
            "",
            "## Next manual step (DaVinci Resolve Studio)",
            "",
            "Open **DaVinci Resolve → Preferences → Media Storage**.",
            "",
        ]
    )
    if fb:
        lines.extend(
            [
                "SV_CACHE was not available or not writable; until the volume is back, "
                f"point Media Storage to **`{eff}`** for actual disk operations.",
                f"Target canonical workspace root remains **`{CANONICAL_ACTIVE}`** when SV_CACHE is mounted.",
                "Remove default locations on **Macintosh HD** (for example **Movies**) if you do not want Resolve to use them.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "In **Media Storage**, keep only **`/Volumes/SV_CACHE/davinci`** as the workspace root for Resolve cache, proxies, optimized media, gallery stills, render temps, and related exports.",
                "Remove default locations on **Macintosh HD** (for example **Movies**) if you do not want Resolve to use them.",
                "",
            ]
        )
    wr = report.get("warnings") or []
    er = report.get("errors") or []
    if wr:
        lines.extend(["## Warnings", ""])
        for w in wr:
            lines.append(f"- {w}")
        lines.append("")
    if er:
        lines.extend(["## Errors", ""])
        for e in er:
            lines.append(f"- {e}")
        lines.append("")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except OSError as exc:
        errors.append(f"Markdown write failed {path}: {exc}")


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []
    created_dirs: list[str] = []
    legacy_paths_detected: list[str] = []
    legacy_paths_migrated: list[str] = []
    migration_warnings: list[str] = []

    primary_ok, probe_warnings = _probe_sv_cache_writable()
    warnings.extend(probe_warnings)

    if primary_ok:
        effective_write_root = PRIMARY_DAVINCI
        json_active_root = str(PRIMARY_DAVINCI.resolve())
        fallback_used = False
        json_target = PRIMARY_DAVINCI / "logs" / "davinci_storage_init.json"
    else:
        effective_write_root = FALLBACK_DAVINCI
        json_active_root = str(FALLBACK_DAVINCI.resolve())
        fallback_used = True
        json_target = FALLBACK_DAVINCI / "logs" / "davinci_storage_init.json"

    _ensure_dir(effective_write_root, errors)

    for name in SUBDIRS:
        sub = effective_write_root / name
        if _ensure_dir(sub, errors):
            created_dirs.append(str(sub.resolve()))

    if primary_ok:
        _run_primary_legacy_migrations(
            migration_warnings, legacy_paths_detected, legacy_paths_migrated
        )

    layout_ok = all((effective_write_root / s).is_dir() for s in SUBDIRS)
    storage_ready = layout_ok
    unified_root = bool(primary_ok and layout_ok)

    timestamp = _utc_now_iso()
    report: dict = {
        "active_root": json_active_root,
        "created_dirs": sorted(set(created_dirs)),
        "effective_write_root": str(effective_write_root.resolve()),
        "fallback_used": fallback_used,
        "legacy_paths_detected": legacy_paths_detected,
        "legacy_paths_migrated": legacy_paths_migrated,
        "migration_warnings": migration_warnings,
        "timestamp": timestamp,
        "unified_root": unified_root,
    }
    if warnings:
        report["warnings"] = warnings
    if errors:
        report["errors"] = errors
    report["writable"] = layout_ok and not any(
        e.startswith("mkdir failed") for e in errors
    )

    def _sync_report() -> None:
        report["writable"] = layout_ok and not any(
            e.startswith("mkdir failed") for e in errors
        )
        report["unified_root"] = unified_root
        report["legacy_paths_detected"] = legacy_paths_detected
        report["legacy_paths_migrated"] = legacy_paths_migrated
        report["migration_warnings"] = migration_warnings
        report["effective_write_root"] = str(effective_write_root.resolve())
        if warnings:
            report["warnings"] = warnings
        else:
            report.pop("warnings", None)
        if errors:
            report["errors"] = errors
        else:
            report.pop("errors", None)

    _write_json(json_target, report, errors)
    _sync_report()
    _write_markdown(CONTROL_CENTER_LOG, report, errors)
    _sync_report()
    _write_json(json_target, report, errors)

    print(f"JSON report: {json_target}", file=sys.stderr)
    print(f"Markdown status: {CONTROL_CENTER_LOG}", file=sys.stderr)
    if str(effective_write_root.resolve()) != CANONICAL_ACTIVE:
        print(
            f"effective_write_root={effective_write_root.resolve()} "
            f"(canonical prefs target remains {CANONICAL_ACTIVE})",
            file=sys.stderr,
        )

    ready_s = "true" if storage_ready else "false"
    unified_s = "true" if unified_root else "false"
    print(f"DAVINCI_STORAGE_READY={ready_s}")
    print(f"UNIFIED_DAVINCI_ROOT={unified_s}")
    print(f"ACTIVE_DAVINCI_ROOT={CANONICAL_ACTIVE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
