# StateVerge 研发证据与费用追踪

本目录用于在 **不改动生产代码** 的前提下，为 StateVerge 项目建立 **R&D 过程、工具与设备、费用与证据** 的归档体系，便于：

- **研发过程记录**（问题、技术路径、结果、可指向文件的证据链）
- **软件订阅与设备使用**（用途、与项目的关联、收据路径占位）
- **IRS 相关费用与业务关联资料整理**（**非**税务、会计或法律建议，仅为个人资料整理与备忘清单）
- **未来 EB-1 / NIW 等移民申请可能用到的证据线索整理**（**不**构成移民法律建议；以事实记录与可检索引用为主）
- **按月汇总**（结合模板与 `generate_monthly_report.py` 生成 `monthly_reports/` 下的月报草案）
- **自动文件树变更追踪**（对指定目录做快照与 diff，写入 `auto_tracking_events.csv`；不读取文件内容，排除常见大媒体与疑似密钥文件路径）

> **重要声明**  
> 本仓库中任何文档、模板或脚本 **不构成** 税务、会计、移民法方面的专业建议。你应在填写金额、扣减类别与申报前 **自行** 咨询持牌 CPA 或移民律师。此处仅为 **工作笔记与资料索引** 工具。  
> 若你在本目录中填写**本人**用于备查的**法律身份/申请人姓名字段**（如日志、CSV 的备注等），建议统一为 **Ziwei Zhang**；与 StateVerge **主持人/屏幕 persona 用的英文名**可分开，不必与本字段混同。

## 文件一览

| 文件 | 用途 |
|------|------|
| `research_log.md` | 按日/按条研发日志 |
| `dev_milestones.md` | 里程碑与可引用产出 |
| `subscriptions.md` | 订阅与费用维度 |
| `equipment_inventory.md` | 设备与业务用途 |
| `irs_expense_log.csv` | 费用行级 CSV（可对接收据路径） |
| `eb1_niw_evidence_log.md` | 与移民证据线索相关的 **非**法律结论式归档表 |
| `project_timeline.md` | 按月的粗粒度时间线 |
| `tool_usage_matrix.md` | 工具在 StateVerge 中的角色与证据价值 |
| `architecture_contribution_log.md` | 原创架构与设计贡献备忘 |
| `monthly_summary_template.md` | 月度总结空模板 |
| `monthly_reports/` | 由 `generate_monthly_report.py` 输出（`YYYY-MM-summary.md`） |
| `auto_tracking_events.csv` | 自动 diff 结果（文件路径级事件；非文件内容） |
| `logs/tracking/file_snapshot.json` | 与自动追踪配对的本地快照（相对仓库根目录） |

## 自动追踪

- 手动扫描并写入 `auto_tracking_events.csv` / 更新 `logs/tracking/file_snapshot.json`（`docs/tracking/monthly_reports/` 下自动生成的月报不纳入 diff，避免与月报脚本的自触发噪声）：

  ```bash
  python scripts/tracking/track_stateverge_activity.py --mode manual
  ```

- 安装 `post-commit` 钩子（非 git 仓库时仅提示，不报错；若已存在非本脚本钩子时不会覆盖，需手动手合并）：

  ```bash
  python scripts/tracking/install_git_hooks.py
  ```

- 查看最近 20 条自动追踪事件：

  ```bash
  python scripts/tracking/track_stateverge_activity.py --summary
  ```

## 命令示例（在仓库根目录执行）

