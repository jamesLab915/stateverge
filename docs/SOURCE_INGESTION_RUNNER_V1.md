# Source Ingestion Runner v1 — StateVerge

Executable pipeline: **Real Source Adapters v1** → **Ingest Contract** (`throughIngestContract`) → **optional `events` INSERT**. Does **not** call `updateScores` (truth-layer application stays on your existing schedule or manual runs).

---

## 1. Flow

```
Adapter (vendor shape)
    → raw event object
    → throughIngestContract(raw)
         ├─ normalizeIncomingEvent
         └─ validateIncomingEvent
    → if validation.ok === false
         └─ append JSONL line to data/ingest_rejections.jsonl + console [reject]
    → if validation.ok === true
         └─ INSERT INTO events (… source_type, confidence, source_name, source_url …)
              applied_to_scores = FALSE
```

No path skips validation. Failed validation never touches the database.

Runner summary line: **`validated_ok`** = passed contract; **`inserted`** = rows actually written (0 when `--dry-run`).

---

## 2. Commands

| Command | Purpose |
|---------|---------|
| `npm run ingest:run` | Run default adapters (official, institutional, reputable_media) with built-in samples |
| `npm run ingest:run -- --dry-run` | Validate only; no DB writes |
| `npm run ingest:run -- --country=us` | Override `target_country_code` on each sample (helps FK to `countries`) |
| `npm run ingest:run -- --adapters=official,reputable_media` | Subset |
| `npm run ingest:report` | Summarize `data/ingest_rejections.jsonl` |
| `npm run ingest:report -- --json` | Machine-readable report |

Environment:

- `INGEST_ADAPTERS` — comma list (same as `--adapters`)
- `INGEST_DEFAULT_COUNTRY` — same as `--country`
- `PGUSER`, `PGHOST`, `PGDATABASE`, `PGPORT` — PostgreSQL (see other scripts)

---

## 3. Rejection handling

Each failed attempt appends one JSON line to **`data/ingest_rejections.jsonl`** (gitignored) with:

- `ts`, `adapter`, `errors[]`, `categories[]`, `primaryCategory`, `rawPreview`

**Category mapping** (from error codes):

| Category | Typical codes |
|----------|----------------|
| `missing_field` | empty country, title, summary, source_type, source_name, URL, etc. |
| `invalid_source_type` | `unrecognized_source_type` |
| `invalid_date` | `missing_or_invalid_event_date` |
| `invalid_url` | `source_url_must_be_http_or_https` |
| `invalid_confidence` | `invalid_confidence_not_numeric` |
| `invalid_impact` | impact direction / strength |
| `invalid_reference` | `invalid_parent_event_id` |
| `database` | `db_insert_failed` (FK, missing column, etc.) |
| `other` | anything else |

`npm run ingest:report` aggregates **total_rejected**, **by_primary_category**, **by_category** (from each error code), and **by_error_code**.

---

## 4. Truth layer vs signal layer

- **Runner** only inserts rows; it does **not** run `updateScores`.
- **`applied_to_scores`** is always **`FALSE`** on insert; scoring is unchanged.
- **Layer** (`truth` vs `signal`) from validation is logged to stdout for visibility; automatic score eligibility is still enforced later by `eventSourcePolicy` + `updateScores` (see `SOURCE_QUALITY_CALIBRATION.md`).

---

## 5. Provenance guarantee

Inserts use **`validation.value`**, which always includes canonical **`source_type`**, integer **`confidence`**, **`source_name`**, and **`source_url`** when `ok === true`. An extra internal guard rejects if any of these are missing (should not happen if validation stays consistent).

---

## 6. Files

| Path | Role |
|------|------|
| `scripts/runSourceIngestion.js` | Runner |
| `scripts/reportRejectedIngests.js` | Report |
| `lib/ingest/throughIngestContract.js` | normalize + validate |
| `lib/ingest/classifyIngestRejection.js` | Error → category |
| `lib/ingest/ingestRejectionLog.js` | JSONL path + read |
| `data/ingest_rejections.jsonl` | Rejection log (generated, not committed) |
