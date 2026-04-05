/**
 * Consequence Enrichment Runner v1 — backfill event_consequences (+ optional actors) for
 * events that have none. Does not call updateScores or touch country_scores.
 *
 * Usage: node scripts/runConsequenceEnrichment.js [--limit=100] [--dry-run]
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const {
  runConsequenceEnrichmentStep,
  parseEnrichmentArgs,
} = require("../lib/pipeline/consequenceEnrichmentStep.js");

async function main() {
  const { limit, dryRun } = parseEnrichmentArgs(process.argv.slice(2));
  const stats = await runConsequenceEnrichmentStep({ limit, dryRun });
  console.log(
    `Consequence enrichment done. events=${stats.eventsProcessed} consequence_rows=${stats.consequenceRows} actors_attached=${stats.actorsAttached} dryRun=${stats.dryRun}`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
