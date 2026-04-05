/**
 * Read-only verification after local → Neon migration.
 * Connects via the same rules as the app (lib/db/pgPool.js): DATABASE_URL first, else PG*.
 *
 * Usage:
 *   node scripts/verifyNeonMigration.js [--json]
 *
 * Checks row counts for core tables; prints "note" lines if a table is missing.
 * Also lists public tables named federal_* when present.
 */
require("dotenv").config({
  path: require("path").join(__dirname, "..", ".env.local"),
  override: true,
});

const { createPgPool } = require("../lib/db/pgPool.js");

const pool = createPgPool();

async function safeCount(sql) {
  try {
    const { rows } = await pool.query(sql);
    return { ok: true, value: Number(rows[0]?.c ?? 0) };
  } catch (e) {
    if (e.code === "42P01" || e.code === "42703") {
      return { ok: false, note: e.message };
    }
    throw e;
  }
}

async function listFederalTables() {
  try {
    const { rows } = await pool.query(
      `SELECT table_name FROM information_schema.tables
       WHERE table_schema = 'public' AND table_name LIKE 'federal_%'
       ORDER BY table_name`
    );
    return rows.map((r) => r.table_name);
  } catch (e) {
    return { note: e.message };
  }
}

async function main() {
  const json = process.argv.includes("--json");
  const lines = [];
  const report = {
    verified_at: new Date().toISOString(),
    connection: process.env.DATABASE_URL
      ? "DATABASE_URL"
      : "PGHOST/PGUSER/PGDATABASE/...",
    checks: {},
  };

  const tables = [
    ["countries", `SELECT COUNT(*)::bigint AS c FROM countries`],
    ["events", `SELECT COUNT(*)::bigint AS c FROM events`],
    [
      "country_score_snapshots",
      `SELECT COUNT(*)::bigint AS c FROM country_score_snapshots`,
    ],
    [
      "event_consequences",
      `SELECT COUNT(*)::bigint AS c FROM event_consequences`,
    ],
  ];

  for (const [name, sql] of tables) {
    const r = await safeCount(sql);
    if (r.ok) {
      report.checks[name] = r.value;
      lines.push(`${name}: ${r.value}`);
    } else {
      report.checks[name] = { note: r.note };
      lines.push(`${name}: note — ${r.note}`);
    }
  }

  const ai = await safeCount(
    `SELECT COUNT(*)::bigint AS c FROM ai_insights_cache`
  );
  if (ai.ok) {
    report.checks.ai_insights_cache = ai.value;
    lines.push(`ai_insights_cache: ${ai.value} rows (table accessible)`);
  } else {
    report.checks.ai_insights_cache = { note: ai.note };
    lines.push(`ai_insights_cache: note — ${ai.note}`);
  }

  const federalList = await listFederalTables();
  if (Array.isArray(federalList)) {
    report.federal_tables_found = federalList;
    if (federalList.length === 0) {
      lines.push("federal_*: note — no public tables matching federal_%");
    } else {
      for (const t of federalList) {
        const c = await safeCount(
          `SELECT COUNT(*)::bigint AS c FROM ${quoteIdent(t)}`
        );
        if (c.ok) {
          report.checks[t] = c.value;
          lines.push(`${t}: ${c.value}`);
        } else {
          report.checks[t] = { note: c.note };
          lines.push(`${t}: note — ${c.note}`);
        }
      }
    }
  } else {
    report.federal_tables_found = federalList;
    lines.push(`federal_*: note — ${federalList.note}`);
  }

  await pool.end();

  if (json) {
    console.log(JSON.stringify(report, null, 2));
  } else {
    console.log("StateVerge — Neon migration verification (read-only)\n");
    console.log(lines.join("\n"));
  }
}

function quoteIdent(ident) {
  return `"${String(ident).replace(/"/g, '""')}"`;
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
