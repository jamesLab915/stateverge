/**
 * Observability — summarize recent evolution pipeline runs + ingest rejection log.
 *
 * Usage: node scripts/reportPipelineHealth.js [--days=7] [--json]
 */
const path = require("path");
const {
  listRunLogFiles,
  readJsonlFile,
  getEvolutionMetricsDir,
} = require("../lib/observability/evolutionRunLogger.js");
const { readRejectionLogLines } = require("../lib/ingest/ingestRejectionLog.js");

function parseArgs(argv) {
  let days = 7;
  let json = false;
  for (const a of argv) {
    if (a.startsWith("--days=")) {
      const n = parseInt(a.slice("--days=".length), 10);
      if (Number.isFinite(n) && n > 0 && n <= 365) days = n;
    } else if (a === "--json") json = true;
  }
  return { days, json };
}

function cutoffDate(days) {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}

/**
 * @param {string} file
 * @param {string} minDay YYYY-MM-DD inclusive
 */
function fileInWindow(file, minDay) {
  const base = path.basename(file);
  const m = /^runs-(\d{4}-\d{2}-\d{2})\.jsonl$/.exec(base);
  if (!m) return false;
  return m[1] >= minDay;
}

/**
 * @param {Record<string, unknown>[]} records
 */
function aggregatePipeline(records) {
  const totals = {
    ingestion_validated_ok: 0,
    ingestion_inserted: 0,
    ingestion_rejected: 0,
    enrichment_consequence_rows: 0,
    enrichment_events_processed: 0,
    enrichment_actors_attached: 0,
    scoring_events_marked_applied: 0,
    scoring_countries_updated: 0,
    /** proxy: one snapshot INSERT per country updated per scoring run */
    scoring_snapshots_written_estimate: 0,
    scoring_skipped_dry_run_runs: 0,
  };
  let runs_ok = 0;
  let runs_failed = 0;
  /** @type {Record<string, number>} */
  const failures_by_step = {
    ingestion: 0,
    enrichment: 0,
    scoring: 0,
    unknown: 0,
  };

  /** @type {Record<string, unknown> | null} */
  let last_run = null;

  for (const r of records) {
    if (r.overallOk === true) runs_ok++;
    else if (r.overallOk === false) runs_failed++;

    const ts = String(r.ts || "");
    if (!last_run || ts > String(last_run.ts || "")) last_run = r;

    if (r.overallOk === false && Array.isArray(r.steps)) {
      const failed = r.steps.find((s) => s && s.ok === false);
      const n = failed && failed.name ? String(failed.name) : "unknown";
      if (failures_by_step[n] !== undefined) failures_by_step[n]++;
      else failures_by_step.unknown++;
    }

    for (const s of r.steps || []) {
      const st = /** @type {Record<string, number>} */ (s.stats || {});
      if (s.name === "ingestion") {
        totals.ingestion_validated_ok += Number(st.validatedOk || 0);
        totals.ingestion_inserted += Number(st.inserted || 0);
        totals.ingestion_rejected += Number(st.rejected || 0);
      } else if (s.name === "enrichment") {
        totals.enrichment_consequence_rows += Number(st.consequenceRows || 0);
        totals.enrichment_events_processed += Number(st.eventsProcessed || 0);
        totals.enrichment_actors_attached += Number(st.actorsAttached || 0);
      } else if (s.name === "scoring") {
        if (st.skipped) {
          totals.scoring_skipped_dry_run_runs += 1;
        } else {
          totals.scoring_events_marked_applied += Number(st.eventsMarkedApplied || 0);
          const cu = Number(st.countriesUpdated || 0);
          totals.scoring_countries_updated += cu;
          totals.scoring_snapshots_written_estimate += cu;
        }
      }
    }
  }

  return {
    pipeline_runs_in_window: records.length,
    pipeline_runs_ok: runs_ok,
    pipeline_runs_failed: runs_failed,
    failures_by_step,
    totals,
    last_run,
  };
}

function aggregateIngestRejections(lines) {
  const by_primary = {};
  for (const row of lines) {
    const p = String(row.primaryCategory || "other");
    by_primary[p] = (by_primary[p] || 0) + 1;
  }
  return {
    total_lines: lines.length,
    by_primary_category: by_primary,
  };
}

function main() {
  const { days, json } = parseArgs(process.argv.slice(2));
  const minDay = cutoffDate(days);
  const files = listRunLogFiles().filter((f) => fileInWindow(f, minDay));

  /** @type {Record<string, unknown>[]} */
  const records = [];
  for (const f of files) {
    records.push(...readJsonlFile(f));
  }
  records.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));

  const pipeline = aggregatePipeline(records);
  const rejectLines = readRejectionLogLines();
  const ingest_rejections = aggregateIngestRejections(rejectLines);

  const out = {
    generated_at: new Date().toISOString(),
    window_days: days,
    metrics_dir: getEvolutionMetricsDir(),
    pipeline,
    ingest_rejections_log: ingest_rejections,
    cache_invalidations: {
      tracked: false,
      note: "Evolution pipeline v1 does not emit cache invalidation events; use runDailyEvolution or app hooks when applicable.",
    },
  };

  if (json) {
    console.log(JSON.stringify(out, null, 2));
  } else {
    console.log("StateVerge — pipeline health (observability v1)\n");
    console.log(JSON.stringify(out, null, 2));
  }
}

main();
