const { createPgPool } = require("../lib/db/pgPool.js");
const {
  computePowerScore,
  classifyPower,
  powerTrendFromScores,
  buildPowerNote,
} = require("../lib/powerMath.js");

const pool = createPgPool();

async function saveSnapshot() {
  try {
    const { rows: scores } = await pool.query(`
      SELECT * FROM country_scores ORDER BY country_code
    `);

    const inserted = [];

    for (const s of scores) {
      const power_score = computePowerScore(s);
      const power_classification = classifyPower({ ...s, power_score });

      await pool.query(
        `
        UPDATE country_scores
        SET power_score = $1, power_classification = $2
        WHERE country_code = $3
        `,
        [power_score, power_classification, s.country_code]
      );

      const prevSnap = await pool.query(
        `
        SELECT power_score FROM country_score_snapshots
        WHERE country_code = $1
        ORDER BY id DESC
        LIMIT 1
        `,
        [s.country_code]
      );

      const prevRow = prevSnap.rows[0];
      const prevPower = prevRow
        ? prevRow.power_score != null && Number.isFinite(Number(prevRow.power_score))
          ? Number(prevRow.power_score)
          : computePowerScore(prevRow)
        : null;

      const { power_delta, power_trend } = powerTrendFromScores(prevPower, power_score);
      const power_note = buildPowerNote(power_trend, power_delta);

      const ins = await pool.query(
        `
        INSERT INTO country_score_snapshots (
          country_code,
          snapshot_date,
          governance,
          social_order,
          economy,
          human_capital,
          infrastructure,
          innovation,
          openness,
          future_potential,
          overall,
          risk,
          opportunity,
          note,
          power_score,
          power_delta,
          power_trend,
          power_note
        )
        SELECT
          country_code,
          CURRENT_DATE,
          governance,
          social_order,
          economy,
          human_capital,
          infrastructure,
          innovation,
          openness,
          future_potential,
          overall,
          risk,
          opportunity,
          'Snapshot from script',
          $2::double precision,
          $3::double precision,
          $4,
          $5
        FROM country_scores
        WHERE country_code = $1
        RETURNING id, country_code, snapshot_date, power_score, power_trend
        `,
        [s.country_code, power_score, power_delta, power_trend, power_note]
      );

      inserted.push(ins.rows[0]);
    }

    console.log("Snapshot saved:");
    console.table(inserted);
  } catch (error) {
    console.error("Failed to save snapshot:", error);
  } finally {
    await pool.end();
  }
}

saveSnapshot();
