# StateVerge

Country intelligence and **Evolution Engine** (scores, events, scenarios, US federal layer) built with [Next.js](https://nextjs.org).

## Requirements

- Node.js 20+
- PostgreSQL (local) or [Neon](https://neon.tech) via `DATABASE_URL`

## Environment

Copy `.env.example` to `.env.local` (if present) or set:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | **Preferred.** Full Postgres URL (e.g. Neon; include `?sslmode=require` if needed). |
| `PGHOST`, `PGPORT`, `PGUSER`, `PGPASSWORD`, `PGDATABASE` | Used when `DATABASE_URL` is unset (local dev). Default user: `USER` or `postgres`; default DB name: `nationmatrix` unless overridden. |
| `DATABASE_SSL=false` | Disable SSL for `DATABASE_URL` (rare; local only). |

See `docs/NEON_MIGRATION_PLAN.md`, `docs/ENVIRONMENT_SWITCHING.md`, and `docs/STATEVERGE_RENAME_PLAN.md`.

## Scripts

```bash
npm run dev          # Next.js dev server
npm run build && npm start
npm run pipeline:run # ingest → enrich → score (orchestrated)
```

Database migration helpers (see `docs/NEON_MIGRATION_PLAN.md`):

```bash
npm run db:export:local   # pg_dump → data/db-exports/stateverge-export-*/
npm run db:import:neon    # pg_restore or psql into NEON_DATABASE_URL / DATABASE_URL
npm run db:verify:neon    # read-only row counts on core tables
```

Further operational docs live under `docs/`.

## Learn More

- [Next.js Documentation](https://nextjs.org/docs)
