const { createPgPool } = require("../lib/db/pgPool.js");
const {
  computePowerScore,
  classifyPower,
  powerTrendFromScores,
  buildPowerNote,
} = require("../lib/powerMath.js");

const pool = createPgPool();

async function backfillCountryScores(client) {
  const { rows } = await client.query(`SELECT * FROM country_scores`);
  for (const r of rows) {
    const power_score = computePowerScore(r);
    const power_classification = classifyPower({ ...r, power_score });
    await client.query(
      `UPDATE country_scores SET power_score = $1, power_classification = $2 WHERE country_code = $3`,
      [power_score, power_classification, r.country_code]
    );
  }
  console.log(`Updated country_scores power for ${rows.length} rows.`);
}

async function backfillSnapshots(client) {
  const { rows: codes } = await client.query(
    `SELECT DISTINCT country_code FROM country_score_snapshots ORDER BY country_code`
  );

  for (const { country_code } of codes) {
    const { rows } = await client.query(
      `SELECT * FROM country_score_snapshots WHERE country_code = $1 ORDER BY id ASC`,
      [country_code]
    );

    let prevPower = null;
    for (const snap of rows) {
      const power_score = computePowerScore(snap);
      const { power_delta, power_trend } = powerTrendFromScores(prevPower, power_score);
      const power_note = buildPowerNote(power_trend, power_delta);

      await client.query(
        `UPDATE country_score_snapshots
         SET power_score = $1, power_delta = $2, power_trend = $3, power_note = $4
         WHERE id = $5`,
        [power_score, power_delta, power_trend, power_note, snap.id]
      );

      prevPower = power_score;
    }
  }

  console.log("Backfilled country_score_snapshots power fields.");
}

async function main() {
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    await backfillCountryScores(client);
    await backfillSnapshots(client);
    await client.query("COMMIT");
    console.log("recomputePower complete.");
  } catch (e) {
    await client.query("ROLLBACK");
    console.error(e);
    process.exit(1);
  } finally {
    client.release();
    await pool.end();
  }
}

main();
