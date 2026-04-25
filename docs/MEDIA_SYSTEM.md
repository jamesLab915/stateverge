# StateVerge Media Source System

## 1. 目标

为每个 `topics/<topic>/` 主题，从**旁白稿 / presenter 稿 / 简要**中抽取搜索词，在 **Pexels → Pixabay → DVIDS** 中检索、去重、按分辨率排序，将下载结果写入 `topics/<topic>/assets/raw/`，并生成 `topics/<topic>/assets/media_manifest.json`，供后期剪辑或 Mix 管线使用。

## 2. 支持来源

| 源 | 内容 | 说明 |
|----|------|------|
| Pexels | 视频 | 需 `PEXELS_API_KEY`；缺 key 时该源跳过、不崩溃 |
| Pixabay | 视频、图片 | 需 `PIXABAY_API_KEY`；优先拉满 `per_page` 视频，再补图片 |
| DVIDS | 政府/军方公开影像 | 多为美国防部 DVIDS 图库；**通常需** [api.dvidshub.net](https://api.dvidshub.net/) 注册的 public key（`DVIDS_API_KEY`）；无 key 时先尝试无参请求，若返回 403 则该源不返回可下载项 |

## 3. .env 配置

在 `~/StateVerge/.env` 中（示例见仓库根 `.env.example`）：

```env
PEXELS_API_KEY=
PIXABAY_API_KEY=
DVIDS_API_KEY=   # 可选
```

## 4. 调用方式（在仓库根）

```bash
export PYTHONPATH="$PWD"
cd ~/StateVerge
```

```bash
python -m src.integrations.media_sources.selector --topic chernobyl
```

## 5. 只生成 manifest、不下载文件

```bash
python -m src.integrations.media_sources.selector --topic chernobyl --no-download
```

## 6. 指定来源

```bash
python -m src.integrations.media_sources.selector --topic chernobyl --source pexels,pixabay
```

`--source` 默认：`pexels,pixabay,dvids`（逗号分隔、无空格）。

## 7. 输入 / 输出目录

**脚本查找顺序**（`--input` 可覆盖为任意 `txt`/`json` 路径）：

1. `topics/<topic>/brief/narration_script.txt`
2. `topics/<topic>/narration_script.txt`
3. `topics/<topic>/presenter/presenter_script.json`
4. `topics/<topic>/script/narration_script.txt`（常见脚手架旁白路径）
5. `topics/<topic>/brief/production_brief.json`

**输出**：

- 原始下载：`topics/<topic>/assets/raw/`
- 清单：`topics/<topic>/assets/media_manifest.json`（含 `topic`、`generated_at`、`segments`、每条的 `source/url/download_url/file/license` 等）

可选项（常用）：

- `--per-page`（每源、每 query 的条数上限，默认 5）
- `--max-queries`（全局搜索词上限，默认 8）
- `--max-per-segment`（每 segment 条数，默认 6）
- `--force`（覆盖已存在非空文件）
- `--no-download`

## 8. 与 Mix Engine 的关系

- `assets/raw/` 是**可审计**的真实素材落盘；可在后期人工或自动挑选后复制到 `topics/<topic>/assets/...`、Envato 子目录、或再喂给 `mix_engine` 的时间线 JSON。
- 本模块**不**修改 `mix_engine`、`production`、`presenter_pipeline` 的代码路径。

## 9. 合规与许可

- **Pexels / Pixabay**：清单中应保留 `license`、`source`、`url`；最终商用以各站当前条款与单条资源说明为准。
- **DVIDS / 军方与历史素材**：保留 `author`（credit）、`url` 页面与单位信息；DVIDS 有独立 ToS 与使用场景限制。
- **不要**将素材在叙事中当作**未经证实的“历史铁证”**；档案类镜头仅作**视觉/叙事辅助**，与学术或法律证据应区分。
