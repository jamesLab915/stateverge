const { createPgPool } = require("../lib/db/pgPool.js");
const { buildConsequenceRows } = require("../lib/eventConsequenceMapping.js");
const {
  buildCountryConsequenceRowsFromTemplate,
  buildDefaultCountryConsequenceRows,
} = require("../lib/evolution/consequenceTemplates.js");

const pool = createPgPool();

async function main() {
  const { rows: events } = await pool.query(`
    SELECT e.* FROM events e
    WHERE NOT EXISTS (
      SELECT 1 FROM event_consequences ec WHERE ec.event_id = e.id
    )
    ORDER BY e.id ASC
  `);

  let n = 0;
  for (const e of events) {
    let rows = buildCountryConsequenceRowsFromTemplate(e);
    if (rows.length === 0) rows = buildConsequenceRows(e);
    if (rows.length === 0) rows = buildDefaultCountryConsequenceRows(e);
    for (const r of rows) {
      await pool.query(
        `
        INSERT INTO event_consequences (
          event_id, target_country_code, dimension, impact_value,
          time_horizon, confidence, explanation
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        `,
        [
          e.id,
          e.country_code,
          r.dimension,
          r.impact_value,
          r.time_horizon,
          r.confidence,
          r.explanation,
        ]
      );
      n++;
    }
  }

  console.log(`Backfilled ${n} consequence row(s) for ${events.length} event(s).`);
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
