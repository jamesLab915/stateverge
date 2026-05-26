#!/usr/bin/env python3
"""Read-only publish gate for SV_TRANSFER/ready_to_upload (no uploads).

Writes JSON report under ``$STATEVERGE_ROOT/logs/davinci/publish_gate_report.json``.
``--dry-run`` still performs checks and writes the report with ``dry_run: true``.

``DAVINCI_BYPASS``: unset → bypass ON (same as enabling); renders-origin in
``ready_to_upload`` is allowed. When ``0``/``false``/``no``, paths listed as
successful bypass collects are rejected; paths from ``davinci_export_manifest``
(status ok) count as DaVinci-approved.

Fail-open: broken files are marked fail; process exits 0 unless argparse fails.
"""

from __future__ import annotations

# IMPORTANT:
# Do not mux original iPhone/VFR/high-fps MOV/MP4 with -c:v copy after audio replacement.
# It can stretch long footage into multi-hour slow motion.
# Final publish artifacts should come from CFR-normalized sources or explicit CFR re-encodes.

import argparse
import csv
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from utils.davinci_ffprobe import ffprobe_bin, ffprobe_json, stream_summary  # noqa: E402
from utils.storage_paths import get_davinci_logs_dir, get_transfer_ready_to_upload  # noqa: E402

VIDEO_EXTS = {".mp4", ".mov", ".m4v"}

MIN_DURATION_SEC = 5.0
MIN_BYTES = 1 * 1024 * 1024

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("davinci_publish_gate")


def davinci_bypass_enabled() -> bool:
    """Unset → bypass ON. ``DAVINCI_BYPASS=0`` / ``false`` / ``no`` → full DaVinci workflow rules."""
    raw = os.environ.get("DAVINCI_BYPASS")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def load_manifest_resolved_paths(
    csv_path: Path,
    column: str,
    *,
    ok_statuses: frozenset[str],
) -> set[str]:
    """Fail-open: missing/unreadable CSV → empty set."""
    out: set[str] = set()
    if not csv_path.is_file():
        return out
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if not row:
                    continue
                st = (row.get("status") or "").strip().lower()
                if st not in ok_statuses:
                    continue
                raw_p = (row.get(column) or "").strip()
                if not raw_p:
                    continue
                try:
                    out.add(str(Path(raw_p).expanduser().resolve()))
                except OSError:
                    out.add(raw_p)
    except OSError as exc:
        log.warning("manifest read failed %s: %s", csv_path, exc)
    return out


def is_under_root(candidate: Path, root: Path) -> bool:
    try:
        c = candidate.resolve()
        r = root.resolve()
    except OSError:
        return False
    return c == r or r in c.parents


def collect_candidates(root: Path, *, limit: int | None) -> list[Path]:
    if not root.is_dir():
        log.warning("ready_to_upload missing or not a directory: %s", root)
        return []
    found: list[Path] = []
    try:
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if p.name.startswith("._") or (p.name.startswith(".") and not p.stem):
                continue
            if p.suffix.lower() not in VIDEO_EXTS:
                continue
            found.append(p)
    except OSError as exc:
        log.warning("rglob failed: %s", exc)
        return []

    def mtime(pp: Path) -> float:
        try:
            return pp.stat().st_mtime
        except OSError:
            return 0.0

    found.sort(key=mtime, reverse=True)
    if limit is not None and limit > 0:
        found = found[:limit]
    return found


