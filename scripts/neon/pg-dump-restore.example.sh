#!/usr/bin/env bash
# Example: dump local DB and restore to Neon (edit variables).
# StateVerge — not executed automatically; copy and run in your environment.
#
# Usage:
#   chmod +x scripts/neon/pg-dump-restore.example.sh
#   # Edit LOCAL_URL and NEON_URL, then run sections as needed.

set -euo pipefail

# Local Postgres (or source URL):
# LOCAL_URL="postgresql://USER:PASS@localhost:5432/nationmatrix"

# Neon connection string from dashboard:
# NEON_URL="postgresql://USER:PASS@ep-xxx.region.aws.neon.tech/neondb?sslmode=require"

# echo "Dump schema..."
# pg_dump "$LOCAL_URL" --schema-only --no-owner --no-privileges -f ./schema.sql

# echo "Restore schema to Neon..."
# psql "$NEON_URL" -f ./schema.sql

# echo "Dump data (custom)..."
# pg_dump "$LOCAL_URL" -Fc -f ./data.dump

# echo "Restore data..."
# pg_restore --no-owner --no-privileges -d "$NEON_URL" ./data.dump

echo "Prefer npm scripts: npm run db:export:local && npm run db:import:neon -- --file=..."
echo "See docs/NEON_MIGRATION_PLAN.md — edit this file and uncomment steps for manual runs."
