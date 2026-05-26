#!/usr/bin/env bash
# Install StateVerge zsh aliases: sv-status, sv-eject, sv-import
# Idempotent: removes managed block and any prior aliases with the same names.

set -euo pipefail

SELF="${BASH_SOURCE[0]:-$0}"
chmod +x "$SELF" 2>/dev/null || true

ZSHRC="${HOME}/.zshrc"
BLOCK_START="# >>> StateVerge CLI shortcuts (managed by install_cli_shortcuts.sh)"
BLOCK_END="# <<< StateVerge CLI shortcuts"

if [[ ! -f "${ZSHRC}" ]]; then
  touch "${ZSHRC}"
fi

tmp="$(mktemp)"
cleanup() { rm -f "${tmp}"; }
trap cleanup EXIT

awk -v s="${BLOCK_START}" -v e="${BLOCK_END}" '
  $0 == s { skip = 1; next }
  $0 == e { skip = 0; next }
  skip == 1 { next }
  /^[[:space:]]*alias[[:space:]]+sv-(status|eject|import)=/ { next }
  { print }
' "${ZSHRC}" > "${tmp}"

{
  cat "${tmp}"
  printf '\n%s\n' "${BLOCK_START}"
  printf '%s\n' \
    "alias sv-status='bash ~/StateVerge/scripts/system/stateverge_system_status.sh'" \
    "alias sv-eject='bash ~/StateVerge/scripts/system/eject_stateverge_ssd.sh'" \
    "alias sv-import='python3 ~/StateVerge/scripts/media/import_airdrop_to_ssd.py'"
  printf '%s\n' "${BLOCK_END}"
} > "${ZSHRC}"

echo "Updated ${ZSHRC} with sv-status, sv-eject, sv-import."
echo "Open a new terminal tab, or run:  source ~/.zshrc"
