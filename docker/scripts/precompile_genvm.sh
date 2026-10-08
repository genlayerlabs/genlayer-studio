#!/usr/bin/env bash
set -euo pipefail

if ! docker compose -f docker-compose.yml -f docker-compose.ci.yml \
    run --rm --no-deps --entrypoint /entrypoint.sh jsonrpc true; then
    echo "::error::GenVM precompile failed"
    exit 1
fi