```bash
cd /Users/ziweizhang/StateVerge

python scripts/tracking/add_expense.py --date 2026-04-24 --vendor Cursor --amount "" --category "Software Subscription" --tool Cursor --description "AI coding assistant for StateVerge development" --business-purpose "Used for software development and automation engineering"

python scripts/tracking/add_research_log.py --date 2026-04-24 --title "Built tracking system for StateVerge R&D evidence" --project-area "Project governance" --problem "Need structured records for tax and future immigration evidence" --tools "Cursor, Markdown, Python" --approach "Created tracking documents and CLI scripts" --result "Reusable audit trail system" --evidence "docs/tracking/" --next-step "Attach receipts and update monthly" --eb1 "Supports sustained R&D evidence" --irs "Supports business expense documentation"

python scripts/tracking/add_milestone.py --date 2026-04-24 --milestone "Introduced R&D evidence tracking system" --area "Project governance" --contribution "Structured docs, CSV, CLI append scripts" --tools "Python, Markdown" --output "docs/tracking/ + scripts/tracking/" --evidence "docs/tracking/README.md" --relevance "Documents sustained engineering practice"

python scripts/tracking/generate_monthly_report.py --month 2026-04
```

## 与生产代码的关系

- **不修改** `src/production/`、`src/presenter_pipeline/` 等业务代码逻辑。
- 本体系为 `docs/tracking/`、`scripts/tracking/` 与 `logs/tracking/`（快照与可选日志），可独立维护。

## 自动证据系统说明

本目录在原有的 R&D / IRS 笔记之上，新增了一个 **自动证据采集层**，用于为 IRS 报税与 EB1 / NIW 移民申请整理事实证据。所有模块都保证：

- **非侵入**：不监听键盘、不截图、不读取浏览器历史、不监控非 StateVerge 项目。
- **隐私优先**：不保存完整邮件正文、不记录账号密码、不读取 API key（除 `.env` 中显式声明的 `GITHUB_TOKEN` / `GITHUB_REPO`）。
- **只在仓库内写**：所有输出都落在 `~/StateVerge/docs/tracking/` 与 `~/StateVerge/logs/tracking/`，不写到 `~/stateverge-system` 或仓库外任何位置。
- 详细隐私范围见 [`privacy_notice.md`](privacy_notice.md)。

| 模块 | 职责 | 输出 |
|------|------|------|
| `email_subscription_parser.py` | 仅解析 `.eml` 中的 receipt / invoice / payment / subscription / renewal 关键词邮件，提取金额、币种、订阅名、计费周期 | `email_subscription_log.csv` |
| `github_activity_collector.py` | 通过 GitHub API（`fetch`）或本地 `git log`（`local-log`）记录 commits / PRs / 行数 / 区域 / 证据等级 | `github_activity_log.csv` |
| `dev_time_tracker.py` | 手动 session 起止 + 基于本地 git commit 的 auto 估算（不监控键盘 / 屏幕） | `logs/tracking/dev_time.json`, `docs/tracking/dev_time_summary.csv` |
| `project_progress_tracker.py` | 扫描 `topics/<topic>/` 与 `output/<topic>/`，按脚本 → 音频 → 段落视频 → 终片自动判断阶段与百分比 | `project_progress.csv` |
| `tool_usage_tracker.py` | 在所有 tracking CSV / Markdown 中聚合工具关键词（Cursor、Runway、HeyGen、ElevenLabs、Pexels、Pixabay、DVIDS…）出现频次 | `tool_usage_stats.csv` |
| `track_stateverge_activity.py` | 文件树快照 diff（仅路径与 mtime/size，排除媒体与疑似密钥文件） | `auto_tracking_events.csv`, `logs/tracking/file_snapshot.json` |
| `generate_monthly_report.py` | 汇总以上全部 CSV / Markdown 为单页月报：IRS 证据、EB1 / NIW 证据、研发时长、GitHub 活动、项目进度、工具使用、缺口清单 | `monthly_reports/YYYY-MM-summary.md` |
| `install_git_hooks.py` | 安装 `.git/hooks/post-commit`：每次 commit 后自动运行追踪 + GitHub local-log + 项目进度 + 工具使用统计 | `.git/hooks/post-commit` |

### 用途说明

- **IRS**：辅助整理订阅、设备、工具与商业用途的资料；不构成税务建议。
- **EB1 / NIW**：辅助整理持续研发记录、自动化系统构建与 GitHub 历史；不构成移民法律意见。
- **法律姓名**：法律 / 申请人字段统一填 **Ziwei Zhang**；StateVerge 视频中的 `James` 仅作为主持人 persona，不进入法律字段。

