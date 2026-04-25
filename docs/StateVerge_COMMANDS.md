# StateVerge 常用 Python 指令速查（可当作操作手册）

> 工作目录默认：`~/StateVerge`  
> 建议每次在仓库根目录先执行：  
> `export PYTHONPATH="$PWD"`（若你使用虚拟环境，请先 `source .venv/bin/activate`）  
> **注意**：与成片相关的根目录是 **`topics/<slug>/`**，不是仓库根下单独的 `output/<topic>/`（除非你的目录另有自定义软链）。

**示例 topic**：`us-separation-of-powers`（下文中把 `TOPIC` 或示例 slug 换成你的 topic 目录名）

---

## 一、项目初始化 / 检查

### 什么时候用

- 第一次拉仓库、换电脑、**出片前**怀疑环境或网络 API
- 记不起子命令时，用下方入口打印 `--help`（无子命令时可能 `exit 1`，属正常）

```bash
cd ~/StateVerge
export PYTHONPATH="$PWD"
```

- **环境 / API 连通性**（会尝试部分云端请求）：

```bash
python -m src.integrations.healthcheck
```

- **`src/run_pipeline.py`**

  - **TODO：当前项目未发现 `src/run_pipeline.py`，请勿照抄。**

- **等价入口**（看用法/帮助）：

```bash
python -m src.production.cli
python -m src.presenter_pipeline.cli
python -m src.production.ltx_batch_helper
python -m src.utils.sort_envato
```

---

## 二、Production 层

### 什么时候用

| 场景 | 用哪条（见下表 / 下节命令） |
|------|----------------|
| 新 topic、还没有 brief | `--generate-brief` |
| 新 topic 或 **改了 prompt/文案** 要重出本层脚本 | `--generate-scripts` |
| 要生成分镜/场景计划 JSON | `--generate-ltx-plan` 再 `ltx_batch` |
| 外部 LTX/工具出完片段，要在本机拼主叙事 | `ltx_batch --assemble` |
| 要做包装成片（低third、BGM、SFX 等） | `--package` |

`export PYTHONPATH="$PWD"` 后：

| 目的 | 命令 |
|------|------|
| 生成 / 补全 brief | `python -m src.production.cli --topic us-separation-of-powers --generate-brief` |
| 生成剧本（narration + presenter 脚本等） | `python -m src.production.cli --topic us-separation-of-powers --generate-scripts` |
| 生成 LTX 场景计划 `ltx_scene_plan.json` | `python -m src.production.cli --topic us-separation-of-powers --generate-ltx-plan` |
| 从计划导出 LTX 侧 prompt / manifest | `python -m src.production.ltx_batch_helper --topic us-separation-of-powers --export-prompts` |
| 在外部按 manifest 出片后，合成主叙事线 | `python -m src.production.ltx_batch_helper --topic us-separation-of-powers --assemble` |
| 打包为 `final_packaged.mp4` | `python -m src.production.cli --topic us-separation-of-powers --package` |

---

## 三、Presenter 层

### 什么时候用

- 要建立/刷新主持人时间线、落盘 `presenter` 相关目录
- 要**重新出配音** 或 按时间线**切分音频对轨**
- 要拼每段 **base**、为 Runway 准备目入目出
- 在 **Runway 出片**放到约定目录后，要 **段拼 + 全长合进 narrative**

`export PYTHONPATH="$PWD"` 后，对同一 topic 典型顺序为：

