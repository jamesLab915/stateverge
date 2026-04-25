# Mix Engine（AI 纪录片混剪）

## 目标

将 **LTX 生成镜头**、**Envato 真实 B-roll**、以及**采访/档案在转录、改写、AI 配音后的音轨**按 `mix/timeline.json` 统一时间线，经 **ffmpeg** 规范到 **1920×1080 / 30fps / yuv420p / AAC 48kHz 立体声** 后，拼成 `topics/<topic>/output/final_mix.mp4`。

不替代现有 `src/production` / `src/presenter_pipeline`；本模块**仅**负责混剪时间线与合成。

## 推荐内容比例（经验值）

| 来源 | 约占比 |
|------|--------|
| LTX | ~60% |
| Envato | ~25% |
| 采访/档案（重配后音轨） | ~15% |

## 合规与用法说明

- **采访**：不得直接使用可能侵权的**原始**同步对白；应使用**转录 → 改写 → 独立 AI 配音**后的 `sources/audio/*` 再进时间线。
- **Envato**：作**真实感 B-roll** 与情绪衔接，不当作**历史/事实的独立证据**。
- **LTX**：AI 画面，与文案、数据层一致时可作为叙述视觉。

## 目录约定（在 `topics/<topic>/` 下）

| 路径 | 用途 |
|------|------|
| `ltx/` | LTX 工程/中间文件（可选，与 `video/` 配合） |
| `video/` | 主 LTX 输出 MP4（`generate_timeline` 会扫描 `*.mp4`） |
| `envato/` | Envato B-roll MP4 |
| `sources/raw/` | 原始采访/档案（不入时间线，由你处理） |
| `sources/transcripts/` | 转录文本 |
| `sources/rewritten/` | 润色稿 |
| `sources/audio/` | 可上时间线的**已授权/已重配**音轨 `*.wav` / `*.mp3` / `*.m4a` |
| `mix/timeline.json` | 时间线（数组或 `{"clips":[...]}`，见 `timeline_loader`） |
| `mix/clips/` | 合成时中间片段（`--keep-temp` 时保留） |
| `output/` | 成片，含 `final_mix.mp4` |

## 命令

```bash
cd ~/StateVerge
export PYTHONPATH="$PWD"

# 根据 video/、envato/、sources/audio/ 自动生成时间线（不覆盖已有，除非 --force）
python -m src.mix_engine.generate_timeline --topic chernobyl

# 混剪合成
python -m src.mix_engine.build_video --topic chernobyl

# 仅打印计划
python -m src.mix_engine.build_video --topic chernobyl --dry-run

# 验证成片
./scripts/verify_final_video.sh chernobyl final_mix.mp4
```

## 依赖

- 系统已安装 `ffmpeg`、`ffprobe`，且在 `PATH` 中。
