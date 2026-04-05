# StateVerge — brand rename plan (v1)

## Scope

- **Renamed** user-facing and developer-facing branding from **NationMatrix** to **StateVerge**.
- Phrases such as “NationMatrix Evolution Engine” became **StateVerge** or **StateVerge Evolution Engine** where context required a product name.

## What we intentionally did **not** change

| Item | Reason |
|------|--------|
| **PostgreSQL database name** default `nationmatrix` when using discrete `PG*` env vars (`lib/db/pgPool.js`) | Avoids breaking existing local databases and scripts; Neon uses `DATABASE_URL`, which embeds the remote DB name. |
| **SQL migration file names** (e.g. `migrate_nationmatrix_evolution_v1.sql`) | Historical paths referenced by `migrateEvolutionEngine.js`, `migratePowerTracking.js`; renaming files adds churn without schema benefit. |
| **Table and column names** | Out of scope; Evolution Engine and queries depend on them. |
| **Repository directory name** `nationmatrix/` on disk | Can be renamed outside git by the owner; not required for product branding. |

## Files and areas updated (brand layer)

| Area | Examples |
|------|----------|
| Package | `package.json` — `name`: `stateverge`, `description` |
| App shell | `app/layout.tsx` — `metadata.title`, nav brand text |
| Landing / copy | `app/page.tsx`, `app/power/page.tsx` |
| Library headers | e.g. `lib/powerMath.js` |
| DB connection comment | `lib/db/pgPool.js` |
| CLI banners | Scripts that print “StateVerge …” prefixes |
| Documentation | `README.md`, `docs/*.md` (technical titles use “StateVerge Evolution Engine” where appropriate) |
| HTTP | `User-Agent` for source onboarding fetch (if present) |
| Example env | `.env.example` project naming |

## Where “NationMatrix” may still appear

- **This document** and **`docs/NEON_MIGRATION_PLAN.md`** — when explaining what was renamed or default DB name `nationmatrix`.
- **SQL filenames** `migrate_nationmatrix_evolution_v1.sql` — path string in migration scripts.
- **Comments** inside legacy SQL files, if any, only where they refer to historical filenames.

A repo-wide search for `NationMatrix` should only hit intentional documentation or historical filenames — not user-visible UI strings.

## Verification

1. Run `npm run dev` and confirm the browser tab title and header show **StateVerge**.
2. Skim `README.md` and `docs/` for consistent product naming.

## Follow-up (optional)

- Rename git remote / GitHub repo / Vercel project to **StateVerge** when you are ready.
- Publish under a **StateVerge** npm scope only if you publish packages.
