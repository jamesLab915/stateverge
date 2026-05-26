#!/usr/bin/env bash
# 一键生成长视频背景 bed（默认 2h）：可选 GUI 选音乐 → build_2h_music →（macOS）在 Finder 中露出输出文件。
#
# 用法：
#   ./scripts/start_long_music_oneclick.sh /path/to/track.wav
#   LONG_MUSIC_INPUT=/path/to/track.mp3 ./scripts/start_long_music_oneclick.sh
#   ./scripts/start_long_music_oneclick.sh    # macOS：弹出文件选择框
#
# 环境变量：
#   LONG_MUSIC_INPUT   主音乐路径（与 positional 二选一）
#   LONG_MUSIC_OUTPUT  输出路径（默认 assets/out/long_bed_<时间戳>.m4a）
#   SKIP_REVEAL=1      macOS 下不执行 open -R

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

if [[ ! -f ".venv/bin/python" ]]; then
  echo "[long_music_oneclick] error: missing .venv — create: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

pick_music_gui_darwin() {
  osascript <<'APPLESCRIPT' 2>/dev/null || true
try
  POSIX path of (choose file with prompt "选择主音乐文件（须已获授权使用）")
on error number -128
  ""
end try
APPLESCRIPT
}

MUSIC=""
if [[ "${#}" -ge 1 ]]; then
  MUSIC="$1"
elif [[ -n "${LONG_MUSIC_INPUT:-}" ]]; then
  MUSIC="${LONG_MUSIC_INPUT}"
elif [[ "$(uname -s)" == "Darwin" ]] && command -v osascript >/dev/null 2>&1; then
  echo "[long_music_oneclick] 请在对话框中选择主音乐…" >&2
  MUSIC="$(pick_music_gui_darwin | tr -d '\r')"
else
  echo "usage: $0 /path/to/licensed_music.wav" >&2
  echo "  or: LONG_MUSIC_INPUT=/path/to/music.mp3 $0" >&2
  exit 2
fi

MUSIC="${MUSIC//$'\n'/}"
MUSIC="${MUSIC//$'\r'/}"
if [[ -z "${MUSIC}" ]]; then
  echo "[long_music_oneclick] 未选择文件，已取消。" >&2
  exit 2
fi

if [[ ! -f "${MUSIC}" ]]; then
  echo "[long_music_oneclick] error: file not found: ${MUSIC}" >&2
  exit 2
fi

stamp="$(date +%Y%m%d_%H%M%S)"
OUT="${LONG_MUSIC_OUTPUT:-${PROJECT_ROOT}/assets/out/long_bed_${stamp}.m4a}"
mkdir -p "$(dirname "${OUT}")"

echo "[long_music_oneclick] music=${MUSIC}" >&2
echo "[long_music_oneclick] output=${OUT}" >&2

"${SCRIPT_DIR}/build_2h_music.sh" "${MUSIC}" "${OUT}"

if [[ "$(uname -s)" == "Darwin" && "${SKIP_REVEAL:-0}" != "1" ]]; then
  open -R "${OUT}"
fi

echo "[long_music_oneclick] done: ${OUT}" >&2
