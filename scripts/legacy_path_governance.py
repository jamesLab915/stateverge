#!/usr/bin/env python3
"""Legacy Path Governance Cleanup v1 — scan /Volumes/StateVerge string refs (fail-open)."""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HOME = Path.home()
STATEVERGE = HOME / "StateVerge"
CONTROL_CENTER = HOME / "StateVerge_Control_Center"
SV_CACHE_PRIMARY = Path("/Volumes/SV_CACHE")
REPO_LOGS = STATEVERGE / "logs"

NEEDLE = "/Volumes/StateVerge"
REPLACEMENT = "/Volumes/SV_TRANSFER"

SCAN_ROOTS = [STATEVERGE, CONTROL_CENTER]

IGNORE_DIR_NAMES = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "logs",
}

SCAN_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".sh",
    ".env",
    ".mdc",
}

MAX_FILE_BYTES = 2 * 1024 * 1024

MEDIUM_KEYS = re.compile(
    r"(legacy|fallback|compatibility|deprecated)",
    re.IGNORECASE,
)
CRITICAL_LINE_KEYS = re.compile(
    r"(upload|render|ready_to_upload|queue|publish|music|media_index)",
    re.IGNORECASE,
)
CRITICAL_PATH_SEGMENTS = re.compile(
    r"(nyc_auto|upload|publish|queue|ready_to_upload|render|music|media_index)",
    re.IGNORECASE,
)

STORAGE_PATHS_BASENAME = "storage_paths.py"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_output_dir() -> tuple[Path, list[str]]:
    """Prefer SV_CACHE/logs; else ~/StateVerge/logs."""
    notes: list[str] = []
    primary = SV_CACHE_PRIMARY / "logs"
    try:
        if SV_CACHE_PRIMARY.exists():
            primary.mkdir(parents=True, exist_ok=True)
            probe = primary / ".legacy_path_governance_write_probe"
            probe.write_text("1", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return primary, notes
    except OSError as exc:
        notes.append(f"sv_cache_logs_unavailable:{exc!r}")

    fb = REPO_LOGS
    try:
        fb.mkdir(parents=True, exist_ok=True)
        probe = fb / ".legacy_path_governance_write_probe"
        probe.write_text("1", encoding="utf-8")
        probe.unlink(missing_ok=True)
        notes.append("output_dir_fallback:~/StateVerge/logs (SV_CACHE unavailable or not writable)")
        return fb, notes
    except OSError as exc:
        notes.append(f"output_dir_fallback_failed:{exc!r}")
        try:
            fb.mkdir(parents=True, exist_ok=True)
            return fb, notes
        except OSError:
            return HOME, notes + ["output_dir_fallback:last_resort_home"]


def _char_before_hash_not_in_string(line: str, hash_idx: int) -> bool:
    """Rough check: '#' at hash_idx is not inside a single- or double-quoted segment (best-effort)."""
    if hash_idx <= 0:
        return True
    i = 0
    in_single = in_double = False
    escape = False
    while i < hash_idx:
        c = line[i]
        if escape:
            escape = False
            i += 1
            continue
        if c == "\\" and (in_single or in_double):
            escape = True
            i += 1
            continue
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        i += 1
    return not in_single and not in_double


def _python_tail_comment_contains_needle(line: str, needle: str) -> bool:
    """True if needle appears only after a '#' that is outside quotes (Python-ish)."""
    if needle not in line:
        return False
    start = 0
    while True:
        h = line.find("#", start)
        if h < 0:
            return False
        if _char_before_hash_not_in_string(line, h):
            tail = line[h + 1 :]
            if needle in tail and needle not in line[:h]:
                return True
            if needle in tail and needle in line[:h]:
                return False
        start = h + 1


def _js_line_comment_contains_needle(line: str, needle: str) -> bool:
    """True if needle only after last '//' outside strings (simplified)."""
    if needle not in line:
        return False
    idx = line.rfind("//")
    if idx < 0:
        return False
    if not _char_before_hash_not_in_string(line, idx):  # reuse: not in string at position
        return False
    before, after = line[:idx], line[idx + 2 :]
    return needle in after and needle not in before


def _full_line_comment_starts(line_stripped: str) -> bool:
    return (
        line_stripped.startswith("#")
        or line_stripped.startswith("//")
        or line_stripped.startswith("*")
        or line_stripped.startswith("/*")
        or line_stripped.endswith("*/")
    )


@dataclass
class LineHit:
    line_no: int
    text: str
    risk: str  # low | medium | critical
    occurrences: int = 1


@dataclass
class FileScan:
    path: str
    extension: str
    hits: list[LineHit] = field(default_factory=list)
    error: str | None = None


def _py_advance_triple_quote_state(line: str, state: str | None) -> str | None:
    """Update triple-quoted string state after scanning a full Python line (best-effort)."""
    pos = 0
    n = len(line)
    while pos < n:
        if state is None:
            d = line.find('"""', pos)
            s = line.find("'''", pos)
            opts = [(d, '"""'), (s, "'''")]
            opts = [(i, delim) for i, delim in opts if i >= 0]
            if not opts:
                break
            start, delim = min(opts, key=lambda x: x[0])
            state = delim
            pos = start + 3
            continue
        close = line.find(state, pos)
        if close < 0:
            break
        state = None
        pos = close + 3
    return state


def _classify_line(
    *,
    line: str,
    ext: str,
    file_path: Path,
    in_py_triple_string: bool,
) -> str:
    if ext == ".md":
        return "low"

    st = line.strip()
    if _full_line_comment_starts(st):
        return "low"

    if ext == ".py":
        if _python_tail_comment_contains_needle(line, NEEDLE):
            return "low"
        if in_py_triple_string:
            return "medium"

    if ext in (".ts", ".tsx", ".js"):
        if _js_line_comment_contains_needle(line, NEEDLE):
            return "low"

    if ext in (".sh", ".yaml", ".yml", ".toml", ".mdc", ".env"):
        if _python_tail_comment_contains_needle(line, NEEDLE):
            return "low"

    fp_s = str(file_path).lower()
    if CRITICAL_LINE_KEYS.search(line):
        return "critical"
    if CRITICAL_PATH_SEGMENTS.search(fp_s):
        return "critical"

    if MEDIUM_KEYS.search(line):
        return "medium"

    if ext == ".json":
        return "medium"

    return "medium"


def _should_skip_path(path: Path) -> bool:
    parts_lower = {p.lower() for p in path.parts}
    if STORAGE_PATHS_BASENAME in path.name:
        return True
    return False


def iter_scan_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root, topdown=True):
                dp = Path(dirpath)
                dirnames[:] = [d for d in dirnames if d not in IGNORE_DIR_NAMES and d != ".venv"]
                if any(part in IGNORE_DIR_NAMES for part in dp.parts):
                    continue
                for fn in filenames:
                    if fn.endswith(".pyc") or fn.startswith("._"):
                        continue
                    fp = dp / fn
                    if _should_skip_path(fp):
                        continue
                    suf = fp.suffix.lower()
                    if suf not in SCAN_EXTENSIONS:
                        continue
                    try:
                        if fp.is_file() and fp.stat().st_size > MAX_FILE_BYTES:
                            continue
                    except OSError:
                        continue
                    out.append(fp)
        except OSError:
            continue
    return out


