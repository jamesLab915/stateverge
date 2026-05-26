# StateVerge storage tiers and DaVinci publish flow

Logical volumes are resolved via `config/storage_map.env` and environment variables (`STATEVERGE_ROOT`, `SV_CACHE`, `SV_TRANSFER`, …). Application code should use `src/utils/storage_paths.py`, not hard-coded `/Volumes/...` paths.

## Tier roles

| Tier | Role |
|------|------|
| **SV_CACHE** | Fast production layer: ingest scratch, automation caches, ffmpeg outputs, DaVinci handoff folders. |
| **SV_TRANSFER** | Curated, human-approved transit layer: selections, edit-ready materials, **publish-ready exports**. |
| **SV_BACKUP** | Backup targets for projects, exports, tracking. |
| **SV_ARCHIVE** | Planned long-term cold archive (future expansion). |

## End-to-end publish chain (two modes)

### Full DaVinci path (`DAVINCI_BYPASS=0`)

1. **SV_CACHE/inbox** — inbound automation intake.
2. **Auto classify / rough cut** — tooling may produce intermediate cuts (ffmpeg), e.g. ``renders/<project>/rough_cut.mp4``.
3. **Audio cleanup layer** — ``scripts/audio_cleanup_pipeline.py`` reads ``rough_cut.mp4``, applies denoise / loudnorm / gain (video ``-c:v copy``), writes ``renders/<project>/audio_clean/final_audio_clean.mp4`` plus ``audio_cleanup_report.json``. Default scratch output: ``SV_CACHE/audio_clean/``. Never overwrites the rough cut or source media.
4. **SV_CACHE/renders** — automation-rendered video (typical ffmpeg output).
5. **`davinci_prepare_queue.py`** — queue into **SV_CACHE/davinci_inbox** (hardlink when possible, else `copy2`).
6. **Manual / semi-auto in DaVinci** — projects under **SV_CACHE/davinci_projects**; exports to **SV_CACHE/davinci_exports**.
7. **`davinci_collect_exports.py`** — collect validated exports into **SV_TRANSFER/ready_to_upload** (default **copy**; ffprobe must see a **video** stream).
8. **`davinci_publish_gate.py`** — read-only report on **ready_to_upload** (technical checks + optional **audio** loudness / peak hints; **non-blocking**). **No upload** in this step.
9. **Upload / publish_pack** — only after the gate passes.

### DaVinci Bypass path (default if env unset)

When **`DAVINCI_BYPASS` is unset or enabled** (see below), Resolve is optional for the current phase:

1. **SV_CACHE/inbox** → classify / ffmpeg → **SV_CACHE/renders** (optionally **audio_cleanup_pipeline.py** after ``rough_cut.mp4``).
2. **`davinci_bypass_collect.py`** — validated renders → **SV_TRANSFER/ready_to_upload** (copy/move; ffprobe + duration/size thresholds).
3. **`davinci_publish_gate.py`** — same technical checks; provenance rules depend on `DAVINCI_BYPASS`; optional audio-layer hints (warnings only).

DaVinci directories (**davinci_inbox**, **davinci_exports**, etc.) remain on disk for when you switch modes; nothing is deleted by these scripts.

Optional bookkeeping: **SV_CACHE/davinci_done** for staged “processed” markers or archives (mkdir only by layout init; no automatic moves required).

## DaVinci Bypass Mode

Environment variable:

| Value | Meaning |
|-------|---------|
| *(unset)* | **Bypass ON** — treat as `DAVINCI_BYPASS=1`; renders may flow directly to `ready_to_upload` via `davinci_bypass_collect.py`. |
| `1`, `true`, etc. | Bypass ON (same idea). |
| `0`, `false`, `no`, `off` | Bypass OFF — full DaVinci workflow is the intended path; publish gate **rejects** files whose path appears as a successful **`davinci_bypass_collect.py`** row in `davinci_bypass_manifest.csv` (status `ok`). Paths recorded via **`davinci_collect_exports.py`** (`davinci_export_manifest.csv`, status `ok`) are treated as DaVinci-approved. Other paths in `ready_to_upload` still pass **technical** checks only (**fail-open legacy** compatibility). |

**Current phase (typical):** `renders` → `ready_to_upload` → publish gate → (future) upload.

**Future with Resolve:** `renders` → `davinci_inbox` → `davinci_exports` → `ready_to_upload` → publish gate → upload.

The DaVinci directory layout and scripts stay in place so you can flip **`DAVINCI_BYPASS=0`** without restructuring storage.

## Core rules

- **Default bypass:** automation can promote **validated** renders into **`ready_to_upload`** without Resolve; gate still enforces ffprobe, duration, and size.
- **Full DaVinci mode:** prefer **`davinci_collect_exports.py`** so finals are tracked in **`davinci_export_manifest.csv`**; gate blocks known **bypass** placements when **`DAVINCI_BYPASS=0`**.
- **ffmpeg rough cuts** remain staging material; bypass only moves files that pass **`davinci_bypass_collect.py`** thresholds (video stream, duration > 5s, size > 1MB).
- **DaVinci remains the long-term quality gate** when bypass is turned off and Resolve is back in the loop.

## Scripts (reference)

| Script | Purpose |
|--------|---------|
| `scripts/init_storage_layout.py` | `mkdir -p` standard layout under resolved volumes (non-destructive). |
| `scripts/davinci_prepare_queue.py` | Renders/uploads → `davinci_inbox`. |
| `scripts/davinci_collect_exports.py` | `davinci_exports` → `ready_to_upload`. |
| `scripts/davinci_bypass_collect.py` | **`DAVINCI_BYPASS` path:** `renders` → `ready_to_upload` (validated). |
| `scripts/audio_cleanup_pipeline.py` | After rough cut: denoise + loudnorm + gain; **video copy**; `audio_cleanup_report.json`. |
| `scripts/davinci_publish_gate.py` | JSON report for `ready_to_upload` (+ bypass provenance when env off; optional audio metrics). |

Manifests and reports live under **`$STATEVERGE_ROOT/logs/davinci/`** (default `~/StateVerge/logs/davinci/`).

## DaVinci Resolve export directory (manual setup)

In DaVinci Resolve, set **Deliver** / export output to the resolved **`SV_CACHE/davinci_exports`** path from your machine’s `storage_map.env` (or the fallback under `_storage_fallback/sv_cache/davinci_exports` when the volume is offline). Keeping exports confined to that folder lets `davinci_collect_exports.py` gather finals without scanning unrelated cache trees.
