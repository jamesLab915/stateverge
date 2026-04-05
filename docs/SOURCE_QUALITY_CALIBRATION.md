# Source Quality Calibration — Event Source Layer v1

Purpose: verify that **source policy** protects the truth layer (national ledger from `updateScores`) without silent distortion.

Implementation reference: `lib/evolution/eventSourcePolicy.js`, `docs/EVENT_SOURCE_POLICY.md`, `scripts/updateScores.js`.

---

## 1. Canonical `source_type` list

| `source_type` | Role |
|---------------|------|
| `official` | Primary government / regulator publications |
| `institutional` | Agencies, legislatures, IGOs, central banks |
| `reputable_media` | Edited outlets |
| `market_data` | Markets / official statistics feeds |
| `x_signal` | Short-cycle social / platform signals |
| `inferred` | Rule-derived or unknown-ingest normalized |
| `ai_generated` | LLM / synthetic generators |

Normalization aliases (e.g. `departmental` → `institutional`) apply in `normalizeSourceType()` before eligibility checks.

---

## 2. Default country `events.confidence` (integer 0–100)

| `source_type` | Default score |
|---------------|----------------|
| official | 95 |
| institutional | 88 |
| market_data | 85 |
| reputable_media | 75 |
| inferred | 55 |
| x_signal | 45 |
| ai_generated | 40 |

Explicit `confidenceScore` in ingest helpers overrides defaults.

---

## 3. Participation in automatic scoring (`updateScores`)

**Eligible by default** (included in the “active” batch):

- `source_type` **NULL** or **''** (legacy rows — compatibility)
- `official`, `institutional`, `reputable_media`, `market_data` (after normalization)

**Deferred by default** (remain unapplied until policy or env changes):

- `ai_generated`, `inferred`, `x_signal` (after normalization)

**Not “forbidden”:** deferred events are **not** deleted; they stay `applied_to_scores = false` until:

- `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1` is set, **or**
- `source_type` / metadata is corrected and the job is re-run.

---

## 4. Environment override

| Variable | Effect |
|----------|--------|
| `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1` | Low-trust types **enter** the same scoring pipeline as high-trust. |

Use only in dev/demo or after explicit review.

---

## 5. Known risks

| Risk | Mitigation |
|------|------------|
| **Unknown vendor labels** normalize to `inferred` → **deferred**, which can surprise operators who expected scoring. | Ingest must map to canonical types; monitor `report:source-mix`. |
| **Legacy NULL `source_type`** is treated as **eligible** — preserves old behavior but mixes trust levels. | Backfill `source_type` when possible. |
| **Typos** that match an alias may change trust tier (e.g. `media` → `reputable_media`). | Prefer explicit canonical strings at ingest. |
| **Federal layer** uses separate `federal_events.confidence` text; this calibration doc focuses on **country `events`** for `updateScores`. | See `EVENT_SOURCE_POLICY.md` for federal mapping. |

---

## 6. Automated checks

- `npm run test:source-quality` — policy unit tests (no DB required).
- `npm run report:source-mix` — live DB distribution (requires PostgreSQL).

---

## 7. When to tune parameters

- If **too many** events sit deferred, review ingest mapping before enabling `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES`.
- If **too few** protections, avoid blanket env allow; tighten ingest instead.

Do not widen eligibility in code without updating this document and `EVENT_SOURCE_POLICY.md`.