| 步骤 | 命令 |
|------|------|
| plan | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --plan` |
| TTS | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --tts` |
| split audio | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --split-audio` |
| build base | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --build-base` |
| prepare runway | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --prepare-runway` |
| 插入主持段 / 全片合并 | 本仓库**无**名为 `insert` 的子命令；在放好 Runway 输出后使用 `--assemble-segments` 与 `--assemble-full`（见 `README_PRESENTER_PIPELINE.md`） |
| | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-segments` |
| | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-full` |
| 可选：整段备料到 Runway 前 | `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --full-prep` |

---

## 四、完整视频流水线（从 topic 到 final_packaged）

### 什么时候用

- 要一张图看清「从 0 到出片」全链路、对照自己卡在哪一步

1. 初始化与脚本：

```bash
export PYTHONPATH="$PWD"
python -m src.production.cli --topic us-separation-of-powers --generate-brief
python -m src.production.cli --topic us-separation-of-powers --generate-scripts
```

2. 视觉计划 + LTX 主片（需外部在 LTX 中按 `export-prompts` 产出后）：

```bash
python -m src.production.cli --topic us-separation-of-powers --generate-ltx-plan
python -m src.production.ltx_batch_helper --topic us-separation-of-powers --export-prompts
# … 在 LTX 中生成每场景片段 …
python -m src.production.ltx_batch_helper --topic us-separation-of-powers --assemble
```

3. 主持人音频、分段、Runway 输入与合并：

```bash
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --plan
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --tts
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --split-audio
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --build-base
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --prepare-runway
# … 在 Runway 中完成 lip sync，输出放回约定目录 …
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-segments
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-full
```

4. 最终包装成片：

```bash
python -m src.production.cli --topic us-separation-of-powers --package
```

**产物**（以项目约定为准）：`topics/<topic>/output/final_with_presenter.mp4`、`final_packaged.mp4` 等。路径定义见 `src/production/paths.py` / `src/presenter_pipeline/fs_utils.py`。

---

## 五、Tracking / IRS / EB1-NIW 记录

### 什么时候用

- 要记**费用/研发日志/里程碑**、要**月末汇总**、要看自动文件树 diff、要装 `post-commit` 钩子

在仓库根目录：

```bash
python scripts/tracking/add_expense.py --date 2026-04-24 --vendor Cursor --amount "" --category "Software Subscription" --tool Cursor --description "说明" --business-purpose "业务目的"
```

```bash
python scripts/tracking/add_research_log.py --date 2026-04-24 --title "标题" --project-area "领域" --problem "问题" --tools "工具" --approach "方法" --result "结果" --evidence "路径" --next-step "下一步" --eb1 "移民线索" --irs "税务线索"
```

```bash
python scripts/tracking/add_milestone.py --date 2026-04-24 --milestone "里程碑" --area "范围" --contribution "技术贡献" --tools "工具" --output "产出" --evidence "证据路径" --relevance "相关性"
```

```bash
python scripts/tracking/track_stateverge_activity.py --mode manual
python scripts/tracking/track_stateverge_activity.py --summary
python scripts/tracking/generate_monthly_report.py --month 2026-04
python scripts/tracking/install_git_hooks.py
```

---

## 六、常用 force 重跑参数

### 什么时候用

- 误以为有「一次 `--force-all` 全部重算」

- **TODO：在 `src/` 中未发现 `--force-script`、`--force-voice`、`--force-visuals`、`--force-sections`、`--force-final`、`--force-all` 等命令行参数。**

- 👉 当前通过 **只重跑相关子命令** 实现局部重算，而不是 force flag。详见下节 **「十、♻️ 重跑策略」**。

---

## 七、常见问题排查（步骤链）

> 下述命令均已在仓库中实现；`ffprobe` 为**系统终端**工具，**不是**本仓库的 Python 模块。若需仓库内的一键 `ffprobe` 脚本，见各条 `TODO`。

### 【视频黑屏】

1. 先重拼全长时间线（**命令存在**；需 narrative 与源文件已齐）  
   `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-full`
2. 再检查本 topic 下**分段/合成产物**（本仓库在 `presenter/`，**不是**仓库根下 `output/<topic>/`）：  
   例如 `topics/<slug>/presenter/segments/` 、`presenter/final/` 、`topics/<slug>/output/`（详见「十一」）
3. 在终端查 **ffmpeg 是否在 PATH**：`which ffmpeg`
4. 若仍失败，需要看容器轨情况：  
   **TODO：本仓库未提供 ffprobe 的专用 Python 封装脚本**；可手动在终端对成片执行： `ffprobe -hide_banner 你的文件.mp4`

### 【有声音没画面】

1. `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --build-base`  
2. `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-segments`  
3. 再确认主叙事 `topics/<slug>/video/narrative_main.mp4` 是否存在、是否损坏

### 【有画面没声音】

1. `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --tts`  
2. `python -m src.presenter_pipeline.cli --topic us-separation-of-powers --split-audio`  
3. `python -m src.integrations.healthcheck`

### 【素材没下载（本仓库指 Envato 自 Downloads 分拣到 assets）】

1. `python -m src.utils.sort_envato --once`

### 【HeyGen 主持人没插入】

1. **TODO：未在仓库中发现独立 HeyGen Python CLI（如 heygen_*.py）**  
2. 当前以 presenter + Runway 流程为主，请用 `--assemble-segments` / `--assemble-full` 等排查

### 【LTX 文件缺失】

1. `python -m src.production.ltx_batch_helper --topic us-separation-of-powers --assemble`（看终端 `skipped` / 缺片提示）  
2. 若无 plan 类文件： `python -m src.production.cli --topic us-separation-of-powers --generate-ltx-plan`

### 【Envato 素材没识别】

1. `python -m src.utils.sort_envato --once`  
2. 再核对 `assets/envato` 分桶与 `brief/production_brief.json` 中相关字段（见 `README_ENVATO_SORT.md`）

### 【API key 缺失】

1. `python -m src.integrations.healthcheck`

---

## 九、🔥 最常用「一键/最少复制」流程（直接照抄跑）

### 9.1 极速压缩（**命令均真实存在**；但很容易中途失败，请读说明）

> ⚠️ 下列为「最少行数」示例。实际跑通常还需要：`--generate-ltx-plan` 再 `export-prompts`；LTX/Runway 手动的出片与落盘；`narrative_main`；以及通常 `build-base` → `prepare-runway` → 段 `assemble` 后，才适合全长 `assemble-full`。

```bash
cd ~/StateVerge
export PYTHONPATH="$PWD"

