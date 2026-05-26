#!/usr/bin/env python3
"""Scan StateVerge repo for YouTube token path references; write Control Center audit v1."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parent.parent.parent
CONTROL_LOGS = Path.home() / "StateVerge_Control_Center" / "logs"
OUT_JSON = CONTROL_LOGS / "youtube_token_path_audit_v1.json"
OUT_MD = CONTROL_LOGS / "youtube_token_path_audit_v1.md"

SCAN_EXTS = {".py", ".md", ".plist", ".sh"}
SKIP_DIR_PARTS = frozenset({".git", "__pycache__", ".venv", "node_modules", ".mypy_cache"})

# (regex, role hint)
PATH_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"data/youtube/token\.json"), "long_token"),
    (re.compile(r"\.secrets/youtube/token\.json"), "legacy_long_token"),
    (re.compile(r"token_shorts\.json"), "shorts_token"),
    (re.compile(r"client_secrets\.json"), "client_secrets"),
    (re.compile(r"STATEVERGE_YOUTUBE_TOKEN"), "env_long_token"),
    (re.compile(r"youtube_auth_init"), "script_ref"),
    (re.compile(r"youtube_upload"), "script_ref"),
    (re.compile(r"youtube_batch_upload"), "script_ref"),
    (re.compile(r"auto_publish_queue"), "script_ref"),
    (re.compile(r"confirm_shorts_channel"), "script_ref"),
]


def main() -> int:
    entries: list[dict[str, str]] = []
    for path in CODE_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in SCAN_EXTS and path.suffix == "":
            continue
        if path.suffix.lower() not in SCAN_EXTS:
            continue
        if any(p in path.parts for p in SKIP_DIR_PARTS):
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = str(path.relative_to(CODE_ROOT))
        for i, line in enumerate(lines, start=1):
            for rx, role in PATH_PATTERNS:
                if rx.search(line):
                    token_path_found = ""
                    m = re.search(
                        r"(?:['\"])([^'\"]*(?:token|secrets|youtube)[^'\"]*)(?:['\"])|"
                        r"(/Users/[^\s'\"]+(?:token|secrets|youtube)[^\s'\"]*)",
                        line,
                    )
                    if m:
                        token_path_found = m.group(1) if m.group(1) else (m.group(2) or "")
                    issue = ""
                    proposed = "Use youtube_token_paths.resolve_long_form_upload_token for long uploads."
                    if "hennyhowie" in line or "192.168.12.90" in line or "/Air/" in line:
                        issue = "forbidden_host_reference"
                        proposed = "Remove; Pro-only ziweizhang paths only."
                    if ".secrets/youtube/token.json" in line and "legacy" not in role and "youtube_token_paths" not in line:
                        issue = issue or "possible_legacy_long_token_path"
                    if role == "long_token" and "data/youtube" not in line and "CODE_ROOT" not in line:
                        issue = issue or "verify_resolution"
                    entries.append(
                        {
                            "file": rel,
                            "line": str(i),
                            "token_path_found": token_path_found or line.strip()[:200],
                            "role": role,
                            "issue": issue,
                            "proposed_fix": proposed,
                        }
                    )
                    break

    CONTROL_LOGS.mkdir(parents=True, exist_ok=True)
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "code_root": str(CODE_ROOT),
        "entry_count": len(entries),
        "entries": entries,
    }
    OUT_JSON.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    md_lines = [
        "# YouTube token path audit v1",
        "",
        f"Generated: `{meta['generated_at']}`",
        f"CODE_ROOT: `{CODE_ROOT}`",
        "",
        "| file | line | role | issue | proposed_fix |",
        "|---|---:|---|---|---|",
    ]
    for e in entries:
        md_lines.append(
            f"| `{e['file']}` | {e['line']} | {e['role']} | {e.get('issue','')} | {e['proposed_fix']} |"
        )
    OUT_MD.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(f"OK: wrote {OUT_JSON} and {OUT_MD} ({len(entries)} hits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
