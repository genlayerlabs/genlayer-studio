#!/usr/bin/env bash
set -euo pipefail

if ! docker compose --env-file .env.default --env-file .env -f docker-compose.yml -f docker-compose.ci.yml \
    run --rm --no-deps --entrypoint /entrypoint.sh jsonrpc true; then
    echo "::error::GenVM precompile failed"
    exit 1
fi
