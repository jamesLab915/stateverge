const fs = require("fs");
const path = require("path");
const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function main() {
  const sqlPath = path.join(__dirname, "sql", "migrate_nationmatrix_evolution_v1.sql");
  const sql = fs.readFileSync(sqlPath, "utf8");
  const client = await pool.connect();
  try {
    await client.query(sql);
    console.log("Applied migrate_nationmatrix_evolution_v1.sql");
  } finally {
    client.release();
    await pool.end();
  }
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
