"""
Static catalog for Studio 「工具箱」页：列出面板入口 + 常用 CLI（便于查阅，不全等同自动化）。
"""

from __future__ import annotations

from typing import Any

# Each section: id, title, blurb?, items[]
# item: title, desc, kind in ("page","shell","doc"), href?, command?, doc_path?, note?

TOOLBOX_SECTIONS: tuple[dict[str, Any], ...] = (
    {
        "id": "topic_studio",
        "title": "Topic · 创作 · 流水线",
        "blurb": "原侧边栏除 NYC 发布监控外的入口；topics/、创作与导出相关。「NYC 发布监控」同列于首条。",
        "items": (
            {
                "title": "NYC 发布监控（launchd 今日摘要）",
                "desc": "外盘 07_AUTOMATION/logs；JSON：<code>/api/nyc</code>",
                "kind": "page",
                "href": "/nyc",
            },
            {
                "title": "总览（流水线状态表）",
                "desc": "所有 topic 进度一览 · 等价点击左上角 SV 品牌",
                "kind": "page",
                "href": "/",
            },
            {
                "title": "证据档案（IRS · EB-1 / NIW）",
                "desc": "docs/tracking 浏览、下载与常用脚本命令",
                "kind": "page",
                "href": "/tracking",
            },
            {
                "title": "创作工作台",
                "desc": "OpenAI 文案 + ElevenLabs 旁白（需 .env）",
                "kind": "page",
                "href": "/create",
            },
            {
                "title": "素材选择",
                "desc": "按 topic 勾选成片素材",
                "kind": "page",
                "href": "/assets/picker",
            },
            {
                "title": "渲染中心",
                "desc": "Shorts FFmpeg 渲染",
                "kind": "page",
                "href": "/render",
            },
            {
                "title": "小红书工坊",
                "desc": "笔记抓取 / 配音 / 拼接",
                "kind": "page",
                "href": "/xhs",
            },
            {
                "title": "Shorts 工厂",
                "desc": "topics/*/shorts/ 状态",
                "kind": "page",
                "href": "/shorts",
            },
            {
                "title": "财报分析",
                "desc": "topics/*/finance/ 管线",
                "kind": "page",
                "href": "/finance",
            },
            {
                "title": "素材库",
                "desc": "全局素材与 Downloads",
                "kind": "page",
                "href": "/assets",
            },
        ),
    },
    {
        "id": "launch",
        "title": "一键启动",
        "blurb": "插好 NO NAME + StateVerge 盘后可用",
        "items": (
            {
                "title": "外接盘一键（Studio + TS→MP4 + 打开浏览器）",
                "desc": "等同于脚本 studio_external_drives_oneclick.sh",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\n./scripts/studio_external_drives_oneclick.sh",
            },
            {
                "title": "仅启动 Studio Dashboard",
                "desc": "前台运行 uvicorn（可加 SV_DASHBOARD_PORT）",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\n./scripts/start_studio_dashboard.sh",
            },
            {
                "title": "Finder 双击启动 Studio（外接盘一键）",
                "desc": "scripts/Launch_StateVerge_Studio.command — 首次需在终端 chmod +x；需挂载 NO NAME + StateVerge",
                "kind": "shell",
                "command": "chmod +x \"$HOME/StateVerge/scripts/Launch_StateVerge_Studio.command\"\nopen \"$HOME/StateVerge/scripts/Launch_StateVerge_Studio.command\"",
            },
            {
                "title": "长视频音乐 bed 一键（GUI 选歌 → 2h）",
                "desc": "scripts/start_long_music_oneclick.sh；双击打 Launch_Long_Music_Bed.command",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\nchmod +x scripts/Launch_Long_Music_Bed.command scripts/start_long_music_oneclick.sh\n./scripts/start_long_music_oneclick.sh /path/to/licensed_track.wav",
            },
        ),
    },
    {
        "id": "disk_scripts",
        "title": "磁盘 / 素材脚本（终端）",
        "items": (
            {
                "title": "TS → MP4（NO NAME → StateVerge/NYC/video）",
                "desc": "递归转换 .ts；校验通过后删原片",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/convert_no_name_ts_to_stateverge_nyc.py",
            },
            {
                "title": "NYC/video：超过 10 分钟的移到子文件夹",
                "desc": "默认进入 NYC/video/long_over_10min；依赖 ffprobe。可加 --recursive。",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/nyc_video_bucket_long.py --dry-run\npython3 scripts/nyc_video_bucket_long.py",
            },
            {
                "title": "NYC/video：FILE 前缀文件单独归档",
                "desc": "把文件名以 FILE 开头的常见视频移到 NYC/video/FILE（可先 dry-run；可加 --recursive）。",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/nyc_video_bucket_file_prefix.py --dry-run\npython3 scripts/nyc_video_bucket_file_prefix.py",
            },
            {
                "title": "下载目录 → 视频/音乐 → NYC/video + NYC/music（可选清空下载）",
                "desc": "递归解压 + 散落文件分类迁入；仅终端命令；--purge-remnants 会删掉 Downloads 内全部残留（先 dry-run）。",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/downloads_media_to_stateverge_nyc.py --dry-run\npython3 scripts/downloads_media_to_stateverge_nyc.py --dry-run --purge-remnants\npython3 scripts/downloads_media_to_stateverge_nyc.py --purge-remnants",
            },
            {
                "title": "下载目录 → 解压 → 音频 → NYC/music（删压缩包）",
                "desc": "递归解压；音频迁入 NYC/music；--purge-remnants 清空 Downloads（务必先 dry-run）。需挂载 StateVerge。",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/downloads_extract_mp3_to_nyc_music.py --dry-run --deep --purge-remnants\npython3 scripts/downloads_extract_mp3_to_nyc_music.py --deep --purge-remnants",
            },
            {
                "title": "行车记录仪 clips → 长片 + Short（ffmpeg）",
                "desc": "拼接、可选降噪；详见脚本 --help",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython3 scripts/dashcam_clip_pack.py -i \"/path/to/clips\" -o \"/path/to/out\"",
            },
        ),
    },
    {
        "id": "env_check",
        "title": "环境与 API",
        "items": (
            {
                "title": "集成健康检查（OpenAI / ElevenLabs / …）",
                "desc": "需在仓库根配置 .env",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\nexport PYTHONPATH=\"$PWD\"\npython -m src.integrations.healthcheck",
            },
        ),
    },
    {
        "id": "production",
        "title": "Production CLI（将 TOPIC 换成你的 slug）",
        "blurb": "请先在终端 cd 到仓库根目录（含 topics/ 的那一层）。",
        "items": (
            {
                "title": "生成 brief",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.cli --topic TOPIC --generate-brief",
            },
            {
                "title": "生成口播 / 脚本",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.cli --topic TOPIC --generate-scripts",
            },
            {
                "title": "生成 LTX 场景计划",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.cli --topic TOPIC --generate-ltx-plan",
            },
            {
                "title": "导出 LTX prompts",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.ltx_batch_helper --topic TOPIC --export-prompts",
            },
            {
                "title": "拼接 LTX 生成片段 → narrative_main",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.ltx_batch_helper --topic TOPIC --assemble",
            },
            {
                "title": "最终包装 package",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.cli --topic TOPIC --package",
            },
            {
                "title": "Production 全部子命令帮助",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.production.cli --help",
            },
        ),
    },
    {
        "id": "presenter",
        "title": "Presenter CLI（主持人 / Runway Lip Sync）",
        "blurb": "同上：先在仓库根目录执行；TOPIC 替换为你的 slug。",
        "items": (
            {
                "title": "流程备忘（高频顺序）",
                "desc": "plan → tts → split-audio → build-base → prepare-runway →（手动 Runway）→ assemble-segments → assemble-full",
                "kind": "doc",
                "note": "详见 README_PRESENTER_PIPELINE.md",
            },
            {
                "title": "一次性备料（到 prepare-runway 前）",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.presenter_pipeline.cli --topic TOPIC --full-prep",
            },
            {
                "title": "assemble-segments → assemble-full",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.presenter_pipeline.cli --topic TOPIC --assemble-segments\npython -m src.presenter_pipeline.cli --topic TOPIC --assemble-full",
            },
            {
                "title": "Presenter CLI 帮助",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.presenter_pipeline.cli --help",
            },
        ),
    },
    {
        "id": "shorts_more",
        "title": "Shorts / 配音 / 字幕工具脚本",
        "items": (
            {
                "title": "为 topic 批量 ElevenLabs TTS",
                "kind": "shell",
                "command": "cd \"$HOME/stateverge\" 2>/dev/null || cd \"$HOME/StateVerge\"\npython scripts/generate_eleven_tts_for_topic.py --topic TOPIC",
            },
            {
                "title": "Shorts 工厂（竖屏成片）",
                "kind": "shell",
                "command": "python scripts/stateverge_shorts_factory.py --help",
            },
            {
                "title": "一键长片管线（脚本体量较大）",
                "kind": "shell",
                "command": "python scripts/stateverge_oneclick_video.py --help",
            },
            {
                "title": "Shorts 输出体检",
                "kind": "shell",
                "command": "python scripts/diagnose_shorts_outputs.py",
            },
            {
                "title": "根据成片音频重对齐字幕",
                "kind": "shell",
                "command": "python scripts/realign_subtitles_from_final_audio.py --help",
            },
        ),
    },
    {
        "id": "xhs_finance",
        "title": "小红书 / 财报",
        "items": (
            {
                "title": "小红书 Storyboard（OpenAI）",
                "kind": "shell",
                "command": "python scripts/xhs_storyboard.py --help",
            },
            {
                "title": "小红书链接抓取",
                "kind": "shell",
                "command": "python scripts/xhs_grab.py --help",
            },
            {
                "title": "财报 Short 文案生成（FMP）",
                "kind": "shell",
                "command": "python scripts/generate_finance_short.py --help",
            },
        ),
    },
    {
        "id": "tracking",
        "title": "Tracking / 研发记录（可选）",
        "items": (
            {
                "title": "记一笔支出",
                "kind": "shell",
                "command": "python scripts/tracking/add_expense.py --help",
            },
            {
                "title": "研发日志 / 里程碑 / 月度汇总",
                "kind": "shell",
                "command": "python scripts/tracking/add_research_log.py --help\npython scripts/tracking/add_milestone.py --help\npython scripts/tracking/generate_monthly_report.py --help",
            },
            {
                "title": "文档",
                "kind": "doc",
                "note": "docs/tracking/README.md",
            },
        ),
    },
    {
        "id": "assets_sort",
        "title": "素材分拣",
        "items": (
            {
                "title": "Envato：Downloads → assets/envato",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.utils.sort_envato --once",
            },
            {
                "title": "Envato 监听模式（需 watchdog）",
                "kind": "shell",
                "command": "export PYTHONPATH=\"$PWD\"\npython -m src.utils.sort_envato --watch",
            },
        ),
    },
    {
        "id": "docs",
        "title": "文档速查（仓库根目录）",
        "items": (
            {
                "title": "指令大全（强烈推荐收藏）",
                "kind": "doc",
                "note": "StateVerge_COMMANDS.txt",
            },
            {
                "title": "Studio Dashboard 说明",
                "kind": "doc",
                "note": "docs/STUDIO_DASHBOARD.md",
            },
            {
                "title": "Presenter 流水线",
                "kind": "doc",
                "note": "README_PRESENTER_PIPELINE.md",
            },
            {
                "title": "Envato 分拣说明",
                "kind": "doc",
                "note": "README_ENVATO_SORT.md",
            },
        ),
    },
)


def catalog_dict() -> dict[str, Any]:
    return {"sections": TOOLBOX_SECTIONS}
