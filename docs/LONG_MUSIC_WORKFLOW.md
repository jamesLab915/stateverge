# Long-form background music workflow

This repo ships small **ffmpeg-based helpers** to loop, level, fade, and optionally mix **already-licensed** library music for long YouTube-style timelines.

## Licensing

- Use **only music you have the legal right to use** on your channel (e.g. Envato Elements license tied to your account, direct composer agreement, etc.).
- These tools perform **technical processing only**: transcoding, looping, fades, optional ambience blend, and loudness shaping via simple gain — **not** a substitute for clearing rights.

## Finding usable beds (Envato / similar)

Try keyword clusters such as:

- `ambient city night calm piano soft background loopable seamless no vocals`
- `minimal ambient piano urban night atmosphere slow emotional background seamless loop`
- `cinematic ambient underscore slow evolving texture no melody minimal dark atmosphere`

Prefer assets labeled **loopable / seamless** when you need clean repetition.

## Build a 2-hour master

From the repo root (`~/StateVerge`), with ffmpeg on `PATH`:

```bash
./.venv/bin/python -m src.audio_tools.build_long_music \
  --music /path/to/licensed_track.wav \
  --output ./assets/out/long_bed.m4a \
  --duration 7200 \
  --music-volume 0.85 \
  --fade-in 5 \
  --fade-out 8
```

With ambience (optional):

```bash
./.venv/bin/python -m src.audio_tools.build_long_music \
  --music /path/to/main_bed.mp3 \
  --ambience /path/to/subtle_room_tone.wav \
  --output ./assets/out/long_bed.mp3 \
  --duration 7200 \
  --music-volume 0.85 \
  --ambience-volume 0.12 \
  --fade-in 5 \
  --fade-out 8
```

Wrapper that defaults to **7200 s** and picks a random ambience file from **`/Volumes/StateVerge/NYC/music`** (top-level `.mp3` / `.wav` / `.m4a` only) when that directory exists and the volume is mounted. Override the scan directory with:

`NYC_MUSIC_DIR=/path/to/dir scripts/build_2h_music.sh ...`

```bash
chmod +x scripts/build_2h_music.sh   # once
scripts/build_2h_music.sh /path/to/music.mp3 ./assets/out/long_bed.m4a
```

The wrapper only looks at **files in that folder’s root** (not subfolders). To layer only subtle beds, set `NYC_MUSIC_DIR` to a dedicated subfolder, or pass `--ambience` explicitly via the Python module.

## One-click (macOS)

1. **Studio + TS→MP4 + browser**  
   In Terminal once:
   `chmod +x scripts/Launch_StateVerge_Studio.command`  
   Then double-click **`scripts/Launch_StateVerge_Studio.command`** in Finder (same behavior as `studio_external_drives_oneclick.sh`).
2. **Long bed (pick music in GUI → ~2h → reveal in Finder)**  
   `chmod +x scripts/Launch_Long_Music_Bed.command scripts/start_long_music_oneclick.sh`  
   Double-click **`scripts/Launch_Long_Music_Bed.command`**, or run `./scripts/start_long_music_oneclick.sh /path/to/track.wav`.

Optional env: `LONG_MUSIC_OUTPUT`, `SKIP_REVEAL=1`, `NYC_MUSIC_DIR` (see `build_2h_music.sh`).

## Defaults

| Parameter           | Default |
|---------------------|--------:|
| `--duration`        | 7200 s  |
| `--music-volume`    | 0.85    |
| `--ambience-volume` | 0.12    |
| `--fade-in`         | 5 s     |
| `--fade-out`        | 8 s     |

## Using the output in video

1. Export your **final picture lock** (or timeline) at the target duration (e.g. 2:00:00).
2. Import `long_bed.m4a` / `.mp3` as an audio track in DaVinci Resolve, Premiere, or ffmpeg-based pipelines.
3. If you mux with ffmpeg, map video + this audio and choose a suitable AAC bitrate for YouTube (often 192 kbps stereo is fine for music beds).

Example ffmpeg mux (adjust paths and codecs to match your master):

```bash
ffmpeg -i video_master.mp4 -i long_bed.m4a \
  -map 0:v:0 -map 1:a:0 -c:v copy -c:a aac -b:a 192k \
  -shortest -movflags +faststart youtube_upload.mp4
```

The project’s **`mix_engine`** / **`auto_video`** flows are unchanged; this is a standalone preprocessing step you run **before** or **alongside** your usual compose pipeline.

## Module reference

- `src/audio_tools/build_long_music.py` — argparse CLI, ffmpeg invocation.