# 1. 生成内容
python -m src.production.cli --topic us-separation-of-powers --generate-brief
python -m src.production.cli --topic us-separation-of-powers --generate-scripts

# 2. LTX（只导出 prompt；若未先发 ltx 计划，请先 9.2 里多一行 --generate-ltx-plan）
python -m src.production.ltx_batch_helper --topic us-separation-of-powers --export-prompts

# 3. Presenter
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --plan
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --tts
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --split-audio

# 4. 拼接（缺前置时可能直接报错，属正常）
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-full
```

### 9.2 推荐可跑通概率更高（在 9.1 上补关键步）

> **TODO：在 LTX/Runway 中人工出片** 仍非本仓库的 Python 命令，下面用注释标出断点。

```bash
cd ~/StateVerge
export PYTHONPATH="$PWD"

python -m src.production.cli --topic us-separation-of-powers --generate-brief
python -m src.production.cli --topic us-separation-of-powers --generate-scripts
python -m src.production.cli --topic us-separation-of-powers --generate-ltx-plan
python -m src.production.ltx_batch_helper --topic us-separation-of-powers --export-prompts
# … 在外部 LTX/工具中生成 topics/<slug>/ltx/generated/scene_XXX.mp4 …
python -m src.production.ltx_batch_helper --topic us-separation-of-powers --assemble

python -m src.presenter_pipeline.cli --topic us-separation-of-powers --plan
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --tts
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --split-audio
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --build-base
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --prepare-runway
# … 在 Runway 中完成 lip sync，输出放回 topics/<slug>/presenter/lipsync_output/ …
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-segments
python -m src.presenter_pipeline.cli --topic us-separation-of-powers --assemble-full

