#!/usr/bin/env python3
"""Scan StateVerge repos for legacy hard-coded paths (read-only, no auto-fix)."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SKIP_DIR_NAMES = {"node_modules", ".git", "__pycache__", ".next", "venv", ".venv", "dist", "build"}
TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mjs",
    ".cjs",
    ".sh",
    ".env",
    ".yaml",
    ".yml",
    ".toml",
    ".html",
    ".css",
}

PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("legacy_stateverge_vol", re.compile(r"/Volumes/StateVerge"), "Use SV_* from storage_map.env / storage_paths."),
    ("legacy_sv_work", re.compile(r"/Volumes/SV_WORK\b"), "Use SV_CACHE / SV_TRANSFER / SV_BACKUP."),
    ("frontend_v2", re.compile(r"frontend_v2"), "Do not use frontend_v2; use Control Center frontend."),
    ("node_modules_on_volume", re.compile(r"/Volumes/[^\s\"']*node_modules"), "Avoid node_modules under /Volumes."),
]


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _severity(path: Path, matched: str, rule: str) -> tuple[str, str]:
    s = str(path).lower()
    if path.name == "storage_map.env" and "StateVerge" in str(path):
        return "info", "Explicit legacy keys in storage map; intentional."
    if rule == "node_modules_on_volume" and "node_modules" in path.parts:
        return "info", "dependency tree; often benign if inside ignored dir (should be skipped)."
    if "/logs/" in s or (path.suffix.lower() == ".md" and "readme" in path.name.lower()):
        return "info", "Documentation or logs; update when convenient."
    if path.suffix.lower() in (".py", ".ts", ".tsx", ".js", ".jsx") and "test" not in s:
        if "readme" in path.name.lower():
            return "info", "Review if this is user-facing doc."
        return "blocker", "Replace with storage_paths helpers or env-driven paths."
    if path.suffix.lower() == ".md":
        return "info", "Documentation reference."
    return "warn", "Review context; may be intentional fallback."


def scan_roots(roots: list[Path]) -> dict[str, Any]:
    hits: list[dict[str, Any]] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob("*"):
                try:
                    if not p.is_file():
                        continue
                    if any(part in SKIP_DIR_NAMES for part in p.parts):
                        continue
                    if p.suffix.lower() not in TEXT_SUFFIXES and p.suffix:
                        continue
                    if p.stat().st_size > 5_000_000:
                        continue
                except OSError:
                    continue
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for i, line in enumerate(text.splitlines(), 1):
                    for rule, rx, suggestion in PATTERNS:
                        m = rx.search(line)
                        if not m:
                            continue
                        sev, sug2 = _severity(p, m.group(0), rule)
                        hits.append(
                            {
                                "file": str(p),
                                "line": i,
                                "matched_text": m.group(0),
                                "rule": rule,
                                "severity": sev,
                                "suggestion": suggestion + " " + sug2,
                            }
                        )
        except OSError:
            continue

    counts = {"blocker": 0, "warn": 0, "info": 0}
    for h in hits:
        counts[h["severity"]] = counts.get(h["severity"], 0) + 1

    return {
        "created_at": _utc(),
        "roots": [str(r) for r in roots],
        "counts": counts,
        "hits": hits,
    }


def main() -> int:
    home = Path.home()
    roots = [home / "StateVerge", home / "StateVerge_Control_Center"]
    payload = scan_roots(roots)

    try:
        if Path("/Volumes/SV_CACHE").is_dir():
            json_path = Path("/Volumes/SV_CACHE/logs/legacy_path_audit.json")
        else:
            cache_logs = Path("/Volumes/SV_CACHE/logs")
            try:
                _repo = Path(__file__).resolve().parent.parent
                _src = _repo / "src"
                if str(_src) not in sys.path:
                    sys.path.insert(0, str(_src))
                from utils.storage_paths import get_sv_cache  # noqa: E402

                cache_logs = get_sv_cache(verbose=False) / "logs"
            except Exception:
                pass
            json_path = cache_logs / "legacy_path_audit.json"
    except OSError:
        json_path = home / "StateVerge" / "logs" / "legacy_path_audit.json"
    try:
        json_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = json_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(json_path)
    except OSError as exc:
        payload["write_error"] = repr(exc)

    md_lines = [
        "# Legacy path audit",
        "",
        f"created_at: {payload.get('created_at')}",
        "",
        f"blocker: {payload.get('counts', {}).get('blocker', 0)}",
        f"warn: {payload.get('counts', {}).get('warn', 0)}",
        f"info: {payload.get('counts', {}).get('info', 0)}",
        "",
        "## Recent blockers (up to 30)",
        "",
    ]
    bl = [h for h in (payload.get("hits") or []) if h.get("severity") == "blocker"][:30]
    for h in bl:
        md_lines.append(f"- `{h.get('file')}`:{h.get('line')} — `{h.get('matched_text')}`")
    md_path = home / "StateVerge_Control_Center" / "logs" / "legacy_path_audit.md"
    try:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("\n".join(md_lines), encoding="utf-8")
    except OSError:
        pass

    print(json.dumps({"ok": True, "json": str(json_path), "markdown": str(md_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
