# Real Source Adapters v1 — StateVerge

First batch of **structural** adapters that map vendor-shaped records into the **Ingest Contract v1** raw object. **No new pages, no new product features** — this is an ingestion entry structure only. Evolution Engine scoring logic is **unchanged**; validated events still enter through the same persistence and `updateScores` paths as today.

**Rule:** real feeds must **never** write directly to truth-layer / `events` without passing the ingest contract. Flow is always:

1. Vendor record → adapter `rawEventFrom*Record(...)` → **raw event object** (plain `Record`, snake_case–friendly keys)
2. `throughIngestContract(raw)` → `{ normalized, validation }`  
   - `normalized` = `normalizeIncomingEvent(raw)`  
   - `validation` = `validateIncomingEvent(raw)` (internally normalizes again for a single source of truth)
3. If `validation.ok === true`, persist via existing scripts/SQL using **`validation.value`** only. If `ok === false`, log/quarantine and **do not** apply scores or treat as ground truth.

See also: [`INGEST_CONTRACT_V1.md`](./INGEST_CONTRACT_V1.md), [`EVENT_SOURCE_POLICY.md`](./EVENT_SOURCE_POLICY.md).

**Code layout**

| Path | Role |
|------|------|
| `lib/ingest/adapters/officialAdapter.js` | `official` |
| `lib/ingest/adapters/institutionalAdapter.js` | `institutional` |
| `lib/ingest/adapters/reputableMediaAdapter.js` | `reputable_media` |
| `lib/ingest/adapters/throughIngestContract.js` | `normalizeIncomingEvent` + `validateIncomingEvent` wrapper |
| `lib/ingest/adapters/index.js` | Public exports |
| `lib/ingest/normalizeIncomingEvent.js` | Normalization |
| `lib/ingest/validateIncomingEvent.js` | Validation + `truth` vs `signal` layer hint |

---

## 1. First-batch sources (v1 placeholders)

| Adapter | Intended real sources (future wire-up) | `source_type` |
|---------|----------------------------------------|---------------|
| **official** | Executive offices, gazettes, primary regulator releases (e.g. White House briefing room, national gazette APIs) | `official` |
| **institutional** | Central banks, legislatures, IGOs, statistical agencies (e.g. ECB/Fed press, UN DESA, national stats offices) | `institutional` |
| **reputable_media** | Edited wires / newspapers (e.g. Reuters, AP-style RSS or partner APIs) | `reputable_media` |

v1 ships **mock-friendly** vendor structs and parsers only; HTTP/HTML/RSS clients can replace the sample record constructors later without changing the ingest boundary.

---

## 2. Field mapping (per adapter)

### Official (`officialAdapter.js`)

| Vendor field | Ingest raw key |
|--------------|----------------|
| `jurisdiction` / `countryCode` | `target_country_code` |
| `headline` / `title` | `title` |
| `summary` / `bodyText` / `dek` | `summary` |
| `canonicalUrl` / `url` | `source_url` |
| `issuerDisplayName` | `source_name` |
| `issuedAt` / `publishedAt` | `event_date` |
| `topicSlug` | `event_type` (default `official_publication`) |
| (optional) `impact_direction`, `impact_strength` | passthrough |
| (fixed) — | `source_type`: `official` |

### Institutional (`institutionalAdapter.js`)

| Vendor field | Ingest raw key |
|--------------|----------------|
| `targetCountryCode` / `countryCode` | `target_country_code` |
| `title` / `headline` | `title` |
| `abstractText` / `summary` / `description` | `summary` |
| `link` / `url` | `source_url` |
| `organizationName` | `source_name` |
| `pubDate` / `publishedAt` | `event_date` |
| `category` | `event_type` (default `institutional_notice`) |
| (optional) impact fields | passthrough |
| (fixed) — | `source_type`: `institutional` |

### Reputable media (`reputableMediaAdapter.js`)

| Vendor field | Ingest raw key |
|--------------|----------------|
| `countryHint` / `countryCode` | `target_country_code` |
| `headline` / `title` | `title` |
| `description` / `summary` / `contentSnippet` | `summary` |
| `articleUrl` / `link` | `source_url` |
| `outletName` | `source_name` |
| `pubDate` / `publishedAt` | `event_date` |
| `section` | `event_type` (default `reputable_media_report`) |
| (optional) impact fields | passthrough |
| (fixed) — | `source_type`: `reputable_media` |

---

## 3. Impact placeholders

Many feeds do not emit `impact_direction` / `impact_strength`. Adapters default to **`positive`** and **`5`** via `lib/ingest/adapters/shared.js` when omitted. A later **enrichment** step (not in v1 scope) should override before high-stakes persistence if needed.

---

## 4. normalizeIncomingEvent / validateIncomingEvent

### 4.1 `normalizeIncomingEvent(raw)`

Runs **before** strict validation. Input is the adapter **raw** object (may use camelCase aliases; see `normalizeIncomingEvent.js`). Output is a stable snake_case–oriented object including:

- `country_code`, `event_type`, `title`, `summary`, `event_date` (YYYY-MM-DD or empty)
- `impact_direction`, `impact_strength`, `source_type` (canonical via `eventSourcePolicy.normalizeSourceType`)
- `confidence` (from `prepareCountryEventSourceFields` when `confidence` / `confidenceScore` absent on raw — default score by source type)
- `source_name`, `source_url`, optional `parent_event_id`, optional `primary_actor`

Does **not** guarantee ingest validity (e.g. missing URL); use validation for that.

### 4.2 `validateIncomingEvent(raw)`

Uses normalized fields and **rejects** with `ok: false` + `errors[]` when any of the following fail (non-exhaustive; see source for exact codes):

- Missing/empty: `country_code`, `event_type`, `title`, `summary`, `event_date`, `source_type`, `source_name`
- `source_type` not in canonical list / known aliases → `unrecognized_source_type`
- `source_url` missing or not `http://` / `https://`
- `impact_direction` not `positive` or `negative`
- `impact_strength` not integer 1–10
- Optional `confidence` / `confidenceScore` present but not numeric clampable to 0–100

On success: `ok: true`, `value` = merged normalized payload, `layer`: `truth` | `signal` per `isEligibleForAutomaticScoreApplication` (these three adapters map to **`truth`** when valid).

### 4.3 `throughIngestContract(raw)`

```text
raw (adapter output) → normalizeIncomingEvent(raw) → normalized
                    → validateIncomingEvent(raw)   → validation { ok, value?, errors?, layer }
```

**Important:** pass the **same** `raw` the adapter returned into `validateIncomingEvent` (as implemented); do not skip validation or INSERT `normalized` alone into truth tables.

**Truth vs signal:** Adapters do not set `applied_to_scores`; the DB writer and `updateScores` remain the authority.

---

## 5. Usage

```javascript
const {
  rawEventFromOfficialRecord,
  sampleOfficialRecord,
  throughIngestContract,
} = require("./lib/ingest/adapters");

const raw = rawEventFromOfficialRecord(sampleOfficialRecord());
const { validation } = throughIngestContract(raw);
if (!validation.ok) throw new Error(validation.errors.join(", "));
// use validation.value for INSERT (not shown in v1)
```

Tests: `npm run test:real-source-adapters` (`scripts/testRealSourceAdapters.js` — no DB).

---

## 6. Evolution Engine boundary

- **Unchanged:** evolution scoring jobs, `updateScores`, snapshot/trend scripts — **no** changes required to adopt adapters.
- **New:** only the **ingest path** that builds `raw` and calls `throughIngestContract` before any INSERT. All real sources must use this contract; **no** direct “truth” writes from adapter output.
