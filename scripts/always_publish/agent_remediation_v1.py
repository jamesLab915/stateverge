#!/usr/bin/env python3
"""Agent Remediation Bridge v1 — plan-only safe actions; never bypass guards or upload."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from always_publish.candidate_selector import emergency_active, load_guards_plan  # noqa: E402
from always_publish.paths import DOCTOR_REPORT_PATHS, scripts_on_path  # noqa: E402
from always_publish.scheduler import (  # noqa: E402
    build_missing,
    build_today_plan,
    get_status_snapshot,
    load_config,
    refresh_backlog,
)

VERSION = "agent_remediation_v1"
REPORT_JSON = Path("/Volumes/SV_CACHE/logs/agent_remediation_v1_latest.json")
REPORT_JSON_FALLBACK = Path.home() / "StateVerge_Control_Center" / "logs" / "agent_remediation_v1_latest.json"

ALLOWED_ACTION_IDS = frozenset(
    {
        "refresh_backlog",
        "dry_run_today",
        "build_missing_long_plan",
        "build_missing_shorts_plan",
        "report_blockers",
        "suggest_bootstrap_nyc_autopublish",
    }
)

NYC_LAUNCH_LABEL = "com.stateverge.nyc.autopublish"
SHORTS_LAUNCH_LABEL = "com.stateverge.shorts.autopublish"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _launchctl_state(label: str) -> dict[str, Any]:
    rc = 1
    out = ""
    err = ""
    try:
        r = subprocess.run(
            ["launchctl", "print", f"gui/{os.getuid()}/{label}"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        rc = int(r.returncode)
        out = r.stdout or ""
        err = r.stderr or ""
    except (OSError, subprocess.TimeoutExpired) as exc:
        err = repr(exc)
    loaded = rc == 0 and "program =" in out
    plist_path = ""
    m = re.search(r"path = ([^;]+);", out)
    if m:
        plist_path = m.group(1).strip()
    return {
        "label": label,
        "launchctl_rc": rc,
        "loaded": loaded,
        "plist_path": plist_path,
        "stderr_tail": err[-1500:],
    }


def _load_doctor_report() -> dict[str, Any]:
    for p in DOCTOR_REPORT_PATHS:
        try:
            if p.is_file():
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if isinstance(data, dict):
                    return {"path": str(p), "report": data}
        except (OSError, json.JSONDecodeError):
            continue
    return {"path": None, "report": {}}


def gather_context() -> dict[str, Any]:
    scripts_on_path()
    ap_status = get_status_snapshot()
    guards = load_guards_plan()
    em_active, em_detail = emergency_active()
    doctor = _load_doctor_report()
    launchd = {
        "nyc": _launchctl_state(NYC_LAUNCH_LABEL),
        "shorts": _launchctl_state(SHORTS_LAUNCH_LABEL),
    }
    return {
        "generated_at": _utc_iso(),
        "version": VERSION,
        "always_publish_status": ap_status,
        "guards": guards,
        "doctor": doctor,
        "long_emergency": {"active": em_active, "detail": em_detail},
        "launchd": launchd,
    }


def _action(action_id: str, *, reason: str, priority: int, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if action_id not in ALLOWED_ACTION_IDS:
        raise ValueError(f"forbidden_action:{action_id}")
    return {
        "id": action_id,
        "reason": reason,
        "priority": priority,
        "params": params or {},
        "safe_to_apply": action_id
        in ("refresh_backlog", "dry_run_today", "build_missing_long_plan", "build_missing_shorts_plan"),
    }


def build_action_plan(ctx: dict[str, Any]) -> dict[str, Any]:
    ap = ctx.get("always_publish_status") if isinstance(ctx.get("always_publish_status"), dict) else {}
    guards = ctx.get("guards") if isinstance(ctx.get("guards"), dict) else {}
    launchd = ctx.get("launchd") if isinstance(ctx.get("launchd"), dict) else {}
    em = ctx.get("long_emergency") if isinstance(ctx.get("long_emergency"), dict) else {}

    blockers: list[str] = []
    if guards.get("doctor_blocked"):
        blockers.append("doctor_gate_block_upload")
    if not guards.get("channel_guard_ok", True):
        blockers.append("channel_guard_failed")
    if guards.get("long_emergency_active") or em.get("active"):
        blockers.append("long_autopublish_emergency_active")
    if guards.get("shorts_global_lock"):
        blockers.append("shorts_global_lock_active")

    nyc_ld = launchd.get("nyc") if isinstance(launchd.get("nyc"), dict) else {}
    shorts_ld = launchd.get("shorts") if isinstance(launchd.get("shorts"), dict) else {}
    if not nyc_ld.get("loaded"):
        blockers.append("launchd_nyc_autopublish_not_loaded")
    if not shorts_ld.get("loaded"):
        blockers.append("launchd_shorts_autopublish_not_loaded")

    for b in ap.get("blockers") or []:
        if isinstance(b, str) and b not in blockers:
            blockers.append(b)

    actions: list[dict[str, Any]] = []

    if blockers:
        actions.append(
            _action(
                "report_blockers",
                reason="human_must_review_guards_or_emergency",
                priority=1,
                params={"blockers": blockers},
            )
        )

    actions.append(
        _action("refresh_backlog", reason="keep_backlog_index_current", priority=20)
    )
    actions.append(
        _action("dry_run_today", reason="refresh_today_plan_without_upload", priority=25)
    )

    long_plan = ap.get("long_plan") if isinstance(ap.get("long_plan"), dict) else {}
    long_guarded = bool(guards.get("doctor_blocked")) or bool(
        guards.get("long_emergency_active") or em.get("active")
    )
    if not long_plan.get("path") and not long_guarded:
        actions.append(
            _action(
                "build_missing_long_plan",
                reason="long_slot_empty_guards_ok_plan_only",
                priority=30,
            )
        )

    shorts_lock = bool(guards.get("shorts_global_lock"))
    shorts_count = int(ap.get("today_shorts_count") or 0)
    need_shorts = int((ap.get("quota") or {}).get("allow_shorts") or 0) if isinstance(ap.get("quota"), dict) else 0
    if not shorts_lock and shorts_count < min(4, max(need_shorts, 1)):
        actions.append(
            _action(
                "build_missing_shorts_plan",
                reason="shorts_slots_underfilled_plan_only",
                priority=35,
            )
        )

    if not nyc_ld.get("loaded"):
        bootstrap_cmd = (
            "launchctl bootstrap gui/$(id -u) "
            "~/StateVerge_Control_Center/launchagents/com.stateverge.nyc.autopublish.plist"
        )
        actions.append(
            _action(
                "suggest_bootstrap_nyc_autopublish",
                reason="nyc_launchagent_not_loaded",
                priority=40,
                params={"command": bootstrap_cmd, "run_requires": ["--apply-safe", "--allow-bootstrap"]},
            )
        )

    actions.sort(key=lambda a: int(a.get("priority") or 99))
    return {
        "version": VERSION,
        "generated_at": ctx.get("generated_at") or _utc_iso(),
        "blockers": blockers,
        "actions": actions,
        "constraints": {
            "no_bypass_doctor_gate": True,
            "no_bypass_channel_guard": True,
            "no_bypass_long_emergency": True,
            "no_auto_public": True,
            "no_token_changes": True,
            "no_delete_source_material": True,
            "no_new_launchd": True,
            "shorts_global_lock_respected": True,
        },
        "context_summary": {
            "today_long_ready": ap.get("today_long_ready"),
            "today_shorts_count": ap.get("today_shorts_count"),
            "backlog_count": ap.get("backlog_count"),
            "nyc_launchd_loaded": nyc_ld.get("loaded"),
            "shorts_launchd_loaded": shorts_ld.get("loaded"),
        },
    }


def _write_report(plan: dict[str, Any], *, applied: dict[str, Any] | None = None) -> str:
    payload = {**plan, "applied": applied or {}}
    path = REPORT_JSON
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        path = REPORT_JSON_FALLBACK
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def apply_safe_actions(
    plan: dict[str, Any],
    *,
    allow_bootstrap: bool = False,
) -> dict[str, Any]:
    """Only refresh backlog, dry-run plan, plan-only build_missing, write report — never upload."""
    applied: dict[str, Any] = {"steps": [], "skipped": []}
    cfg = load_config()
    for act in plan.get("actions") or []:
        if not isinstance(act, dict):
            continue
        aid = str(act.get("id") or "")
        if aid == "suggest_bootstrap_nyc_autopublish":
            cmd = (act.get("params") or {}).get("command")
            applied["steps"].append({"id": aid, "printed_command": cmd, "executed": False})
            if allow_bootstrap and cmd:
                applied["skipped"].append(
                    {"id": aid, "reason": "bootstrap_not_auto_run_v1_print_only_even_with_flag"}
                )
            continue
        if aid == "report_blockers":
            applied["steps"].append({"id": aid, "blockers": plan.get("blockers")})
            continue
        if aid == "refresh_backlog":
            try:
                out = refresh_backlog()
                applied["steps"].append({"id": aid, "ok": True, "result": out})
            except Exception as exc:  # noqa: BLE001
                applied["steps"].append({"id": aid, "ok": False, "error": repr(exc)})
            continue
        if aid == "dry_run_today":
            try:
                out = build_today_plan(cfg, dry_run=True)
                applied["steps"].append({"id": aid, "ok": True, "plan_date": out.get("date")})
            except Exception as exc:  # noqa: BLE001
                applied["steps"].append({"id": aid, "ok": False, "error": repr(exc)})
            continue
        if aid == "build_missing_long_plan":
            try:
                out = build_missing(dry_run=True)
                applied["steps"].append({"id": aid, "ok": True, "build_steps": out.get("build_steps")})
            except Exception as exc:  # noqa: BLE001
                applied["steps"].append({"id": aid, "ok": False, "error": repr(exc)})
            continue
        if aid == "build_missing_shorts_plan":
            try:
                out = build_today_plan(cfg, dry_run=True)
                applied["steps"].append(
                    {
                        "id": aid,
                        "ok": True,
                        "shorts_plans_count": len(out.get("shorts_plans") or []),
                    }
                )
            except Exception as exc:  # noqa: BLE001
                applied["steps"].append({"id": aid, "ok": False, "error": repr(exc)})
            continue
        applied["skipped"].append({"id": aid, "reason": "not_in_apply_safe_set"})

    report_path = _write_report(plan, applied=applied)
    applied["report_path"] = report_path
    return applied


def run_scan(*, apply_safe: bool = False, allow_bootstrap: bool = False) -> dict[str, Any]:
    ctx = gather_context()
    plan = build_action_plan(ctx)
    plan["context"] = {
        "guards": ctx.get("guards"),
        "launchd": ctx.get("launchd"),
        "long_emergency": ctx.get("long_emergency"),
        "doctor_path": (ctx.get("doctor") or {}).get("path"),
    }
    if apply_safe:
        plan["applied"] = apply_safe_actions(plan, allow_bootstrap=allow_bootstrap)
    else:
        plan["report_path"] = _write_report(plan)
    plan["ok"] = True
    return plan


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Agent Remediation Bridge v1")
    p.add_argument("--scan-only", action="store_true", help="Build action plan JSON only")
    p.add_argument("--apply-safe", action="store_true", help="Run safe ops only (no upload)")
    p.add_argument(
        "--allow-bootstrap",
        action="store_true",
        help="With --apply-safe: acknowledge bootstrap hint (still print-only in v1)",
    )
    p.add_argument("--json", action="store_true", default=True)
    args = p.parse_args(argv)

    if not args.scan_only and not args.apply_safe:
        args.scan_only = True

    out = run_scan(apply_safe=bool(args.apply_safe), allow_bootstrap=bool(args.allow_bootstrap))
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
