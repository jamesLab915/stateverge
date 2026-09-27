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
