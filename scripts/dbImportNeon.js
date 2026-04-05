/**
 * Import a dump into Neon (or any Postgres) using DATABASE_URL-style target.
 * Large restores: prefer Neon **direct** (non-pooler) connection string for pg_restore.
 *
 * Target resolution (first match wins):
 *   NEON_DATABASE_URL → TARGET_DATABASE_URL → IMPORT_DATABASE_URL → DATABASE_URL (warn)
 *
 * Usage:
 *   node scripts/dbImportNeon.js --file=path/to/stateverge-full.dump [--jobs=4] [--clean]
 *
 * --file   Required. Custom format (.dump) uses pg_restore; .sql uses psql -f.
 * --clean  Passes --clean to pg_restore (drops objects before recreate; use with care).
 * --jobs   Parallel jobs for pg_restore (custom format only).
 *
 * Plain SQL alternative (manual):
 *   psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f stateverge-full.sql
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

function parseArgs(argv) {
  let file = null;
  let jobs = "4";
  let clean = false;
  for (const a of argv) {
    if (a.startsWith("--file=")) file = a.slice("--file=".length).trim();
    else if (a.startsWith("--jobs=")) jobs = a.slice("--jobs=".length).trim();
    else if (a === "--clean") clean = true;
  }
  return { file, jobs, clean };
}

function getTargetUrl() {
  if (process.env.NEON_DATABASE_URL?.trim()) {
    return {
      url: process.env.NEON_DATABASE_URL.trim(),
      source: "NEON_DATABASE_URL",
    };
  }
  if (process.env.TARGET_DATABASE_URL?.trim()) {
    return {
      url: process.env.TARGET_DATABASE_URL.trim(),
      source: "TARGET_DATABASE_URL",
    };
  }
  if (process.env.IMPORT_DATABASE_URL?.trim()) {
    return {
      url: process.env.IMPORT_DATABASE_URL.trim(),
      source: "IMPORT_DATABASE_URL",
    };
  }
  if (process.env.DATABASE_URL?.trim()) {
    console.warn(
      "[db:import:neon] Using DATABASE_URL as restore target. Set NEON_DATABASE_URL to be explicit."
    );
    return {
      url: process.env.DATABASE_URL.trim(),
      source: "DATABASE_URL",
    };
  }
  console.error(
    "Missing target: set NEON_DATABASE_URL, TARGET_DATABASE_URL, IMPORT_DATABASE_URL, or DATABASE_URL"
  );
  process.exit(1);
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

function appendLog(logPath, line) {
  fs.appendFileSync(logPath, `${line}\n`, "utf8");
  process.stdout.write(`${line}\n`);
}

function main() {
  const { file, jobs, clean } = parseArgs(process.argv.slice(2));
  if (!file) {
    console.error("Usage: node scripts/dbImportNeon.js --file=path/to/dump [--jobs=4] [--clean]");
    process.exit(1);
  }
  const absFile = path.resolve(file);
  if (!fs.existsSync(absFile)) {
    console.error(`File not found: ${absFile}`);
    process.exit(1);
  }

  const { url, source } = getTargetUrl();
  const logDir = path.join(__dirname, "..", "data", "db-exports");
  fs.mkdirSync(logDir, { recursive: true });
  const logPath = path.join(
    logDir,
    `import-neon-${new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19)}.log`
  );

  appendLog(logPath, `[${new Date().toISOString()}] stateverge db:import:neon`);
  appendLog(logPath, `Target source: ${source}`);
  appendLog(logPath, `Target (redacted): ${redactUrl(url)}`);
  appendLog(logPath, `Input file: ${absFile}`);

  const lower = absFile.toLowerCase();
  const isPlainSql = lower.endsWith(".sql");

  if (isPlainSql) {
    const r = spawnSync(
      "psql",
      [url, "-v", "ON_ERROR_STOP=1", "-f", absFile],
      {
        encoding: "utf8",
        maxBuffer: 128 * 1024 * 1024,
      }
    );
    if (r.stdout) appendLog(logPath, r.stdout);
    if (r.stderr) appendLog(logPath, r.stderr);
    if (r.error) {
      appendLog(logPath, `ERROR: ${r.error.message}`);
      process.exit(1);
    }
    if (r.status !== 0) {
      appendLog(logPath, `psql exited with code ${r.status}`);
      process.exit(r.status ?? 1);
    }
  } else {
    const args = [
      "--no-owner",
      "--no-privileges",
      "--verbose",
      "-d",
      url,
      "-j",
      jobs,
    ];
    if (clean) args.push("--clean");
    args.push(absFile);

    const r = spawnSync("pg_restore", args, {
      encoding: "utf8",
      maxBuffer: 128 * 1024 * 1024,
    });
    /** pg_restore often returns 1 for non-fatal warnings */
    if (r.stdout) appendLog(logPath, r.stdout);
    if (r.stderr) appendLog(logPath, r.stderr);
    if (r.error) {
      appendLog(logPath, `ERROR: ${r.error.message}`);
      process.exit(1);
    }
    if (r.status !== 0 && r.status !== 1) {
      appendLog(logPath, `pg_restore exited with code ${r.status}`);
      process.exit(r.status);
    }
    if (r.status === 1) {
      appendLog(
        logPath,
        "Note: pg_restore exited with code 1 (often warnings only). Review output above."
      );
    }
  }

  appendLog(logPath, `[${new Date().toISOString()}] Import finished.`);
  appendLog(logPath, `Log file: ${logPath}`);
  console.log(`\nDone. Log: ${logPath}`);
}

main();
