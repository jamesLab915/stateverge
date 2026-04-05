const fs = require("fs");
const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function main() {
  const sqlPath = path.join(__dirname, "sql", "migrate_us_federal_power_tracking_v1.sql");
  const sql = fs.readFileSync(sqlPath, "utf8");
  const client = await pool.connect();
  try {
    await client.query(sql);
    console.log("migrate_us_federal_power_tracking_v1.sql applied.");
    const v2 = fs.readFileSync(
      path.join(__dirname, "sql", "migrate_evolution_engine_v2.sql"),
      "utf8"
    );
    await client.query(v2);
    console.log("migrate_evolution_engine_v2.sql applied.");
    console.log("Next: node scripts/seedUSFederalV1.js && node scripts/recomputeUSFederalScores.js");
  } finally {
    client.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
