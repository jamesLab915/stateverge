/**
 * Baseline metrics snapshot for Evolution Engine v1 (read-only counts).
 * Usage: node scripts/exportBaselineMetrics.js [--json]
 */
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env.local"), override: true });

const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function safeCount(query, label) {
  try {
    const { rows } = await pool.query(query);
    return rows[0]?.c ?? rows[0]?.count ?? 0;
  } catch (e) {
    if (e.code === "42P01" || e.code === "42703") return null;
    throw e;
  }
}

async function main() {
  const json = process.argv.includes("--json");

  const metrics = {
    baseline: "Evolution Engine v1",
    generated_at: new Date().toISOString(),
    countries: await safeCount(`SELECT COUNT(*)::int AS c FROM countries`),
    events: await safeCount(`SELECT COUNT(*)::int AS c FROM events`),
    event_consequences: await safeCount(
      `SELECT COUNT(*)::int AS c FROM event_consequences`
    ),
    country_score_snapshots: await safeCount(
      `SELECT COUNT(*)::int AS c FROM country_score_snapshots`
    ),
    events_unapplied: await safeCount(
      `SELECT COUNT(*)::int AS c FROM events WHERE COALESCE(applied_to_scores, FALSE) = FALSE`
    ),
  };

  const ai = await safeCount(`SELECT COUNT(*)::int AS c FROM ai_insights_cache`);
  metrics.ai_insights_cache_total = ai;

  if (ai != null) {
    let keyColOk = true;
    try {
      await pool.query(`SELECT cache_key FROM ai_insights_cache LIMIT 1`);
    } catch (e) {
      if (e.code === "42703") keyColOk = false;
      else throw e;
    }
    if (keyColOk) {
      metrics.ai_insights_cache = {
        evolution_summary: await safeCount(
          `SELECT COUNT(*)::int AS c FROM ai_insights_cache WHERE cache_key LIKE 'evolution-summary:%'`
        ),
        narrative_graph: await safeCount(
          `SELECT COUNT(*)::int AS c FROM ai_insights_cache WHERE cache_key LIKE 'narrative-graph:%'`
        ),
        evolution_contrast: await safeCount(
          `SELECT COUNT(*)::int AS c FROM ai_insights_cache WHERE cache_key LIKE 'evolution-contrast:%'`
        ),
        narrative_graph_compare: await safeCount(
          `SELECT COUNT(*)::int AS c FROM ai_insights_cache WHERE cache_key LIKE 'narrative-graph-compare:%'`
        ),
        compare_causal: await safeCount(
          `SELECT COUNT(*)::int AS c FROM ai_insights_cache WHERE cache_key LIKE 'compare-causal:%'`
        ),
      };
    } else {
      metrics.ai_insights_cache_note =
        "cache_key column missing; run evolution migrations for per-key counts.";
    }
  }

  metrics.federal_actors = await safeCount(
    `SELECT COUNT(*)::int AS c FROM federal_actors`
  );
  metrics.federal_events = await safeCount(
    `SELECT COUNT(*)::int AS c FROM federal_events`
  );
  metrics.federal_event_consequences = await safeCount(
    `SELECT COUNT(*)::int AS c FROM federal_event_consequences`
  );

  const fac = await safeCount(`SELECT COUNT(*)::int AS c FROM federal_ai_cache`);
  metrics.federal_ai_cache_total = fac;

  await pool.end();

  if (json) {
    console.log(JSON.stringify(metrics, null, 2));
  } else {
    console.log("StateVerge Baseline Metrics (v1)\n");
    console.log(JSON.stringify(metrics, null, 2));
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
