# Orchestration Layer v1 — StateVerge

Single entrypoint **`npm run pipeline:run`** (`scripts/runEvolutionPipeline.js`) runs the three existing layers **in order**:

1. **Ingestion** — `lib/pipeline/sourceIngestionStep.js` (same behavior as `scripts/runSourceIngestion.js`)
2. **Enrichment** — `lib/pipeline/consequenceEnrichmentStep.js` (same as `scripts/runConsequenceEnrichment.js`)
3. **Scoring** — `runUpdateScores()` from `scripts/updateScores.js`

No database schema changes. No UI. This **does not replace** `scripts/runDailyEvolution.js`.

---

## 1. Pipeline boundaries (why layers stay separate)

| Layer | Responsibility | Primary artifacts |
|--------|----------------|---------------------|
| Ingestion | Validate + insert **events** (provenance, narrative, impact fields) | `events` |
| Enrichment | Derive **structured impacts** and optional **actors** for rows that lack `event_consequences` | `event_consequences`, `actors`, `event_actors` |
| Scoring | Apply eligible pending events to **ledger** + snapshots | `country_scores`, `country_score_snapshots`, `events.applied_to_scores` |

**Why not mix**

- **Ingest** must stay cheap and contract-bound (`throughIngestContract`); stuffing consequence math here blurs validation vs modeling.
- **Enrich** can be re-run or batched (`--limit`) without re-ingesting.
- **Scoring** applies **source policy** (defer low-trust) and **noise caps**; it must not run inside ingest.

---

## 2. Commands

```bash
# Full pipeline: ingest → enrich → score
npm run pipeline:run

# Pass country override to ingestion (see runSourceIngestion)
npm run pipeline:run -- --country=us

# Limit enrichment batch size (same as enrich:consequences)
npm run pipeline:run -- --limit=100

# Dry-run: ingestion + enrichment validate / count only; scoring step is skipped (no DB writes)
npm run pipeline:run -- --dry-run

# Single layer (last flag wins if multiple --*-only are passed)
npm run pipeline:run -- --ingest-only
npm run pipeline:run -- --enrich-only
npm run pipeline:run -- --score-only
```

Environment: `.env.local` + `PG*` (see other scripts). Ingestion honors **`INGEST_ADAPTERS`** and CLI **`--adapters=`** via `parseSourceIngestArgs` (same as `scripts/runSourceIngestion.js`). Pipeline **`--country`** / **`--dry-run`** override or combine with those ingest flags when both are present.

---

## 3. Output / observability

After a run, the script prints **`=== Evolution pipeline summary ===`** with each executed step:

- `started` / `finished` (ISO timestamps)
- `ok` (boolean)
- `error` (only if the step threw — **failure location** is the step name)
- `stats` (JSON): per-step counters from the underlying runner (e.g. `validatedOk`, `inserted`, `rejected`, `eventsProcessed`, `consequenceRows`, `pending`, `eventsMarkedApplied`, …)

**Failure handling:** If a step throws, the process exits with code **1**, the failing step is listed with `ok: false` and `error`, and the error is also printed to stderr with `[evolution-pipeline] FAILED at step "…"`.

**Persisted audit:** Each run appends one JSON line under `data/metrics/evolution/` (`OBSERVABILITY_LAYER_V1.md`). Summarize with **`npm run report:pipeline-health`**.

---

## 4. Dry-run semantics

| Step | `--dry-run` behavior |
|------|----------------------|
| Ingestion | Same as `runSourceIngestion.js --dry-run`: no `INSERT` into `events`; logs `[dry-run]` lines |
| Enrichment | Same as `runConsequenceEnrichment.js --dry-run`: no writes; counts hypothetical consequence rows |
| Scoring | **Not executed.** Summary shows `scoring` with `stats.skipped: true` and a reason string (avoids partial score writes) |

To exercise **only** scoring logic against the DB, run **`--score-only`** **without** `--dry-run`, or run `node scripts/updateScores.js` directly.

---

## 5. Retrying after failure

1. Read stderr and the summary block to see **which step** failed (`ingestion` / `enrichment` / `scoring`).
2. Fix data or config, then:
   - Re-run the **full** pipeline, or
   - Run **`--ingest-only`**, **`--enrich-only`**, or **`--score-only`** to repeat just that layer (safe if prior steps already succeeded).
3. Ingestion is **not** idempotent across sample adapters (may insert duplicate events if samples run again). Enrichment is idempotent for already-filled consequences; scoring marks events applied once.

---

## 6. Relation to `runDailyEvolution.js`

| | `pipeline:run` | `runDailyEvolution.js` |
|---|----------------|-------------------------|
| Purpose | Linear **ingest → enrich → score** with structured summary | Broader **daily** loop (countries, auto-events env, snapshots, cache invalidation, etc.) |
| Replaces? | No | Kept as-is |

Use **`pipeline:run`** for a clear, observable **three-stage** evolution path. Use **`runDailyEvolution.js`** when you need its full cron-oriented workflow.

---

## 7. Files

| Path | Role |
|------|------|
| `scripts/runEvolutionPipeline.js` | Orchestrator CLI |
| `lib/pipeline/sourceIngestionStep.js` | Ingestion implementation |
| `lib/pipeline/consequenceEnrichmentStep.js` | Enrichment implementation |
| `scripts/updateScores.js` | Exports `runUpdateScores()`; CLI still runs when executed directly |
