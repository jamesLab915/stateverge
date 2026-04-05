const { createPgPool } = require("../lib/db/pgPool.js");
const allCountries = require("../data/allCountries");
const { computePowerScore, classifyPower } = require("../lib/powerMath.js");

const pool = createPgPool();

function getTemplateByRegion(region) {
  const r = String(region).toLowerCase();

  if (r.includes("europe")) {
    return {
      governance: 78,
      social_order: 79,
      economy: 76,
      human_capital: 77,
      infrastructure: 78,
      innovation: 74,
      openness: 75,
      future_potential: 73,
      overall: 76,
      risk: 34,
      opportunity: 74,
    };
  }

  if (r.includes("asia")) {
    return {
      governance: 70,
      social_order: 72,
      economy: 77,
      human_capital: 73,
      infrastructure: 74,
      innovation: 72,
      openness: 69,
      future_potential: 81,
      overall: 74,
      risk: 42,
      opportunity: 82,
    };
  }

  if (r.includes("north america")) {
    return {
      governance: 76,
      social_order: 75,
      economy: 80,
      human_capital: 77,
      infrastructure: 76,
      innovation: 78,
      openness: 75,
      future_potential: 79,
      overall: 77,
      risk: 38,
      opportunity: 83,
    };
  }

  if (r.includes("south america")) {
    return {
      governance: 63,
      social_order: 65,
      economy: 68,
      human_capital: 67,
      infrastructure: 66,
      innovation: 62,
      openness: 61,
      future_potential: 74,
      overall: 66,
      risk: 54,
      opportunity: 72,
    };
  }

  if (r.includes("middle east")) {
    return {
      governance: 68,
      social_order: 72,
      economy: 74,
      human_capital: 68,
      infrastructure: 73,
      innovation: 66,
      openness: 70,
      future_potential: 79,
      overall: 71,
      risk: 48,
      opportunity: 80,
    };
  }

  if (r.includes("africa")) {
    return {
      governance: 58,
      social_order: 60,
      economy: 61,
      human_capital: 59,
      infrastructure: 57,
      innovation: 54,
      openness: 55,
      future_potential: 71,
      overall: 59,
      risk: 60,
      opportunity: 74,
    };
  }

  if (r.includes("oceania")) {
    return {
      governance: 77,
      social_order: 78,
      economy: 74,
      human_capital: 76,
      infrastructure: 75,
      innovation: 72,
      openness: 73,
      future_potential: 75,
      overall: 75,
      risk: 33,
      opportunity: 73,
    };
  }

  return {
    governance: 70,
    social_order: 72,
    economy: 75,
    human_capital: 73,
    infrastructure: 74,
    innovation: 71,
    openness: 70,
    future_potential: 78,
    overall: 73,
    risk: 45,
    opportunity: 80,
  };
}

async function run() {
  const client = await pool.connect();

  try {
    await client.query("BEGIN");

    for (const item of allCountries) {
      const [code, name, region, capital, population, gdp] = item;

      const exists = await client.query(
        `SELECT 1 FROM countries WHERE code = $1`,
        [code]
      );

      if (exists.rows.length > 0) {
        console.log(`Skip existing: ${code}`);
        continue;
      }

      const template = getTemplateByRegion(region);
      const power_score = computePowerScore(template);
      const power_classification = classifyPower({ ...template, power_score });

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
          `${name} is an important country in ${region} with evolving economic, governance, and long-term strategic potential.`,
        ]
      );

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
        VALUES (
          $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
        )
        `,
        [
          code,
          template.governance,
          template.social_order,
          template.economy,
          template.human_capital,
          template.infrastructure,
          template.innovation,
          template.openness,
          template.future_potential,
          template.overall,
          template.risk,
          template.opportunity,
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
          template.governance,
          template.social_order,
          template.economy,
          template.human_capital,
          template.infrastructure,
          template.innovation,
          template.openness,
          template.future_potential,
          template.overall,
          template.risk,
          template.opportunity,
          "Initial bulk import",
          power_score,
          null,
          "stable",
          "Power baseline recorded (no prior snapshot).",
        ]
      );

      console.log(`Added: ${name} (${code})`);
    }

    await client.query("COMMIT");
    console.log("Bulk import complete.");
  } catch (error) {
    await client.query("ROLLBACK");
    console.error("Bulk import failed:", error.message);
  } finally {
    client.release();
    await pool.end();
  }
}

run();
