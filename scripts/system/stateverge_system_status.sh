#!/usr/bin/env bash
# StateVerge: read-only system + mount status summary.

set -u

STATEVERGE_ROOT="${STATEVERGE_ROOT:-$HOME/StateVerge}"
VOL_MOUNT="/Volumes/StateVerge"
NYC_BASE="${VOL_MOUNT}/NYC_AUTO"
LOG_DIR="${STATEVERGE_ROOT}/logs/system"
TS_FILE="$(date '+%Y-%m-%d_%H%M%S')"
STATUS_LOG="${LOG_DIR}/status_${TS_FILE}.log"

mkdir -p "${LOG_DIR}"

out() {
  printf '%s\n' "$1" | tee -a "${STATUS_LOG}"
}

out_sep() {
  out "----------------------------------------"
}

main() {
  out "========== StateVerge system status =========="
  out "Generated: $(date '+%Y-%m-%d %H:%M:%S %Z')"
  out "macOS: $(sw_vers -productVersion 2>/dev/null || echo 'unknown')"
  out_sep

  if [[ -d "${STATEVERGE_ROOT}" ]]; then
    out "StateVerge project path: OK (${STATEVERGE_ROOT})"
  else
    out "StateVerge project path: MISSING (${STATEVERGE_ROOT})"
  fi

  if [[ -d "${VOL_MOUNT}" ]]; then
    out "/Volumes/StateVerge: MOUNTED"
    if df -h "${VOL_MOUNT}" 2>/dev/null | tee -a "${STATUS_LOG}"; then
      :
    else
      out "WARN: df failed for ${VOL_MOUNT}"
    fi
  else
    out "/Volumes/StateVerge: NOT MOUNTED"
    out "Do not run import. Please reconnect SSD."
  fi

  out_sep
  if [[ -d "${NYC_BASE}" ]]; then
    out "NYC_AUTO directory: OK (${NYC_BASE})"
  else
    out "NYC_AUTO directory: not present (SSD may be unmounted or not initialized this tree)"
  fi

  out_sep
  out "Last 10 lines: AirDrop import logs (airdrop_import_*.log):"
  if ls "${LOG_DIR}"/airdrop_import_*.log >/dev/null 2>&1; then
    local latest
    latest="$(ls -t "${LOG_DIR}"/airdrop_import_*.log 2>/dev/null | head -1)"
    if [[ -n "${latest}" ]]; then
      tail -n 10 "${latest}" | tee -a "${STATUS_LOG}" || true
    fi
  else
    out "(no airdrop import log files yet)"
  fi

  out_sep
  out "Last 10 lines: SSD check logs (ssd_check_*.log):"
  if ls "${LOG_DIR}"/ssd_check_*.log >/dev/null 2>&1; then
    local latest_ssd
    latest_ssd="$(ls -t "${LOG_DIR}"/ssd_check_*.log 2>/dev/null | head -1)"
    if [[ -n "${latest_ssd}" ]]; then
      tail -n 10 "${latest_ssd}" | tee -a "${STATUS_LOG}" || true
    fi
  else
    out "(no SSD check log files yet)"
  fi

  out_sep
  if [[ ! -d "${VOL_MOUNT}" ]]; then
    out "Reminder: Do not run import. Please reconnect SSD."
  fi

  out "Full status written to: ${STATUS_LOG}"
  out "========== End status =========="
}

main "$@"
