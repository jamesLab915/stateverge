/**
 * Source Ingestion Runner v1 — adapters → throughIngestContract → INSERT events (validation.ok only).
 * Does not invoke updateScores.
 *
 * Usage:
 *   node scripts/runSourceIngestion.js [--adapters=official,institutional,reputable_media] [--country=us] [--dry-run]
 * Env: INGEST_ADAPTERS (comma list), INGEST_DEFAULT_COUNTRY, PG* (see other scripts)
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const {
  runSourceIngestionStep,
  parseSourceIngestArgs,
} = require("../lib/pipeline/sourceIngestionStep.js");

async function main() {
  const { adapters, country, dryRun } = parseSourceIngestArgs(process.argv.slice(2));
  const stats = await runSourceIngestionStep({ adapters, country, dryRun });
  console.log(
    `\nDone. validated_ok=${stats.validatedOk} inserted=${stats.inserted} rejected=${stats.rejected} dryRun=${stats.dryRun}`
  );
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
