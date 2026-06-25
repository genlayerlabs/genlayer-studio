#!/bin/bash
set -e

GENVM_CACHE_VERSION="${GENVM_EXECUTOR_VERSION_NAME:-${GENVM_TAG:-unknown}}"
CACHE_MARKER="/genvm-cache/pc/.precompiled-${GENVM_CACHE_VERSION}-$(uname -m)"

if [ -f "$CACHE_MARKER" ]; then
    echo "GenVM ${GENVM_CACHE_VERSION} already precompiled for this host, skipping."
else
    echo "Precompiling GenVM ${GENVM_CACHE_VERSION} for host CPU..."
    /genvm/bin/post-install.py --default-steps false --precompile true
    mkdir -p "$(dirname "$CACHE_MARKER")"
    touch "$CACHE_MARKER"
    echo "Precompilation complete."
fi

exec python3 -m backend.consensus.run_worker
