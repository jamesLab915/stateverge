#!/usr/bin/env python3
"""Diagnose Controlled OpenAI Agent Intelligence v1."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
SCRIPTS = STATEVERGE / "scripts"
CONTROL = Path.home() / "StateVerge_Control_Center"
AI_CLIENT = STATEVERGE / "scripts" / "ai" / "ai_client.py"
POLICY = STATEVERGE / "config" / "ai_agent_policy.json"
README = STATEVERGE / ".secrets" / "openai" / "README.md"
GIT = STATEVERGE / ".gitignore"
META = STATEVERGE / "scripts" / "nyc_auto" / "metadata_generator.py"
OPS = STATEVERGE / "scripts" / "agent_ops_monitor.py"
CRE = STATEVERGE / "scripts" / "agent_creative_ops.py"
MAIN = CONTROL / "backend" / "main.py"
PAGE = CONTROL / "frontend" / "src" / "app" / "server-mode" / "page.tsx"
ALLOWED = CONTROL / "remote_agent" / "allowed_tasks.json"
MOBILE = CONTROL / "remote_agent" / "mobile_command_agent.py"
OUTJ = CONTROL / "logs" / "ai_agent_intelligence_diagnose.json"
OUTM = CONTROL / "logs" / "ai_agent_intelligence_diagnose.md"


def _has(p: Path, s: str) -> bool:
    return p.is_file() and s in p.read_text(encoding="utf-8", errors="replace")


def _diagnose_note_from_key_and_api(key_status: dict[str, Any], api_test: dict[str, Any] | None) -> str:
    if key_status.get("api_key_present"):
        if api_test and api_test.get("ok"):
            return "AI_KEY_AND_API_TEST_OK"
        return "API_KEY_PRESENT_BUT_TEST_FAILED"
    return "AI_KEY_MISSING_BUT_SYSTEM_READY"


def main() -> int:
    rows: dict[str, Any] = {"generated_at": datetime.now(timezone.utc).isoformat(), "checks": {}}
    rows["checks"]["ai_client"] = AI_CLIENT.is_file()
    rows["checks"]["ai_policy"] = POLICY.is_file()
    rows["checks"]["openai_readme"] = README.is_file()
    rows["checks"]["gitignore_secrets"] = GIT.is_file() and ".secrets/" in GIT.read_text(
        encoding="utf-8", errors="replace"
    )
    rows["checks"]["metadata_uses_controlled"] = _has(META, "from ai.ai_client import ai_request")
    rows["checks"]["ops_uses_ai"] = _has(OPS, "from ai.ai_client import ai_request")
    rows["checks"]["creative_uses_ai"] = _has(CRE, "from ai.ai_client import ai_request")
    rows["checks"]["backend_ai_status"] = _has(MAIN, "/api/agent/ai/status")
    rows["checks"]["backend_ai_test"] = _has(MAIN, "/api/agent/ai/test")
    rows["checks"]["frontend_ai_panel"] = _has(PAGE, "AI Intelligence")
    if ALLOWED.is_file():
        t = ALLOWED.read_text(encoding="utf-8", errors="replace")
        rows["checks"]["mobile_diagnose_ai"] = "diagnose_ai_agent_safe" in t
    else:
        rows["checks"]["mobile_diagnose_ai"] = False
    rows["checks"]["mobile_test_ai_handler"] = _has(MOBILE, "def _execute_test_ai_safe_call")
    ok = all(
        rows["checks"].get(x)
        for x in (
            "ai_client",
            "ai_policy",
            "openai_readme",
            "metadata_uses_controlled",
            "ops_uses_ai",
            "creative_uses_ai",
            "backend_ai_status",
            "backend_ai_test",
            "frontend_ai_panel",
            "mobile_diagnose_ai",
            "mobile_test_ai_handler",
        )
    )
    rows["ok"] = ok

    sp = str(SCRIPTS)
    if sp not in sys.path:
        sys.path.insert(0, sp)
    from ai.ai_client import ai_request, get_openai_api_key_status

    key_status = get_openai_api_key_status()
    rows["api_key_present"] = key_status["api_key_present"]
    rows["api_key_source"] = key_status["api_key_source"]
    rows["key_file_exists"] = key_status["key_file_exists"]
    rows["key_file_size_gt_20"] = key_status["key_file_size_gt_20"]
    rows["key_length_gt_20"] = key_status["key_length_gt_20"]

    api_test: dict[str, Any] | None = None
    if key_status["api_key_present"]:
        api_test = ai_request(
            "ops_report_summary",
            '{"ping":true,"note":"diagnose_safe"}',
            system_hint='Return JSON {"ok":true} only.',
        )
        rows["api_test_ok"] = bool(api_test.get("ok"))
        rows["api_test_error_type"] = str(api_test.get("error_type") or "")

    note = _diagnose_note_from_key_and_api(key_status, api_test)
    rows["diagnose_note"] = note

    OUTJ.parent.mkdir(parents=True, exist_ok=True)
    OUTJ.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    md_lines = [
        "# ai_agent_intelligence diagnose",
        "",
        f"ok={ok}",
        "",
        "## key (no secrets printed)",
        "",
        f"- api_key_present: {key_status['api_key_present']}",
        f"- api_key_source: {key_status['api_key_source']}",
        f"- key_file_exists: {key_status['key_file_exists']}",
        f"- key_file_size_gt_20: {key_status['key_file_size_gt_20']}",
        f"- key_length_gt_20: {key_status['key_length_gt_20']}",
        "",
        f"**diagnose_note**: `{note}`",
        "",
    ]
    OUTM.write_text("\n".join(md_lines), encoding="utf-8")
    out_print = {
        "ok": ok,
        "api_key_present": key_status["api_key_present"],
        "api_key_source": key_status["api_key_source"],
        "key_file_exists": key_status["key_file_exists"],
        "key_file_size_gt_20": key_status["key_file_size_gt_20"],
        "key_length_gt_20": key_status["key_length_gt_20"],
        "diagnose_note": note,
    }
    if api_test is not None:
        out_print["api_test_ok"] = bool(api_test.get("ok"))
        et = api_test.get("error_type")
        if et:
            out_print["api_test_error_type"] = et
    print(json.dumps(out_print, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
