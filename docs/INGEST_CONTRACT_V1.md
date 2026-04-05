# Ingest Contract v1 — StateVerge

Unified contract for **country `events`** before persistence or scoring. Goal: real feeds can plug in without silently polluting the truth layer.

Code: `lib/ingest/normalizeIncomingEvent.js`, `lib/ingest/validateIncomingEvent.js`, `lib/evolution/eventSourcePolicy.js`, `scripts/updateScores.js`.

---

## 1. Required fields (before the event is accepted)

| Field | JSON key(s) accepted | Rule |
|-------|----------------------|------|
| Country | `target_country_code`, `country_code`, `countryCode` | Non-empty; normalized to **lowercase** ISO-style code (e.g. `us`). |
| Event kind | `event_type` | Non-empty slug / label after trim. |
| Narrative | `summary` | Non-empty after trim; if only `title` is present, normalizer may copy title → summary (warning in validation metadata). |
| Headline | `title` | Required after normalization; if missing, normalizer copies from `summary` (if any). Otherwise validation fails. |
| When | `event_date`, `eventDate` | Required for v1 contract; normalized to **UTC calendar date `YYYY-MM-DD`**. |
| Source class | `source_type`, `sourceType` | Required; must be a **canonical** `source_type` or a **registered alias** (see Event Source Policy). Unknown vendor strings are **rejected** (do not silently map to `inferred` at ingest boundary). |
| Provenance | `source_name` | Non-empty human-readable label (e.g. outlet or API name). |
| Link | `source_url` | Non-empty; must start with `http://` or `https://`. |
| Impact | `impact_direction` | `positive` or `negative` (lowercase). |
| Impact | `impact_strength` | Integer **1–10**. |

**Confidence** (`confidence`, `confidenceScore`): optional. If absent, defaults come from `prepareCountryEventSourceFields()` / `defaultCountryConfidenceScore(source_type)`. If present, must be a finite number; out-of-range values are **clamped** to 0–100 and reported via `warnings` from validation.

---

## 2. Source fields: `source_type`, `confidence`, `source_name`, `source_url`

| Field | Requirement |
|-------|-------------|
| `source_type` | Canonical enum or known alias only (strict ingest). Stored value is always **canonical** after normalization. |
| `confidence` | Optional 0–100 integer for country ledger; invalid numbers rejected; out-of-range clamped with warning. |
| `source_name` | Required; trimmed; max length enforced in normalizer (200 chars). |
| `source_url` | Required absolute HTTP(S) URL; trimmed. |

Low-trust types (`ai_generated`, `inferred`, `x_signal`) are **valid** for ingestion but are **signal-tier** for automatic scoring (see §5–6).

---

## 3. Core content: `target_country_code`, `event_type`, `event_date`, `summary`

- **`target_country_code`**: same as §1; maps internally to DB `country_code`.
- **`event_type`**: trim, internal whitespace collapsed; no empty string.
- **`event_date`**: Accepts `YYYY-MM-DD`, ISO datetime strings (date part used), or `Date` (in JS). Output always `YYYY-MM-DD`.
- **`summary`**: trim, collapse internal whitespace, max length (e.g. 4000) truncated with optional warning.
- **`title`**: same trimming rules; max length (e.g. 500) truncated with optional warning.

---

## 4. Optional: actor, consequence, `parent_event_id`

### `primary_actor` (optional)

If present, should be an object:

| Key | Rule |
|-----|------|
| `name` | Non-empty string to attach an actor row. |
| `actor_type` | String; default `coalition` if missing. |
| `role` | String; default `influenced` if missing. |
| `summary` | Optional string. |

Matches usage in `lib/eventAttachments.js`.

### Consequences (optional)

`event_consequences` may be an array of objects for advanced loaders. v1 **validation** does not require them; `attachConsequencesAndActors` can still derive consequences from `event_type` + impact when inserting via existing scripts.

### `parent_event_id`

Optional integer ≥ 1. Non-integers or ≤ 0 are rejected. Must reference an existing row in the application layer (DB FK enforced on insert).

---

## 5. Truth layer vs signal layer (behavioral, not a new DB flag)

| Tier | Meaning |
|------|---------|
| **Truth layer (automatic)** | Event **passes** ingest validation **and** `source_type` is eligible for automatic score application per `isEligibleForAutomaticScoreApplication()` (e.g. `official`, `institutional`, `reputable_media`, `market_data`). Legacy `NULL` `source_type` in DB is a separate backward-compat case **outside** this strict v1 contract. |
| **Signal layer** | Event **passes** validation but `source_type` is **low-trust** (`ai_generated`, `inferred`, `x_signal`): stored and visible in the pipeline, but **not** applied to country scores by default (`updateScores` defers unless `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1`). |

---

## 6. When an event must **not** enter the truth layer automatically

- Low-trust canonical types (above), or
- Failing v1 validation (missing fields, unknown `source_type`, bad URL, bad date, etc.) — should **not** be inserted as a trusted row; fix payload or route to a quarantine path.

---

## 7. Usage

```javascript
const { normalizeIncomingEvent } = require("./lib/ingest/normalizeIncomingEvent.js");
const { validateIncomingEvent } = require("./lib/ingest/validateIncomingEvent.js");

const normalized = normalizeIncomingEvent(raw);
const result = validateIncomingEvent(raw);
// result.ok === true → { ok, value, warnings }
// result.ok === false → { ok, errors, warnings }
```

Run checks: `npm run test:ingest-contract`.
