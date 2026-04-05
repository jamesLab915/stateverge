# StateVerge Evolution Engine — Baseline v1 (Freeze)

**Frozen as-of:** development baseline (Evolution Engine v1). Use this document as the reference when extending behavior, tuning parameters, or comparing regressions.

---

## 1. Current system capabilities

| Area | Capability |
|------|------------|
| **Country ledger** | `countries` + `country_scores` (dimension scores, power, risk, opportunity). |
| **Events** | Country-scoped `events` with `event_type`, impact, optional `parent_event_id` chains. |
| **Consequences** | `event_consequences` from templates (`consequenceTemplates`) + mapping + fallback rows. |
| **Snapshots** | `country_score_snapshots` with trend text columns; optional daily idempotent path via `runDailyEvolution`. |
| **Evolution narrative** | Rule engines: `narrativeEngine`, `contrastEngine`, `narrativeGraph`; optional AI polish + `ai_insights_cache`. |
| **US Federal** | `federal_*` tables, actors/events/consequences; wired into narrative graph when `country_code = us` or compare involves US. |
| **Automation** | `updateScores.js`, `backfillEventConsequences.js`, `runDailyEvolution.js`, `autoEvents.js` (ingest). |
| **Signal quality** | `eventSignalQuality.js`: cluster merge, context scaling, noise caps; tests under `scripts/test*.js`. |
| **Frontend** | Country / compare / US pages consume server-side rule + cache; no graph editor product. |

---

## 2. Data flow loop (closed loop)

```
events (ingest / manual)
  → event_consequences (templates / backfill / ensure on apply)
  → updateScores.js (apply unapplied events → country_scores deltas)
  → country_score_snapshots (on apply + optional daily snapshot)
  → narrativeEngine / contrastEngine / narrativeGraph (read DB)
  → ai_insights_cache (optional polish text; keys invalidated by runDailyEvolution)
```

**Idempotency:** `events.applied_to_scores` gates repeated application; snapshot same-day guard in `runDailyEvolution` for the “no new events” path.

---

## 3. Main scripts (reference)

| Script | Role |
|--------|------|
| `scripts/updateScores.js` | Apply pending events → scores + snapshot row with trends. |
| `scripts/backfillEventConsequences.js` | Fill missing consequences for events without rows. |
| `scripts/saveSnapshot.js` | Full snapshot from current `country_scores` (legacy/simple). |
| `scripts/runDailyEvolution.js` | Ingest → backfill → updateScores → idempotent snapshot → cache invalidation. |
| `scripts/autoEvents.js` | Optional OpenAI event generation (`EVOLUTION_COUNTRIES` env). |
| `scripts/runEvolution.js` | Chains autoEvents + backfill + updateScores + insight scripts. |
| `scripts/calibrationSpotcheck.js` | Manual rule vs DB spot check. |
| `scripts/exportBaselineMetrics.js` | Baseline counts (this baseline). |
| `scripts/testSignalQuality.js` / `test*` / `runRegressionChecks.js` | Regression & signal quality. |

---

## 4. Test commands (frozen checklist)

| Command | Purpose |
|---------|---------|
| `npm run test:signal-quality` | Syntax, pure helpers, no-pending path, caps/merge/context. |
| `npm run test:idempotency` | Double `updateScores` + daily dry-run. |
| `npm run test:snapshot-uniqueness` | Duplicate (country, day) detection (soft strict; see `SIGNAL_QUALITY_TESTS.md`). |
| `npm run test:trend-stability` | `trendEngine.classifySeries` behavior. |
| `npm run test:regression` | All above + cache sample + `npm run build`. |

See `docs/SIGNAL_QUALITY_TESTS.md` for the full matrix.

---

## 5. Known limitations (v1)

- **Snapshot history:** Multiple `updateScores` runs per calendar day can duplicate snapshot rows for older workflows; `runDailyEvolution` mitigates for the “daily” path only.
- **Templates:** Unknown `event_type` falls back to default rows; neutral direction dampens but does not remove uncertainty.
- **Compare:** “Divergence” points are high-magnitude ledger events per side, not causal attribution across countries.
- **AI:** Polish does not alter graph nodes/edges or numeric truth; cache keys documented in architecture doc.
- **Federal vs global:** Different template sets; national `events` vs `federal_events` IDs are namespaced in narrative graph.

---

## 6. Default parameters (v1 — code as source of truth)

| Parameter | Value | Location |
|-----------|-------|----------|
| **Merge window** | `3` days | `clusterCountryEvents(..., { windowDays: 3 })` in `updateScores.js` |
| **Cluster merge factor** | `n^(-0.55)` | `clusterMergeFactor` in `eventSignalQuality.js` |
| **neutralDamp** | `0.5` when `impact_direction` is neutral | `consequenceTemplates.js` (`scaleCountryTemplateFields` / `scaleTemplateValues`) |
| **Per-dimension soft cap** | `4.5`; overflow blended with factor `0.35` | `applyNoiseCapsToDimensionDeltas` defaults |
| **L1 cap on dimension deltas** | `28` | same |
| **Power score channel cap** | `3.5`; overflow `0.35` | `applyPowerScoreNoiseCap` |
| **trendEngine volatile** | `vol ≥ 6 && absD ≥ 3` OR `vol ≥ 5 && absD ≥ 2` | `trendEngine.js` |

Tuning should start from these literals unless versioned constants are introduced later.

---

## 7. Related docs

- `docs/SIGNAL_QUALITY_TESTS.md` — test matrix.
- `docs/ARCHITECTURE_OVERVIEW.md` — entities and boundaries.
- `docs/EVENT_SOURCE_POLICY.md` — Event Source Layer v1 (`source_type`, confidence, scoring eligibility).
