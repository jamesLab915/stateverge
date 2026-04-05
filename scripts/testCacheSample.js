/**
 * Narrative/cache regression: sampled keys match expected prefixes; no spaces.
 */
const { createPool } = require("./_regressionEnv.js");

const PREFIXES = [
  "evolution-summary:",
  "narrative-graph:",
  "evolution-contrast:",
  "narrative-graph-compare:",
];

async function main() {
  const pool = createPool();
  try {
    try {
      await pool.query(`SELECT cache_key FROM ai_insights_cache LIMIT 1`);
    } catch (e) {
      if (e.code === "42P01" || e.code === "42703") {
        console.log(
          "⚠ Skip: ai_insights_cache missing or different schema (run evolution migrations)."
        );
        await pool.end();
        process.exit(0);
      }
      throw e;
    }

    const { rows } = await pool.query(
      `
      SELECT cache_key
      FROM ai_insights_cache
      WHERE cache_key LIKE ANY($1::text[])
      LIMIT 500
      `,
      [PREFIXES.map((p) => p + "%")]
    );

    let bad = 0;
    for (const r of rows) {
      const k = String(r.cache_key);
      if (k.includes(" ") || k.length < 5) bad++;
    }

    if (bad > 0) {
      console.error("FAIL: invalid cache_key samples");
      process.exit(1);
    }

    console.log(
      `✓ Cache key sample OK (${rows.length} row(s) under evolution/narrative prefixes).`
    );
    if (rows.length === 0) {
      console.log("  (no rows yet — table empty is OK for fresh DB)");
    }
    process.exit(0);
  } catch (e) {
    console.error(e);
    process.exit(1);
  } finally {
    await pool.end();
  }
}

main();
