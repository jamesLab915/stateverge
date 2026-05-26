#!/bin/bash
# 双击运行：选主音乐 → 生成约 2 小时 bed（可选 NYC/music 随机环境音）→ Finder 定位输出
cd "$(dirname "$0")/.." || { echo "Cannot cd to StateVerge repo"; exit 1; }
exec ./scripts/start_long_music_oneclick.sh