### 自动证据系统命令

```bash
cd /Users/ziweizhang/StateVerge

# 1. 邮件订阅 / 账单解析（仅解析关键词邮件，不保存正文）
python scripts/tracking/email_subscription_parser.py --eml-dir ~/Downloads

# 2. GitHub 研发活动（local-log 离线模式 + fetch 在线模式可选）
python scripts/tracking/github_activity_collector.py --mode local-log
python scripts/tracking/github_activity_collector.py --mode fetch

# 3. 本地研发时间（session 与 auto 模式）
python scripts/tracking/dev_time_tracker.py --mode session --start
python scripts/tracking/dev_time_tracker.py --mode session --end
python scripts/tracking/dev_time_tracker.py --mode auto
python scripts/tracking/dev_time_tracker.py --mode summary

# 4. 项目推进进度（扫描 topics/ 与 output/）
python scripts/tracking/project_progress_tracker.py

# 5. 工具使用频率统计
python scripts/tracking/tool_usage_tracker.py

# 6. 月度统一证据报告（IRS + EB1 / NIW）
python scripts/tracking/generate_monthly_report.py --month 2026-04

# 7. 安装 git post-commit 钩子（自动联动以上模块）
python scripts/tracking/install_git_hooks.py
```

### 每日自动刷新（macOS launchd）

无需手动执行，每天 **23:30** 本地时间自动跑一次 `track_stateverge_activity → project_progress_tracker → tool_usage_tracker → dev_time_tracker --mode auto`，结果写入 `logs/tracking/launchd_stdout.log` 与 `logs/tracking/launchd_stderr.log`。

非侵入：**不监控键盘、不截图、不读取私人文件夹**，仅扫描 `~/StateVerge` 仓库内文件树。

```bash
# 安装每日自动刷新（写入 ~/Library/LaunchAgents/com.stateverge.tracking.autorefresh.plist）
python3 scripts/tracking/install_launchd_autotracker.py --install

# 查看状态（plist 是否存在 / launchctl 是否加载 / 最近日志）
python3 scripts/tracking/install_launchd_autotracker.py --status

# 卸载
python3 scripts/tracking/install_launchd_autotracker.py --uninstall
```

## Expenses Tracking

为了在不影响现有 `irs_expense_log.csv` 行级日志的前提下，单独记录 **结构化的费用账单**（例如 LLC 注册、注册代理服务、出版费等一次性 / 多项打包的支出），新增了一个独立的 expenses 模块：

- 所有费用记录放在仓库根目录下的 [`tracking/expenses/`](../../tracking/expenses/)。
- 每个 JSON 文件代表 **一笔或一组费用**（例如 `2026-llc-registration.json`）。
- 使用 [`scripts/tracking/sum_expenses.py`](../../scripts/tracking/sum_expenses.py) 汇总所有 JSON 文件，按 `category` 分组并合计。

### 文件格式

```json
{
  "project": "StateVerge",
  "category": "legal_setup",
  "type": "llc_registration",
  "date": "2026-04-25",
  "items": [
    { "name": "New York LLC filing fee", "amount": 205, "currency": "USD" }
  ],
  "total": 880,
  "notes": "Initial legal setup cost for StateVerge LLC",
  "owner": "Ziwei Zhang"
}
```

- `category` 用于分组（如 `legal_setup` / `tools` / `media` 等）。
- 若提供了 `total`，将以 `total` 为准；否则脚本会从 `items[].amount` 累加。
- `owner` 字段填写法律身份姓名（**Ziwei Zhang**），与视频主持人 persona `James` 不混用。

### 汇总命令

```bash
cd /Users/ziweizhang/StateVerge
python scripts/tracking/sum_expenses.py
```

输出示例：

```
[expenses]
total=880 USD

by_category:
legal_setup=880
```

> 该模块为 **追加式**：不修改现有 tracking 自动系统、不新增 watcher、不影响 `src/production/` 与 `src/presenter_pipeline/` 任何代码。