def scan_files(warnings: list[str]) -> list[FileScan]:
    results: list[FileScan] = []
    for fp in iter_scan_files():
        ext = fp.suffix.lower()
        try:
            raw = fp.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"read_failed:{fp}:{exc!r}")
            results.append(FileScan(str(fp), ext, error=repr(exc)))
            continue
        if NEEDLE not in raw:
            continue
        lines = raw.splitlines()
        hits: list[LineHit] = []
        py_triple: str | None = None
        for i, line in enumerate(lines):
            in_py_triple = ext == ".py" and py_triple is not None
            if NEEDLE in line:
                risk = _classify_line(
                    line=line,
                    ext=ext,
                    file_path=fp,
                    in_py_triple_string=in_py_triple,
                )
                occ = line.count(NEEDLE)
                hits.append(LineHit(i + 1, line[:500], risk, occurrences=occ))
            if ext == ".py":
                py_triple = _py_advance_triple_quote_state(line, py_triple)
        if hits:
            results.append(FileScan(str(fp), ext, hits=hits))
    return results


def _file_max_risk(fs: FileScan) -> str:
    order = {"low": 0, "medium": 1, "critical": 2}
    m = 0
    for h in fs.hits:
        m = max(m, order.get(h.risk, 1))
    return {0: "low", 1: "medium", 2: "critical"}[m]


def aggregate_counts(file_scans: list[FileScan]) -> tuple[int, int, int, int, list[str], list[str]]:
    total = low = medium = crit = 0
    critical_files: list[str] = []
    remaining_runtime: list[str] = []
    seen_crit: set[str] = set()
    seen_rem: set[str] = set()
    for fs in file_scans:
        if fs.error or not fs.hits:
            continue
        mx = _file_max_risk(fs)
        for h in fs.hits:
            o = max(1, int(h.occurrences))
            total += o
            if h.risk == "low":
                low += o
            elif h.risk == "medium":
                medium += o
            else:
                crit += o
        if mx == "critical":
            if fs.path not in seen_crit:
                seen_crit.add(fs.path)
                critical_files.append(fs.path)
        if mx in ("medium", "critical"):
            if fs.path not in seen_rem:
                seen_rem.add(fs.path)
                remaining_runtime.append(fs.path)
    return total, low, medium, crit, critical_files, remaining_runtime