def _audio_cleanup_metrics(path: Path) -> dict[str, object]:
    """Optional loudness / peak snapshot (warnings only; fail-open)."""
    if str(os.environ.get("PUBLISH_GATE_SKIP_AUDIO_PROBE", "")).strip().lower() in (
        "1",
        "true",
        "yes",
    ):
        return {"skipped": True, "warnings": []}
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    try:
        import audio_cleanup_pipeline as acp  # noqa: E402
    except Exception as exc:  # noqa: BLE001
        return {"import_error": repr(exc), "warnings": []}
    try:
        m = acp.analyze_audio_metrics(path)
    except Exception as exc:  # noqa: BLE001
        return {"probe_error": repr(exc), "warnings": []}
    warnings: list[str] = []
    lufs = m.get("measured_integrated_lufs")
    peak = m.get("peak_db")
    if lufs is not None and not (-16.0 <= float(lufs) <= -12.0):
        warnings.append("integrated_lufs_outside_recommended_-12_to_-16")
    if peak is not None and float(peak) > -1.0:
        warnings.append("peak_above_minus_1_db")
    if m.get("analyze_error"):
        warnings.append(f"audio_analyze:{m['analyze_error']}")
    return {
        "measured_integrated_lufs": lufs,
        "peak_db": peak,
        "warnings": warnings,
    }


