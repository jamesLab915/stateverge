# StateVerge — Architecture Overview (Evolution Engine v1)

High-level relationships only; implementation details live in `lib/` and `scripts/`.

---

## 1. Global country layer

```
countries (1) ──< (1) country_scores
     │
     └──< events ──< event_consequences (target_country_code)
              │
              └──< event_actors >── actors
```

- **`country_scores`:** Current ledger (dimensions, overall, risk, opportunity, power).
- **`events`:** Time-stamped signals per `country_code`; may link to **`parent_event_id`** (same table) for chains.
- **`event_consequences`:** Dimension deltas applied by `updateScores.js`; truth for causal narrative and graph edges.
- **`country_score_snapshots`:** Historical points for trends and evolution summaries.

**Narratives & graphs (read path):** Rule engines query these tables; they do not invent numeric facts.

---

## 2. US Federal module vs global country layer

| Layer | Tables (representative) | Relationship |
|-------|-------------------------|--------------|
| **Global** | `countries`, `events`, `event_consequences`, `actors` | Default for all `country_code` values. |
| **US Federal** | `federal_actors`, `federal_events`, `federal_event_actors`, `federal_event_consequences` | Parallel actor/event model for US institutions; **not** merged into `events` automatically. |

- Narrative **graph** for `us` can include **both** national `events` and `federal_*` nodes when rules pull federal data.
- Compare pages can mix **two country ledgers**; US federal is an additional lens when `us` is involved.

---

## 3. Narratives, contrast, and narrative graph

| Output | Source | Cache key pattern (examples) |
|--------|--------|-------------------------------|
| Evolution summary | `narrativeEngine` + optional polish | `evolution-summary:{code}` |
| Evolution contrast | `contrastEngine` + optional polish | `evolution-contrast:{left}-{right}` |
| Narrative graph (country) | `narrativeGraph.buildCountryNarrativeGraph` | `narrative-graph:{code}` |
| Narrative graph (compare) | `buildCompareNarrativeGraph` | `narrative-graph-compare:{left}-{right}` |
| Compare causal (AI assist) | `compare-causal:{sortedPair}` | Separate from contrast graph |

Pages merge **rule truth** (direction, stability strings) with cached wording where applicable.

---

## 4. Truth layer vs AI layer

| | Truth layer | AI layer |
|---|-------------|----------|
| **Owns** | Scores, events, consequences, snapshots, graph nodes/edges, `importance` from rules | Optional wording polish only |
| **Does not** | — | Invent node IDs, edges, or numeric ledger values |

- **`ai_insights_cache`:** JSON payloads for polished text; keys listed above. Invalidation batch runs from `runDailyEvolution`.
- **Federal:** `federal_ai_cache` may exist for US-specific AI snippets (separate from `ai_insights_cache`).

---

## 5. Cache layer role

- **Performance / UX:** Avoid recomputing OpenAI on every request for the same key.
- **Safety:** Application code merges cached strings with **recomputed** rule fields so drift is bounded (see country/compare pages).
- **Operational:** Daily scripts can delete keys to force refresh on next visit.

---

## 6. Event source layer (v1)

- Country **`events`** may set **`source_type`** (canonical labels: `official` … `ai_generated`); **`confidence`** is an integer 0–100.
- **`federal_events`** use the same canonical **`source_type`** strings; **`confidence`** remains `confirmed` | `contested` | `speculative`.
- **`updateScores`** only auto-applies high-trust source types unless `EVOLUTION_ALLOW_LOW_TRUST_SOURCES_FOR_SCORES=1`. See **`docs/EVENT_SOURCE_POLICY.md`**.

## 7. Scripts vs app

- **`scripts/*.js`:** Batch jobs (scores, snapshots, evolution loop, metrics, tests).
- **`app/`:** Server components read DB + rules + cache; no client-side graph truth computation.
