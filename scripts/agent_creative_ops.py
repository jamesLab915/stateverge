#!/usr/bin/env python3
"""
StateVerge Agent Creative Ops v1 — diagnose / propose / report.
apply-safe-upgrade: v1 does not auto-edit source files; runs validation scripts and writes plan/result only.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATEVERGE = Path.home() / "StateVerge"
CONTROL_CENTER = Path.home() / "StateVerge_Control_Center"
PERMS_PATH = STATEVERGE / "config" / "agent_permissions.json"
REPORTS = CONTROL_CENTER / "logs" / "agent_upgrade_reports"
BACKUPS = CONTROL_CENTER / "logs" / "agent_upgrade_backups"
LATEST_SUMMARY = CONTROL_CENTER / "logs" / "agent_creative_ops_latest.json"


def _venv_py() -> str:
    p = STATEVERGE / ".venv_audio" / "bin" / "python3"
    return str(p) if p.is_file() else sys.executable


def _load_perms() -> dict[str, Any]:
    if not PERMS_PATH.is_file():
        return {}
    try:
        return json.loads(PERMS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _run_script(rel: str, extra: list[str], *, timeout: float = 600) -> dict[str, Any]:
    script = STATEVERGE / rel
    if not script.is_file():
        return {"skipped": True, "path": str(script)}
    r = subprocess.run(
        [_venv_py(), str(script), *extra],
        cwd=str(STATEVERGE),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "returncode": r.returncode,
        "stdout_tail": (r.stdout or "")[-6000:],
        "stderr_tail": (r.stderr or "")[-6000:],
    }


def _py_compile_paths(paths: list[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for p in paths:
        if not p.is_file():
            out[str(p)] = {"ok": False, "error": "missing"}
            continue
        r = subprocess.run(
            [sys.executable, "-m", "py_compile", str(p)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        out[str(p)] = {"ok": r.returncode == 0, "stderr": (r.stderr or "")[-1500:]}
    return out


def cmd_diagnose(target: str) -> dict[str, Any]:
    perms = _load_perms()
    res: dict[str, Any] = {"permissions": perms.get("permissions"), "scripts": {}}
    scripts = [
        "scripts/diagnose_auto_publish_v2.py",
        "scripts/diagnose_shorts_autopublish.py",
        "scripts/diagnose_metadata_generation.py",
        "scripts/diagnose_long_audio_policy.py",
        "scripts/diagnose_real_sound_gate.py",
    ]
    if target in ("all", "youtube_ops", "long"):
        res["scripts"]["auto_publish_v2"] = _run_script("scripts/diagnose_auto_publish_v2.py", [])
    if target in ("all", "shorts"):
        res["scripts"]["shorts_autopublish"] = _run_script("scripts/diagnose_shorts_autopublish.py", [])
    if target in ("all", "metadata"):
        res["scripts"]["metadata"] = _run_script("scripts/diagnose_metadata_generation.py", [])
    if target in ("all", "audio"):
        res["scripts"]["long_audio"] = _run_script("scripts/diagnose_long_audio_policy.py", [])
        res["scripts"]["real_sound"] = _run_script("scripts/diagnose_real_sound_gate.py", [])
    if target == "all":
        for s in scripts:
            key = s.replace("scripts/", "").replace(".py", "")
            if key not in str(res["scripts"]):
                res["scripts"][key] = _run_script(s, [])
    res["py_compile_backend"] = _py_compile_paths([CONTROL_CENTER / "backend" / "main.py"])
    return res


def cmd_propose(target: str, ts: str) -> dict[str, Any]:
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{ts}_proposal.md"
    lines = [
        f"# Creative Ops Proposal ({target})",
        "",
        "- Host: PRO_ONLY",
        "- No token edits, no uploads, no bypass guards.",
        "",
        "## Suggested next steps",
        "",
        "1. Run `diagnose_*` scripts and review JSON/Markdown outputs under `StateVerge_Control_Center/logs/`.",
        "2. For Shorts: tune highlight weights in `shorts_cut_upload_job.py` (manual edit with review).",
        "3. For Long: confirm chronological assembly + duration gates in NYC automation scripts.",
        "4. For metadata: adjust templates under `StateVerge` topic / publish pack paths after dry-run.",
        "",
    ]
    try:
        scripts_dir = STATEVERGE / "scripts"
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from ai.ai_client import ai_request

        diag = cmd_diagnose(target)
        brief = json.dumps({"target": target, "diagnose_summary_keys": list((diag or {}).keys())}, default=str)[:8000]
        air = ai_request(
            "upgrade_proposal",
            brief,
            system_hint="Return JSON with keys proposal_md_bullets (array of strings). No credentials.",
        )
        lines.append("## AI-assisted proposal (controlled client)")
        lines.append("")
        if air.get("ok") and isinstance(air.get("data"), dict):
            bullets = air["data"].get("proposal_md_bullets")
            if isinstance(bullets, list):
                for b in bullets[:24]:
                    lines.append(f"- {b}")
            else:
                lines.append(str(air.get("data"))[:2000])
        else:
            lines.append("_AI unavailable or budgeted out; using local template only._")
        lines.append("")
    except Exception as exc:  # noqa: BLE001
        lines.append("## AI-assisted proposal")
        lines.append("")
        lines.append(f"_AI path skipped: {type(exc).__name__}_")
        lines.append("")
    body = "\n".join(lines)
    path.write_text(body, encoding="utf-8")
    return {"proposal_path": str(path)}


def cmd_apply_safe(target: str, allow_code_edit: bool, ts: str) -> dict[str, Any]:
    """v1: no filesystem code edits; validation + plan/result only."""
    REPORTS.mkdir(parents=True, exist_ok=True)
    plan = REPORTS / f"{ts}_plan.md"
    result = REPORTS / f"{ts}_result.md"
    plan.write_text(
        "\n".join(
            [
                "# apply-safe-upgrade plan (v1)",
                "",
                f"- target: {target}",
                f"- allow_code_edit: {allow_code_edit}",
                "- **Automated source edits: DISABLED in v1** (manual PR / future version).",
                "",
            ]
        ),
        encoding="utf-8",
    )
    diag = cmd_diagnose(target)
    dry: dict[str, Any] = {}
    if target in ("all", "shorts"):
        dry["shorts"] = _run_script(
            "scripts/nyc_auto/auto_publish_queue_shorts.py",
            ["--dry-run", "--max-count", "1"],
            timeout=900,
        )
    if target in ("all", "long"):
        dry["long"] = _run_script(
            "scripts/nyc_auto/auto_publish_queue.py",
            ["--dry-run", "--max-count", "1", "--audio-mode", "auto"],
            timeout=900,
        )
    result.write_text(
        json.dumps({"diagnose": diag, "dry_runs": dry}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return {"plan": str(plan), "result": str(result), "dry_runs": dry}


def cmd_report() -> dict[str, Any]:
    if not REPORTS.is_dir():
        return {"reports": []}
    paths = sorted(REPORTS.glob("*_result.md"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]
    return {"latest_results": [str(p) for p in paths]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("diagnose", "propose", "apply-safe-upgrade", "report"), required=True)
    ap.add_argument("--target", default="all")
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--write-report", action="store_true", default=False)
    ap.add_argument("--allow-code-edit", action=argparse.BooleanOptionalAction, default=False)
    ap.add_argument("--allow-upload", action="store_true", default=False)
    ap.add_argument("--max-files", type=int, default=10)
    ap.add_argument("--backup-before-edit", action=argparse.BooleanOptionalAction, default=True)
    args = ap.parse_args()
    if args.allow_upload:
        print(json.dumps({"error": "allow_upload forbidden for agent_creative_ops v1"}, indent=2), file=sys.stderr)
        return 2
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out: dict[str, Any] = {"mode": args.mode, "target": args.target, "ts": ts}
    if args.mode == "diagnose":
        out["result"] = cmd_diagnose(args.target)
    elif args.mode == "propose":
        out["result"] = cmd_propose(args.target, ts)
    elif args.mode == "apply-safe-upgrade":
        if not args.allow_code_edit:
            out["note"] = "allow_code_edit=false: v1 still performs validation/dry-run only (no file edits)."
        out["result"] = cmd_apply_safe(args.target, args.allow_code_edit, ts)
    else:
        out["result"] = cmd_report()
    if args.write_report or args.mode != "report":
        LATEST_SUMMARY.parent.mkdir(parents=True, exist_ok=True)
        LATEST_SUMMARY.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "summary_path": str(LATEST_SUMMARY)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
