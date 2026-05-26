#!/bin/bash
# 双击运行：外接盘一键启动 Studio + TS→MP4 + 打开浏览器（等同 studio_external_drives_oneclick.sh）
cd "$(dirname "$0")/.." || { echo "Cannot cd to StateVerge repo"; exit 1; }
exec ./scripts/studio_external_drives_oneclick.sh
