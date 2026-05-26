#!/bin/bash
set -e

OUT="assets/brand/stateverge_intro.mp4"

# ---------- font selection ----------
# Title:   prefer Arial Black (heaviest), then Arial Bold, then no fontfile.
# Subtitle: prefer Arial Bold (clean), then Arial Black, then no fontfile.
TITLE_FONT=""
SUB_FONT=""
for f in \
  "/System/Library/Fonts/Supplemental/Arial Black.ttf" \
  "/System/Library/Fonts/Supplemental/Arial Bold.ttf" ; do
  if [ -f "$f" ] && [ -z "$TITLE_FONT" ]; then TITLE_FONT="$f"; fi
done
for f in \
  "/System/Library/Fonts/Supplemental/Arial Bold.ttf" \
  "/System/Library/Fonts/Supplemental/Arial Black.ttf" ; do
  if [ -f "$f" ] && [ -z "$SUB_FONT" ]; then SUB_FONT="$f"; fi
done
TITLE_FONT_ARG=""
SUB_FONT_ARG=""
[ -n "$TITLE_FONT" ] && TITLE_FONT_ARG="fontfile='${TITLE_FONT}':"
[ -n "$SUB_FONT"   ] && SUB_FONT_ARG="fontfile='${SUB_FONT}':"
echo "[intro] title font  = ${TITLE_FONT:-<default>}"
echo "[intro] subtitle font = ${SUB_FONT:-<default>}"

# ---------- timing ----------
# total 3.0s :  0..0.6 fade-in   0.6..2.4 hold (with breath)   2.4..3.0 fade-out
# breath: gentle cosine on alpha during the hold, range 0.92..1.00, period 1.8s
ENV='if(lt(t,0.6),t/0.6,if(lt(t,2.4),0.96+0.04*cos(2*PI*(t-0.6)/1.8),if(lt(t,3.0),(3.0-t)/0.6,0)))'

# ---------- layout ----------
# Title: fontsize 110 + borderw=3 (same color) -> fake-bold thickening on top of Arial Black.
# Subtitle: fontsize 28, sits closer to title (y = h/2 + 14), tighter look via smaller weight.
TITLE_FILTER="drawtext=${TITLE_FONT_ARG}text='STATEVERGE':fontcolor=white:fontsize=110:borderw=3:bordercolor=white:x=(w-text_w)/2:y=(h/2)-90:alpha='${ENV}'"
SUB_FILTER="drawtext=${SUB_FONT_ARG}text='WHY SYSTEMS COLLAPSE':fontcolor=white:fontsize=28:x=(w-text_w)/2:y=(h/2)+14:alpha='${ENV}'"

ffmpeg -y -hide_banner -loglevel error \
  -f lavfi -i color=c=black:s=1920x1080:r=30:d=3 \
  -vf "${TITLE_FILTER},${SUB_FILTER}" \
  -c:v libx264 -pix_fmt yuv420p -movflags +faststart \
  "$OUT"

echo "DONE: $OUT"
open "$OUT"
