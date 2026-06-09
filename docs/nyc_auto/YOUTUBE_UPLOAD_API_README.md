# YouTube Data API 上传层（NYC_AUTO）

## 目的

将 ` /Volumes/StateVerge/NYC_AUTO/output/packages/<project_id>/` 内的上传包（视频路径 + 文案草案）通过 **YouTube Data API v3** 上传到你的频道。

**默认值为 `private`。** 仅当同时使用 `--privacy public` 与 `--allow-public` 时才允许公开。不会自动删除本地文件，不会修改包内 `title_options.txt` / `description.txt` / `tags.txt`。

---

## Google Cloud 配置概要

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 创建项目。
2. 启用 **YouTube Data API v3**。
3. 配置 **OAuth consent screen**（External 或 Internal 视组织而定）。
4. 创建 **OAuth 客户端 ID**，类型选择 **Desktop app**。
5. 下载 JSON，保存为：
   ```text
   ~/StateVerge/.secrets/youtube/client_secrets.json
   ```
6. 在 **OAuth consent screen → Scopes** 中，确保包含与 **YouTube Data API v3** 相关的 **`youtube.upload`** 与 **`youtube.readonly`**（与 `youtube_scopes.py` 中列表一致）；否则重新授权时可能被拒或缺少频道只读能力。
7. 本地参考形状见：`.secrets/youtube/client_secrets.example.json`（勿把真实密钥提交 git）。

---

## 安装依赖

```bash
cd ~/StateVerge
python3 -m pip install -r requirements-youtube.txt
```

---

## 首次授权（仅建立 token，不上传）

```bash
python3 scripts/nyc_auto/youtube_auth_init.py
```

可选：

```bash
python3 scripts/nyc_auto/youtube_auth_init.py \
  --client-secrets ~/.secrets/youtube/client_secrets.json \
  --token ~/StateVerge/data/youtube/token.json
```

成功后 token 默认写入：`~/StateVerge/data/youtube/token.json`。

授权范围包括 **`youtube.upload`**（上传）与 **`youtube.readonly`**（读取频道等元数据）。若旧 token 仅有 upload scope，`channels().list(mine=True)` 会报 *insufficient authentication scopes*，需删除 token 后重新跑 `youtube_auth_init.py`。

确认当前登录频道（不上传）：

```bash
python3 scripts/nyc_auto/youtube_whoami.py
```

Shorts 专用频道（Real NYC Shorts）：

```bash
python3 scripts/nyc_auto/youtube_whoami.py --token ~/StateVerge/data/youtube/token_shorts.json
```

---

## 单文件直传（无需 publish_pack 目录）

| 脚本 | OAuth | 频道 |
|------|-------|------|
| `youtube_upload_direct_nyc.py` | `data/youtube/token.json` | StateVerge NYC（长视频） |
| `youtube_upload_direct_nyc_shorts.py` | `data/youtube/token.json` | StateVerge NYC 上发布 **Short 成片**（路径可含 `shorts_clips`） |
| `youtube_upload_direct_zhang_ziwei.py` | `data/youtube/token_shorts.json` | **国语音乐** 频道 · 张子维 MV（占用原 Shorts OAuth 槽位） |
| ~~`youtube_upload_direct_shorts.py`~~ | — | **已停用**（channel guard: `shorts_channel_repurposed`） |

长频道发 Short 示例：

```bash
cd ~/StateVerge
.venv/bin/python3 scripts/nyc_auto/youtube_upload_direct_nyc_shorts.py \
  --video "/Volumes/SV_TRANSFER/ready_to_upload/shorts_clips/your_clip.mp4" \
  --title "NYC Evening Walk #Shorts" \
  --privacy unlisted
```

---

## Dry-run（只打印计划上传内容，不调用 API）

单包：

```bash
python3 scripts/nyc_auto/youtube_upload.py \
  --package-dir "/Volumes/StateVerge/NYC_AUTO/output/packages/<project_id>" \
  --dry-run
```

批量：

```bash
python3 scripts/nyc_auto/youtube_batch_upload.py --dry-run --limit 1
```

---

## 私密上传（默认）

```bash
python3 scripts/nyc_auto/youtube_upload.py \
  --package-dir "/Volumes/StateVerge/NYC_AUTO/output/packages/<project_id>" \
  --privacy private
```

---

## Unlisted 上传

```bash
python3 scripts/nyc_auto/youtube_upload.py \
  --package-dir "/Volumes/StateVerge/NYC_AUTO/output/packages/<project_id>" \
  --privacy unlisted
```

---

## Public 上传（必须显式双重确认）

```bash
python3 scripts/nyc_auto/youtube_upload.py \
  --package-dir "/Volumes/StateVerge/NYC_AUTO/output/packages/<project_id>" \
  --privacy public \
  --allow-public
```

---

## 批量上传

```bash
python3 scripts/nyc_auto/youtube_batch_upload.py --privacy private --limit 3
```

可选：`--type shorts|long|all`、`--playlist-id <id>`、`--packages-root <path>`。

---

## 与 NYC pipeline 集成

```bash
python3 scripts/nyc_auto/nyc_auto_pipeline.py --project-id <id> --package --youtube-upload --privacy private
```

```bash
python3 scripts/nyc_auto/nyc_auto_pipeline.py --project-id <id> --youtube-upload --privacy unlisted
```

```bash
python3 scripts/nyc_auto/nyc_auto_pipeline.py --project-id <id> --youtube-upload --privacy public --allow-public
```

仅上传时需已有包目录；可先 `--package` 再 `--youtube-upload`，或预先生成包。

`--dry-run-upload`：走与线上一致的元数据解析，但不调用 YouTube API（不写成功历史）。

---

## 日志与历史 CSV

| 位置 | 说明 |
|------|------|
| `/Volumes/StateVerge/NYC_AUTO/logs/youtube_upload_history.csv` | SSD 上汇总（SSD 未挂载时仅写本地） |
| `~/StateVerge/data/youtube/upload_history.csv` | 本地汇总 |
| `/Volumes/StateVerge/NYC_AUTO/logs/youtube_upload_YYYY-MM-DD.log` | SSD 详细日志 |
| `~/StateVerge/logs/system/youtube_upload_YYYY-MM-DD.log` | 项目内详细日志 |

`status=success` 的 `video_path` 默认不会重复上传；需重传时加 `--force-reupload`。

---

## 安全与合规提醒

- **默认 private**；不要把 `token.json`、`client_secrets.json` 提交到 Git（见 `.gitignore`）。
- **upload_history** 用于防重复上传；每次 `videos.insert` 消耗 YouTube API **quota**。
- Shorts 与长视频可使用不同 `--playlist-id` 分批管理。
- **版权**：使用带版权配乐仍可能触发 Content ID；API 上传不代表获得版权许可。

---

## Git 中应忽略的文件

- 整个目录：`~/StateVerge/.secrets/`
- `~/StateVerge/data/youtube/token*.json`
- `~/StateVerge/data/youtube/client_secrets*.json`
- 通配：`**/*.client_secret.json`

（已在仓库 `.gitignore` 中配置。）

`upload_history.csv` **未**被默认忽略，若含敏感信息可自行再忽略。
