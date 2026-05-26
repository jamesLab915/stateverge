#!/usr/bin/env bash
# Build ~2h looped music (+ optional random ambience) for long-form video.
# Usage: scripts/build_2h_music.sh /path/to/music.mp3 /path/to/output.m4a

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "${#}" -lt 2 ]]; then
  echo "usage: scripts/build_2h_music.sh /path/to/music.mp3 /path/to/output.m4a" >&2
  exit 2
fi

MUSIC="$1"
OUT="$2"
# 与外接盘 NYC/music 一致；默认解析自 config/storage_map.env + STATEVERGE_VOL；可用 NYC_MUSIC_DIR 覆盖
_DEFAULT_NYC_MUSIC="$(cd "${ROOT}" && python3 -c 'import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd() / "src")); from utils.storage_paths import get_nyc_music_root; print(get_nyc_music_root())')" || true
if [[ -z "${_DEFAULT_NYC_MUSIC}" ]]; then
  _DEFAULT_NYC_MUSIC="/Volumes/StateVerge/NYC/music"
fi
AMB_DIR="${NYC_MUSIC_DIR:-${_DEFAULT_NYC_MUSIC}}"

EXTRA=( )
if [[ -d "${AMB_DIR}" ]]; then
  amb_files=( )
  shopt -s nullglob
  for f in "${AMB_DIR}"/*.mp3 "${AMB_DIR}"/*.wav "${AMB_DIR}"/*.m4a \
           "${AMB_DIR}"/*.MP3 "${AMB_DIR}"/*.WAV "${AMB_DIR}"/*.M4A; do
    [[ -f "$f" ]] || continue
    base=$(basename "$f")
    [[ "${base:0:1}" == "." ]] && continue
    amb_files+=("$f")
  done
  shopt -u nullglob
  if [[ ${#amb_files[@]} -gt 0 ]]; then
    idx=$((RANDOM % ${#amb_files[@]}))
    EXTRA+=(--ambience "${amb_files[$idx]}")
    echo "[build_2h_music] using ambience: ${amb_files[$idx]}" >&2
  fi
fi

exec "${ROOT}/.venv/bin/python" -m src.audio_tools.build_long_music \
  --music "${MUSIC}" \
  --output "${OUT}" \
  --duration 7200 \
  "${EXTRA[@]}"
