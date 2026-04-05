/**
 * Export PostgreSQL (typically local) for Neon migration prep.
 * Uses pg_dump (must be on PATH; version should match server when possible).
 *
 * Connection resolution (first match wins):
 *   LOCAL_DATABASE_URL → POSTGRES_SOURCE_URL → DATABASE_URL (warn) → PG* composite URL
 *
 * Usage:
 *   node scripts/dbExportLocal.js [--out=DIR] [--output-dir=DIR]
 *   [--skip-plain-sql]   (omit large plain-SQL full dump; keeps .dump + schema-only)
 *
 * Output directory default: data/db-exports/stateverge-export-<ISO-timestamp>/
 * Files:
 *   stateverge-full.dump      (-Fc, schema + data, for pg_restore)
 *   stateverge-schema-only.sql
 *   stateverge-full.sql       (unless --skip-plain-sql)
 *   export.log
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function parseArgs(argv) {
  let outDir = null;
  let skipPlainSql = false;
  for (const a of argv) {
    if (a.startsWith("--out=")) outDir = a.slice("--out=".length).trim();
    else if (a.startsWith("--output-dir="))
      outDir = a.slice("--output-dir=".length).trim();
    else if (a === "--skip-plain-sql") skipPlainSql = true;
  }
  return { outDir, skipPlainSql };
}

function getExportConnectionString() {
  if (process.env.LOCAL_DATABASE_URL?.trim()) {
    return {
      url: process.env.LOCAL_DATABASE_URL.trim(),
      source: "LOCAL_DATABASE_URL",
    };
  }
  if (process.env.POSTGRES_SOURCE_URL?.trim()) {
    return {
      url: process.env.POSTGRES_SOURCE_URL.trim(),
      source: "POSTGRES_SOURCE_URL",
    };
  }
  if (process.env.DATABASE_URL?.trim()) {
    console.warn(
      "[db:export:local] Warning: using DATABASE_URL. Set LOCAL_DATABASE_URL to point explicitly at the DB you want to dump (e.g. local)."
    );
    return {
      url: process.env.DATABASE_URL.trim(),
      source: "DATABASE_URL",
    };
  }
  const u = process.env.PGUSER || process.env.USER || "postgres";
  const h = process.env.PGHOST || "localhost";
  const p = String(process.env.PGPORT || 5432);
  const d = process.env.PGDATABASE || "nationmatrix";
  const pass = process.env.PGPASSWORD;
  let url;
  if (pass !== undefined && pass !== "") {
    url = `postgresql://${encodeURIComponent(u)}:${encodeURIComponent(pass)}@${h}:${p}/${d}`;
  } else {
    url = `postgresql://${encodeURIComponent(u)}@${h}:${p}/${d}`;
  }
  return { url, source: "PGHOST/PGUSER/PGDATABASE/PGPORT" };
}

function redactUrl(url) {
  try {
    const u = new URL(url);
    if (u.password) u.password = "***";
    return u.toString();
  } catch {
    return "[unparseable connection string]";
  }
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function appendLog(logPath, line) {
  fs.appendFileSync(logPath, `${line}\n`, "utf8");
  process.stdout.write(`${line}\n`);
}

function checkPgDump() {
  const r = spawnSync("pg_dump", ["--version"], {
    encoding: "utf8",
  });
  if (r.error || r.status !== 0) {
    console.error(
      "pg_dump not found or failed. Install PostgreSQL client tools and ensure pg_dump is on PATH."
    );
    process.exit(1);
  }
  return (r.stdout || "").trim();
}

function main() {
  const { outDir: userOut, skipPlainSql } = parseArgs(process.argv.slice(2));
  checkPgDump();

  const root = path.join(__dirname, "..");
  const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const defaultOut = path.join(
    root,
    "data",
    "db-exports",
    `stateverge-export-${ts}`
  );
  const outDir = userOut ? path.resolve(userOut) : defaultOut;
  ensureDir(outDir);
  const logPath = path.join(outDir, "export.log");

  const { url, source } = getExportConnectionString();
  appendLog(logPath, `[${new Date().toISOString()}] stateverge db:export:local`);
  appendLog(logPath, `Connection source: ${source}`);
  appendLog(logPath, `Connection (redacted): ${redactUrl(url)}`);

  const pgDump = (args, label) => {
    appendLog(logPath, "");
    appendLog(logPath, `--- ${label} ---`);
    const r = spawnSync("pg_dump", args, {
      encoding: "utf8",
      maxBuffer: 128 * 1024 * 1024,
    });
    if (r.stdout) appendLog(logPath, r.stdout);
    if (r.stderr) appendLog(logPath, r.stderr);
    if (r.error) {
      appendLog(logPath, `ERROR: ${r.error.message}`);
      return r.status ?? 1;
    }
    if (r.status !== 0) {
      appendLog(logPath, `pg_dump exited with code ${r.status}`);
      return r.status;
    }
    return 0;
  };

  const customDump = path.join(outDir, "stateverge-full.dump");
  const schemaSql = path.join(outDir, "stateverge-schema-only.sql");
  const fullSql = path.join(outDir, "stateverge-full.sql");

  let code = pgDump(
    [url, "-Fc", "-f", customDump, "--no-owner", "--no-privileges"],
    "Custom format (schema + data) → stateverge-full.dump"
  );
  if (code !== 0) process.exit(code);

  code = pgDump(
    [url, "--schema-only", "--no-owner", "--no-privileges", "-f", schemaSql],
    "Schema-only SQL → stateverge-schema-only.sql"
  );
  if (code !== 0) process.exit(code);

  if (!skipPlainSql) {
    code = pgDump(
      [url, "--no-owner", "--no-privileges", "-f", fullSql],
      "Plain SQL full dump → stateverge-full.sql"
    );
    if (code !== 0) process.exit(code);
  } else {
    appendLog(logPath, "");
    appendLog(
      logPath,
      "--- Skipped plain SQL full dump (--skip-plain-sql) ---"
    );
  }

  appendLog(logPath, "");
  appendLog(logPath, `[${new Date().toISOString()}] Export complete.`);
  appendLog(logPath, `Output directory: ${outDir}`);
  appendLog(
    logPath,
    `Artifacts: stateverge-full.dump, stateverge-schema-only.sql${
      skipPlainSql ? "" : ", stateverge-full.sql"
    }`
  );
  console.log(`\nDone. Output: ${outDir}`);
}

main();
