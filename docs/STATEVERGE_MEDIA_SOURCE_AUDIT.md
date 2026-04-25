# StateVerge 媒体素材源审计

**范围**：`.env.example`、`src/`、`scripts/`、`docs/`、`topics/` 根级文档（不含 `.venv`）。**日期**：以仓库当时快照为准。

## 1. 各素材源是否已“正式接入”

| 源 | 状态 | 说明 |
|----|------|------|
| **Pexels** | **未接入**（无 API 与下载模块） | 仅出现在**文档/表格**与 **Envato 包内许可证说明/外链** 中。 |
| **Pixabay** | **未接入**（无 API 与下载模块） | 仅在 `docs/tracking/subscriptions.md` 的订阅占位行中出现。 |
| **DVIDS (dvids / DVIDS)** | **未接入** | 全项目搜索**无**代码、环境变量与脚本引用。 |
| **Envato** | **已部分接入** | 环境变量、healthcheck、生产侧素材选型与分拣脚本；见下文。 |
| **LTX / Runway / HeyGen 等** | 按既有 `integrations` / `production` 设计，**非** Pexels/Pixabay/DVIDS 路径。本报告不展开。 |

## 2. 相关文件路径（按源）

### Pexels

- `docs/tracking/subscriptions.md`：表格占位行 “Pexels / Pixabay (if paid)”.
- `assets/envato/_licenses/*`：多处 **Pexels 作品 URL 文本**（为 Envato 包带的说明/帮助文件，**非** 项目 API 调用）：
  - `_HELP.txt`, `Helpfile.txt`, `Video Links.txt`, `Help Media.txt`, `Links.txt` 等.

### Pixabay

- `docs/tracking/subscriptions.md`：同表并列提及（无独立实现）。

### DVIDS

- 未发现相关路径。

### Envato（本仓库中“真实可运行”的素材/授权侧）

- `.env.example`：`ENVATO_API_KEY=`
- `src/integrations/healthcheck.py`：`_check_envato()` 检查 `ENVATO_API_KEY`。
- `src/production/envato_picker.py`、`src/production/packaging_engine.py`、`src/production/paths.py` 等与 Envato 本地目录/选曲相关（依 brief 与本地 `assets/envato`）。
- `src/utils/sort_envato.py`：从 `~/Downloads` 分拣到 `assets/envato/`（**不** 调 Pexels/Pixabay API）.
- `src/mix_engine/*`：时间线中 `type: "envato"` 指向 `topics/<topic>/envato/*.mp4` 文件，**不** 负责下载.

## 3. 是否有“真实下载逻辑”

| 源 | 结论 |
|----|------|
| Pexels | **无** Python 中针对 Pexels API 的下载/检索实现。 |
| Pixabay | **无**。 |
| DVIDS | **无**。 |
| Envato | **有与 Envato/本地工作流**相关的逻辑：许可证分拣（`sort_envato`）、生产打包时的素材使用；**是否** 走 Envato 官方网络 API 取决于你配置的 `ENVATO_API_KEY` 与具体使用路径，但**不是** Pexels/Pixabay. |

**结论**：Pexels / Pixabay / DVIDS **均未被正式接入**为可编程媒体 API；若需要，应新增独立模块 + 环境变量 + 统一落盘到 `assets/` 或 `topics/<t>/` 的约定子目录.

## 4. 是否仅在文档/许可证文本中提到

- **Pexels**：是（订阅表 + Envato 包内第三方链接说明）。
- **Pixabay**：是（仅订阅表）。
- **DVIDS**：**未在文档中作为独立小节出现**；亦未在代码出现。
- **Envato**：**代码 + 配置** 中均有实际接入点。

## 5. 是否需要补模块（建议）

若产品目标包含 **Pexels / Pixabay 程序化搜图/视频**：

- 建议新增，例如 `src/integrations/pexels_client.py`、`src/integrations/pixabay_client.py`（或 `src/stock/…` 统一入口），在 `.env.example` 中增加 `PEXELS_API_KEY` / `PIXABAY_API_KEY`（以各平台官方变量名为准）.
- **DVIDS**：需确认是否使用公开 API/条款；再决定是否单独 `dvids_*.py` 或 `requests` 封装.

若仅使用**浏览器手下载** + 现有 `sort_envato` 与 `assets/envato` 流程，则**不必**为 Pexels 单独写 API，但应在文档中明确“人工作业/不自动拉 Pexels”.

## 6. 推荐的统一目录结构（与现有一致、可扩展）

在保持 **现有** `assets/envato/` 与 `topics/<topic>/{video,envato,…}` 的前提下，若后续接入**免版税 API** 下载，建议：

```text
assets/
  pexels/         # 可选；API 或脚本拉取的 Pexels 元数据与落盘
  pixabay/        # 可选
  envato/         # 已存在
topics/<topic>/
  video/          # LTX 出片
  envato/         # 本 topic 选用的 Envato/本地 B-roll（与 mix 引擎一致）
  sources/        # 采访/重配音等
```

**避免** 与错误路径 `output/<topic>/` 混淆；成片以 **`topics/<topic>/output/`** 为准。

## 7. 检索关键词摘要（本仓库，排除 `.venv`）

- `PEXELS_API_KEY` / `PIXABAY_API_KEY`：**未**在 `.env.example` 或 `src/` 出现.
- `pexels` / `pixabay` / `dvid`：命中主要为 **上述文档与 `assets/envato/_licenses`**.
- `video_source` / `media_sources` / `asset downloader`：**无** 与三源对应的统一“asset downloader”模块名.

---

*本报告为技术审计，不构成版权或许可法律建议。*
