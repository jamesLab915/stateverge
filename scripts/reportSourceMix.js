/**
 * Report country `events` source_type mix (applied vs pending, high vs low trust).
 * Usage: node scripts/reportSourceMix.js [--json]
 */
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env.local"), override: true });

const { createPgPool } = require("../lib/db/pgPool.js");
const {
  normalizeSourceType,
  isLowTrustSourceType,
} = require("../lib/evolution/eventSourcePolicy.js");

const pool = createPgPool();

function bucket(st) {
  if (st == null || st === "")
    return { key: "legacy_null", trust: "legacy_eligible" };
  const t = normalizeSourceType(st);
  if (isLowTrustSourceType(t)) return { key: t, trust: "low_trust" };
  if (
    t === "official" ||
    t === "institutional" ||
    t === "reputable_media" ||
    t === "market_data"
  ) {
    return { key: t, trust: "high_trust" };
  }
  return { key: t, trust: "other" };
}

async function main() {
  const json = process.argv.includes("--json");

  let rows;
  try {
    const res = await pool.query(`
      SELECT
        source_type,
        COALESCE(applied_to_scores, FALSE) AS applied,
        COUNT(*)::int AS n
      FROM events
      GROUP BY source_type, COALESCE(applied_to_scores, FALSE)
      ORDER BY source_type NULLS FIRST, applied
    `);
    rows = res.rows;
  } catch (e) {
    if (e.code === "42703") {
      console.error(
        "Column source_type missing on events. Run: npm run db:migrate-event-source"
      );
      await pool.end();
      process.exit(2);
    }
    throw e;
  }

  const total = await pool.query(`SELECT COUNT(*)::int AS c FROM events`);
  const appliedTot = await pool.query(
    `SELECT COUNT(*)::int AS c FROM events WHERE COALESCE(applied_to_scores, FALSE) = TRUE`
  );
  const pendingTot = await pool.query(
    `SELECT COUNT(*)::int AS c FROM events WHERE COALESCE(applied_to_scores, FALSE) = FALSE`
  );

  const byType = {};
  let highTrust = 0;
  let lowTrust = 0;
  let legacy = 0;
  let otherTrust = 0;

  for (const r of rows) {
    const k = r.source_type == null ? "(null)" : String(r.source_type);
    if (!byType[k]) byType[k] = { applied: 0, pending: 0 };
    if (r.applied) byType[k].applied += r.n;
    else byType[k].pending += r.n;

    const { trust } = bucket(r.source_type);
    if (trust === "high_trust") highTrust += r.n;
    else if (trust === "low_trust") lowTrust += r.n;
    else if (trust === "legacy_eligible") legacy += r.n;
    else otherTrust += r.n;
  }

  const out = {
    generated_at: new Date().toISOString(),
    events_total: total.rows[0]?.c ?? 0,
    applied_total: appliedTot.rows[0]?.c ?? 0,
    pending_total: pendingTot.rows[0]?.c ?? 0,
    trust_rollups: {
      high_trust_rows: highTrust,
      low_trust_rows: lowTrust,
      legacy_null_or_empty: legacy,
      other_after_normalize: otherTrust,
    },
    by_source_type: byType,
  };

  await pool.end();

  if (json) {
    console.log(JSON.stringify(out, null, 2));
  } else {
    console.log("StateVerge — events source mix\n");
    console.log(JSON.stringify(out, null, 2));
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
