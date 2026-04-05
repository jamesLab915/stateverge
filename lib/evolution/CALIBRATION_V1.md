# Calibration Pass v1 — StateVerge Evolution Engine

## Stable rules (keep)

- **Snapshot window drivers**: `dimDelta` over first/last snapshot in the window — grounded in `country_score_snapshots`.
- **Turning points**: `event_consequences` joined to `events`, ranked by summed `ABS(impact_value)` — explicit in `why_important` prefix.
- **Narrative graph edges**: Built from DB joins only; `key_paths` filtered to paths where each step matches a directed edge (`keyPathSupported`).
- **Federal templates**: Separate from country dimensions; mapping stays in `FEDERAL_TEMPLATES`.

## Needs tuning

- **trendEngine `classifySeries`**: Thresholds (4, 5, 6, `absD`) are heuristic; short windows (N=5) amplify noise. Revisit after more snapshot history per country.
- **Neutral `impact_direction`**: Damped to 50% in templates; if backfill uses mostly neutral, consider 0.4–0.55 range per environment.
- **divergence_points**: Still “top magnitude per side” — not a statistical test of “divergence”; label text now says associational.

## event_type templates to refine

- **`economic_change`**: Wording fixed to not imply “always after reform.”
- **Untyped / default rows** (`buildDefaultCountryConsequenceRows`): Still route through governance/risk/opportunity — consider stricter caps or tagging “low confidence” in explanation when `event_type` is missing.
- **Aliases**: `normType` is plain lowercase — add aliases (e.g. `policy_change` → `policy_shift`) in a future pass if data uses variants.

## Narrative over-explanation risks

- **AI polish** (`evolutionNarrativePolish`, `evolutionContrastPolish`): Can soften or dramatize; mitigated by rule merge on read for direction/stability/bottom-line fields where implemented.
- **Contrast `divergence_points`**: Previously suggested cross-country “context”; now labeled as other side’s trend label for contrast only.
- **Narrative graph `divergence_summary`**: No longer embeds full Evolution Contrast text (avoids duplicate causal tone).

## Files touched in calibration v1

- `lib/evolution/trendEngine.js`
- `lib/evolution/narrativeEngine.js`
- `lib/evolution/contrastEngine.js`
- `lib/evolution/consequenceTemplates.js`
- `lib/evolution/narrativeGraph.js`
- `lib/ai/evolutionNarrativePolish.ts`
