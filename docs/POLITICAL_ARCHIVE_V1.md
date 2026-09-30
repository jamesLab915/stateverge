# StatevergeCN｜政治原话档案 Political Archive v1

> 录像不会失忆。政治人物会改口,录像不会。

Stateverge 保存 **谁、在什么时候、说了什么、后来又说了什么、最后发生了什么**,不替观众判断谁是骗子。

代码:`src/stateverge/cn_news/archive/`(Python 3.11,仅用标准库,SQLite)

## 运行

```bash
python3 -m unittest discover -s tests -t .                               # 测试
PYTHONPATH=src python3 -m stateverge.cn_news.archive init-db               # 建库 data/political_archive.db
PYTHONPATH=src python3 -m stateverge.cn_news.archive history "Name" Iran   # 今天翻旧账
```

`data/political_archive.db` 与 `media/` 不进 git(见 `.gitignore`)。

## 模块

| 文件 | 规格章节 | 作用 |
|------|---------|------|
| `models.py` | 3–6 | `PoliticalClaim`(`political_claims` 全部字段 + `fact_check_rating`)及枚举。枚举里没有 LIE / LIAR / DISHONEST。 |
| `database.py` | 3 | SQLite schema,所有枚举字段都有 `CHECK` 约束;按人物/关键词/时间窗检索。 |
| `source_resolver.py` | 7, 11 | 信源 A–E 分级、素材优先级。E 级只能作线索(`needs_original`)。 |
| `copyright.py` | 6 | `copyright_owner` / `copyright_type`。仅联邦政府作品自动记为 `US_GOVERNMENT_WORK`;州/地方 `.gov` 与 C-SPAN 不自动视为公有领域。`UNKNOWN` 一律阻止发布。 |
| `transcript.py` | 3, 9 | 逐字稿校验(忽略大小写/标点,不接受改写),提取 `context_before/after`,带时间轴时回填 `video_start/end`。支持中文。 |
| `claim_matcher.py` | 10, 17 | 去重(同一讲话多家转载时保留最高等级来源)、“是否同一问题”判断。 |
| `context_check.py` | 9 | 防断章取义:假设情景、转述他人、提问不同;审核人可手动加 `CLIP_EDITED` / `DIFFERENT_AUDIENCE` / `DIFFERENT_TIMEFRAME`。 |
| `contradiction.py` | 2B, 2C, 5 | 输出五种 `comparison_status`;承诺保质期时间线。 |
| `fact_check.py` | 5, 12 | 只接受公认事实核查机构且 URL 在其官网;只能以「X 将该说法评为……」出现。 |
| `claim_search.py` | 2D, 10 | 议题扩展(Iran → war / troops / regime change / ceasefire …),默认回溯 10 年,可插入外部 `SearchProvider`。 |
| `clip_builder.py` | 8 | 默认 3–12 秒;更长需写明理由,硬上限 30 秒;必须至少一种转换性内容,只加字幕不算。生成 ffmpeg 命令,不自行下载或执行。 |
| `script_generator.py` | 13–16 | Archive Card、X 文案、视频脚本、承诺保质期、今天翻旧账、讽刺模式。引语只能来自数据库。 |
| `publish_guard.py` | 12, 17 | 发布前检查,任一失败 → `BLOCK_PUBLISH`。 |
| `pipeline.py` | 19 | `run_archive_stage(event, db)`:聚类之后、LLM 草稿之前调用。 |

## 判定规则(保守优先)

| 情况 | 结果 |
|------|------|
| 不是同一问题 / 原话未逐字核对 / 来源仅 E 级 / 缺少上下文 | `INSUFFICIENT_EVIDENCE`(不生成对比内容) |
| 任一语境标记 | `CONTEXT_CHANGED`(可生成解释型内容,文案禁止“打脸”) |
| 仅措辞不同 | `INSUFFICIENT_EVIDENCE` |
| 说法相同 | `CONSISTENT` |
| 政策立场相反 | `POSITION_CHANGED` |
| 事实性说法 / 否认相反 | `APPARENT_CONTRADICTION` |
| 预测没实现 | 最多 `POSITION_CHANGED`,不算矛盾 |

