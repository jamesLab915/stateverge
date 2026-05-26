#!/usr/bin/env bash
# 外接盘插好后「一键启动」：拉起 Studio Dashboard（若未运行）→ 后台启动 TS→MP4 Python 脚本 → 打开浏览器（工具箱，磁盘脚本节）。
#
# 默认：
#   - 可选检查 STATEVERGE_TS_INPUT（TS 源）；检查 STATEVERGE_VOL（项目 SSD）
#   - 若 http://127.0.0.1:${SV_DASHBOARD_PORT:-8765}/api/health 已是 StateVerge，则复用
#   - 否则后台启动 uvicorn（无 --reload，适合常驻）
#   - 直接 nohup 运行 scripts/convert_no_name_ts_to_stateverge_nyc.py（脚本内自管 logs/ts_convert.pid）
#
# 环境变量：
#   SKIP_CONVERT=1     只打开面板 / 拉起服务，不自动启动 TS 转换脚本
#   SKIP_BROWSER=1     不调用 open 打开浏览器
#   SV_DASHBOARD_HOST  默认 127.0.0.1
#   SV_DASHBOARD_PORT  默认 8765（若已被占用且健康检查通过则仍使用该端口）
#
# 用法：
#   ./scripts/studio_external_drives_oneclick.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

# Load optional keys from ~/StateVerge/config/storage_map.env (does not override existing exports).
eval "$(python3 <<'PY'
import os
import shlex
from pathlib import Path

p = Path.home() / "StateVerge/config/storage_map.env"
if not p.is_file():
    raise SystemExit(0)
for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line or line.startswith("#"):
        continue
    if "=" not in line:
        continue
    k, v = line.split("=", 1)
    k = k.strip()
    if not k.isidentifier():
        continue
    v = v.strip().strip('"').strip("'")
    if k not in os.environ:
        print(f"export {k}={shlex.quote(v)}")
PY
)"

VOL_IN="${STATEVERGE_TS_INPUT:-}"
VOL_OUT="${STATEVERGE_VOL:-/Volumes/StateVerge}"

HOST="${SV_DASHBOARD_HOST:-127.0.0.1}"
PORT="${SV_DASHBOARD_PORT:-8765}"
BASE_URL="http://${HOST}:${PORT}"

log() { echo "[oneclick] $*" >&2; }

require_vol() {
  local p="$1"
  local label="$2"
  if [ ! -d "$p" ]; then
    log "错误：未检测到「${label}」挂载路径："
    log "  ${p}"
    log "请先接入并挂载硬盘后再运行本脚本。"
    exit 2
  fi
}

if [[ -n "${VOL_IN}" ]]; then
  require_vol "$VOL_IN" "TS 源（STATEVERGE_TS_INPUT）"
else
  log "提示：未设置 STATEVERGE_TS_INPUT；可在 storage_map.env 增加一行或使用 export。"
  log "TS→MP4 脚本在无输入卷配置时会跳过（fail-open）。"
fi
require_vol "$VOL_OUT" "项目 SSD（STATEVERGE_VOL / StateVerge）"

if [ ! -f ".venv/bin/activate" ]; then
  log "错误：缺少 .venv，请先：python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
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
  log "Studio 已在运行：${BASE_URL}"
else
  if _port_in_use "${PORT}"; then
    log "错误：端口 ${PORT} 已被占用，但不是 StateVerge Studio（健康检查失败）。"
    log "请关掉占用进程或设置：SV_DASHBOARD_PORT=8766 ./scripts/studio_external_drives_oneclick.sh"
    lsof -i ":${PORT}" -P -sTCP:LISTEN >&2 || true
    exit 3
  fi
  LOG_FILE="${PROJECT_ROOT}/logs/dashboard_oneclick.log"
  log "后台启动 Studio → ${BASE_URL} （日志 ${LOG_FILE}）"
  nohup uvicorn src.studio_dashboard.app:app \
    --host "${HOST}" \
    --port "${PORT}" \
    >>"${LOG_FILE}" 2>&1 &
  echo $! >"${PROJECT_ROOT}/logs/dashboard_oneclick.pid"

  ok=0
  for _ in $(seq 1 60); do
    if _is_stateverge_up; then
      ok=1
      break
    fi
    sleep 0.5
  done
  if [ "${ok}" != "1" ]; then
    log "错误：Studio 未在预期时间内就绪，请查看 ${LOG_FILE}"
    exit 4
  fi
  log "Studio 就绪。"
fi

if [ "${SKIP_CONVERT:-0}" != "1" ]; then
  CONV_PY="${PROJECT_ROOT}/scripts/convert_no_name_ts_to_stateverge_nyc.py"
  LOG_PY="${PROJECT_ROOT}/logs/ts_convert_runner.log"
  if [ ! -f "${CONV_PY}" ]; then
    log "警告：未找到 TS 转换脚本 ${CONV_PY}，跳过转换。"
  else
    mkdir -p "${PROJECT_ROOT}/logs"
    {
      echo ""
      echo "--- shell oneclick $(date -u '+%Y-%m-%dT%H:%M:%SZ') ---"
    } >>"${LOG_PY}"
    nohup python3 "${CONV_PY}" >>"${LOG_PY}" 2>&1 &
    log "已启动 TS→MP4（日志 ${LOG_PY}；锁与进度见 logs/ts_convert.pid、logs/ts_convert_progress.json）。"
    log "若提示已有任务在跑：查看 ts_convert.pid 或稍后再试。"
  fi
else
  log "SKIP_CONVERT=1：跳过自动转换。"
fi

if [ "${SKIP_BROWSER:-0}" != "1" ]; then
  URL_PAGE="${BASE_URL}/tools#sec-disk_scripts"
  log "打开浏览器：${URL_PAGE}"
  open "${URL_PAGE}"
fi

log "完成。"
