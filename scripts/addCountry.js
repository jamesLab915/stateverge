const { createPgPool } = require("../lib/db/pgPool.js");
const { computePowerScore, classifyPower } = require("../lib/powerMath.js");

const pool = createPgPool();

async function addCountry() {
  const [,, code, name, region, capital, population, gdp] = process.argv;

  if (!code || !name || !region || !capital || !population || !gdp) {
    console.log(`
Usage:
node scripts/addCountry.js <code> "<name>" "<region>" "<capital>" <population> <gdp>

Example:
node scripts/addCountry.js in "India" "Asia" "New Delhi" 1428000000 3400000000000
    `);
    process.exit(1);
  }

  const client = await pool.connect();

  try {
    await client.query("BEGIN");

    await client.query(
      `
      INSERT INTO countries (
        code, name, region, capital, population, gdp, summary
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7)
      `,
      [
        code,
        name,
        region,
        capital,
        Number(population),
        Number(gdp),
        `${name} is a strategically important country in ${region} with evolving economic, governance, and long-term development potential.`
      ]
    );

    const row = {
      governance: 70,
      social_order: 72,
      economy: 78,
      human_capital: 74,
      infrastructure: 73,
      innovation: 69,
      openness: 68,
      future_potential: 80,
      overall: 73,
      risk: 45,
      opportunity: 82,
    };
    const power_score = computePowerScore(row);
    const power_classification = classifyPower({ ...row, power_score });

    await client.query(
      `
      INSERT INTO country_scores (
        country_code,
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
        power_score,
        power_classification
      )
      VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
      `,
      [
        code,
        row.governance,
        row.social_order,
        row.economy,
        row.human_capital,
        row.infrastructure,
        row.innovation,
        row.openness,
        row.future_potential,
        row.overall,
        row.risk,
        row.opportunity,
        power_score,
        power_classification,
      ]
    );

    await client.query(
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
      VALUES (
        $1, CURRENT_DATE,
        $2, $3, $4, $5, $6, $7, $8, $9,
        $10, $11, $12, $13,
        $14, $15, $16, $17
      )
      `,
      [
        code,
        row.governance,
        row.social_order,
        row.economy,
        row.human_capital,
        row.infrastructure,
        row.innovation,
        row.openness,
        row.future_potential,
        row.overall,
        row.risk,
        row.opportunity,
        "Initial country import",
        power_score,
        null,
        "stable",
        "Power baseline recorded (no prior snapshot).",
      ]
    );

    await client.query("COMMIT");

    console.log(`Country added successfully: ${name} (${code})`);
  } catch (error) {
    await client.query("ROLLBACK");
    console.error("Failed to add country:", error.message);
  } finally {
    client.release();
    await pool.end();
  }
}

addCountry();
