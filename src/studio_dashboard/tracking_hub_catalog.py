"""
Catalog for Studio 「证据档案」页：docs/tracking 下 IRS / EB-1·NIW 相关资料索引。
浏览器仅允许查看 docs/tracking 前缀内的白名单路径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

TRACKING_PREFIX = "docs/tracking"

# Group rows use key "entries" (not "items") — Jinja treats dict.items as the .items() method.
TRACKING_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "id": "irs",
        "title": "IRS · 费用 / 订阅",
        "blurb": "非税务建议；仅为个人备忘与资料索引（详见 README）。",
        "entries": (
            {
                "slug": "irs-expense-log",
                "rel": "docs/tracking/irs_expense_log.csv",
                "title": "irs_expense_log.csv",
                "desc": "费用行级记录，可对接收据路径",
            },
            {
                "slug": "subscriptions",
                "rel": "docs/tracking/subscriptions.md",
                "title": "subscriptions.md",
                "desc": "订阅维度备忘",
            },
            {
                "slug": "equipment",
                "rel": "docs/tracking/equipment_inventory.md",
                "title": "equipment_inventory.md",
                "desc": "设备与业务用途",
            },
            {
                "slug": "email-subscription-log",
                "rel": "docs/tracking/email_subscription_log.csv",
                "title": "email_subscription_log.csv",
                "desc": "从 .eml 解析的账单字段摘要",
            },
            {
                "slug": "tool-usage-stats",
                "rel": "docs/tracking/tool_usage_stats.csv",
                "title": "tool_usage_stats.csv",
                "desc": "工具关键词聚合统计",
            },
        ),
    },
    {
        "id": "niw",
        "title": "EB-1 · NIW 证据线索",
        "blurb": "非移民法律意见；事实记录与可检索引用为主。",
        "entries": (
            {
                "slug": "eb1-niw-evidence",
                "rel": "docs/tracking/eb1_niw_evidence_log.md",
                "title": "eb1_niw_evidence_log.md",
                "desc": "移民证据线索归档表",
            },
            {
                "slug": "research-log",
                "rel": "docs/tracking/research_log.md",
                "title": "research_log.md",
                "desc": "研发日志",
            },
            {
                "slug": "dev-milestones",
                "rel": "docs/tracking/dev_milestones.md",
                "title": "dev_milestones.md",
                "desc": "里程碑与可引用产出",
            },
            {
                "slug": "architecture-log",
                "rel": "docs/tracking/architecture_contribution_log.md",
                "title": "architecture_contribution_log.md",
                "desc": "架构与设计贡献备忘",
            },
            {
                "slug": "github-activity",
                "rel": "docs/tracking/github_activity_log.csv",
                "title": "github_activity_log.csv",
                "desc": "Git /GitHub 活动记录",
            },
            {
                "slug": "tool-matrix",
                "rel": "docs/tracking/tool_usage_matrix.md",
                "title": "tool_usage_matrix.md",
                "desc": "工具角色与证据价值",
            },
            {
                "slug": "project-timeline",
                "rel": "docs/tracking/project_timeline.md",
                "title": "project_timeline.md",
                "desc": "粗粒度时间线",
            },
        ),
    },
    {
        "id": "rollup",
        "title": "汇总与进度",
        "blurb": "扫描仓库状态的衍生表（可自行定时跑脚本更新）。",
        "entries": (
            {
                "slug": "project-progress",
                "rel": "docs/tracking/project_progress.csv",
                "title": "project_progress.csv",
                "desc": "topics / output 阶段扫描",
            },
            {
                "slug": "dev-time-summary",
                "rel": "docs/tracking/dev_time_summary.csv",
                "title": "dev_time_summary.csv",
                "desc": "session + git 推导的研发时间摘要",
            },
            {
                "slug": "auto-events",
                "rel": "docs/tracking/auto_tracking_events.csv",
                "title": "auto_tracking_events.csv",
                "desc": "文件树快照 diff（路径级）",
            },
        ),
    },
    {
        "id": "meta",
        "title": "说明与模板",
        "entries": (
            {
                "slug": "readme",
                "rel": "docs/tracking/README.md",
                "title": "README.md",
                "desc": "目录说明与命令大全",
            },
            {
                "slug": "privacy",
                "rel": "docs/tracking/privacy_notice.md",
                "title": "privacy_notice.md",
                "desc": "隐私与采集边界",
            },
            {
                "slug": "monthly-template",
                "rel": "docs/tracking/monthly_summary_template.md",
                "title": "monthly_summary_template.md",
                "desc": "月度总结空模板",
            },
        ),
    },
)


TRACKING_QUICK_COMMANDS: tuple[dict[str, str], ...] = (
    {
        "title": "生成月度证据汇总（IRS + EB-1 / NIW）",
        "command": 'cd "$HOME/stateverge" 2>/dev/null || cd "$HOME/StateVerge"\n'
        'export PYTHONPATH="$PWD"\n'
        "python scripts/tracking/generate_monthly_report.py --month 2026-04",
    },
    {
        "title": "手动跑一次文件树追踪 + 摘要",
        "command": 'cd "$HOME/stateverge" 2>/dev/null || cd "$HOME/StateVerge"\n'
        'export PYTHONPATH="$PWD"\n'
        "python scripts/tracking/track_stateverge_activity.py --mode manual\n"
        "python scripts/tracking/track_stateverge_activity.py --summary",
    },
    {
        "title": "GitHub 活动（本地 git，不需 token）",
        "command": 'cd "$HOME/stateverge" 2>/dev/null || cd "$HOME/StateVerge"\n'
        'export PYTHONPATH="$PWD"\n'
        "python scripts/tracking/github_activity_collector.py --mode local-log",
    },
    {
        "title": "追加一笔费用（示例，按需改参数）",
        "command": 'cd "$HOME/stateverge" 2>/dev/null || cd "$HOME/StateVerge"\n'
        'export PYTHONPATH="$PWD"\n'
        'python scripts/tracking/add_expense.py --date 2026-04-29 --vendor "Vendor" '
        '--amount "" --category "Software Subscription" --tool Cursor '
        '--description "..." --business-purpose "StateVerge development"',
    },
    {
        "title": "结构化 expenses JSON 汇总（tracking/expenses/）",
        "command": 'cd "$HOME/stateverge" 2>/dev/null || cd "$HOME/StateVerge"\n'
        'export PYTHONPATH="$PWD"\n'
        "python scripts/tracking/sum_expenses.py",
    },
)


def slug_index() -> dict[str, dict[str, Any]]:
    m: dict[str, dict[str, Any]] = {}
    for group in TRACKING_GROUPS:
        for entry in group["entries"]:
            if entry["slug"] in m:
                raise ValueError(f"duplicate tracking slug: {entry['slug']}")
            m[entry["slug"]] = entry
    return m


def safe_tracking_file(repo_root: Path, rel: str) -> Path | None:
    """Resolve repo_root/rel if it is a file under docs/tracking/."""
    base = (repo_root / TRACKING_PREFIX).resolve()
    raw = repo_root / rel
    try:
        cand = raw.resolve()
    except OSError:
        return None
    try:
        cand.relative_to(base)
    except ValueError:
        return None
    return cand if cand.is_file() else None


def latest_monthly_report(repo_root: Path) -> Path | None:
    d = (repo_root / "docs/tracking/monthly_reports").resolve()
    if not d.is_dir():
        return None
    mds = sorted(d.glob("*.md"), key=lambda p: p.name, reverse=True)
    return mds[0] if mds else None


def hub_context(repo_root: Path) -> dict[str, Any]:
    """Template context additions for tracking hub."""
    latest = latest_monthly_report(repo_root)
    monthly_latest = None
    if latest is not None:
        try:
            rel = latest.relative_to(repo_root.resolve())
        except ValueError:
            rel = None
        if rel is not None:
            monthly_latest = {
                "slug": "monthly-latest",
                "rel": str(rel).replace("\\", "/"),
                "title": latest.name,
                "desc": "按文件名排序取最新的一份月报",
            }
    return {
        "tracking_groups": TRACKING_GROUPS,
        "tracking_quick_commands": TRACKING_QUICK_COMMANDS,
        "monthly_latest": monthly_latest,
    }


def resolve_view_path(repo_root: Path, slug: str) -> tuple[Path | None, str]:
    """
    Returns (absolute path or None, display title).
    slug ``monthly-latest`` selects newest markdown under monthly_reports/.
    """
    if slug == "monthly-latest":
        p = latest_monthly_report(repo_root)
        return (p, p.name if p else "最新月报")
    meta = slug_index().get(slug)
    if not meta:
        return (None, "")
    path = safe_tracking_file(repo_root.resolve(), meta["rel"])
    return (path, meta["title"])
