# StateVerge Studio Dashboard

A local web console (FastAPI + Jinja2 + vanilla HTML/CSS/JS) that gives you a
single bird's-eye view of every topic in `topics/` — what's done, what's
missing, and what to do next.

```
+--------+--------------------------------+----------+
|  Nav   |  Header  +  8 stage cards     |  Side    |
|        |  +  Topic table               |  panel   |
+--------+--------------------------------+----------+
```

## Start it

```bash
cd ~/StateVerge
./scripts/start_studio_dashboard.sh
```

Then open <http://127.0.0.1:8765>.

功能索引页：<http://127.0.0.1:8765/tools>（工具箱：面板入口 + 常用终端命令可复制）。

### 外接盘一键启动（NO NAME → StateVerge TS→MP4）

插好 **`NO NAME`** 与 **`StateVerge`** 后，在项目根目录执行：

```bash
./scripts/studio_external_drives_oneclick.sh
```

会自动：检查挂载 → 若 Studio 未运行则后台拉起 → 启动 TS→MP4 脚本（见 logs/ts_convert_runner.log）→ 用浏览器打开「工具箱」页的「磁盘 / 素材脚本」锚点 `#sec-disk_scripts`。  
仅拉起面板、不自动转换：`SKIP_CONVERT=1 ./scripts/studio_external_drives_oneclick.sh`

桌面双击图标：`~/Desktop/StateVerge一键启动.app`（会在「终端」里跑同一脚本，便于看日志）。

The script:
1. activates `.venv/`
2. installs `fastapi` / `uvicorn` / `jinja2` from `requirements.txt` if missing
3. runs `uvicorn src.studio_dashboard.app:app --reload` on port `8765`

Override host / port via env vars:

```bash
SV_DASHBOARD_HOST=0.0.0.0 SV_DASHBOARD_PORT=9000 ./scripts/start_studio_dashboard.sh
```

## What the scanner checks

Per topic (`topics/<slug>/`) it inspects:

| Artifact          | Path(s)                                                     |
| ----------------- | ----------------------------------------------------------- |
| `script`          | `brief/narration_script.txt`                                |
| `voice`           | `audio/voice.wav` &nbsp;or&nbsp; `audio/voice.mp3`          |
| `manifest`        | `assets/media_manifest.json`                                |
| `video clips`     | `video/*.mp4`, `video/_segments/*.mp4`                      |
| `envato`          | `envato/**/*.mp4`                                           |
| `runway prompts`  | `runway/*.txt`, `runway/*.json`                             |
| `runway videos`   | `runway/*.mp4`                                              |
| `ltx videos`      | `ltx/*.mp4`, `ltx/generated/*.mp4`                          |
| `segments`        | `mix/segments.json`, `mix/timeline.json`, `ltx/audio_chunks/audio_chunks_manifest.json` |
| `final mix`       | `output/final_mix.mp4` &nbsp;or&nbsp; `output/final_video.mp4` |
| `final packaged`  | `output/final_packaged.mp4` &nbsp;or&nbsp; `output/final_video_with_subtitles.mp4` |

> The user-spec names `final_mix.mp4` / `final_packaged.mp4` and the legacy
> pipeline names `final_video.mp4` / `final_video_with_subtitles.mp4` are
> **both accepted** as valid completion markers — so older topics aren't
> falsely flagged as incomplete.

### Skipped topics

The following are filtered out of the dashboard (test scaffolds / fixtures):

* anything starting with `_` (e.g. `_smoke_e2e`)
* anything starting with `SCAFFOLD_` (e.g. `SCAFFOLD_topic_mix`)
* anything starting with `does-not-exist` (smoke-test fixtures)
* explicit names: `prod-test`, `test-topic`, `hidden-rules`

Edit `src/studio_dashboard/scanner.py` (`SKIP_PREFIXES`, `SKIP_NAMES`) to
adjust.

## Status rules (most-broken-first wins)

