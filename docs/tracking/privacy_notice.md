# StateVerge Tracking — Privacy & Compliance Notice

This notice describes exactly what the **StateVerge automated evidence
collection system** under `scripts/tracking/` collects, what it never
collects, and what the collected data is for.

## What is **NOT** collected

The trackers in this directory are deliberately non-invasive. The
following data is **never** captured, written, or transmitted by any
script in `scripts/tracking/`:

- Personal communications (chat content, personal emails, message
  bodies, contacts).
- Full email message bodies (only billing-related metadata fields:
  date, sender domain, subject snippet, parsed amount/currency).
- Email account credentials (no IMAP login, no OAuth tokens stored).
- Passwords, SSH keys, API keys, certificate files (`.env`,
  `id_rsa*`, `*.pem`, `*.p12`, `*.key`, `credentials.json`,
  `serviceaccount.json`, `.netrc` are explicitly skipped during file
  scans).
- Screenshots, screen recordings, clipboard contents.
- Keyboard input or window focus events.
- Browser history, cookies, or extension state.
- Any activity outside the StateVerge project tree
  (`~/StateVerge`).

The tracker explicitly excludes media payloads (`.mp4`, `.mov`,
`.wav`, `.mp3`, `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`) from the
file-tree snapshot and only records **paths and metadata**, never
file contents.

## What **IS** collected (all stored under `~/StateVerge`)

| Source | Stored in | Purpose |
|--------|-----------|---------|
| File-tree path-level diffs | `docs/tracking/auto_tracking_events.csv` | Continuous R&D evidence (paths only, no contents) |
| Local git log (last 30 commits) | `docs/tracking/github_activity_log.csv` | EB-1 / NIW R&D activity record |
| GitHub API (only when `GITHUB_TOKEN` + `GITHUB_REPO` set in `.env`) | same as above | Cross-reference public repo activity |
| Manual session start/end | `logs/tracking/dev_time.json`, `docs/tracking/dev_time_summary.csv` | Self-reported development time |
| Auto-estimated dev time (from local git commit count) | `docs/tracking/dev_time_summary.csv` | Approx. R&D hours; rule-of-thumb only |
| Topic / output file presence | `docs/tracking/project_progress.csv` | Project pipeline progress |
| Tool name mentions in tracking docs/CSVs | `docs/tracking/tool_usage_stats.csv` | Commercial-use evidence |
| Subscription / billing `.eml` files (when explicitly passed via `--eml-dir` / `--eml-file`) | `docs/tracking/email_subscription_log.csv` | IRS subscription evidence (parsed fields only — see exclusion list above) |
| User-entered expense / milestone / research entries | `docs/tracking/irs_expense_log.csv`, `dev_milestones.md`, `research_log.md` | Manually authored, you control content |

## How the data is used

- **IRS / accounting evidence preparation** — line-level expense and
  subscription records for a CPA to categorise.
- **EB-1 / NIW evidence preparation** — sustained R&D, system
  building, and automation records linked to file paths in this
  repository.
- **Internal project management** — topic progress and tool usage
  awareness.

## Important disclaimers

- This system is a **personal note-keeping and indexing tool**. It is
  **not** tax, accounting, immigration, or legal advice.
- All categorisation suggestions ("Evidence Value", "IRS Relevance",
  "EB1_NIW_Relevance") are heuristic labels for triage only. A
  licensed CPA / immigration attorney must validate them before any
  filing.
- The legal / applicant name on file for StateVerge is
  **Ziwei Zhang**. Any occurrence of `James` in StateVerge content is
  a presenter / on-screen persona for the brand and is **not** the
  applicant's legal name. Tracking entries should always use
  **Ziwei Zhang** in legal-name fields.

## Where the data lives

All artefacts produced by this tracker live under:

```
~/StateVerge/docs/tracking/
~/StateVerge/logs/tracking/
~/StateVerge/scripts/tracking/
```

The system **never** writes outside this tree, never writes to
`~/stateverge-system`, and never reads any file outside the
StateVerge repo (the `.env` file at the repo root is the only
exception, and only specific keys — `GITHUB_TOKEN`, `GITHUB_REPO` —
are read).
