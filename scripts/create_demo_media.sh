#!/usr/bin/env bash
# create_demo_media.sh
#
# Generate placeholder test MP4s for a StateVerge topic so the Mix Engine
# can be exercised end-to-end without LTX / Runway / HeyGen / Envato.
#
# Usage:
#   ./scripts/create_demo_media.sh <topic>
#
# For the given topic, this script will:
#   1. Ensure topics/<topic>/{video,envato,mix,output} exist.
#   2. If topics/<topic>/video/ has no *.mp4, generate scene_001/002/003.mp4
#      (1920x1080, 30fps, 6s, silent audio track, color-test card with text).
#   3. If topics/<topic>/envato/ has no *.mp4, generate broll_001/002.mp4
#      (1920x1080, 30fps, 6s, silent audio track, color-test card with text).
#
# All checks are non-destructive: existing MP4s are kept as-is.

set -euo pipefail

TOPIC="${1:-}"
if [[ -z "$TOPIC" ]]; then
  echo "Usage: $0 <topic>" >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT="${STATEVERGE_ROOT:-$REPO_ROOT}"

TOPIC_DIR="$ROOT/topics/$TOPIC"
VIDEO_DIR="$TOPIC_DIR/video"
ENVATO_DIR="$TOPIC_DIR/envato"
MIX_DIR="$TOPIC_DIR/mix"
OUT_DIR="$TOPIC_DIR/output"

mkdir -p "$VIDEO_DIR" "$ENVATO_DIR" "$MIX_DIR" "$OUT_DIR"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "FAIL: ffmpeg not found in PATH" >&2
  exit 1
fi

# Generate a 1920x1080@30fps 6s test MP4 with silent stereo audio and
# a color background plus a centered label. Idempotent: skipped if dst exists.
gen_clip() {
  local dst="$1"
  local color="$2"
  local label="$3"
  if [[ -f "$dst" ]]; then
    echo "skip (exists): $dst"
    return 0
  fi
  local fontfile=""
  for cand in \
    /System/Library/Fonts/Supplemental/Arial.ttf \
    /System/Library/Fonts/Helvetica.ttc \
    /Library/Fonts/Arial.ttf; do
    if [[ -f "$cand" ]]; then
      fontfile="$cand"
      break
    fi
  done
  local label_filter
  if [[ -n "$fontfile" ]]; then
    label_filter=",drawtext=fontfile=${fontfile}:text='${label}':fontcolor=white:fontsize=96:x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.4:boxborderw=24"
  else
    label_filter=",drawbox=x=0:y=480:w=1920:h=120:color=black@0.4:t=fill"
  fi
  # Add temporal noise so the encoder cannot crush the file to a few hundred KB
  # (the verify_final_video.sh script enforces > 5MB). A constant high bitrate
  # is also set as a belt-and-braces measure.
  local vfilter="noise=alls=18:allf=t+u${label_filter}"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "color=c=${color}:s=1920x1080:r=30:d=6" \
    -f lavfi -i "anullsrc=channel_layout=stereo:sample_rate=48000" \
    -vf "${vfilter}" \
    -t 6 \
    -c:v libx264 -pix_fmt yuv420p -preset veryfast \
    -b:v 12M -minrate 10M -maxrate 14M -bufsize 24M \
    -c:a aac -b:a 128k -ar 48000 -ac 2 \
    -movflags +faststart \
    -shortest \
    "$dst"
  echo "wrote: $dst"
}

# Count existing *.mp4 (case-insensitive) in a directory.
count_mp4() {
  local dir="$1"
  find "$dir" -maxdepth 1 -type f \( -iname '*.mp4' \) | wc -l | tr -d ' '
}

VIDEO_COUNT="$(count_mp4 "$VIDEO_DIR")"
ENVATO_COUNT="$(count_mp4 "$ENVATO_DIR")"

echo "topic=$TOPIC"
echo "video/  mp4 count = $VIDEO_COUNT"
echo "envato/ mp4 count = $ENVATO_COUNT"

if [[ "$VIDEO_COUNT" -eq 0 ]]; then
  echo "[create_demo_media] generating scene_001/002/003.mp4 in $VIDEO_DIR"
  gen_clip "$VIDEO_DIR/scene_001.mp4" "0x1f3a8c" "SCENE 001"
  gen_clip "$VIDEO_DIR/scene_002.mp4" "0x8c1f3a" "SCENE 002"
  gen_clip "$VIDEO_DIR/scene_003.mp4" "0x1f8c3a" "SCENE 003"
else
  echo "[create_demo_media] video/ already has $VIDEO_COUNT mp4(s); ensuring scene_001/002/003 placeholders exist alongside"
  gen_clip "$VIDEO_DIR/scene_001.mp4" "0x1f3a8c" "SCENE 001"
  gen_clip "$VIDEO_DIR/scene_002.mp4" "0x8c1f3a" "SCENE 002"
  gen_clip "$VIDEO_DIR/scene_003.mp4" "0x1f8c3a" "SCENE 003"
fi

if [[ "$ENVATO_COUNT" -eq 0 ]]; then
  echo "[create_demo_media] generating broll_001/002.mp4 in $ENVATO_DIR"
  gen_clip "$ENVATO_DIR/broll_001.mp4" "0x303030" "BROLL 001"
  gen_clip "$ENVATO_DIR/broll_002.mp4" "0x202845" "BROLL 002"
else
  echo "[create_demo_media] envato/ already has $ENVATO_COUNT mp4(s); ensuring broll_001/002 placeholders exist alongside"
  gen_clip "$ENVATO_DIR/broll_001.mp4" "0x303030" "BROLL 001"
  gen_clip "$ENVATO_DIR/broll_002.mp4" "0x202845" "BROLL 002"
fi

echo "[create_demo_media] DONE."
