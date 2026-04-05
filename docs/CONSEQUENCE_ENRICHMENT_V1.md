# Consequence Enrichment Runner v1 — StateVerge

Separates **event persistence** from **structured impacts and actors**. After events are stored (ingestion runner), this runner fills `event_consequences` (and optionally `actors` / `event_actors`) without touching scores.

---

## 1. Pipeline position

```
Ingestion runner     →  INSERT events (provenance, summary, impact fields, …)
Enrichment runner    →  INSERT event_consequences (+ optional actors)
updateScores (later) →  country_scores / snapshots (applied_to_scores, etc.)
```

- **Ingestion** does **not** run `attachConsequencesAndActors` (by design for `runSourceIngestion.js`).
- **Enrichment** applies the same consequence-building rules as `scripts/backfillEventConsequences.js`:
  1. `buildCountryConsequenceRowsFromTemplate` (`lib/evolution/consequenceTemplates.js`)
  2. else `buildConsequenceRows` (`lib/eventConsequenceMapping.js`)
  3. else `buildDefaultCountryConsequenceRows`
- **Scores** are unchanged here; run `updateScores` separately when you want ledger updates.

---

## 2. Which events are processed

Rows in `events` such that **no** row exists in `event_consequences` for that `event_id` (same predicate as legacy backfill). Ordered by `id ASC`. Optional `--limit=N`.

---

## 3. Actors: when they are added and when skipped

- **Actors are optional.** Enrichment calls `attachPrimaryActorIfPresent` only when:
  1. The event row carries **mappable actor data** in **`enrichment_payload`** (JSON/JSONB), with shape `{ "primary_actor": { "name": "...", "actor_type"?: "...", "role"?: "...", "summary"?: "..." } }`.
  2. There is **no** existing row in `event_actors` for this `event_id` (idempotent).

- **If `enrichment_payload` is absent** (current default schema for many installs), actor step is a **no-op** — consequences still backfill.

- **No** inference of actors from `source_name` / title in v1 (avoids duplicate or low-quality actor rows).

- The column **`enrichment_payload`** is **optional**; if it does not exist, `SELECT *` simply omits it and actor enrichment stays inert until the column is added and populated by a future ingest path.

---

## 4. Commands

| Command | Purpose |
|---------|---------|
| `npm run enrich:consequences` | Process all events missing consequences |
| `npm run enrich:consequences -- --limit=50` | Cap batch size |
| `npm run enrich:consequences -- --dry-run` | Count consequence rows that would be inserted; no DB writes |

Uses `PGUSER`, `PGHOST`, `PGDATABASE`, `PGPORT` (same as other scripts).

---

## 5. Relationship to `updateScores`

- Enrichment **does not** set `applied_to_scores` and **does not** read `country_scores`.
- After enrichment, pending events may still be deferred by **source policy** when you run `updateScores`; the layering is unchanged (`SOURCE_QUALITY_CALIBRATION.md`, `SOURCE_INGESTION_RUNNER_V1.md`).

---

## 6. Files

| Path | Role |
|------|------|
| `scripts/runConsequenceEnrichment.js` | Runner |
| `scripts/backfillEventConsequences.js` | Legacy standalone backfill (same consequence logic) |
| `lib/eventAttachments.js` | `attachPrimaryActorIfPresent` (shared with ingest-style flows) |
