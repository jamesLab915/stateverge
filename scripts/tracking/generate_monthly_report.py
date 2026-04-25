#!/usr/bin/env python3
"""Generate ``docs/tracking/monthly_reports/YYYY-MM-summary.md`` (stdlib only).

Consolidates IRS + EB1/NIW evidence from every tracking CSV / Markdown source
without modifying production code. Safe to run repeatedly: each invocation
overwrites the per-month report file.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

_PR = Path(__file__).resolve().parent
if str(_PR) not in sys.path:
    sys.path.insert(0, str(_PR))
from _paths import REPO_ROOT, TRACK  # noqa: E402

RESEARCH = TRACK / "research_log.md"
MILESTONES = TRACK / "dev_milestones.md"
EXPENSE_CSV = TRACK / "irs_expense_log.csv"
EMAIL_SUB_CSV = TRACK / "email_subscription_log.csv"
GITHUB_CSV = TRACK / "github_activity_log.csv"
DEV_TIME_CSV = TRACK / "dev_time_summary.csv"
PROJECT_CSV = TRACK / "project_progress.csv"
TOOL_USAGE_CSV = TRACK / "tool_usage_stats.csv"
AUTO_EVENTS_CSV = TRACK / "auto_tracking_events.csv"
TEMPLATE = TRACK / "monthly_summary_template.md"
OUT_DIR = TRACK / "monthly_reports"


def _month_valid(s: str) -> bool:
    return bool(re.match(r"^\d{4}-\d{2}$", s))


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    try:
        with path.open("r", encoding="utf-8", newline="") as f:
            return [{k: (v or "") for k, v in row.items()} for row in csv.DictReader(f)]
    except OSError:
        return []


def _filter_rows_by_month(rows: list[dict[str, str]], ym: str, key: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for row in rows:
        v = (row.get(key) or "").strip()
        if v and v[:7] == ym:
            out.append(row)
    return out


def _safe_float(v: str) -> float:
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _safe_int(v: str) -> int:
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0


def _extract_research_sections(text: str, ym: str) -> list[str]:
    parts: list[str] = []
    pat = re.compile(rf"^##\s+({re.escape(ym)}-\d{{2}})\s*-\s*(.+)$", re.MULTILINE)
    matches = list(pat.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        parts.append(text[start:end].strip())
    return parts


def _milestone_table_for_month(text: str, ym: str) -> str:
    lines = text.splitlines()
    header = ""
    sep = ""
    body: list[str] = []
    for i, ln in enumerate(lines):
        if ln.startswith("| Date |"):
            header = ln
            if i + 1 < len(lines) and re.match(r"^\|[-—| ]+\|", lines[i + 1]):
                sep = lines[i + 1]
            break
    for ln in lines:
        if not ln.startswith("|"):
            continue
        if ln.startswith(f"| {ym}-"):
            body.append(ln)
    if not body:
        return ""
    parts = [header, sep] if header and sep else ([header] if header else [])
    return "\n".join([p for p in parts if p] + body)


def _section_irs(ym: str, *, expenses: list[dict[str, str]], emails: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 1. IRS Evidence", ""]
    if not expenses and not emails:
        out.append("_(no expenses or subscription emails recorded for this month — add receipts via `add_expense.py` or export billing emails to .eml.)_")
        out.append("")
        out.append("**Missing:** subscription/receipt evidence.")
        return out

    if emails:
        out.append("### 1.1 Subscription / billing emails")
        out.append("| Date | Vendor | Amount | Currency | Subscription | Period | Source |")
        out.append("|------|--------|-------:|----------|--------------|--------|--------|")
        for r in emails:
            out.append(
                f"| {r.get('Date','')} | {r.get('Vendor','')} | "
                f"{r.get('Amount','')} | {r.get('Currency','')} | "
                f"{r.get('Subscription Name','')} | {r.get('Billing Period','')} | "
                f"{r.get('Email Source','')} |"
            )
        out.append("")
    else:
        out.append("_No subscription emails parsed this month — run `python scripts/tracking/email_subscription_parser.py --eml-dir ~/Downloads`._")
        out.append("")

    if expenses:
        out.append("### 1.2 Expense ledger")
        total = sum(_safe_float(r.get("Amount", "")) for r in expenses)
        out.append(f"_Total recorded: **{total:.2f}** (sum of `Amount` column; mixed currency not normalised — see CPA)._")
        out.append("")
        out.append("| Date | Vendor | Amount | Tool/Asset | Receipt | Business Purpose |")
        out.append("|------|--------|-------:|-----------|---------|------------------|")
        for r in expenses:
            out.append(
                f"| {r.get('Date','')} | {r.get('Vendor','')} | "
                f"{r.get('Amount','')} {r.get('Currency','')} | "
                f"{r.get('Tool/Asset','') or r.get('Tool','')} | "
                f"{r.get('Receipt Path','') or r.get('Receipt','') or '_missing_'} | "
                f"{r.get('Business Purpose','') or r.get('Description','')} |"
            )
        out.append("")
        missing = [r for r in expenses if not (r.get("Receipt Path") or r.get("Receipt"))]
        if missing:
            out.append(f"**Missing receipts:** {len(missing)} row(s) without `Receipt Path`.")
            out.append("")
    else:
        out.append("_No expense rows yet — add via `python scripts/tracking/add_expense.py ...`._")
        out.append("")
    out.append("> Disclaimer: This is **not** tax advice. Confirm with a licensed CPA.")
    out.append("")
    return out


def _section_eb1(
    ym: str,
    *,
    gh_rows: list[dict[str, str]],
    auto_rows: list[dict[str, str]],
    research_sections: list[str],
    milestone_table: str,
) -> list[str]:
    out: list[str] = ["## 2. EB1 / NIW Evidence", ""]
    high_gh = [r for r in gh_rows if (r.get("Evidence Value") or "").lower() == "high"]
    if not (gh_rows or auto_rows or research_sections or milestone_table):
        out.append("_(no R&D evidence recorded for this month yet)_")
        out.append("")
        return out

    if research_sections:
        out.append("### 2.1 Research log entries")
        for s in research_sections:
            out.append(s)
            out.append("")
    if milestone_table:
        out.append("### 2.2 Milestones")
        out.append(milestone_table)
        out.append("")
    if gh_rows:
        out.append(f"### 2.3 GitHub commits ({len(gh_rows)} total, {len(high_gh)} high-evidence)")
        out.append("| Date | Type | Area | Evidence | Files | +/- | Message |")
        out.append("|------|------|------|----------|------:|----:|---------|")
        for r in gh_rows[:50]:
            out.append(
                f"| {r.get('Date','')} | {r.get('Type','')} | {r.get('Area','')} | "
                f"{r.get('Evidence Value','')} | {r.get('Files Changed','')} | "
                f"+{r.get('Lines Added','0')} -{r.get('Lines Deleted','0')} | "
                f"{(r.get('Message') or '')[:120]} |"
            )
        if len(gh_rows) > 50:
            out.append(f"| ... | ... | ... | ... | ... | ... | _(+{len(gh_rows) - 50} more, see CSV)_ |")
        out.append("")
    if auto_rows:
        creates = sum(1 for r in auto_rows if (r.get("Event Type") or "") == "created")
        modifies = sum(1 for r in auto_rows if (r.get("Event Type") or "") == "modified")
        deletes = sum(1 for r in auto_rows if (r.get("Event Type") or "") == "deleted")
        out.append(f"### 2.4 Auto-tracked file events: {len(auto_rows)} (created {creates}, modified {modifies}, deleted {deletes})")
        out.append("")
    out.append("> Disclaimer: This is **not** legal/immigration advice. Use rows above as factual evidence, not as conclusions.")
    out.append("")
    return out


def _section_dev_time(ym: str, rows: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 3. Development Time", ""]
    if not rows:
        out.append("_(no time records this month — start with `python scripts/tracking/dev_time_tracker.py --mode session --start`)_")
        out.append("")
        return out
    sessions = [r for r in rows if (r.get("Mode") or "") == "session"]
    autos = [r for r in rows if (r.get("Mode") or "") == "auto"]
    total = sum(_safe_float(r.get("Duration Hours", "")) for r in rows)
    days = sorted({(r.get("Date") or "")[:10] for r in rows if r.get("Date")})
    avg = total / max(1, len(days))
    out.append(f"- Total: **{total:.2f} h** across {len(days)} day(s)")
    out.append(f"- Average per active day: **{avg:.2f} h**")
    out.append(f"- Sessions: {len(sessions)} · Auto-estimates: {len(autos)}")
    out.append("")
    out.append("> `session` rows are user-recorded start/end pairs. `auto` rows are derived from local git commits (no keyboard / screen monitoring).")
    out.append("")
    return out


def _section_github(ym: str, rows: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 4. GitHub Activity", ""]
    if not rows:
        out.append("_(no commits or PRs recorded for this month)_")
        out.append("")
        return out
    commits = [r for r in rows if (r.get("Type") or "") == "commit"]
    prs = [r for r in rows if (r.get("Type") or "").startswith("pr")]
    files = sum(_safe_int(r.get("Files Changed", "")) for r in rows)
    add = sum(_safe_int(r.get("Lines Added", "")) for r in rows)
    dlt = sum(_safe_int(r.get("Lines Deleted", "")) for r in rows)
    out.append(f"- Commits: **{len(commits)}** · PRs: **{len(prs)}**")
    out.append(f"- Files changed: **{files}** · +{add} / -{dlt} lines")
    out.append("")
    return out


def _section_progress(rows: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 5. Project Progress", ""]
    if not rows:
        out.append("_(no project progress recorded — run `python scripts/tracking/project_progress_tracker.py`)_")
        out.append("")
        return out
    out.append("| Topic | Stage | % | Script | Audio | Sections | Final | Packaged |")
    out.append("|-------|-------|--:|--------|-------|---------:|-------|----------|")
    for r in rows:
        out.append(
            f"| {r.get('Topic','')} | {r.get('Stage','')} | {r.get('Completion %','')} | "
            f"{r.get('Has Script','')} | {r.get('Has Audio','')} | "
            f"{r.get('Section Count','')} | {r.get('Has Final','')} | "
            f"{r.get('Has Packaged Final','')} |"
        )
    out.append("")
    return out


def _section_tool_usage(rows: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 6. Tool Usage", ""]
    if not rows:
        out.append("_(no tool usage stats — run `python scripts/tracking/tool_usage_tracker.py`)_")
        out.append("")
        return out
    out.append("| Tool | Mentions | Last Used | Area | Evidence | IRS Relevance | EB1/NIW Relevance |")
    out.append("|------|---------:|-----------|------|----------|---------------|-------------------|")
    for r in rows:
        out.append(
            f"| {r.get('Tool','')} | {r.get('Usage Count','')} | "
            f"{r.get('Last Used','')} | {r.get('Area','')} | "
            f"{r.get('Evidence Value','')} | {r.get('IRS Relevance','')} | "
            f"{r.get('EB1_NIW_Relevance','')} |"
        )
    out.append("")
    return out


def _section_auto(ym: str, rows: list[dict[str, str]]) -> list[str]:
    out: list[str] = ["## 7. Automatically Tracked Development Activity", ""]
    if not rows:
        out.append(f"_(no auto-tracked events with timestamp in {ym} — run `python scripts/tracking/track_stateverge_activity.py --mode manual`)_")
        out.append("")
        return out
    for r in rows[:200]:
        ts = r.get("Timestamp", "")
        et = r.get("Event Type", "")
        fp = r.get("File Path", "")
        su = r.get("Summary", "")
        pa = r.get("Project Area", "")
        ev = r.get("Evidence Value", "")
        out.append(f"- **{ts}** `[{et}]` `{fp}`  \n  {su}  \n  _{pa} · evidence: {ev}_")
    if len(rows) > 200:
        out.append(f"_… {len(rows) - 200} more event(s) truncated; see `auto_tracking_events.csv`._")
    out.append("")
    return out


def _section_missing(
    *,
    expenses: list[dict[str, str]],
    emails: list[dict[str, str]],
    gh_rows: list[dict[str, str]],
    progress_rows: list[dict[str, str]],
) -> list[str]:
    out: list[str] = ["## 8. Missing Evidence Checklist", ""]
    items: list[str] = []
    no_receipt = [r for r in expenses if not (r.get("Receipt Path") or r.get("Receipt"))]
    if no_receipt:
        items.append(f"- [ ] **Receipts** missing for {len(no_receipt)} expense row(s).")
    else:
        items.append("- [x] All expense rows have a receipt path.")
    no_invoice = [r for r in emails if not (r.get("Amount") or "").strip()]
    if no_invoice:
        items.append(f"- [ ] **Invoice amount** unparsed in {len(no_invoice)} subscription email(s).")
    no_url = [r for r in gh_rows if not (r.get("URL") or "").startswith("http")]
    if no_url:
        items.append(f"- [ ] **GitHub URL** missing for {len(no_url)} commit/PR row(s) (configure `GITHUB_REPO` for HTTPS URLs).")
    no_summary = list((TRACK / "monthly_reports").glob("*.md")) if (TRACK / "monthly_reports").is_dir() else []
    items.append(f"- [{'x' if no_summary else ' '}] Monthly summary file present in `docs/tracking/monthly_reports/`.")
    no_output = [r for r in progress_rows if r.get("Has Final") != "Yes" and r.get("Has Packaged Final") != "Yes"]
    if no_output:
        items.append(f"- [ ] **Final video** not yet produced for {len(no_output)} topic(s).")
    if not items:
        items.append("- [x] No gaps detected.")
    out.extend(items)
    out.append("")
    return out


def _read_template() -> str:
    if not TEMPLATE.is_file():
        return ""
    try:
        return TEMPLATE.read_text(encoding="utf-8")
    except OSError:
        return ""


def _build_report(ym: str) -> str:
    research = RESEARCH.read_text(encoding="utf-8") if RESEARCH.is_file() else ""
    milestones = MILESTONES.read_text(encoding="utf-8") if MILESTONES.is_file() else ""
    research_sections = _extract_research_sections(research, ym)
    milestone_table = _milestone_table_for_month(milestones, ym)

    expenses = _filter_rows_by_month(_read_csv_rows(EXPENSE_CSV), ym, "Date")
    emails = _filter_rows_by_month(_read_csv_rows(EMAIL_SUB_CSV), ym, "Date")
    gh_rows = _filter_rows_by_month(_read_csv_rows(GITHUB_CSV), ym, "Date")
    dev_rows = _filter_rows_by_month(_read_csv_rows(DEV_TIME_CSV), ym, "Date")
    progress_rows = _read_csv_rows(PROJECT_CSV)
    tool_rows = _read_csv_rows(TOOL_USAGE_CSV)
    auto_rows = _filter_rows_by_month(_read_csv_rows(AUTO_EVENTS_CSV), ym, "Timestamp")

    lines: list[str] = [
        f"# StateVerge Monthly Evidence Report - {ym}",
        "",
        f"_Generated: {datetime.now().astimezone().isoformat(timespec='seconds')}_",
        "",
        "_Applicant / legal name on file: **Ziwei Zhang**. \"James\" if it appears in StateVerge artifacts is a presenter/host persona only._",
        "",
    ]
    lines += _section_irs(ym, expenses=expenses, emails=emails)
    lines += _section_eb1(
        ym,
        gh_rows=gh_rows,
        auto_rows=auto_rows,
        research_sections=research_sections,
        milestone_table=milestone_table,
    )
    lines += _section_dev_time(ym, dev_rows)
    lines += _section_github(ym, gh_rows)
    lines += _section_progress(progress_rows)
    lines += _section_tool_usage(tool_rows)
    lines += _section_auto(ym, auto_rows)
    lines += _section_missing(
        expenses=expenses,
        emails=emails,
        gh_rows=gh_rows,
        progress_rows=progress_rows,
    )
    tpl = _read_template()
    if tpl.strip():
        lines += [
            "---",
            "<details><summary>Template reference</summary>",
            "",
            tpl,
            "",
            "</details>",
        ]
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build per-month IRS + EB1/NIW evidence summary.")
    ap.add_argument("--month", required=True, help="YYYY-MM (e.g. 2026-04)")
    args = ap.parse_args(list(argv) if argv is not None else None)
    ym = args.month.strip()
    if not _month_valid(ym):
        print(f"[tracking] error=bad_month value={ym!r} (expected YYYY-MM)", file=sys.stderr)
        return 1
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{ym}-summary.md"
    out_path.write_text(_build_report(ym), encoding="utf-8")
    print(f"[tracking] action=write file={out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
