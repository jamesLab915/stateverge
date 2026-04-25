# StateVerge 法律姓名 / 申请人字段审计

**政策（本次）**

- 与**法律姓名、创始人、拥有者、申请人、受益人**及 **IRS / EB-1 / NIW 证明材料** 中应出现的个人姓名字段，**统一为：`Ziwei Zhang`**。
- **`James` / 主持人用名** 的例外（**不得** 改为 `Ziwei Zhang`）仅包括：
  1. 用户**英文名**备注、非法律身份叙述；
  2. **StateVerge 品牌/视频主持人** 名称（persona）；
  3. `presenter` / `host` / `narrator` 叙事设定。

**本次对仓库的扫描**（`docs/`、`src/`、`scripts/`、`topics/`、`.env.example`；不含 `.venv`）

- **`James` / `James Zhang`**：grep **无** 命中.
- **`founder` / `owner` / `applicant` / `petitioner` / `beneficiary`（个人身份意）**：**无** 在业务/tracking 中误填的命中（一般英文库内的 `owner` 未出现在你方业务文案中；本次未在以上目录发现需改的句子）.
- **`IRS` / `EB1` / `EB-1` / `NIW`**：出现在 `docs/tracking/*`、`scripts/tracking/*` 的**节标题/字段名**中，为**工作记录/免责声明**用语，**未** 夹带第三方律所或他人姓名；**无** 需将 “James” 整体替换的段落。

## 1. 修改了哪些文件

- **无** 将 `James` 批量替换为 `Ziwei Zhang` 的 diff（**无** 命中）.
- **已更新** `docs/tracking/README.md`：在重要声明中增加**一条**说明——本人备查用法律/申请人姓名字段建议为 **Ziwei Zhang**，并说明可与主持人/屏幕 **persona 用的英文名**分开（**未** 在文案中写死 “James” 示例，避免与法律字段 grep 混淆）.
- 若你之后在 `docs/tracking/irs_expense_log.csv` 等文件中手写受益人/持卡人字段，请与 CPA 要求一致并优先使用 **Ziwei Zhang**（若该文件适用你本人）.

## 2. 哪些 James 被保留、原因

- 当前仓库在 `docs/ src/ scripts/ topics/ .env.example` 中 **0 处** `James` 命中，**无** 需为“主持人保留”的实例。
- `.venv/` 内 pip 的 `AUTHORS.txt` 含第三方贡献者名 “James …”，**不属于** StateVerge 内容，**不修改**.

## 3. 哪些 James 被改为 Ziwei Zhang

- **0 处**（无匹配可改）.

## 4. 是否发现 IRS / EB-1 / NIW “自动脚本”

| 项目 | 路径/说明 |
|------|------------|
| 费用/研发日志等 | `scripts/tracking/add_expense.py`、`add_research_log.py`、`add_milestone.py` |
| 月报 | `scripts/tracking/generate_monthly_report.py` |
| 文件树变更 | `scripts/tracking/track_stateverge_activity.py` |

- 上述脚本**仅**生成/追加 **个人工作记录、CSV 与月报**；**不是**向 IRS/移民局提交的官方表格自动化。
- **免责声明** 在 `docs/tracking/README.md` 等处：*不构成税务或移民法律建议*。

## 5. 自动脚本（tracking）当前输出路径

- `docs/tracking/irs_expense_log.csv`
- `docs/tracking/research_log.md`（`add_research_log` 追加）
- `docs/tracking/dev_milestones.md`（`add_milestone` 追加）
- `docs/tracking/auto_tracking_events.csv`
- `docs/tracking/monthly_reports/YYYY-MM-summary.md`
- `logs/tracking/file_snapshot.json`

**均位于仓库内** `~/StateVerge` 的 `docs/tracking/` 与 `logs/tracking/` 下，**不** 写往系统目录或 `output/<topic>/`.

## 6. 是否仍有“法律姓名”风险

- 仓库内**未**发现将错误法定姓名写死的句子；**若** 你在本地或 CI 的 **`.env`、未提交片段** 中存有个人全名，请**勿** 提交到 git。
- 在 **on-screen 或解说稿** 中若将主持人称为 “James” 为**有意设定**，可保留，与 **Ziwei Zhang** 的法律材料用途**分开**即可。

## 7. 验证命令（在仓库根执行时结果）

```bash
grep -R "James Zhang\|James" -n docs src scripts topics .env.example README.md 2>/dev/null
```

**2025-04-24 再跑**上述命令的**结果**：唯一命中在 **`docs/STATEVERGE_LEGAL_NAME_AUDIT.md`** 本文件内（政策段、第 2/3 节统计、`grep` 行文本、对主持人 **persona** 的说明等），**不** 代表业务数据里写入了 “James” 作为法律姓名；**除本文件外**，`docs/` `src/` `scripts/` `topics/` `.env.example` `README.md` **0 行** “James / James Zhang”.

若需**排除**本审计文件、只看业务内容，可执行：

```bash
grep -R "James Zhang\|James" -n docs src scripts topics .env.example README.md 2>/dev/null | grep -v "STATEVERGE_LEGAL_NAME_AUDIT.md" || true
# 预期：无输出
```

*本文件为项目内部审计记录，非法律意见。*
