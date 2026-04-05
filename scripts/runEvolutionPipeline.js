/**
 * Evolution Pipeline Orchestrator v1 — ingestion → enrichment → scoring (sequential).
 * Does not replace runDailyEvolution.js (different scope; see ORCHESTRATION_LAYER_V1.md).
 *
 * Usage:
 *   node scripts/runEvolutionPipeline.js [--country=us] [--limit=100] [--dry-run]
 *   [--ingest-only] [--enrich-only] [--score-only]
 *
 * Observability: appends JSON lines to data/metrics/evolution/runs-YYYY-MM-DD.jsonl (see OBSERVABILITY_LAYER_V1.md).
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const {
  runSourceIngestionStep,
  parseSourceIngestArgs,
} = require("../lib/pipeline/sourceIngestionStep.js");
const { runConsequenceEnrichmentStep } = require("../lib/pipeline/consequenceEnrichmentStep.js");
const { runUpdateScores } = require("./updateScores.js");
const {
  appendEvolutionPipelineRun,
  buildPipelineRunRecord,
} = require("../lib/observability/evolutionRunLogger.js");

function parseArgs(argv) {
  let country = null;
  let limit = null;
  let dryRun = false;
  /** @type {'all'|'ingest'|'enrich'|'score'} */
  let mode = "all";
  for (const a of argv) {
    if (a.startsWith("--country="))
      country = a.slice("--country=".length).trim().toLowerCase() || null;
    else if (a.startsWith("--limit=")) {
      const n = parseInt(a.slice("--limit=".length), 10);
      if (Number.isFinite(n) && n > 0) limit = n;
    } else if (a === "--dry-run") dryRun = true;
    else if (a === "--ingest-only") mode = "ingest";
    else if (a === "--enrich-only") mode = "enrich";
    else if (a === "--score-only") mode = "score";
  }
  return {
    country,
    limit,
    dryRun,
    runIngest: mode === "all" || mode === "ingest",
    runEnrich: mode === "all" || mode === "enrich",
    runScore: mode === "all" || mode === "score",
  };
}

/**
 * @param {{ name: string, startedAt?: string, finishedAt?: string, ok?: boolean, error?: string, stats?: Record<string, unknown> }} step
 * @param {() => Promise<Record<string, unknown>>} fn
 * @param {typeof step[]} report
 */
async function runStep(step, fn, report) {
  step.startedAt = new Date().toISOString();
  try {
    step.stats = await fn();
    step.ok = true;
  } catch (e) {
    step.ok = false;
    step.error = e instanceof Error ? e.message : String(e);
    step.finishedAt = new Date().toISOString();
    report.push(step);
    throw e;
  }
  step.finishedAt = new Date().toISOString();
  report.push(step);
}

async function main() {
  const argv = process.argv.slice(2);
  const args = parseArgs(argv);
  const ingestCli = parseSourceIngestArgs(argv);

  /** @type {Array<{ name: string, startedAt?: string, finishedAt?: string, ok?: boolean, error?: string, stats?: Record<string, unknown> }>} */
  const report = [];

  let exitCode = 0;

  try {
    if (args.runIngest) {
      await runStep(
        { name: "ingestion" },
        () =>
          runSourceIngestionStep({
            adapters: ingestCli.adapters,
            country: args.country ?? ingestCli.country,
            dryRun: args.dryRun || ingestCli.dryRun,
          }),
        report
      );
    }

    if (args.runEnrich) {
      await runStep(
        { name: "enrichment" },
        () =>
          runConsequenceEnrichmentStep({
            limit: args.limit,
            dryRun: args.dryRun,
          }),
        report
      );
    }

    if (args.runScore) {
      const scoreStep = { name: "scoring" };
      if (args.dryRun) {
        scoreStep.startedAt = new Date().toISOString();
        scoreStep.finishedAt = scoreStep.startedAt;
        scoreStep.ok = true;
        scoreStep.stats = {
          skipped: true,
          reason: "dry-run: scoring step not executed (updateScores performs DB writes)",
        };
        report.push(scoreStep);
      } else {
        await runStep(scoreStep, () => runUpdateScores(), report);
      }
    }
  } catch (e) {
    const last = report[report.length - 1];
    const where = last ? `step "${last.name}"` : "pipeline";
    console.error(`\n[evolution-pipeline] FAILED at ${where}:`, e);
    exitCode = 1;
  } finally {
    try {
      appendEvolutionPipelineRun(
        buildPipelineRunRecord({
          report,
          argv,
          pipelineArgs: args,
          exitCode,
        })
      );
    } catch (logErr) {
      console.warn(
        "[observability] failed to append evolution run log:",
        logErr instanceof Error ? logErr.message : logErr
      );
    }
  }

  console.log("\n=== Evolution pipeline summary ===\n");
  for (const s of report) {
    console.log(`— ${s.name}`);
    console.log(`  started:  ${s.startedAt}`);
    console.log(`  finished: ${s.finishedAt || "(n/a)"}`);
    console.log(`  ok:       ${s.ok}`);
    if (s.error) console.log(`  error:    ${s.error}`);
    if (s.stats) console.log(`  stats:    ${JSON.stringify(s.stats)}`);
    console.log("");
  }

  if (exitCode) process.exit(exitCode);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
