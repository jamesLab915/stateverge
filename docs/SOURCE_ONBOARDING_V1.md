# Source Onboarding v1 — StateVerge

Onboards **three curated, high-trust RSS feeds** into the existing **adapter → Ingest Contract → `events`** path. No UI; no core schema changes. Validation is **mandatory**; there is no direct INSERT around `validateIncomingEvent` / `throughIngestContract`.

---

## 1. Curated sources (v1)

| id | Display name | `source_type` | Real Source Adapter | Feed |
|----|----------------|---------------|---------------------|------|
| `whitehouse` | The White House (RSS) | `official` | `officialAdapter` | `https://www.whitehouse.gov/news/feed/` |
| `fed_press` | Federal Reserve — All Press Releases | `institutional` | `institutionalAdapter` | `https://www.federalreserve.gov/feeds/press_all.xml` |
| `bbc_world` | BBC News — World | `reputable_media` | `reputableMediaAdapter` | `https://feeds.bbci.co.uk/news/world/rss.xml` |

Feeds may change format; if parsing returns zero items, check `rssLite` / feed HTML.

---

## 2. Field gaps (all RSS-backed rows)

RSS does not provide structured `impact_direction` / `impact_strength`. Adapters apply **defaults** (`positive` / `5`) per `lib/ingest/adapters/shared.js`. `event_type` is a coarse slug (e.g. `official_publication`, `economy_policy`).

Per-source notes are listed in code (`lib/onboarding/curatedSources.js` → `fieldGaps`) and copied into the **onboarding report** JSON.

**Country**

- `whitehouse` / `fed_press` default to **`us`**.
- `bbc_world` defaults to **`gb`** (UK outlet). Override with `--country=` for experiments (e.g. testing FK against your `countries` table).

---

## 3. Commands

| Command | Purpose |
|---------|---------|
| `npm run source:onboard -- --list` | List curated `source` ids |
| `npm run source:onboard -- --source=whitehouse --limit=3 --dry-run` | Trial run: fetch, validate, **no** DB insert |
| `npm run source:onboard -- --source=fed_press --limit=5` | Production ingest for up to 5 items |
| `npm run source:onboard -- --report-rejections` | Runs `ingest:report` (JSON) — global rejection log |
| `npm run ingest:report` | Same rejection aggregate as ingestion runner |

**Network:** onboarding **requires outbound HTTPS** to fetch feeds.

**Report file:** default `data/metrics/onboarding/last-report.json` (overridable with `--report=/path/file.json`).

---

## 4. Production chain (after successful insert)

Ingest script only writes **`events`**. Full stack:

1. `npm run enrich:consequences -- --limit=<N>` — backfill `event_consequences` for new rows  
2. `npm run pipeline:run -- --score-only` — `updateScores`  
3. Observability: `npm run report:pipeline-health`  

Or use **`npm run pipeline:run`** for a full three-phase run when appropriate (`ORCHESTRATION_LAYER_V1.md`).

---

## 5. Onboarding report contents (`source_onboarding_report_v1`)

Written after each run (trial or insert):

- `source_id`, `display_name`, `feed_url`, `source_type`, `adapter`, `effective_country`
- `field_gaps[]`
- `fetch`: `items_parsed`, `limit`, `dry_run`
- `results`: `validated_ok`, `inserted`, `rejected`, **`stable_into_events`** (all parsed items passed contract and, if not dry-run, inserted)
- `rejection_sample` (up to 20 rows)
- `production_chain_hint` (commands)

---

## 6. Stability criteria

**`stable_into_events: true`** when:

- At least one item was parsed from RSS, and  
- Every item passed validation (`validated_ok === items_parsed`), and  
- None rejected by DB (`rejected === 0`), and  
- Either `--dry-run` (contract-only) or `inserted === validated_ok`.

---

## 7. Code layout

| Path | Role |
|------|------|
| `lib/onboarding/rssLite.js` | Minimal `<item>` parsing |
| `lib/onboarding/curatedSources.js` | Source metadata + `toRaw` |
| `scripts/runSourceOnboarding.js` | CLI: fetch → adapter → `throughIngestContract` → `insertValidatedEvent` |
| `lib/pipeline/sourceIngestionStep.js` | Exports **`insertValidatedEvent`** (shared INSERT) |

Rejections are logged to `data/ingest_rejections.jsonl` like other ingest paths.
