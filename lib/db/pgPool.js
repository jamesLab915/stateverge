/**
 * Single PostgreSQL connection factory for StateVerge (app + Node scripts).
 * Prefer DATABASE_URL (Neon / hosted Postgres). Falls back to PG* discrete env vars.
 *
 * Does not rename logical database objects — only connection configuration.
 */

const { Pool } = require("pg");

/**
 * @returns {import("pg").PoolConfig}
 */
function getPgPoolConfig() {
  const url = process.env.DATABASE_URL && String(process.env.DATABASE_URL).trim();
  if (url) {
    const sslOff =
      process.env.DATABASE_SSL === "false" ||
      process.env.PGSSLMODE === "disable";
    return {
      connectionString: url,
      ssl: sslOff ? false : { rejectUnauthorized: true },
    };
  }

  /** @type {import("pg").PoolConfig} */
  const cfg = {
    user: process.env.PGUSER || process.env.USER || "postgres",
    host: process.env.PGHOST || "localhost",
    database: process.env.PGDATABASE || "nationmatrix",
    port: Number(process.env.PGPORT || 5432),
  };
  if (
    process.env.PGPASSWORD !== undefined &&
    process.env.PGPASSWORD !== ""
  ) {
    cfg.password = process.env.PGPASSWORD;
  }
  return cfg;
}

function createPgPool() {
  return new Pool(getPgPoolConfig());
}

module.exports = {
  createPgPool,
  getPgPoolConfig,
};