def maybe_apply_fixes(file_scans: list[FileScan], apply: bool, warnings: list[str]) -> int:
    fixed = 0
    for fs in file_scans:
        if fs.error or not fs.hits:
            continue
        if _file_max_risk(fs) != "low":
            continue
        p = Path(fs.path)
        if STORAGE_PATHS_BASENAME in p.name:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            warnings.append(f"autofix_read_failed:{p}:{exc!r}")
            continue
        if NEEDLE not in text:
            continue
        new_text = text.replace(NEEDLE, REPLACEMENT)
        if new_text == text:
            continue
        if not apply:
            continue
        try:
            p.write_text(new_text, encoding="utf-8")
            fixed += 1
        except OSError as exc:
            warnings.append(f"autofix_write_failed:{p}:{exc!r}")
    return fixed


def _build_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Legacy Path Governance v1",
        "",
        f"Generated: `{payload.get('generated_at')}`",
        f"Output directory: `{payload.get('output_dir')}`",
        "",
        "## Summary",
        "",
        f"- **total_legacy_references**: {payload.get('total_legacy_references')}",
        f"- **low_risk_count**: {payload.get('low_risk_count')}",
        f"- **medium_risk_count**: {payload.get('medium_risk_count')}",
        f"- **critical_risk_count**: {payload.get('critical_risk_count')}",
        f"- **auto_fixed_count**: {payload.get('auto_fixed_count')}",
        f"- **legacy_governance_ok**: {payload.get('legacy_governance_ok')}",
        f"- **error_count**: {payload.get('error_count')}",
        "",
        "## Auto-fix behavior",
        "",
        "Writes replace `/Volumes/StateVerge` with `/Volumes/SV_TRANSFER` **only** in files where every match is classified **low** risk (comments / markdown).",
        "No writes occur unless you pass **`--apply`**; without it, the tool only reports.",
        "",
        "The following paths are never modified: `storage_paths.py`, and any file containing medium or critical matches.",
        "",
        "## Critical files",
        "",
    ]
    for c in payload.get("critical_files") or []:
        lines.append(f"- `{c}`")
    if not payload.get("critical_files"):
        lines.append("- _(none)_")
    lines.extend(["", "## Remaining runtime dependencies", ""])
    for c in payload.get("remaining_runtime_dependencies") or []:
        lines.append(f"- `{c}`")
    if not payload.get("remaining_runtime_dependencies"):
        lines.append("- _(none)_")
    lines.extend(["", "## Warnings", ""])
    for w in (payload.get("warnings") or [])[:80]:
        lines.append(f"- {w}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Legacy Path Governance v1 (StateVerge Media OS).")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write low-risk replacements to disk (default: report only).",
    )
    args = parser.parse_args()

    warnings: list[str] = []
    error_count = 0

    out_dir, dir_notes = resolve_output_dir()
    for n in dir_notes:
        warnings.append(n)

    file_scans: list[FileScan] = []
    try:
        file_scans = scan_files(warnings)
    except Exception as exc:  # noqa: BLE001
        error_count += 1
        warnings.append(f"scan_unexpected:{exc!r}")
        file_scans = []

    total, low_c, med_c, crit_c, critical_files, remaining_runtime = aggregate_counts(file_scans)
    auto_fixed = maybe_apply_fixes(file_scans, bool(args.apply), warnings)

    legacy_ok = crit_c == 0

    files_payload: list[dict[str, Any]] = []
    for fs in file_scans:
        row: dict[str, Any] = {
            "path": fs.path,
            "extension": fs.extension,
            "max_risk": _file_max_risk(fs) if fs.hits and not fs.error else None,
            "hits": [
                {"line": h.line_no, "risk": h.risk, "occurrences": h.occurrences, "sample": h.text}
                for h in fs.hits
            ],
        }
        if fs.error:
            row["error"] = fs.error
        files_payload.append(row)

    payload: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "total_legacy_references": total,
        "low_risk_count": low_c,
        "medium_risk_count": med_c,
        "critical_risk_count": crit_c,
        "auto_fixed_count": auto_fixed,
        "critical_files": critical_files,
        "remaining_runtime_dependencies": remaining_runtime,
        "error_count": error_count,
        "warnings": warnings,
        "output_dir": str(out_dir),
        "legacy_governance_ok": legacy_ok,
        "apply_mode": bool(args.apply),
        "files": files_payload,
    }

    json_path = out_dir / "legacy_path_governance_report.json"
    md_path = out_dir / "legacy_path_governance_report.md"

    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        md_path.write_text(_build_markdown(payload), encoding="utf-8")
    except OSError as exc:
        error_count += 1
        warnings.append(f"write_report_failed:{exc!r}")

    print("LEGACY_PATH_GOVERNANCE_V1_DONE")
    print(f"TOTAL_LEGACY_REFERENCES={total}")
    print(f"LOW_RISK_COUNT={low_c}")
    print(f"MEDIUM_RISK_COUNT={med_c}")
    print(f"CRITICAL_RISK_COUNT={crit_c}")
    print(f"AUTO_FIXED_COUNT={auto_fixed}")
    print(f"LEGACY_GOVERNANCE_OK={'true' if legacy_ok else 'false'}")
    print(f"ERROR_COUNT={error_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
