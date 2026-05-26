#!/usr/bin/env bash
# StateVerge: safely eject StateVerge volume. No force unmount unless --force.

set -u

STATEVERGE_ROOT="${STATEVERGE_ROOT:-$HOME/StateVerge}"
VOL_MOUNT="/Volumes/StateVerge"
LOG_DIR="${STATEVERGE_ROOT}/logs/system"
DATE_STAMP="$(date +%Y-%m-%d)"
LOG_FILE="${LOG_DIR}/eject_${DATE_STAMP}.log"
FORCE=0

for arg in "$@"; do
  case "${arg}" in
    --force) FORCE=1 ;;
  esac
done

mkdir -p "${LOG_DIR}"

log() {
  local msg="$1"
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  printf '[%s] %s\n' "${ts}" "${msg}" | tee -a "${LOG_FILE}"
}

device_identifier_for_mount() {
  diskutil info "${VOL_MOUNT}" 2>/dev/null | awk -F: '/Device Identifier/ {gsub(/^ +/,"",$2); print $2; exit}'
}

print_lsof_hint() {
  log "INFO: checking open files on ${VOL_MOUNT} (may be slow)"
  if command -v lsof >/dev/null 2>&1; then
    lsof +D "${VOL_MOUNT}" 2>/dev/null | tee -a "${LOG_FILE}" || true
  else
    log "WARN: lsof not available"
  fi
  log "HINT: Close Finder windows, Terminal tabs, Cursor, FFmpeg, or other apps using this volume, then retry."
  log "HINT: Without --force, this script does not kill any process."
}

main() {
  log "========== Eject StateVerge started (force=${FORCE}) =========="

  if [[ ! -d "${VOL_MOUNT}" ]]; then
    log "WARN: ${VOL_MOUNT} is not mounted; nothing to eject"
    log "========== Eject finished (no-op) =========="
    return 0
  fi

  local dev_id
  dev_id="$(device_identifier_for_mount)"
  if [[ -z "${dev_id}" ]]; then
    log "ERROR: could not resolve device identifier for ${VOL_MOUNT}"
    print_lsof_hint
    log "========== Eject finished (ERROR) =========="
    return 1
  fi

  log "INFO: syncing filesystems"
  sync || log "WARN: sync returned non-zero (continuing)"

  local unmount_cmd=(diskutil unmountDisk)
  if [[ "${FORCE}" -eq 1 ]]; then
    unmount_cmd+=(force)
    log "WARN: using force unmount for ${dev_id}"
  fi
  unmount_cmd+=("${dev_id}")

  log "INFO: running: ${unmount_cmd[*]}"
  local ec=0
  "${unmount_cmd[@]}" >>"${LOG_FILE}" 2>&1 || ec=$?
  if [[ "${ec}" -eq 0 ]]; then
    log "SAFE TO REMOVE: StateVerge SSD ejected"
    log "========== Eject finished (success) =========="
    return 0
  fi

  log "ERROR: unmount/eject failed for ${dev_id}"
  print_lsof_hint

  if [[ "${FORCE}" -eq 0 ]]; then
    log "INFO: retry with: $0 --force (uses diskutil force unmount only; still does not kill processes)"
  fi

  log "========== Eject finished (failure) =========="
  return 1
}

main "$@"