python -m src.production.cli --topic us-separation-of-powers --package
```

---

## 十、♻️ 重跑策略（省时间，避免全量重跑）

👉 当前 **没有** 全局 `force` 时，用「只重跑与改动相关的子命令」；**不要**假设存在 `--force-all`（与「六」中 TODO 一致）。

| 你改了什么 | 通常只重跑 | 说明 |
|------------|------------|------|
| 只改口播/主持**文案** | `python -m src.production.cli --topic <slug> --generate-scripts` | 若 LTX/主持时序大改，再向下游补跑 |
| 只重出**旁白/主持音** | `--tts` 然后 `--split-audio` | 若切段变了，多从 `build-base` 起重跑见日志 |
| 只换主持画面/Runway 片 | `--assemble-segments` 与/或 `--assemble-full` | 长片合成异常时可先试 `--assemble-full` |
| LTX 只重换片段、脚本不变 | `python -m src.production.ltx_batch_helper --topic <slug> --assemble` | 再按需接 presenter 链 |
| 只要重新打包装 | `python -m src.production.cli --topic <slug> --package` | 依赖上游成片路径仍有效 |

**成片异常但怀疑只是合成步骤坏了**：优先

`python -m src.presenter_pipeline.cli --topic <slug> --assemble-full`

再视情况：

`python -m src.production.cli --topic <slug> --package`

---

## 十一、📁 输出文件在哪里

以仓库内 **`topics/<slug>/`** 为 topic 根（**不是** 仓库根下独立的 `output/<topic>/`），与 `src/production/paths.py`、`src/presenter_pipeline/fs_utils.py` 一致：

| 位置 | 主要内容 |
|------|----------|
| `brief/` | `production_brief.json`、`ltx_scene_plan.json`、`packaging_manifest.json` |
| `script/` | `narration_script.txt`、`presenter_script.json` |
| `video/` | `narrative_main.mp4`（LTX 拼接后） |
| `audio/` | 主持人/旁白相关子路径（以实际生成为准，见本 topic 下目录） |
| `presenter/` | `plan/`、`segments/`、`base/`、`lipsync_input/`、`lipsync_output/`、`final/`、`manifests/` |
| `output/` | `final_with_presenter.mp4`、`final_packaged.mp4` |
| `ltx/` | `prompts/`、`generated/` 等 LTX 批处理相关 |

> 本仓库**没有**固定名为 `output/<topic>/sections/*.mp4` 的单一树形；分段产物多在 `presenter/segments/` 等，具体文件名以你磁盘上为准。

---

## 十二、⚠️ 常见坑（经验总结）

- 没配 API key → `healthcheck` 对应项 FAIL，脚本/TTS/云端步骤失败
- 没先 `--generate-ltx-plan` 就 `export-prompts` / assemble → plan 或路径不符合预期
- LTX 片段没落到 `ltx/generated` → 拼不出 `narrative_main` 或表现为缺片
- Runway 出片**未放进** `presenter/lipsync_output` 约定结构 → 后面 assemble 全挂
- 没 `--split-audio` 就强行走下游 → 时间线/对轨乱
- 多版本/混用 ffmpeg 与参数 → 易黑屏、无声、音画错位（统一用 `which ffmpeg` 检查）
- **路径记错**：成片在 **`topics/<slug>/output/`**，不是以为的仓库根 **`output/`**

---

## 十三、Media Sources / 统一素材获取系统

从旁白 / presenter 稿等生成搜索词，从 **Pexels、Pixabay、DVIDS** 拉取素材到 `topics/<topic>/assets/raw/`，并写 `media_manifest.json`。详见 `docs/MEDIA_SYSTEM.md`；需 `PEXELS_API_KEY` / `PIXABAY_API_KEY`（`DVIDS_API_KEY` 可选）。

`export PYTHONPATH="$PWD"` 后：

```bash
python -m src.integrations.media_sources.selector --topic chernobyl
```

只写清单、不下载：

```bash
python -m src.integrations.media_sources.selector --topic chernobyl --no-download
```

指定来源：

```bash
python -m src.integrations.media_sources.selector --topic chernobyl --source pexels,pixabay,dvids
```

---

## 十四、Mix Engine / 纪录片混剪系统

> 不替代 `src/production` / `src/presenter_pipeline`；在 **`topics/<topic>/`** 下组织 LTX、Envato、采访重配音音轨，用 `mix/timeline.json` 混剪为 **`output/final_mix.mp4`**。详见 `src/mix_engine/README.md`、示例目录 `topics/SCAFFOLD_topic_mix/`。

| 目录（均在 `topics/<topic>/` 下） | 用途 |
|----------------------------------|------|
| `video/` | LTX 主输出 MP4（`generate_timeline` 扫描 `*.mp4`） |
| `envato/` | Envato 真实 B-roll MP4 |
| `sources/` | `raw` / `transcripts` / `rewritten` / `audio`（上时间线的一般是 `audio/` 下重配音） |
| `mix/` | `timeline.json`、中间 `clips/` |
| `output/` | 成片，如 `final_mix.mp4`（与 `final_packaged.mp4` 可并存） |

```bash
cd ~/StateVerge
export PYTHONPATH="$PWD"
```

1. 生成 `mix/timeline.json`（不覆盖已存在，除非 `--force`）：

```bash
python -m src.mix_engine.generate_timeline --topic chernobyl
```

2. 强制重建时间线：

```bash
python -m src.mix_engine.generate_timeline --topic chernobyl --force
```

3. 合成混剪视频：

```bash
python -m src.mix_engine.build_video --topic chernobyl
```

4. 仅打印计划、不跑 ffmpeg：

```bash
python -m src.mix_engine.build_video --topic chernobyl --dry-run
```

5. 验证 `final_mix.mp4`（第二参数为 `output/` 下文件名）：

```bash
./scripts/verify_final_video.sh chernobyl final_mix.mp4
```

6. 验证 `final_packaged.mp4`（缺省第二参数时）：

```bash
./scripts/verify_final_video.sh chernobyl
```

---

## 十五、本速查文件在仓库中的位置

- 纯文本：仓库根目录 `StateVerge_COMMANDS.txt`
- 本文档：仓库内 `docs/StateVerge_COMMANDS.md`（你当前所读即此文件）
