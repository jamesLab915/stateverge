# StateVerge 研发日志

> 每条一条独立区块；也可用 `scripts/tracking/add_research_log.py` 追加。

## 模板

## YYYY-MM-DD - Title

- Project Area:
- Problem:
- Why It Matters:
- Tools Used:
- Technical Approach:
- Files/Modules Changed:
- Result:
- Evidence:
- Next Step:
- EB1/NIW Relevance:
- IRS/Business Relevance:

---

## 2026-04-24 - R&D evidence tracking system bootstrap

- Project Area: Project governance / compliance-adjacent documentation
- Problem: Unstructured record of R&D, subscriptions, and evidence links.
- Why It Matters: Improves auditability and future packet assembly without touching production code.
- Tools Used: Cursor, Markdown, Python (standard library)
- Technical Approach: Added `docs/tracking/` and CLI append scripts.
- Files/Modules Changed: `docs/tracking/*`, `scripts/tracking/*` (new only)
- Result: Reusable log + monthly report draft generator
- Evidence: `docs/tracking/README.md`, this file
- Next Step: Attach real receipts; replace placeholder rows in `irs_expense_log.csv`
- EB1/NIW Relevance: Documents sustained, systematic R&D and tooling investment (non-legal, factual)
- IRS/Business Relevance: Supports business-purpose narrative for software & equipment (consult CPA)