def evaluate(
    path: Path,
    root: Path,
    *,
    bypass_mode: bool,
    bypass_manifest_paths: set[str],
    export_manifest_paths: set[str],
) -> dict[str, object]:
    checks: dict[str, object] = {
        "file_exists": False,
        "path_under_ready_to_upload": False,
        "ffprobe_readable": False,
        "has_video_stream": False,
        "has_audio_stream": False,
        "duration_gt_5s": False,
        "size_gt_1mb": False,
        "davinci_bypass_enabled": bypass_mode,
        "provenance_ok": True,
        "provenance_detail": "",
        "audio_clean_output_hint": False,
        "audio_layer_warnings": [],
    }
    err_parts: list[str] = []

    try:
        exists = path.is_file()
    except OSError as exc:
        exists = False
        err_parts.append(f"is_file_error:{exc}")
    checks["file_exists"] = exists
    if not exists:
        return {"pass": False, "checks": checks, "error": ";".join(err_parts) or "not_a_file"}

    under = is_under_root(path, root)
    checks["path_under_ready_to_upload"] = under
    if not under:
        err_parts.append("path_not_under_ready_to_upload")

    size_ok = False
    duration_val = 0.0
    try:
        st = path.stat()
        size_ok = st.st_size > MIN_BYTES
        checks["size_bytes"] = st.st_size
    except OSError as exc:
        err_parts.append(f"stat:{exc}")
    checks["size_gt_1mb"] = size_ok

    data, perr = ffprobe_json(path)
    if data is None:
        err_parts.append(perr or "ffprobe_failed")
    else:
        checks["ffprobe_readable"] = True
        hv, ha, duration_val = stream_summary(data)
        checks["has_video_stream"] = hv
        checks["has_audio_stream"] = ha
        checks["duration_sec"] = duration_val
        checks["duration_gt_5s"] = duration_val > MIN_DURATION_SEC

    try:
        path_key = str(path.expanduser().resolve())
    except OSError:
        path_key = str(path)

    if bypass_mode:
        checks["provenance_ok"] = True
        checks["provenance_detail"] = "bypass_mode_renders_allowed"
    else:
        if path_key in bypass_manifest_paths:
            checks["provenance_ok"] = False
            checks["provenance_detail"] = "blocked_bypass_manifest_requires_davinci_exports"
            err_parts.append("provenance_bypass_collect_requires_davinci_pipeline")
        elif path_key in export_manifest_paths:
            checks["provenance_ok"] = True
            checks["provenance_detail"] = "davinci_export_manifest"
        else:
            checks["provenance_ok"] = True
            checks["provenance_detail"] = "untracked_fail_open_legacy"

    # Audio cleanup layer hints (non-blocking)
    ps = str(path)
    if "audio_clean" in ps.replace("\\", "/") or path.name == "final_audio_clean.mp4":
        checks["audio_clean_output_hint"] = True
    ac_report = path.parent / "audio_cleanup_report.json"
    checks["audio_cleanup_report_present"] = ac_report.is_file()
    if checks["ffprobe_readable"] and checks.get("has_audio_stream") is False:
        checks["audio_layer_warnings"] = ["no_audio_stream"]
    elif checks["ffprobe_readable"] and checks.get("has_audio_stream") is True:
        am = _audio_cleanup_metrics(path)
        checks["audio_metrics"] = am
        for w in am.get("warnings") or []:
            if isinstance(w, str):
                checks["audio_layer_warnings"].append(w)

    tech_ok = all(
        (
            checks["file_exists"],
            checks["path_under_ready_to_upload"],
            checks["ffprobe_readable"],
            checks["has_video_stream"],
            checks["duration_gt_5s"],
            checks["size_gt_1mb"],
        )
    )
    provenance_ok = bool(checks["provenance_ok"])
    passed = tech_ok and provenance_ok
    return {
        "pass": passed,
        "checks": checks,
        "error": ";".join(err_parts) if err_parts else "",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Same checks; report JSON includes dry_run=true.",
    )
    ap.add_argument("--limit", type=int, default=0, help="Max files (0 = no limit).")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.verbose:
        log.setLevel(logging.DEBUG)

    ready_root = get_transfer_ready_to_upload(verbose=args.verbose)
    log_dir = get_davinci_logs_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("log dir mkdir failed %s: %s", log_dir, exc)

    report_path = log_dir / "publish_gate_report.json"
    bypass_mode = davinci_bypass_enabled()
    bypass_manifest = log_dir / "davinci_bypass_manifest.csv"
    export_manifest = log_dir / "davinci_export_manifest.csv"
    bypass_paths = load_manifest_resolved_paths(
        bypass_manifest,
        "target_path",
        ok_statuses=frozenset({"ok"}),
    )
    export_paths = load_manifest_resolved_paths(
        export_manifest,
        "ready_to_upload_path",
        ok_statuses=frozenset({"ok"}),
    )
    log.info(
        "DAVINCI_BYPASS bypass_mode=%s manifest_rows bypass_ok=%s export_ok=%s",
        bypass_mode,
        len(bypass_paths),
        len(export_paths),
    )

    lim = args.limit if args.limit and args.limit > 0 else None
    files = collect_candidates(ready_root, limit=lim)

    items: list[dict[str, object]] = []
    pass_n = fail_n = 0
    for p in files:
        ev = evaluate(
            p,
            ready_root,
            bypass_mode=bypass_mode,
            bypass_manifest_paths=bypass_paths,
            export_manifest_paths=export_paths,
        )
        passed = bool(ev.get("pass"))
        if passed:
            pass_n += 1
        else:
            fail_n += 1
        items.append(
            {
                "path": str(p),
                "pass": passed,
                "checks": ev.get("checks"),
                "error": ev.get("error", ""),
            }
        )
        log.info(
            "%s %s err=%s",
            "PASS" if passed else "FAIL",
            p.name,
            ev.get("error") or "-",
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": bool(args.dry_run),
        "davinci_bypass": {
            "enabled": bypass_mode,
            "env_raw": os.environ.get("DAVINCI_BYPASS"),
            "policy": (
                "renders_ok_without_resolve"
                if bypass_mode
                else "block_known_bypass_manifest_ok_rows_else_allow_untracked_legacy"
            ),
            "bypass_manifest_csv": str(bypass_manifest),
            "export_manifest_csv": str(export_manifest),
            "bypass_manifest_ok_paths": len(bypass_paths),
            "export_manifest_ok_paths": len(export_paths),
        },
        "ffprobe_bin": ffprobe_bin(),
        "ready_to_upload_root": str(ready_root),
        "limits": {"max_files": lim},
        "rules": {
            "min_duration_sec": MIN_DURATION_SEC,
            "min_size_bytes": MIN_BYTES,
            "video_extensions": sorted(VIDEO_EXTS),
        },
        "summary": {"pass": pass_n, "fail": fail_n, "total": len(items)},
        "items": items,
    }

    try:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        log.info("wrote %s (dry_run=%s)", report_path, args.dry_run)
    except OSError as exc:
        log.warning("could not write report %s: %s", report_path, exc)

    log.info("gate summary pass=%s fail=%s dry_run=%s", pass_n, fail_n, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
