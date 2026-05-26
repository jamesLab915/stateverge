#!/usr/bin/env bash
# StateVerge: detect external SSD mount status. Read-only diagnostics only.
# Never formats, repairs, or initializes disks.

set -u

STATEVERGE_ROOT="${STATEVERGE_ROOT:-$HOME/StateVerge}"
VOL_MOUNT="/Volumes/StateVerge"
NYC_BASE="${VOL_MOUNT}/NYC_AUTO"
LOG_DIR="${STATEVERGE_ROOT}/logs/system"
DATE_STAMP="$(date +%Y-%m-%d)"
LOG_FILE="${LOG_DIR}/ssd_check_${DATE_STAMP}.log"
SSD_LOG_COPY="${NYC_BASE}/logs/ssd_check_${DATE_STAMP}.log"

mkdir -p "${LOG_DIR}"

log() {
  local msg="$1"
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  printf '[%s] %s\n' "${ts}" "${msg}" | tee -a "${LOG_FILE}"
}

ensure_nyc_directories() {
  local base="${NYC_BASE}"
  local dirs=(
    "${base}/raw/airdrop/video"
    "${base}/raw/airdrop/image"
    "${base}/raw/airdrop/audio"
    "${base}/raw/airdrop/other"
    "${base}/logs"
    "${base}/quarantine"
  )
  local d
  for d in "${dirs[@]}"; do
    if ! mkdir -p "${d}" 2>>"${LOG_FILE}"; then
      log "WARN: could not create directory: ${d}"
    fi
  done
}

copy_log_to_ssd() {
  if [[ -d "${NYC_BASE}/logs" ]] && cp -f "${LOG_FILE}" "${SSD_LOG_COPY}" 2>>"${LOG_FILE}"; then
    return 0
  fi
  log "WARN: could not copy log to SSD (non-fatal)"
}

external_physical_detected() {
  local out
  if ! out="$(diskutil list external 2>/dev/null)"; then
    return 1
  fi
  if echo "${out}" | grep -q "(external, physical)"; then
    return 0
  fi
  return 1
}

run_diagnostics() {
  log "INFO: running diskutil list external"
  diskutil list external 2>&1 | tee -a "${LOG_FILE}" || true
  log "INFO: running diskutil list"
  diskutil list 2>&1 | tee -a "${LOG_FILE}" || true
  log "INFO: running system_profiler SPUSBDataType"
  system_profiler SPUSBDataType 2>&1 | tee -a "${LOG_FILE}" || true
}

main() {
  log "========== SSD check started =========="
  log "VOL_MOUNT=${VOL_MOUNT}"

  if [[ -d "${VOL_MOUNT}" ]]; then
    ensure_nyc_directories
    log "OK: StateVerge SSD mounted"
    log "========== SSD check finished (OK) =========="
    copy_log_to_ssd
    return 0
  fi

  log "WARN: ${VOL_MOUNT} is not mounted"
  run_diagnostics

  if external_physical_detected; then
    log "WARN: External disk detected but StateVerge not mounted"
    log "========== SSD check finished (WARN) =========="
    return 1
  fi

  log "ERROR: No external disk detected"
  log "========== SSD check finished (ERROR) =========="
  return 2
}

main "$@"
