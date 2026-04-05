# Neon PostgreSQL — migration plan (StateVerge)

## 1. Goal

Move the StateVerge app from **local PostgreSQL** to **Neon** (or any hosted Postgres) **without** renaming business tables, changing Evolution Engine logic, or changing UI. Only connection configuration and operational scripts change.

## 2. Connection model (implemented)

All application and script access uses **`lib/db/pgPool.js`**:

1. If **`DATABASE_URL`** is set → connect with `connectionString` and TLS (`ssl: { rejectUnauthorized: true }`), unless `DATABASE_SSL=false` or `PGSSLMODE=disable`.
2. Else → **`PGHOST`**, **`PGPORT`**, **`PGUSER`**, **`PGPASSWORD`** (optional), **`PGDATABASE`** (default `nationmatrix` for local compatibility).

Neon is **not** referenced in business code; only env vars differ.

### Pooled vs unpooled (Neon)

| Use case | Recommendation |
|----------|----------------|
| Next.js app in production / serverless | Neon **pooled** connection string when the platform opens many short connections. |
| `pg_dump`, `pg_restore`, large one-shot imports | Neon **direct (unpooled)** connection string to avoid pooler limits and long-transaction issues. |
| Daily scripts (`pipeline:run`, cron-style jobs) | Usually **pooled** is fine; if you see timeouts, try direct. |

After import, point **`DATABASE_URL`** at the same database using whichever endpoint Neon recommends for your host (often pooled for Vercel).

## 3. Export from local PostgreSQL

**Prerequisites:** `pg_dump` on your PATH (PostgreSQL client tools; version close to server major version is ideal).

**Recommended:** set `LOCAL_DATABASE_URL` so you never accidentally dump the wrong DB when `DATABASE_URL` points at Neon:

```bash
export LOCAL_DATABASE_URL='postgresql://USER@localhost:5432/nationmatrix'
npm run db:export:local
```

Options:

- `--out=DIR` or `--output-dir=DIR` — output directory (default: `data/db-exports/stateverge-export-<timestamp>/`).
- `--skip-plain-sql` — skip the large plain `stateverge-full.sql` (keeps custom `.dump` + `stateverge-schema-only.sql`).

**Artifacts** (in the output directory):

| File | Description |
|------|-------------|
| `stateverge-full.dump` | Custom format (`-Fc`) — **preferred for `pg_restore`**. |
| `stateverge-schema-only.sql` | Schema only. |
| `stateverge-full.sql` | Full plain SQL (optional / skippable). |
| `export.log` | Commands and logs (passwords not printed). |

**Fallback connection order** for export: `LOCAL_DATABASE_URL` → `POSTGRES_SOURCE_URL` → `DATABASE_URL` (warns) → composite `PG*` URL.

### Manual one-liners (equivalent)

Custom format:

```bash
pg_dump "$LOCAL_DATABASE_URL" -Fc -f stateverge-full.dump --no-owner --no-privileges
```

Plain SQL:

```bash
pg_dump "$LOCAL_DATABASE_URL" --no-owner --no-privileges -f stateverge-full.sql
```

## 4. Import into Neon

1. Create a Neon project and database in the Neon console.
2. Prefer a **direct (unpooled)** connection string for restore.
3. Set **`NEON_DATABASE_URL`** (or `TARGET_DATABASE_URL` / `IMPORT_DATABASE_URL`) to that string.

```bash
export NEON_DATABASE_URL='postgresql://USER:PASSWORD@ep-xxxxx.region.aws.neon.tech/neondb?sslmode=require'
npm run db:import:neon -- --file=data/db-exports/stateverge-export-.../stateverge-full.dump
```

Options:

- `--jobs=4` — parallel `pg_restore` workers (custom format only).
- `--clean` — `pg_restore --clean` (drops objects before recreate). **Destructive** on target DB; use only when you intend to replace schema.

**Plain SQL import** (if you exported only `.sql`):

```bash
psql "$NEON_DATABASE_URL" -v ON_ERROR_STOP=1 -f stateverge-full.sql
```

Resolve extension / permission errors in Neon’s dashboard (enable extensions if needed).

**Note:** `pg_restore` may exit with code `1` when emitting warnings; the import script logs output and exits non-zero only on harder failures.

## 5. Application configuration after import

1. Set **`DATABASE_URL`** in Vercel / host / `.env.local` to Neon (often the **pooled** URL for production).
2. Remove reliance on discrete `PG*` in production if the URL is complete.
3. Restart `npm run dev` or redeploy.

## 6. Verification

**Automated (read-only):**

```bash
# Point DATABASE_URL (or PG*) at Neon first
npm run db:verify:neon
npm run db:verify:neon -- --json
```

Checks include: `countries`, `events`, `country_score_snapshots`, `event_consequences`, `ai_insights_cache`, and any `federal_*` tables present in `public`.

**Baseline comparison:**

```bash
npm run baseline:metrics
```

**Build:**

```bash
npm run build
```

### Web routes (smoke)

| Route | Check |
|-------|--------|
| `/countries` | List loads |
| `/countries/us` | Country detail |
| `/compare/us-cn` | Compare (adjust pair if needed) |
| `/power` | Power view |
| `/timeline/us` | Timeline |
| `/scenario/us` | Scenarios |
| `/causal/us` | Causal |
| `/us/power` | US federal power |
| `/us/actors` | Actors |
| `/us/events` | Events |

### Scripts (smoke)

```bash
npm run build
npm run baseline:metrics
npm run test:regression
npm run pipeline:run -- --dry-run --country=us
npm run report:pipeline-health
```

If a command needs network (e.g. ingestion), run in an environment with API keys and outbound access; DB-only commands need only a valid `DATABASE_URL`.

## 7. Failure and rollback

- **Before cutover:** keep the local cluster and the `stateverge-full.dump` file; you can re-import to a fresh Neon branch or database.
- **After cutover:** point `DATABASE_URL` back to local Postgres or to a Neon backup branch.
- **Neon:** use dashboard branches / PITR to restore; see Neon docs for your plan.

Re-import is **not** idempotent without `--clean` or a fresh empty database — plan accordingly.

## 8. Switching back to local development

See **`docs/ENVIRONMENT_SWITCHING.md`**.

## 9. Reference files

| File | Purpose |
|------|---------|
| `lib/db/pgPool.js` | Connection factory |
| `scripts/dbExportLocal.js` | `npm run db:export:local` |
| `scripts/dbImportNeon.js` | `npm run db:import:neon` |
| `scripts/verifyNeonMigration.js` | `npm run db:verify:neon` |
| `docs/STATEVERGE_RENAME_PLAN.md` | Brand vs schema naming |
