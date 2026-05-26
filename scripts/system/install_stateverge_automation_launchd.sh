#!/usr/bin/env bash
# Install/uninstall LaunchAgents for StateVerge SSD check and AirDrop import.

set -u

STATEVERGE_ROOT="${STATEVERGE_ROOT:-$HOME/StateVerge}"
LOG_DIR="${STATEVERGE_ROOT}/logs/system"
LA_DIR="${HOME}/Library/LaunchAgents"
PLIST_SSD="com.stateverge.ssdcheck.plist"
PLIST_AIR="com.stateverge.airdropimport.plist"
DOMAIN="gui/$(id -u)"

mkdir -p "${LOG_DIR}"

usage() {
  cat <<'EOF'
Usage: install_stateverge_automation_launchd.sh --install | --uninstall | --status

  --install    Write LaunchAgents, bootstrap for current user
  --uninstall  Bootout and remove plist files
  --status     Show launchctl print for both jobs
EOF
}

write_plist_ssd() {
  local out="${LA_DIR}/${PLIST_SSD}"
  /usr/bin/python3 - "$out" "$STATEVERGE_ROOT" "${LOG_DIR}/launchd_ssdcheck.out.log" <<'PY'
import pathlib, plistlib, sys
out, root, log = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
script = pathlib.Path(root) / "scripts" / "system" / "check_external_ssd.sh"
data = {
    "Label": "com.stateverge.ssdcheck",
    "ProgramArguments": ["/bin/bash", "-lc", str(script)],
    "RunAtLoad": True,
    "StandardOutPath": log,
    "StandardErrorPath": log,
    "StartCalendarInterval": [
        {"Hour": 9, "Minute": 0},
        {"Hour": 18, "Minute": 0},
        {"Hour": 23, "Minute": 0},
    ],
}
out.write_bytes(plistlib.dumps(data))
PY
}

write_plist_airdrop() {
  local out="${LA_DIR}/${PLIST_AIR}"
  /usr/bin/python3 - "$out" "$STATEVERGE_ROOT" "${LOG_DIR}/launchd_airdropimport.out.log" <<'PY'
import pathlib, plistlib, sys
out, root, log = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
script = pathlib.Path(root) / "scripts" / "media" / "import_airdrop_to_ssd.py"
data = {
    "Label": "com.stateverge.airdropimport",
    "ProgramArguments": ["/usr/bin/python3", str(script)],
    "RunAtLoad": True,
    "StartInterval": 900,
    "StandardOutPath": log,
    "StandardErrorPath": log,
}
out.write_bytes(plistlib.dumps(data))
PY
}

do_install() {
  mkdir -p "${LA_DIR}"
  write_plist_ssd
  write_plist_airdrop
  echo "Wrote ${LA_DIR}/${PLIST_SSD}"
  echo "Wrote ${LA_DIR}/${PLIST_AIR}"

  # Idempotent: bootout if already loaded, then bootstrap (does not touch other agents).
  /bin/launchctl bootout "${DOMAIN}" "${LA_DIR}/${PLIST_SSD}" 2>/dev/null || true
  /bin/launchctl bootout "${DOMAIN}" "${LA_DIR}/${PLIST_AIR}" 2>/dev/null || true
  /bin/launchctl bootstrap "${DOMAIN}" "${LA_DIR}/${PLIST_SSD}" || {
    echo "WARN: bootstrap ssdcheck failed; trying legacy load" >&2
    /bin/launchctl load -w "${LA_DIR}/${PLIST_SSD}" 2>/dev/null || true
  }
  /bin/launchctl bootstrap "${DOMAIN}" "${LA_DIR}/${PLIST_AIR}" || {
    echo "WARN: bootstrap airdropimport failed; trying legacy load" >&2
    /bin/launchctl load -w "${LA_DIR}/${PLIST_AIR}" 2>/dev/null || true
  }

  echo "Installed and loaded for domain ${DOMAIN}."
}

do_uninstall() {
  /bin/launchctl bootout "${DOMAIN}" "${LA_DIR}/${PLIST_SSD}" 2>/dev/null || \
    /bin/launchctl unload "${LA_DIR}/${PLIST_SSD}" 2>/dev/null || true
  /bin/launchctl bootout "${DOMAIN}" "${LA_DIR}/${PLIST_AIR}" 2>/dev/null || \
    /bin/launchctl unload "${LA_DIR}/${PLIST_AIR}" 2>/dev/null || true

  rm -f "${LA_DIR}/${PLIST_SSD}" "${LA_DIR}/${PLIST_AIR}"
  echo "Removed LaunchAgents for StateVerge automation (other agents untouched)."
}

do_status() {
  echo "=== ${PLIST_SSD} ==="
  /bin/launchctl print "${DOMAIN}/com.stateverge.ssdcheck" 2>&1 || echo "(not loaded)"
  echo "=== ${PLIST_AIR} ==="
  /bin/launchctl print "${DOMAIN}/com.stateverge.airdropimport" 2>&1 || echo "(not loaded)"
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi
  case "$1" in
    --install) do_install ;;
    --uninstall) do_uninstall ;;
    --status) do_status ;;
    *) usage; exit 1 ;;
  esac
}

main "$@"
