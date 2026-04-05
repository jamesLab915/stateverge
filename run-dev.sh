#!/usr/bin/env zsh
set -euo pipefail

# Secrets belong in .env.local (gitignored). Never commit API keys in this file.
if [[ -f .env.local ]]; then
  set -a
  source .env.local
  set +a
fi

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "run-dev.sh: set OPENAI_API_KEY in .env.local or your environment." >&2
  exit 1
fi

rm -rf .next
npm run dev
