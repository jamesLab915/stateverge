# Presenter 插入流水线

面向本地生产的 Python 3.11+ 工具链：规则化规划主持人时间线、ElevenLabs 配音、按 Runway 要求切分音频、用 **母版 + 可选生成池** 拼 base 视频、准备 Lip Sync 输入、在你放回 Runway 输出后回拼每段，并最终把主持段 **加插** 进 `narrative_main.mp4`。

## 身份硬规则（固定主持人）

- **唯一参考图**（未来变体生成必用）：`assets/presenter_reference/host_master.png`  
  若不存在：会在 `presenter_manifest.json` 中写入 `reference_image_status: missing` 与 `errors`，并打清晰日志；**不影响** `presenter_masters` 拼接与整条母版流水线。
- 所有主持镜头视为 **同一人** 的不同变体；禁止在 Prompt/配置中改人脸、发型、衣着、年龄感、性别等；仅允许语境、动作幅度、镜头远近、情绪与光线（详见 `variant_generator.py` 中常量）。
- 可选第二素材池（未来或混用）：`assets/presenter_generated/{intro,insert,outro}/` — 拼接时 **优先** `presenter_masters`，再使用对应子目录下的 mp4（按时长粗分为 5s/10s 池）。

## 环境

- Python 3.11+
- `ffmpeg` / `ffprobe` 在 `PATH` 中
- 依赖：`requirements.txt`（`requests`, `python-dotenv`）

## 安装

```bash
cd ~/StateVerge
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# 填写 ELEVENLABS_*；放入母版 mp4 与（建议）host_master.png
```

## 目录约定

- `assets/presenter_reference/host_master.png` — 固定身份参考（变体生成 stub 会检查）
- `assets/presenter_masters/5s/`、`10s/` — 主母版池（必用做第一来源）
- `assets/presenter_generated/{intro,insert,outro}/` — 预留池
- `topics/<topic>/` — 脚本、主片、音频、presenter 子目录、`output/final_with_presenter.mp4`

`--plan` 会 `mkdir` 上述 `assets` 标准子目录。

## 使用

```bash
export PYTHONPATH="$PWD"
python -m src.presenter_pipeline.cli --topic <slug> --plan
python -m src.presenter_pipeline.cli --topic <slug> --tts
python -m src.presenter_pipeline.cli --topic <slug> --split-audio
python -m src.presenter_pipeline.cli --topic <slug> --build-base
python -m src.presenter_pipeline.cli --topic <slug> --prepare-runway
python -m src.presenter_pipeline.cli --topic <slug> --assemble-segments
python -m src.presenter_pipeline.cli --topic <slug> --assemble-full
```

合成前准备（不含 Runway 回片与全片）：

```bash
python -m src.presenter_pipeline.cli --topic <slug> --full-prep
```

**变体生成占位（v1 不调 API）**：检查参考图并写入 `generated_variants`：

```bash
python -m src.presenter_pipeline.cli --topic <slug> --variants-stub
```

## 日志格式

统一为一行：

`[presenter] topic=<slug> segment=<name|--> action=<step> details=<...>`

## Manifest

`topics/<topic>/presenter/manifests/presenter_manifest.json` 含：

- `reference_image_path`, `reference_image_status`, 顶层 `errors`
- `generated_variants`（由 `--variants-stub` 或未来真实生成更新）
- 每段：`split_points`, `base_video_paths`, `selected_master_files`, `selected_plan`, Runway 路径等

## 与 Runway

- **输入**：`lipsync_input/<segment>/part_01/{base_video.mp4,audio.wav,meta.json}`（`meta.json` 含 `segment_type`）
- **输出**（你放入）：`lipsync_output/<segment>/part_01.mp4`

## 环境变量

见 `.env.example`；`STATEVERGE_ROOT` 可选，默认 `~/StateVerge`。

## 示例脚本

见仓库根目录 `presenter_script.example.json`，可复制为 `topics/<slug>/script/presenter_script.json`。
