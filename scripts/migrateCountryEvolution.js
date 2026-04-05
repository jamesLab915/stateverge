const fs = require("fs");
const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function main() {
  const sqlPath = path.join(__dirname, "sql", "migrate_country_evolution_v1.sql");
  const sql = fs.readFileSync(sqlPath, "utf8");
  await pool.query(sql);
  console.log("migrate_country_evolution_v1.sql applied.");
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