| Status            | Trigger                                                |
| ----------------- | ------------------------------------------------------ |
| `Missing Script`  | no `brief/narration_script.txt`                        |
| `Missing Voice`   | has script, no voice file                              |
| `Missing Assets`  | has script + voice, no manifest / envato / clips       |
| `Need Mix`        | has assets, no `final_mix.mp4` / `final_video.mp4`     |
| `Need Package`    | has mix, no `final_packaged.mp4` / `…_with_subtitles.mp4` |
| `Ready`           | all of the above ✓                                     |

The right-side **next step suggestion** picks the worst cluster and tells you
which queue to clear first.

## 8 pipeline stages (top cards)

| #  | Name        | "Done" condition                                            |
| -- | ----------- | ----------------------------------------------------------- |
| 1  | 主题输入    | the topic dir exists                                        |
| 2  | 资料抓取    | manifest **or** envato/* **or** video/*                     |
| 3  | AI文案      | `narration_script.txt`                                      |
| 4  | 旁白生成    | `voice.wav` / `voice.mp3`                                   |
| 5  | 镜头Prompt  | `runway/*` **or** `mix/segments.json` / `timeline.json`     |
| 6  | 视频生成    | any clips: video/, envato/, runway/, ltx/                   |
| 7  | 包装合成    | `final_mix.mp4` / `final_video.mp4`                         |
| 8  | 发布准备    | `final_packaged.mp4` / `…_with_subtitles.mp4`               |

Stage card color:

* **green** — ≥ 75% of topics done
* **yellow** — 30–75%
* **red**    — < 30%

## Endpoints

| URL                                  | Type | What it returns                                              |
| ------------------------------------ | ---- | ------------------------------------------------------------ |
| `GET  /`                             | HTML | 总览 — 8 stage cards + topic table                            |
| `GET  /create`                       | HTML | 创作工作台 — script + voice generation                         |
| `GET  /assets/picker?topic=<slug>`   | HTML | 素材选择 — visual asset picker (see below)                     |
| `GET  /render?topic=<slug>`          | HTML | 渲染中心 — Shorts FFmpeg renderer (see below)                  |
| `GET  /shorts`                       | HTML | Shorts工厂 — `topics/<slug>/shorts/` pipeline status          |
| `GET  /finance`                      | HTML | 财报分析 — `topics/<slug>/finance/` pipeline status           |
| `GET  /assets`                       | HTML | 素材库 — asset libraries + `~/Downloads` uncategorized        |
| `GET  /api/topics`                   | JSON | full overview snapshot                                    |
| `GET  /api/shorts`                   | JSON | full shorts snapshot                                      |
| `GET  /api/finance`                  | JSON | full finance snapshot                                     |
| `GET  /api/assets`                   | JSON | source counts + recent files + missing-assets topics      |
| `POST /api/create/script`            | JSON | run OpenAI; write `brief/title.txt` + `narration_script.txt` + `shot_prompts.json` |
| `POST /api/create/voice`             | JSON | run ElevenLabs over the existing `narration_script.txt`; write `audio/voice.mp3` + `voice.wav` |
| `GET  /api/assets/picker?topic=<slug>` | JSON | full picker snapshot (sources + items + totals)         |
| `POST /api/assets/select`            | JSON | persist `topics/<slug>/mix/selected_assets.json`          |
| `POST /api/render/short`             | JSON | run FFmpeg; write `topics/<slug>/output/final_short.mp4`  |
| `GET  /api/health`                   | JSON | `{"ok": true, "service": "..."}` — quick liveness check   |

The JSON endpoints are flat single-snapshot dumps so you can script against
them (CI checks, Slack bots, cron alerts).

### Shorts pipeline (`/shorts`)

Per topic looks at `topics/<slug>/shorts/`:

| Artifact      | Path                                                |
| ------------- | --------------------------------------------------- |
| script        | `shorts/script.txt`                                 |
| voice         | `shorts/voice.wav` / `voice.mp3`                    |
| subtitles     | `shorts/subtitles.srt` / `subtitles.ass`            |
| final short   | `shorts/output/final_short.mp4` / `output/final.mp4`|

Status: `Missing Script` → `Missing Voice` → `Missing Subtitles` →
`Need Render` → `Ready` (most-broken-first).

### Finance pipeline (`/finance`)

Per topic looks at `topics/<slug>/finance/`:

| Artifact      | Path                                  |
| ------------- | ------------------------------------- |
| data          | `finance/data.json`                   |
| analysis      | `finance/analysis.json`               |
| script        | `finance/narration_script.txt`        |
| voice         | `finance/voice.wav` / `voice.mp3`     |
| final short   | `finance/output/final_short.mp4`      |

Status: `Missing Data` → `Missing Analysis` → `Missing Script` →
`Missing Voice` → `Need Render` → `Ready`.

### 创作工作台 (`/create`)

Single-page workflow that turns a `topic slug + title + length + language +
style` into the artifacts the rest of the pipeline expects.

**Form**

| Field    | Values                                                  |
| -------- | ------------------------------------------------------- |
| topic    | `[A-Za-z0-9][A-Za-z0-9._-]{0,63}` — also a `<datalist>` of existing topic dirs for quick reuse |
| title    | optional — leave blank to let the model invent one      |
| length   | `short` (≈60-90s) · `long` (≈8-12 min)                  |
| language | `zh` · `en`                                             |
| style    | `finance` · `documentary` · `social` · `funny`          |

**Buttons**

1. **生成文案** → `POST /api/create/script` → calls OpenAI once with
   `response_format=json_object` and writes:
   * `topics/<slug>/brief/title.txt`
   * `topics/<slug>/brief/narration_script.txt`
   * `topics/<slug>/brief/shot_prompts.json`  *(an array of
     `{id, text_chunk, visual, duration_sec}` per shot — `visual` is always
     English so it can feed Runway / LTX directly)*
2. **生成 ElevenLabs 旁白** → `POST /api/create/voice` → reads the existing
   `narration_script.txt`, chunks it (reusing
   `scripts/generate_eleven_tts_for_topic.py`), POSTs each chunk to
   ElevenLabs, concatenates with `ffmpeg`, transcodes to 48 kHz stereo PCM:
   * `topics/<slug>/audio/chunks/chunk_NNN.mp3`
   * `topics/<slug>/audio/voice.mp3`
   * `topics/<slug>/audio/voice.wav`
3. **进入素材选择** → links to `/assets`.
4. **进入渲染** → placeholder for the rendering page (next iteration).

**Safety guarantees** (enforced by `services/paths.py`):

* topic slug is regex-validated; rejected on dot/slash/backslash/empty/etc.
* every write path is resolved and re-checked against `repo_root/topics`,
  refusing anything that escapes via symlink or `..`
* if any of the target files already exist, they are first **moved** to
  `<dir>/archive/<YYYYMMDD_HHMMSS>/<filename>` — never deleted, never
  overwritten silently
* `OPENAI_API_KEY` / `ELEVENLABS_API_KEY` are read from `.env` server-side;
  the rendered HTML and JSON responses only ever expose the last 4
  characters of the keys (`…vJJz`) for "yes I see it" UX

**Curl examples**

```bash
# 1. Generate the script (short Chinese documentary)
curl -sS -X POST http://127.0.0.1:8765/api/create/script \
  -H 'Content-Type: application/json' \
  -d '{
        "topic": "ai_future_short",
        "title": "AI 让普通人变得更强还是更脆弱",
        "length": "short", "language": "zh", "style": "documentary"
      }' | jq .

# 2. Generate the ElevenLabs voice for that script
curl -sS -X POST http://127.0.0.1:8765/api/create/voice \
  -H 'Content-Type: application/json' \
  -d '{"topic": "ai_future_short"}' | jq .
```

**.env keys read by /create**

```
OPENAI_API_KEY              required for /api/create/script
OPENAI_MODEL                optional, default gpt-4o-mini
ELEVENLABS_API_KEY          required for /api/create/voice
ELEVENLABS_VOICE_ID         required (refuses to auto-pick a voice)
ELEVENLABS_MODEL_ID         optional, default eleven_multilingual_v2
ELEVENLABS_STABILITY        optional, default 0.5
ELEVENLABS_SIMILARITY_BOOST optional, default 0.8
ELEVENLABS_STYLE            optional, default 0.0
ELEVENLABS_USE_SPEAKER_BOOST optional, default true
```

### 素材选择 (`/assets/picker`)

Visual picker for one topic. Open with `/assets/picker?topic=<slug>`; without
the query, you get a topic chooser.

**Sources scanned** (in order, capped at 250 most-recent files / source):

| ID            | Path                                    | Scope  |
| ------------- | --------------------------------------- | ------ |
| `envato`      | `assets/envato/`                        | global |
| `runway`      | `assets/runway/`                        | global |
| `pexels`      | `assets/pexels/`                        | global |
| `pixabay`     | `assets/pixabay/`                       | global |
| `topic_raw`   | `topics/<slug>/assets/raw/`             | topic  |
| `topic_video` | `topics/<slug>/video/`                  | topic  |
| `topic_envato`| `topics/<slug>/envato/`                 | topic  |

For each video the picker shows: filename · duration · resolution · size MB ·
relative path. ffprobe results are cached in `.cache/asset_probe.json` keyed
by `abs_path::mtime::size`, so re-opening the page is sub-second.

**Save** writes `topics/<slug>/mix/selected_assets.json` (old file → 
`mix/archive/<YYYYMMDD_HHMMSS>/`):

```json
{
  "topic": "ai_future_cn",
  "selected_at": "2026-04-29T05:30:00+00:00",
  "options": { "per_clip_sec": 6.0, "shuffle": false },
  "items": [
    { "source": "envato", "rel_path": "...", "abs_path": "...",
      "filename": "...", "size_bytes": 12345678,
      "duration_sec": 15.0, "width": 1920, "height": 1080, "fps": 29.97 },
    ...
  ]
}
```

### 渲染中心 (`/render`)

One-click FFmpeg renderer. Open with `/render?topic=<slug>`; without the
query, you get a topic chooser.

**Pipeline**:
1. Probe `topics/<slug>/audio/voice.wav` for duration.
2. Read `topics/<slug>/mix/selected_assets.json`; validate every `abs_path`
   actually exists, lives under the repo, and has a video extension.
3. Build a segment plan: loop through items in order, per-clip duration =
   `options.per_clip_sec` (default 6.0s), trimmed by source clip length, never
   shorter than 1.0s. Last segment is shrunk so total coverage equals voice
   duration. Avoids placing the same clip twice in a row.
4. Encode each segment to `output/.render_tmp/seg_NNN.mp4` with
   `scale=1080:1920:force_original_aspect_ratio=increase, crop=1080:1920,
   fps=30, setpts=PTS-STARTPTS`, libx264 + yuv420p, CRF 20.
5. `ffmpeg -f concat -safe 0 -c copy` → `output/.render_tmp/silent.mp4`.
6. Mux silent.mp4 + voice.wav → `topics/<slug>/output/final_short.mp4` with
   `-map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k -ar 48000 -ac 2
   -shortest -movflags +faststart`.
7. ffprobe the final to verify; truncate any prior `render_error.log`.

**Output spec** (always):

| Property   | Value          |
| ---------- | -------------- |
| Resolution | 1080 × 1920    |
| Frame rate | 30 fps         |
| Pixel fmt  | yuv420p        |
| Video      | libx264, CRF 20|
| Audio      | AAC 192k, 48 kHz, stereo |
| Streamable | `+faststart`   |

**Safety**:

* old `output/final_short.mp4` is moved to
  `output/archive/<YYYYMMDD_HHMMSS>/` *before* the mux that would otherwise
  overwrite it — so you never lose a previous take
* on any failure, ffmpeg's stderr + a header line is appended to
  `topics/<slug>/output/render_error.log` (tail -f friendly)
* every input path is `Path.resolve()`d and refused unless inside `repo_root`

**Curl examples**

```bash
# 1. Inspect the picker payload (sources + items + totals)
curl -sS "http://127.0.0.1:8765/api/assets/picker?topic=ai_future_cn" | jq '.totals'

# 2. Save a selection (3 clips)
curl -sS -X POST http://127.0.0.1:8765/api/assets/select \
  -H 'Content-Type: application/json' \
  -d '{
        "topic": "ai_future_cn",
        "per_clip_sec": 6.0,
        "items": [
          {"abs_path": "/Users/me/StateVerge/topics/ai_future_cn/assets/raw/segment_01_pexels_005.mp4"},
          {"abs_path": "/Users/me/StateVerge/topics/ai_future_cn/assets/raw/segment_01_pexels_006.mp4"},
          {"abs_path": "/Users/me/StateVerge/topics/ai_future_cn/assets/raw/segment_01_pexels_007.mp4"}
        ]
      }' | jq .

# 3. Render to topics/ai_future_cn/output/final_short.mp4
curl -sS -X POST http://127.0.0.1:8765/api/render/short \
  -H 'Content-Type: application/json' \
  -d '{"topic": "ai_future_cn"}' | jq .
```

### Asset library (`/assets`)

Scans (recursively, capped at 20k files / source):

* `assets/envato/`
* `assets/runway/`
* `assets/pexels/`
* `assets/pixabay/`
* `assets/xhs/`        (bonus, from the XHS grabber)
* `~/Downloads/`       (top-level only, kept fast)

For each source: counts of videos / images / audio / archives. Side panel
breaks out `~/Downloads` "uncategorized" (videos + images + zips that haven't
been moved into `assets/`), plus the slugs of topics flagged
`Missing Assets` by the main scanner.

## Files added by this feature

```
src/studio_dashboard/__init__.py
src/studio_dashboard/scanner.py                   (overview + shorts + finance + assets)
src/studio_dashboard/app.py                       (7 page routes + 5 GET + 5 POST JSON routes)
src/studio_dashboard/services/__init__.py
src/studio_dashboard/services/paths.py            (slug + path safety, archive helper)
src/studio_dashboard/services/openai_writer.py    (single-call script + title + shot prompts)
src/studio_dashboard/services/eleven_tts.py       (chunk → ElevenLabs → mp3 → wav)
src/studio_dashboard/services/asset_picker.py     (multi-source video scan + ffprobe cache + save)
src/studio_dashboard/services/ffmpeg_renderer.py  (plan + per-segment encode + concat + mux)
src/studio_dashboard/templates/_base.html         (shared layout w/ nav + topbar slots)
src/studio_dashboard/templates/index.html         (总览)
src/studio_dashboard/templates/create.html        (创作工作台)
src/studio_dashboard/templates/asset_picker.html  (素材选择)
src/studio_dashboard/templates/render.html        (渲染中心)
src/studio_dashboard/templates/topic_chooser.html (no-topic landing for picker / render)
src/studio_dashboard/templates/shorts.html        (Shorts工厂)
src/studio_dashboard/templates/finance.html       (财报分析)
src/studio_dashboard/templates/assets.html        (素材库)
src/studio_dashboard/static/style.css
scripts/start_studio_dashboard.sh
docs/STUDIO_DASHBOARD.md
requirements.txt   (added fastapi, uvicorn[standard], jinja2)
```

No existing files are deleted or renamed.

## Extending

* **New artifact**: add a check in `scanner.scan_topic()`, set a flag on
  `TopicStatus`, and reference it in the relevant stage's `STAGES` lambda
  (in `app.py`'s render or `scanner.summarize`).
* **New nav page**: append to `NAV_ITEMS` in `app.py` and add a `<section>`
  with matching `id` to `templates/index.html` (only `#overview` is wired
  today; the others are placeholders for future panels).
* **Watch mode**: `--reload` is on by default — edit any of the python /
  template / css files and the server hot-reloads. Just refresh the browser.
