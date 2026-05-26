#!/usr/bin/env python3
"""Always Publish Scheduler v1 — plan-only CLI (fail-open)."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Bootstrap package imports when run as script or -m
_SCRIPTS = Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from always_publish.backlog_manager import (  # noqa: E402
    record_plan_only,
    refresh_backlog_index,
    save_publish_calendar_snapshot,
)
from always_publish.candidate_selector import (  # noqa: E402
    collect_long_candidates_by_level,
    collect_shorts_candidates_by_level,
    load_guards_plan,
)
from always_publish.fallback_policy import (  # noqa: E402
    describe_fallback,
    pick_long_level,
    pick_shorts_level,
)
from always_publish.paths import CONFIG_PATH, scripts_on_path  # noqa: E402
from always_publish.publish_calendar import build_calendar, is_special_long_day  # noqa: E402
from always_publish.quota_guard import check_quota  # noqa: E402
from always_publish.report_writer import write_status  # noqa: E402


def load_config() -> dict[str, Any]:
    try:
        if CONFIG_PATH.is_file():
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                return data
    except (OSError, json.JSONDecodeError):
        pass
    return {
        "long_daily_time": "10:00",
        "long_special_every_days": 3,
        "long_special_time": "16:00",
        "shorts_times": ["09:30", "13:30", "17:30", "21:30"],
        "privacy": "unlisted",
        "review_required": True,
        "fallback_enabled": True,
        "max_daily_long": 1,
        "max_daily_shorts": 4,
    }


def _apply_simulations(
    *,
    simulate_bad_clip: bool,
    simulate_davinci_down: bool,
    simulate_no_new_materials: bool,
    long_by_level: dict[str, list[dict[str, Any]]],
    shorts_by_level: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]], list[str]]:
    warnings: list[str] = []
    if simulate_no_new_materials:
        warnings.append("simulate_no_new_materials")
        return (
            {k: [] for k in long_by_level},
            {k: [] for k in shorts_by_level},
            warnings,
        )
    if simulate_bad_clip:
        warnings.append("simulate_bad_clip")
        for k in list(long_by_level.keys()):
            long_by_level[k] = [
                r for r in (long_by_level.get(k) or []) if "clean_real_sound" in str(r.get("path", "")).lower()
            ] or []
        for k in list(shorts_by_level.keys()):
            shorts_by_level[k] = []
    if simulate_davinci_down:
        warnings.append("simulate_davinci_down")
        long_by_level["L1_ready_nyc_long_clips"] = []
        long_by_level["L2_ready_other_long"] = []
    return long_by_level, shorts_by_level, warnings


def build_today_plan(
    cfg: dict[str, Any],
    *,
    dry_run: bool = True,
    on_date: date | None = None,
    simulate_bad_clip: bool = False,
    simulate_davinci_down: bool = False,
    simulate_no_new_materials: bool = False,
) -> dict[str, Any]:
    scripts_on_path()
    d = on_date or date.today()
    day = d.isoformat()
    warnings: list[str] = []
    blockers: list[str] = []

    guards = load_guards_plan()
    if guards.get("doctor_blocked"):
        blockers.append("doctor_gate_block_upload")
    if not guards.get("channel_guard_ok"):
        blockers.append("channel_guard_failed")
    if guards.get("long_emergency_active"):
        blockers.append("long_autopublish_emergency_active")
    if guards.get("shorts_global_lock"):
        blockers.append("shorts_global_lock_active")

    calendar = build_calendar(cfg, on_date=d)
    save_publish_calendar_snapshot(calendar)

    quota = check_quota(day, cfg)
    long_by_level = collect_long_candidates_by_level()
    shorts_by_level = collect_shorts_candidates_by_level()

    if dry_run and (simulate_bad_clip or simulate_davinci_down or simulate_no_new_materials):
        long_by_level, shorts_by_level, sim_w = _apply_simulations(
            simulate_bad_clip=simulate_bad_clip,
            simulate_davinci_down=simulate_davinci_down,
            simulate_no_new_materials=simulate_no_new_materials,
            long_by_level=long_by_level,
            shorts_by_level=shorts_by_level,
        )
        warnings.extend(sim_w)

    refresh_backlog_index(
        long_candidates=long_by_level.get("L1_ready_nyc_long_clips", [])
        + long_by_level.get("L2_ready_other_long", []),
        shorts_candidates=shorts_by_level.get("L1_ready_shorts_clips", []),
    )
    backlog_count = sum(len(v) for v in long_by_level.values()) + sum(len(v) for v in shorts_by_level.values())

    long_plan: dict[str, Any] | None = None
    today_long_ready = False
    allow_long = int(quota.get("allow_long") or 0)
    special = is_special_long_day(d, int(cfg.get("long_special_every_days") or 3))

    if allow_long > 0 and cfg.get("fallback_enabled", True):
        lvl, row = pick_long_level(long_by_level)
        fb = describe_fallback(lvl, kind="long")
        if row:
            long_plan = {
                **fb,
                "path": row.get("path"),
                "content_type": row.get("content_type"),
                "duration_sec": row.get("duration_sec"),
                "privacy": str(cfg.get("privacy") or "unlisted"),
                "review_required": bool(cfg.get("review_required", True)),
                "slot": "long_special" if special and lvl != "L6_no_material" else "long_daily",
                "planned_upload_blocked_by_guards": bool(blockers),
                "dry_run": dry_run,
            }
            today_long_ready = lvl != "L6_no_material" and not blockers
        else:
            long_plan = {**fb, "path": None, "dry_run": dry_run, "planned_upload_blocked_by_guards": bool(blockers)}
    elif allow_long <= 0:
        warnings.append("long_quota_exhausted")
        long_plan = {"fallback_level": "quota_exhausted", "path": None, "dry_run": dry_run}

    shorts_times = calendar.get("shorts_times") or ["09:30", "13:30", "17:30", "21:30"]
    need_shorts = min(int(quota.get("allow_shorts") or 0), int(cfg.get("max_daily_shorts") or 4), len(shorts_times))
    lvl_s, rows_s = pick_shorts_level(shorts_by_level, need=need_shorts or 4)
    shorts_plans: list[dict[str, Any]] = []
    for i, row in enumerate(rows_s[:4]):
        slot_time = shorts_times[i] if i < len(shorts_times) else ""
        shorts_plans.append(
            {
                **describe_fallback(lvl_s, kind="shorts"),
                "slot_index": i + 1,
                "slot_time": slot_time,
                "path": row.get("path"),
                "duration_sec": row.get("duration_sec"),
                "vertical": row.get("vertical"),
                "privacy": str(cfg.get("privacy") or "unlisted"),
                "review_required": bool(cfg.get("review_required", True)),
                "planned_upload_blocked_by_guards": bool(blockers) or guards.get("shorts_global_lock"),
                "dry_run": dry_run,
            }
        )
    while len(shorts_plans) < 4:
        i = len(shorts_plans)
        slot_time = shorts_times[i] if i < len(shorts_times) else ""
        shorts_plans.append(
            {
                **describe_fallback("L6_no_material", kind="shorts"),
                "slot_index": i + 1,
                "slot_time": slot_time,
                "path": None,
                "dry_run": dry_run,
                "planned_upload_blocked_by_guards": bool(blockers),
            }
        )

    today_shorts_count = sum(1 for sp in shorts_plans if sp.get("path"))
    if dry_run and today_long_ready and long_plan and long_plan.get("path"):
        record_plan_only(day, long_paths=[str(long_plan["path"])], shorts_paths=[])
    if dry_run:
        record_plan_only(
            day,
            long_paths=[],
            shorts_paths=[str(sp["path"]) for sp in shorts_plans if sp.get("path")],
        )

    payload: dict[str, Any] = {
        "ok": True,
        "version": "always_publish_scheduler_v1",
        "mode": "dry_run" if dry_run else "plan",
        "date": day,
        "dry_run": dry_run,
        "privacy": str(cfg.get("privacy") or "unlisted"),
        "guards": guards,
        "calendar": calendar,
        "quota": quota,
        "long_plan": long_plan,
        "shorts_plans": shorts_plans,
        "today_long_ready": today_long_ready,
        "today_shorts_count": today_shorts_count,
        "backlog_count": backlog_count,
        "blockers": blockers,
        "warnings": warnings,
        "special_long_day": special,
    }
    payload["status_paths"] = write_status(payload)
    return payload


def get_status_snapshot() -> dict[str, Any]:
    """Lightweight status for API (re-run plan if missing)."""
    from always_publish.daily_delivery_state import load_delivery_state
    from always_publish.paths import STATUS_JSON_PATH

    delivery = load_delivery_state()
    try:
        if STATUS_JSON_PATH.is_file():
            data = json.loads(STATUS_JSON_PATH.read_text(encoding="utf-8", errors="replace"))
            if isinstance(data, dict):
                data["delivery"] = delivery
                data["delivery_complete"] = bool(delivery.get("delivery_complete"))
                data["long_remaining"] = int(delivery.get("long_remaining") or 0)
                data["shorts_remaining"] = int(delivery.get("shorts_remaining") or 0)
                data["long_uploaded"] = int(delivery.get("long_uploaded") or 0)
                data["shorts_uploaded"] = int(delivery.get("shorts_uploaded") or 0)
                data["hard_block_reason"] = delivery.get("hard_block_reason") or ""
                data["last_attempt_at"] = delivery.get("last_attempt_at") or ""
                data["next_retry_eta_minutes"] = int(delivery.get("next_retry_eta_minutes") or 0)
                return data
    except (OSError, json.JSONDecodeError):
        pass
    plan = build_today_plan(load_config(), dry_run=True)
    plan["delivery"] = delivery
    plan["delivery_complete"] = bool(delivery.get("delivery_complete"))
    plan["long_remaining"] = int(delivery.get("long_remaining") or 0)
    plan["shorts_remaining"] = int(delivery.get("shorts_remaining") or 0)
    plan["long_uploaded"] = int(delivery.get("long_uploaded") or 0)
    plan["shorts_uploaded"] = int(delivery.get("shorts_uploaded") or 0)
    plan["hard_block_reason"] = delivery.get("hard_block_reason") or ""
    plan["last_attempt_at"] = delivery.get("last_attempt_at") or ""
    plan["next_retry_eta_minutes"] = int(delivery.get("next_retry_eta_minutes") or 0)
    return plan


def refresh_backlog() -> dict[str, Any]:
    long_by = collect_long_candidates_by_level()
    shorts_by = collect_shorts_candidates_by_level()
    doc = refresh_backlog_index(
        long_candidates=long_by.get("L1_ready_nyc_long_clips", [])
        + long_by.get("L2_ready_other_long", []),
        shorts_candidates=shorts_by.get("L1_ready_shorts_clips", []),
    )
    return {"ok": True, "backlog": doc}


def build_missing(*, dry_run: bool = True) -> dict[str, Any]:
    """Stage plan + optional subprocess to create_next_long / shorts job — never uploads."""
    import subprocess

    plan = build_today_plan(load_config(), dry_run=dry_run)
    results: dict[str, Any] = {"plan": plan, "build_steps": []}
    guards = plan.get("guards") or {}
    if guards.get("doctor_blocked") or guards.get("long_emergency_active"):
        results["build_steps"].append({"step": "long_build_skipped", "reason": "guards_blocked"})
    elif plan.get("long_plan") and not plan["long_plan"].get("path"):
        sv = Path.home() / "StateVerge"
        script = sv / "scripts" / "nyc_auto" / "create_next_long_master.py"
        if script.is_file() and not dry_run:
            try:
                r = subprocess.run(
                    [sys.executable, str(script), "--dry-run"],
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    cwd=str(sv),
                )
                results["build_steps"].append(
                    {
                        "step": "create_next_long_master",
                        "returncode": r.returncode,
                        "stdout_tail": (r.stdout or "")[-800:],
                    }
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                results["build_steps"].append({"step": "create_next_long_master", "error": repr(exc)})
        else:
            results["build_steps"].append(
                {"step": "create_next_long_master", "skipped": True, "dry_run": dry_run}
            )
    return results


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Always Publish Scheduler v1")
    p.add_argument("--dry-run", action="store_true", help="Plan only; no upload")
    p.add_argument("--dry-run-today", action="store_true", help="Today's plan (default)")
    p.add_argument("--simulate-bad-clip", action="store_true")
    p.add_argument("--simulate-davinci-down", action="store_true")
    p.add_argument("--simulate-no-new-materials", action="store_true")
    p.add_argument("--refresh-backlog", action="store_true")
    p.add_argument("--json", action="store_true", help="Print full JSON plan")
    args = p.parse_args(argv)

    if args.refresh_backlog:
        out = refresh_backlog()
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    dry = True
    if not args.dry_run and not args.dry_run_today:
        args.dry_run_today = True

    plan = build_today_plan(
        load_config(),
        dry_run=dry,
        simulate_bad_clip=bool(args.simulate_bad_clip),
        simulate_davinci_down=bool(args.simulate_davinci_down),
        simulate_no_new_materials=bool(args.simulate_no_new_materials),
    )
    if args.json:
        print(json.dumps(plan, indent=2, ensure_ascii=False))
    else:
        lp = plan.get("long_plan") or {}
        print(
            f"always_publish date={plan.get('date')} long_ready={plan.get('today_long_ready')} "
            f"shorts={plan.get('today_shorts_count')}/4 long_fb={lp.get('fallback_level')} "
            f"blockers={len(plan.get('blockers') or [])}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
