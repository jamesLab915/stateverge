#!/usr/bin/env bash
# Verify a StateVerge topic output MP4.
# Usage: ./scripts/verify_final_video.sh <topic> [output_filename.mp4]
# Default filename: final_packaged.mp4
# Needs: ffprobe, awk. No jq.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -n "${STATEVERGE_ROOT:-}" ]]; then
  ROOT="$STATEVERGE_ROOT"
else
  ROOT="$REPO_ROOT"
fi

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <topic> [output_filename.mp4]" >&2
  exit 1
fi

TOPIC="$1"
FNAME="${2:-final_packaged.mp4}"
VID="$ROOT/topics/$TOPIC/output/$FNAME"
MIN_BYTES=$((5 * 1024 * 1024))
MIN_DUR=30
fail=0

ok() { echo "PASS: $1"; }
ko() { echo "FAIL: $1"; fail=1; }

if [[ ! -f "$VID" ]]; then
  ko "file not found: $VID"
  exit 1
fi
ok "file exists: $VID"

if stat -f%z "$VID" >/dev/null 2>&1; then
  SIZE=$(stat -f%z "$VID")
else
  SIZE=$(stat -c%s "$VID")
fi
if [[ "$SIZE" -le $MIN_BYTES ]]; then
  ko "file size not > 5MB (got $SIZE bytes)"
else
  ok "file size > 5MB ($SIZE bytes)"
fi

if ! command -v ffprobe >/dev/null 2>&1; then
  ko "ffprobe not in PATH"
  exit 1
fi

if ! ffprobe -v error -i "$VID" -show_format -of default=noprint_wrappers=1:nokey=0 >/dev/null 2>&1; then
  ko "ffprobe could not read file"
  exit 1
fi
ok "ffprobe can read the file"

VLINE=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_type -of default=nw=1:nk=1 "$VID" 2>/dev/null || true)
ALINE=$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of default=nw=1:nk=1 "$VID" 2>/dev/null || true)
if [[ -n "$VLINE" ]]; then
  ok "has video stream"
else
  ko "no video stream"
fi
if [[ -n "$ALINE" ]]; then
  ok "has audio stream"
else
  ko "no audio stream"
fi

DUR_STR=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VID" 2>/dev/null | head -1 || true)
if [[ -z "$DUR_STR" || "$DUR_STR" == "N/A" ]]; then
  ko "could not read duration"
else
  if awk -v d="$DUR_STR" "BEGIN{exit !((d+0) > $MIN_DUR)}"; then
    ok "duration > ${MIN_DUR}s ($DUR_STR s)"
  else
    ko "duration not > ${MIN_DUR}s (got $DUR_STR s)"
  fi
fi

W=$(ffprobe -v error -select_streams v:0 -show_entries stream=width -of default=nw=1:nk=1 "$VID" 2>/dev/null | head -1 || echo 0)
H=$(ffprobe -v error -select_streams v:0 -show_entries stream=height -of default=nw=1:nk=1 "$VID" 2>/dev/null | head -1 || echo 0)
if [[ "$W" == "1920" && "$H" == "1080" ]]; then
  ok "resolution 1920x1080"
else
  ko "resolution not 1920x1080 (got ${W}x${H})"
fi

exit "$fail"
