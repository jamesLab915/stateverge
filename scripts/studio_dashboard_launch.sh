#!/usr/bin/env bash
# 仅启动 Studio Dashboard（不检查外接盘、不触发 TS→MP4）。
#
# 用法：
#   ./scripts/studio_dashboard_launch.sh
# 环境变量：
#   SKIP_BROWSER=1     不自动打开浏览器
#   SV_DASHBOARD_HOST    默认 127.0.0.1
#   SV_DASHBOARD_PORT    默认 8765

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

HOST="${SV_DASHBOARD_HOST:-127.0.0.1}"
PORT="${SV_DASHBOARD_PORT:-8765}"
BASE_URL="http://${HOST}:${PORT}"

log() { echo "[dashboard] $*" >&2; }

if [ ! -f ".venv/bin/activate" ]; then
  log "缺少 .venv，请先：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python -c "import fastapi, uvicorn, jinja2" >/dev/null 2>&1 || {
  log "正在安装依赖 requirements.txt ..."
  pip install -r requirements.txt
}

export PYTHONPATH="$PROJECT_ROOT"

_health_body=""
_fetch_health() {
  _health_body="$(curl -sf --max-time 2 "${BASE_URL}/api/health" || true)"
}

_is_stateverge_up() {
  _fetch_health
  [[ "${_health_body}" == *"stateverge-studio-dashboard"* ]]
}

_port_in_use() {
  local p="$1"
  lsof -i ":${p}" -P -sTCP:LISTEN -t >/dev/null 2>&1
}

mkdir -p logs

if _is_stateverge_up; then
  log "已在运行：${BASE_URL}"
else
  if _port_in_use "${PORT}"; then
    log "错误：端口 ${PORT} 已被占用且健康检查未通过。"
    log "可改用：SV_DASHBOARD_PORT=8766 $0"
    lsof -i ":${PORT}" -P -sTCP:LISTEN >&2 || true
    exit 3
  fi
  LOG_FILE="${PROJECT_ROOT}/logs/dashboard_launch.log"
  log "后台启动 → ${BASE_URL} （日志 ${LOG_FILE}）"
  nohup uvicorn src.studio_dashboard.app:app \
    --host "${HOST}" \
    --port "${PORT}" \
    >>"${LOG_FILE}" 2>&1 &
  echo $! >"${PROJECT_ROOT}/logs/dashboard_launch.pid"

  ok=0
  for _ in $(seq 1 60); do
    if _is_stateverge_up; then
      ok=1
      break
    fi
    sleep 0.5
  done
  if [ "${ok}" != "1" ]; then
    log "未在预期时间内就绪，请查看 ${LOG_FILE}"
    exit 4
  fi
  log "就绪。"
fi

if [ "${SKIP_BROWSER:-0}" != "1" ]; then
  log "打开浏览器：${BASE_URL}/"
  open "${BASE_URL}/"
fi

log "完成。"
