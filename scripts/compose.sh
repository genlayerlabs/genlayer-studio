#!/usr/bin/env bash
# Runs `docker compose` with both configuration layers: tracked `.env.default`,
# then optional `.env` overrides (which may reference tracked keys). Works with
# a missing or partial `.env`, and with explicit files:
#   scripts/compose.sh -f docker-compose.yml -f docker-compose.ci.yml up -d
set -euo pipefail
cd "$(dirname "$0")/.."
files=(--env-file .env.default)
[ -f .env ] && files+=(--env-file .env)
# COMPOSE_BIN lets tests/CI substitute the binary (e.g. docker-compose).
read -r -a bin <<<"${COMPOSE_BIN:-docker compose}"
exec "${bin[@]}" "${files[@]}" "$@"