内置立场判断是粗略的启发式;生产环境应传入 `stance_judge`(例如读取完整上下文的 LLM 判断),结果仍须人工审核。

## 发布前检查(`publish_guard.check`)

自动检查:原话已核对、日期有效、原始来源、上下文、同一问题、无判定用语(引语内和归属事实核查的句子除外)、版权已记录、片段长度、转换性内容、讽刺标记、引语均来自数据库、证据数量(≥2;含事实核查时 ≥3)、事实核查以归属句式出现。

审核人确认(`ReviewerAttestation`):已阅读完整上下文、没有把观点写成事实、已检查后续更正。审核人确认不能推翻自动检查的失败结果。

## 接入 StatevergeCN

```
采集 → 热度 → 聚类
  → run_archive_stage()   # 搜历史言论 → 匹配 → 对比 → 来源/版权 → Archive Card
  → LLM 草稿 → 人审(ReviewerAttestation)→ publish_guard.check() → 发布
```

外部采集器实现 `SearchProvider.search(person, keyword, since, until) -> list[PoliticalClaim]`,返回的言论以 `CANDIDATE` 入库,须再经过逐字稿、上下文与版权检查。

## v1 未包含

- 真实采集器(C-SPAN、白宫、通讯社等):目前只有接口,需要按网站条款接入。
- 视频下载:`download_status` 由外部流程维护;本模块只生成 ffmpeg 命令。
- 测试夹具只使用虚构人物,仓库中不包含任何真实政治人物的言论数据。

## 发布到 X(`x_publisher.py`)

```bash
export X_API_KEY=... X_API_SECRET=... X_ACCESS_TOKEN=... X_ACCESS_TOKEN_SECRET=...
# 预览(默认,不发帖)
PYTHONPATH=src python3 -m stateverge.cn_news.archive x-post <较早claim_id> <较新claim_id> \
    --reviewer 名字 --context-reviewed --opinion-checked --corrections-checked
# 确认无误后真正发布
PYTHONPATH=src python3 -m stateverge.cn_news.archive x-post ... --post
```

- 使用 X API v2 `POST /2/tweets`,OAuth 1.0a 用户令牌(在 developer.x.com 的 App 设置里开启 Read and Write 后生成 Access Token)。
- 发帖前一定经过 `publish_guard.check`;`BLOCK_PUBLISH` 时一条都不发。手动改过的文案也会重新检查。
- 超过 280 字符(中文与 emoji 按 2 计,链接按 23 计)自动拆成串推,并标注 `1/2`、`2/2`。
- 每张 Archive Card 只发一次(`x_posts` 表)。串推中途失败时,重跑会从断点接着回复,不会重复发帖。
- 发布成功后,相关言论的 `archive_status` 改为 `PUBLISHED`。
- v1 只发文字;视频片段上传(media upload)尚未实现。

## 人工整理批量导入(`importer.py`)

```bash
PYTHONPATH=src python3 -m stateverge.cn_news.archive import 文件.json --dry-run   # 只检查
PYTHONPATH=src python3 -m stateverge.cn_news.archive import 文件.json             # 写入数据库
```

格式见 `docs/archive_import_example.json`(虚构人物示例)。

- 信源等级、版权类型由程序根据网址计算,文件里不能直接填写。
- `transcript_verified` 不能在文件里声明;必须提供逐字稿(`transcript` 文本、`transcript_file` 文件,或带时间轴的 `transcript_segments`),原话逐字出现在逐字稿中才算核实。带时间轴时自动填入片段起止秒数。
- 事实核查通过 `fact_check: {source, url, rating}` 录入,只接受公认机构。
- 同一人物 + 日期 + 原话生成固定 ID;重复导入会更新内容,但保留发布状态与对比关系。
- 某一条出错不影响其他条,报告会逐条列出错误和警告。

