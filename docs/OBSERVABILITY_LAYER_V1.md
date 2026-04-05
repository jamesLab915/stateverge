# Observability Layer v1 — StateVerge

Evolution **pipeline** runs (`npm run pipeline:run`) emit **append-only JSON lines** for audit and health review. No UI changes; no core schema changes.

---

## 1. Where logs are written

| Path | Content |
|------|---------|
| `data/metrics/evolution/runs-YYYY-MM-DD.jsonl` | One JSON object per pipeline invocation (UTC date in filename) |
| `data/ingest_rejections.jsonl` | Ingest contract rejections (see `SOURCE_INGESTION_RUNNER_V1.md`) |

Both are **gitignored** artifacts; retain in your ops environment as needed.

---

## 2. Pipeline run record schema (`evolution_pipeline_run_v1`)

Each line includes:

| Field | Meaning |
|-------|---------|
| `schema` | `evolution_pipeline_run_v1` |
| `ts` | Record write time (end of run) |
| `hostname`, `pid` | Process context |
| `argv` | CLI arguments (no secrets) |
| `mode` | `all` \| `ingest` \| `enrich` \| `score` \| `partial` |
| `dryRun` | Whether `--dry-run` was set |
| `overallOk` | `exitCode === 0` |
| `exitCode` | `0` or `1` |
| `steps[]` | Per-step `name`, `ok`, `error`, timestamps, `stats` |

### Per-step observable stats (when present)

| Step | Stats keys (examples) |
|------|------------------------|
| **ingestion** | `validatedOk`, `inserted`, `rejected`, `skippedUnknownAdapter`, `dryRun` |
| **enrichment** | `eventsProcessed`, `consequenceRows`, `actorsAttached`, `dryRun` |
| **scoring** | From `runUpdateScores`: `pending`, `deferred`, `active`, `skippedNoCountryScores`, `countriesUpdated`, `eventsMarkedApplied`; or `skipped: true` on dry-run |

---

## 3. Failure classification (pipeline)

| Signal | Interpretation |
|--------|----------------|
| `exitCode !== 0` | A step threw (DB, validation bug, etc.) |
| `steps[i].ok === false` | Failing step; `error` has message |
| `overallOk === false` | Same as failed exit |

**By step name** (for `report:pipeline-health`):

- `ingestion` — adapter/contract/DB insert
- `enrichment` — consequence or actor insert
- `scoring` — `updateScores` (snapshot / country_scores update)

**Retry:** fix root cause, then re-run `--ingest-only` / `--enrich-only` / `--score-only` as appropriate (`ORCHESTRATION_LAYER_V1.md`).

---

## 4. Rejection classification (ingest)

Not stored in pipeline JSONL; use **`data/ingest_rejections.jsonl`** and **`npm run ingest:report`**.

Categories: `missing_field`, `invalid_source_type`, `invalid_date`, `invalid_url`, `invalid_confidence`, `invalid_impact`, `invalid_reference`, `database`, `other` (see `classifyIngestRejection.js`).

`report:pipeline-health` aggregates **primaryCategory** counts from that file.

---

## 5. Enrichment / scoring / cache — what is tracked

| Area | In pipeline metrics JSONL | Notes |
|------|---------------------------|--------|
| **Enrichment** | `consequenceRows`, `eventsProcessed`, `actorsAttached` | Cumulative sums in health report are over all runs in window |
| **Scoring** | `eventsMarkedApplied`, `countriesUpdated` | `countriesUpdated` ≈ **snapshot rows written** per run (one snapshot per country updated) |
| **Cache** | Not emitted | Pipeline does not invalidate caches; `report:pipeline-health` returns `cache_invalidations.tracked: false` |

For cache invalidation and broader daily jobs, see **`runDailyEvolution.js`**.

---

## 6. How to judge system health

1. **`npm run report:pipeline-health`** — recent window (default 7 days): run success rate, failures by step, summed ingestion/enrichment/scoring totals, ingest rejection breakdown.
2. **Steady state signals** (context-dependent):
   - High `ingestion_rejected` with `invalid_source_type` → tighten adapter mapping or ingest contract inputs.
   - Frequent `scoring` failures → DB constraints, missing `country_scores`, or thrown logic in `updateScores`.
   - `enrichment` rows always 0 while events grow → enrichment not running or backlog cleared.
3. **Last run** — `last_run` in the health report points at the newest `ts` in the window for quick audit.

---

## 7. Commands

| Command | Purpose |
|---------|---------|
| `npm run pipeline:run` | Runs pipeline + appends metrics line |
| `npm run report:pipeline-health` | Human-readable JSON summary |
| `npm run report:pipeline-health -- --days=14 --json` | Machine-readable, longer window |

---

## 8. Files

| Path | Role |
|------|------|
| `lib/observability/evolutionRunLogger.js` | Build + append run records |
| `scripts/reportPipelineHealth.js` | Aggregate health report |
| `scripts/runEvolutionPipeline.js` | Writes log in `finally` (failures included) |
