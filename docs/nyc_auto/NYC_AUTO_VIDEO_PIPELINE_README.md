# NYC_AUTO 视频生产 V1

## 系统用途

NYC_AUTO V1 在**不移动、不修改原始 AirDrop 素材**的前提下：

- 扫描并登记 **≥ 3 分钟**的长视频到 `projects/<project_id>/`（`source.json`、`ffprobe.json` 等）。
- 用 **ffmpeg** 从长视频生成竖屏 **Shorts 候选片段**（9:16，默认 30 秒，默认 5 段）。
- 可选生成 **长视频音乐版**（可垫乐、淡入淡出、原声/配乐比例）。
- 生成本地 **YouTube 上传包草案**（英文标题选项、描述、标签、封面截帧与加字版），**不调用外部 API**。

所有步骤 **fail-open**：单条 ffmpeg 任务失败会记录日志并尽量继续；**仅写入 `projects/` 与 `output/`**，不删除 `raw/airdrop` 中的源文件。

## 目录结构

### 外接盘 `NYC_AUTO`（`/Volumes/StateVerge/NYC_AUTO`）

| 路径 | 说明 |
|------|------|
| `library/long_video/index.csv` | 长视频总索引（去重用） |
| `projects/<project_id>/` | 每项目：`source.json`、`ffprobe.json`、`notes.txt`、`output/`、`shorts/`、`thumbnails/`、`logs/` |
| `output/shorts/` | 同步副本 `/<project_id>_short_NNN.mp4` |
| `output/long/` | `/<project_id>_long_music.mp4` |
| `output/packages/<project_id>/` | 上传包：`title_options.txt`、`description.txt`、`tags.txt`、封面、`selected_video_path.txt` |
| `logs/` | `nyc_ingest_*.log`、`nyc_shorts_*.log` 等 |
| `cache/` | 预留 |

### 项目内 `~/StateVerge`

| 路径 | 说明 |
|------|------|
| `scripts/nyc_auto/` | Python 流水线脚本 |
| `docs/nyc_auto/` | 本文档 |
| `logs/system/` | 本地镜像日志：`nyc_ingest_*.log`、`nyc_shorts_*.log`、`nyc_pipeline_*.log` 等 |

## 长视频如何进入系统

1. 原始视频仍在：`NYC_AUTO/raw/airdrop/video/...`（不会被脚本移动或删除）。
2. 运行入库（见下）。脚本用 **ffprobe** 读时长与编码信息；仅当 **duration ≥ 180 秒** 时登记。
3. `project_id` 规则：`YYYY-MM-DD` + 文件名 slug + 路径指纹哈希 8 位，同一文件重复运行会跳过。
4. 总索引：`library/long_video/index.csv` 追加一行；已存在的 `source_path` 不重复登记。

## 如何生成 Shorts

- 默认从片中均匀取起点：**跳过前/后约 10 秒**；若总长 **> 20 分钟**，在 **10%～90%** 区间内抽样。
- 输出：`projects/<id>/shorts/short_001.mp4` … 及对应 `short_001.json`；并复制到 `output/shorts/<id>_short_001.mp4`。
- 无音轨时自动垫 **anullsrc** 静音 AAC；导出后用 ffprobe 检查含视频与音频。

## 如何生成长视频音乐版

- 读取 `source.json`，支持 `--cut-start`（秒）、`--target-minutes`（限制总长）。
- 若提供 `--music`：配乐循环对齐视频长度，**淡入 3s / 淡出 5s**；有原声时 **原声 20% + 配乐 80%**；无原声时仅配乐。
- 不传 `--music`：保留原声；若无原声则垫静音轨以满足「含音频」校验。
- 输出：`projects/<id>/output/long_music.mp4` 与 `output/long/<id>_long_music.mp4`。

## 如何生成 YouTube 上传包

```bash
python3 scripts/nyc_auto/nyc_generate_youtube_package.py --project-id <id> --type shorts
# 或
python3 scripts/nyc_auto/nyc_generate_youtube_package.py --project-id <id> --type long
```

可选 `--title-topic "Your Topic"`。生成物在 `output/packages/<id>/`，封面同步自 `projects/<id>/thumbnails/`。

## 常用命令

在 **`~/StateVerge`** 下：

```bash
# 仅入库
python3 scripts/nyc_auto/nyc_ingest_long_videos.py

# Shorts（单项目或多项目）
python3 scripts/nyc_auto/nyc_generate_shorts_candidates.py --project-id <id> --count 5 --duration 30
python3 scripts/nyc_auto/nyc_generate_shorts_candidates.py --all

# 长视频音乐版
python3 scripts/nyc_auto/nyc_make_long_music_version.py --project-id <id> --music /path/to/music.mp3 --cut-start 540 --target-minutes 60

# 上传包
python3 scripts/nyc_auto/nyc_generate_youtube_package.py --project-id <id> --type shorts --title-topic "Manhattan Night Drive"

# 一键流水线
python3 scripts/nyc_auto/nyc_auto_pipeline.py --ingest
python3 scripts/nyc_auto/nyc_auto_pipeline.py --all --make-shorts --package --pkg-type shorts
python3 scripts/nyc_auto/nyc_auto_pipeline.py --project-id <id> --make-long --music "/path/to/music.mp3" --cut-start 540 --package --pkg-type long
```

依赖：**ffmpeg / ffprobe** 在 `PATH` 中；外接 SSD **已挂载**为 `/Volumes/StateVerge`。

## 故障排查

| 现象 | 建议 |
|------|------|
| `ERROR: NYC_ROOT not mounted` | 连接 SSD，确认卷名为 `StateVerge` |
| `ffprobe_failed` / `FFMPEG_FAIL` | `ffmpeg -version`；检查源文件是否可读、编码是否怪异 |
| Shorts 全失败 | 源分辨率或时间点非法；查看 `projects/<id>/logs/nyc_shorts_*.log` |
| `VERIFY_FAIL no_audio` | 重跑前删除对应 `.partial.mp4` 残留；检查 ffmpeg 是否裁剪掉音轨 |
| ffmpeg 临时文件 / muxer | 中间产物使用 `*.partial.mp4`，勿用 `.mp4.part`（ffmpeg 可能报 exit 234） |

日志位置：**SSD** `NYC_AUTO/logs/nyc_*_YYYY-MM-DD.log` 与 **本地** `~/StateVerge/logs/system/` 同名文件。

## 安全原则

- **不改原素材**：`raw/airdrop` 下文件只读使用。
- **不删除原视频**。
- **输出只写入** `projects/`、`output/`、`library/`、`logs/`、`cache/`。
- **ffmpeg 失败不拖垮全流程**：单片段/单任务失败记录后继续（一键流水线会带上非零退出码中较大者，便于 CI；交互使用可看日志）。

YouTube Data API 上传（OAuth、默认 private、`upload_history`）说明见 [`YOUTUBE_UPLOAD_API_README.md`](YOUTUBE_UPLOAD_API_README.md)。
