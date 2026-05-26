#!/usr/bin/env bash
# day_vlog: airdrop -> normalized clips -> concat by source mtime -> 2h loop
set -uo pipefail

INPUT="/Volumes/StateVerge/NYC_AUTO/raw/airdrop"
WORK="/Volumes/StateVerge/NYC_AUTO/processed/day_vlog_$(date +%Y%m%d)"
ERR_LOG="$WORK/pipeline_errors.log"
MANIFEST="$WORK/concat_by_mtime.tsv"

mkdir -p "$WORK/video" "$WORK/image" "$WORK/output"
: > "$ERR_LOG"
: > "$MANIFEST"

find_excl() {
  find "$INPUT" -type f ! -name '._*' "$@"
}

skipped_dng="$(find_excl -iname "*.dng" | wc -l | tr -d ' ')"
echo "skipped_dng=${skipped_dng}"

find_excl \( -iname "*.mp4" -o -iname "*.mov" \) | sort > "$WORK/video_list.txt"
find_excl \( -iname "*.jpg" -o -iname "*.jpeg" -o -iname "*.heic" -o -iname "*.png" \) | sort > "$WORK/image_list.txt"

log_fail() {
  echo "[$(date -Iseconds)] $*" | tee -a "$ERR_LOG" >&2
}

run_ffmpeg() {
  local desc="$1"; shift
  local tmp
  tmp="$(mktemp -t ffmpegvlog)"
  if ! ffmpeg -nostdin -hide_banner -loglevel error "$@" >"$tmp" 2>&1; then
    {
      echo "=== ffmpeg failed: $desc ==="
      cat "$tmp"
    } >>"$ERR_LOG"
    log_fail "ffmpeg failed: $desc"
    rm -f "$tmp"
    return 1
  fi
  rm -f "$tmp"
  return 0
}

# refresh dirs (avoid stale clips from aborted runs)
rm -f "$WORK"/video/*.mp4 "$WORK"/image/*.mp4 2>/dev/null || true

i=0
while IFS= read -r img || [[ -n "$img" ]]; do
  [[ -z "$img" ]] && continue
  [[ ! -f "$img" ]] && { log_fail "missing image: $img"; continue; }
  out="$WORK/image/img_$i.mp4"
  mtime=$(stat -f %m "$img")
  if run_ffmpeg "image#$i $img" -y -loop 1 -i "$img" \
    -vf "zoompan=z='min(zoom+0.0005,1.2)':d=150" \
    -t 5 -s 1920x1080 -pix_fmt yuv420p "$out"; then
    printf '%s\t%s\n' "$mtime" "$out" >> "$MANIFEST"
  fi
  i=$((i + 1))
done < "$WORK/image_list.txt"

i=0
while IFS= read -r vid || [[ -n "$vid" ]]; do
  [[ -z "$vid" ]] && continue
  [[ ! -f "$vid" ]] && { log_fail "missing video: $vid"; continue; }
  out="$WORK/video/clip_$i.mp4"
  mtime=$(stat -f %m "$vid")
  if run_ffmpeg "video#$i $vid" -y -i "$vid" -vf "scale=1920:1080" -r 30 \
    -c:v libx264 -preset fast -crf 23 "$out"; then
    printf '%s\t%s\n' "$mtime" "$out" >> "$MANIFEST"
  fi
  i=$((i + 1))
done < "$WORK/video_list.txt"

if [[ ! -s "$MANIFEST" ]]; then
  log_fail "no successful clips; skip concat (empty manifest)"
else
  # concat demuxer: path in single quotes; embed ' as '' (ffmpeg docs)
  : > "$WORK/all.txt"
  sort -t $'\t' -k1,1n -k2,2 "$MANIFEST" | while IFS=$'\t' read -r _mt f; do
    esc=$(printf '%s\n' "$f" | sed "s/'/''/g")
    printf "file '%s'\n" "$esc" >> "$WORK/all.txt"
  done

  if [[ -s "$WORK/all.txt" ]]; then
    tmp="$(mktemp -t ffmpegconcat)"
    if ! ffmpeg -nostdin -hide_banner -loglevel error -y -f concat -safe 0 -i "$WORK/all.txt" \
      -c copy "$WORK/output/day_raw.mp4" >"$tmp" 2>&1; then
      { echo "=== concat day_raw.mp4 ==="; cat "$tmp"; } >>"$ERR_LOG"
      log_fail "concat day_raw.mp4 failed"
    else
      rm -f "$tmp"
      tmp="$(mktemp -t ffmpegloop)"
      if ! ffmpeg -nostdin -hide_banner -loglevel error -y -stream_loop 50 -i "$WORK/output/day_raw.mp4" \
        -t 02:00:00 -c copy "$WORK/output/final_2h.mp4" >"$tmp" 2>&1; then
        { echo "=== final_2h loop ==="; cat "$tmp"; } >>"$ERR_LOG"
        log_fail "final_2h loop failed"
      fi
    fi
    rm -f "$tmp"
  else
    log_fail "all.txt empty after manifest sort"
  fi
fi

echo "[$(date -Iseconds)] finished. output=$WORK/output/final_2h.mp4"
if [[ -f "$WORK/output/final_2h.mp4" ]]; then
  ls -lh "$WORK/output/final_2h.mp4"
else
  log_fail "final_2h.mp4 not produced"
fi
[[ -s "$ERR_LOG" ]] && echo "--- errors ($ERR_LOG) ---" && cat "$ERR_LOG"
