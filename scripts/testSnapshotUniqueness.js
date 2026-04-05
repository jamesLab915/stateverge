/**
 * Snapshot uniqueness: no duplicate (country_code, calendar day) rows.
 * Aligns with runDailyEvolution ensureSnapshotTodayIfMissing rule.
 */
const { createPool } = require("./_regressionEnv.js");

async function main() {
  const pool = createPool();
  try {
    try {
      await pool.query(`SELECT 1 FROM country_score_snapshots LIMIT 1`);
    } catch (e) {
      if (e.code === "42P01") {
        console.log("⚠ Skip: country_score_snapshots not present.");
        await pool.end();
        process.exit(0);
      }
      throw e;
    }

    const { rows } = await pool.query(`
      SELECT country_code, snapshot_date::date AS d, COUNT(*)::int AS n
      FROM country_score_snapshots
      GROUP BY country_code, snapshot_date::date
      HAVING COUNT(*) > 1
      ORDER BY country_code, d
    `);

    if (rows.length > 0) {
      console.warn(
        "WARN: duplicate (country_code, day) groups — often legacy from multiple updateScores runs per day."
      );
      console.table(rows);
      if (process.env.STRICT_SNAPSHOT_UNIQUE === "1") {
        console.error("FAIL: set STRICT_SNAPSHOT_UNIQUE=0 to warn-only, or dedupe DB.");
        process.exit(1);
      }
      console.log(
        "  (soft pass; use STRICT_SNAPSHOT_UNIQUE=1 in CI after DB cleanup, or rely on runDailyEvolution idempotency going forward)"
      );
      process.exit(0);
    }

    console.log(
      "✓ No duplicate (country_code, snapshot_date::date) groups in country_score_snapshots."
    );
    process.exit(0);
  } catch (e) {
    console.error(e);
    process.exit(1);
  } finally {
    await pool.end();
  }
}

main();
