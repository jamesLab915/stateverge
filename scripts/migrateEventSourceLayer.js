/**
 * Apply migrate_event_source_layer_v1.sql (adds events.source_type if missing).
 */
require("dotenv").config({ path: require("path").join(__dirname, "..", ".env.local"), override: true });

const fs = require("fs");
const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function main() {
  const sql = fs.readFileSync(
    path.join(__dirname, "sql", "migrate_event_source_layer_v1.sql"),
    "utf8"
  );
  await pool.query(sql);
  console.log("migrate_event_source_layer_v1 applied.");
  await pool.end();
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