## 官方逐字稿采集(`govinfo_provider.py`)

```bash
export GOVINFO_API_KEY=...   # https://api.data.gov/signup 免费申请;不设则用 DEMO_KEY(限额很低)
PYTHONPATH=src python3 -m stateverge.cn_news.archive collect "Donald Trump" Iran
```

- 总统:搜 CPD(总统文件汇编:讲话、记者会、采访、声明),按任期把文件归属到当时的总统。
- 议员:搜 CREC(国会记录)中本人的发言。
- 只截取目标人物本人发言中包含关键词的完整句子,原样保存;记者提问、其他人发言、标题行都不会被当成引语。全文作为逐字稿,自动核实并保存前后文。
- 结果一律存为 `CANDIDATE`,需要人工审核;关键词命中不等于立场。
- GovInfo 收录有几天到几周的延迟,最新讲话可能还查不到。

## AI 立场判断(`stance_llm.py`)

`x-post ... --llm` 或 `cloud-run --llm` 时使用 OpenAI(`OPENAI_API_KEY`,模型 `ARCHIVE_STANCE_MODEL`,默认 gpt-4o-mini,与项目其他 AI 功能一致)。

- 读取两段言论和各自完整上下文,只回答 SAME / SHIFTED / OPPOSITE / UNCLEAR,不判断是否诚实。
- 网络错误、格式错误、未知标签一律当作 UNCLEAR → `INSUFFICIENT_EVIDENCE`(保守方向)。
- 语境检查、逐字稿、信源等级等规则仍在 AI 之前执行,AI 不能绕过。

## 手机上云端运行(GitHub Actions,`.github/workflows/archive-cloud.yml`)

不需要合并到 main:编辑下面的 JSON 文件并提交,推送就会触发云端任务,结果自动写回同一文件。

1. 一次性设置:repo → Settings → Secrets and variables → Actions,添加
   `X_API_KEY`、`X_API_SECRET`、`X_ACCESS_TOKEN`、`X_ACCESS_TOKEN_SECRET`,
   可选 `GOVINFO_API_KEY`、`OPENAI_API_KEY`(有它就自动启用 AI 立场判断)。
2. **采集**:编辑 `data/archive/collect_requests.json`:
   ```json
   [{"person": "Donald Trump", "topic": "Iran", "years": 10, "status": "pending"}]
   ```
   结果写入 `data/archive/claims/collected-*.json`。
3. **发布**:编辑 `data/archive/publish_queue.json`:
   ```json
   [{"earlier_id": "…", "later_id": "…", "reviewer": "你的名字",
     "context_reviewed": true, "opinion_checked": true, "corrections_checked": true,
     "post": false, "status": "pending"}]
   ```
   先用 `"post": false` 预览:运行后 `status` 变成 `previewed`,`preview` 里是要发的串推。
   确认后改成 `"post": true, "status": "pending"` 再提交,才会真正发到 X。
   被拦截时 `status` 为 `blocked`,`failures` 写明原因。
4. `data/archive/x_ledger.json` 记录已发内容,防止重复发帖,不要手动删除。

数据文件都在公开 repo 里:只放公开言论,不要放任何密钥。

## 本地密钥文件(`.env.local`)

在自己电脑上运行时,不必每次 `export`:

```bash
cp .env.example .env.local   # 然后用编辑器填入 X_API_KEY 等
```

- 程序启动时自动读取 `.env.local`,再读 `.env`;已经存在的环境变量(例如 GitHub Actions Secrets)优先,不会被覆盖。
- `.env*` 已在 `.gitignore` 中,只有不含真实值的 `.env.example` 会进 repo。
- 空值会被忽略,不会覆盖成空字符串。
