# Event Source Policy — Layer v1 (StateVerge)

Defines **canonical** `source_type` labels and **confidence** shapes for:

- Country layer: `events` (+ `events.confidence` as **INTEGER 0–100** ledger weight)
- US Federal layer: `federal_events` (+ `confidence` as **TEXT**: `confirmed` | `contested` | `speculative`)

Implementation: `lib/evolution/eventSourcePolicy.js` and `lib/ingest/eventSourceIngest.js` (re-export).

---

## 1. Canonical `source_type` values

| Value | Meaning | Typical use |
|-------|---------|-------------|
| **official** | Primary government / regulator primary publication | Gazettes, official journals, signed orders |
| **institutional** | Established orgs (IGOs, central banks, legislatures, agencies) | IMF, Fed, congressional record, agency releases |
| **reputable_media** | Edited outlets with corrections policy | Wire, major national papers |
| **market_data** | Prices, flows, exchange/ECB statistics | Market close, macro prints |
| **x_signal** | Short-cycle social / platform signals | High noise; not primary truth |
| **inferred** | Derived inside StateVerge rules from other facts | Model bridges, not raw ingest |
| **ai_generated** | LLM or synthetic generators | `autoEvents.js`; never implied as ground truth |

Legacy strings are normalized via aliases (e.g. `departmental` → `institutional`, `government` → `official`). Unknown → `inferred`.

---

## 2. Confidence mapping

### Country `events.confidence` (integer)

Default scores by canonical type (override with explicit `confidenceScore` in `prepareCountryEventSourceFields`):

| source_type | Default score |
|-------------|----------------|
| official | 95 |
| institutional | 88 |
| market_data | 85 |
| reputable_media | 75 |
| inferred | 55 |
| x_signal | 45 |
| ai_generated | 40 |

### Federal `federal_events.confidence` (text)

| source_type | Default federal confidence |
|-------------|----------------------------|
| official, institutional, market_data | `confirmed` |
| reputable_media | `contested` |
| x_signal, inferred, ai_generated | `speculative` |

Override via `prepareFederalEventSourceFields({ confidenceText: 'confirmed' })` when the row is explicitly verified.

---

## 3. Truth layer guard (automatic scoring)

`updateScores.js` applies **only** events that pass `isEligibleForAutomaticScoreApplication`:

- **Eligible:** `source_type` **NULL** (legacy rows), or `official` | `institutional` | `reputable_media` | `market_data`
- **Deferred (not applied to scores):** `ai_generated`, `inferred`, `x_signal` unless `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1`

Deferred events remain `applied_to_scores = false` until operators change source metadata or set the env flag (e.g. dev/demo).

This prevents low-trust and synthetic streams from silently moving the national ledger.

---

## 4. Database

- `events.source_type` added by `scripts/sql/migrate_event_source_layer_v1.sql` (run `node scripts/migrateEventSourceLayer.js`).
- `federal_events` already had `source_type` + `confidence` text.

---

## 5. Ingest checklist (future connectors)

1. Map vendor label → `normalizeSourceType(raw)`.
2. Country events: `prepareCountryEventSourceFields({ countryCode, sourceType, confidenceScore? })`.
3. Federal events: `prepareFederalEventSourceFields({ sourceType, confidenceText? })`.
4. Never write consequence rows from AI without a backing `events` / `federal_events` row tagged appropriately.
5. Prefer **official** / **institutional** for anything that should move scores in production.

---

## 6. Scripts touched

| Script | Behavior |
|--------|----------|
| `autoEvents.js` | Sets `source_type = ai_generated`, default confidence 40. |
| `addEvent.js` | Example uses `institutional` / score 88. |
| `updateScores.js` | Filters low-trust unless env override. |

---

## 7. Environment

| Variable | Effect |
|----------|--------|
| `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1` | Include `ai_generated`, `inferred`, `x_signal` in `updateScores` application. |
