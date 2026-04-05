# Environment switching — local PostgreSQL vs Neon

This document explains how the StateVerge app and Node scripts choose a database, how to run Postgres locally, how to point everything at Neon, and how to confirm which database is active.

## 1. Connection priority (single rule)

Implemented in `lib/db/pgPool.js`:

1. **`DATABASE_URL`** — if set and non-empty, the pool uses this connection string (TLS enabled for hosted Postgres unless `DATABASE_SSL=false` or `PGSSLMODE=disable`).
2. **Otherwise** — discrete variables:
   - `PGUSER` (default: `USER` env, then `postgres`)
   - `PGHOST` (default: `localhost`)
   - `PGPORT` (default: `5432`)
   - `PGDATABASE` (default: `nationmatrix` — unchanged for backward compatibility)
   - `PGPASSWORD` — optional

Business code does not branch on “Neon vs local”; only the connection layer reads these variables.

## 2. Local PostgreSQL

Typical setup:

- Install PostgreSQL (Homebrew, Postgres.app, Docker, etc.).
- Create a database (often named `nationmatrix` if you follow historical defaults).
- In `.env.local` (not committed), either:

**Option A — discrete vars (no `DATABASE_URL`):**

```bash
PGHOST=localhost
PGPORT=5432
PGUSER=your_os_user
PGDATABASE=nationmatrix
# PGPASSWORD=   # only if your local role uses a password
```

**Option B — single URL:**

```bash
DATABASE_URL=postgresql://USER@localhost:5432/nationmatrix
```

Unset `DATABASE_URL` when you want scripts to use only `PG*` (useful when comparing behaviors).

## 3. Neon

1. Create a project and database in the [Neon console](https://neon.tech).
2. Copy the connection string. It usually includes `sslmode=require` and a host like `ep-....neon.tech`.
3. Set in `.env.local`:

```bash
DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxxx.region.aws.neon.tech/neondb?sslmode=require
```

### Pooled vs unpooled

- **Pooled** (often hostname contains `-pooler` or the Neon UI labels it “Pooled”): best for **short-lived, many concurrent connections** (e.g. serverless / Next.js in production).
- **Direct / unpooled**: best for **long-running migrations, `pg_dump` / `pg_restore`**, and admin tools that hold one connection open.

For `npm run db:import:neon` and heavy restores, prefer a **direct** connection string from Neon’s dashboard to reduce pooler-related timeouts. The app at runtime can still use the pooled URL if your host recommends it.

## 4. `.env.local` variables (cheat sheet)

| Variable | Role |
|----------|------|
| `DATABASE_URL` | Primary connection string when set (Neon or local URL). |
| `DATABASE_SSL=false` or `PGSSLMODE=disable` | Disable SSL (e.g. some local Docker setups). |
| `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` | Used when `DATABASE_URL` is **unset**. |
| `LOCAL_DATABASE_URL` | **Export only** (`db:export:local`): explicit source DB to dump. |
| `NEON_DATABASE_URL` | **Import only** (`db:import:neon`): explicit restore target. |

`POSTGRES_SOURCE_URL`, `TARGET_DATABASE_URL`, and `IMPORT_DATABASE_URL` are optional aliases documented in the migration plan for CI or split environments.

## 5. How to tell which database you are using

1. **Inspect env (without printing secrets):**  
   - If `DATABASE_URL` is set, the app uses it.  
   - Else you are on `PGHOST`/`PGDATABASE` defaults.

2. **Quick read-only check from the repo root:**

   ```bash
   npm run db:verify:neon
   ```

   Counts rows in core tables. Compare counts to a known baseline or to your local export.

3. **Hostname in the URL:**  
   - `localhost` / `127.0.0.1` → local.  
   - `*.neon.tech` → Neon.

4. **Temporary marker (optional):** insert a harmless comment row in a scratch table only on one environment — **not** required for normal operation.

## 6. Switching Neon → local (or back)

- **Use Neon:** set `DATABASE_URL` to the Neon string; restart `next dev` / redeploy.
- **Use local:** remove or comment out `DATABASE_URL` and set `PG*` for your machine, **or** set `DATABASE_URL` to your local `postgresql://...` URL.
- **Import / export** scripts use `LOCAL_DATABASE_URL` / `NEON_DATABASE_URL` when you want to avoid mixing “app URL” with “dump source/target” — see `docs/NEON_MIGRATION_PLAN.md`.

No code changes are required to switch; only environment variables.
