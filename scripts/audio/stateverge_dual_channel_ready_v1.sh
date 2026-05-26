#!/usr/bin/env bash
# Verify StateVerge dual-channel audio mix scripts (py_compile) and emit ready markers.
set -euo pipefail
SV_HOME="${STATEVERGE_HOME:-$HOME/StateVerge}"
# Tilde in STATEVERGE_HOME is not expanded by parameter expansion alone
case "${SV_HOME}" in
  "~"|"~"/*) SV_HOME="${SV_HOME/#\~/$HOME}" ;;
esac
python3 -m py_compile "${SV_HOME}/scripts/audio/stateverge_calm_ambient_mix_v1.py"
python3 -m py_compile "${SV_HOME}/scripts/shorts/shorts_cinematic_mix_v1.py"
echo "CALM_AMBIENT_PIPELINE_READY=true"
echo "SHORTS_CINEMATIC_PIPELINE_READY=true"
