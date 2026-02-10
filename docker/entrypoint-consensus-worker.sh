#!/bin/bash
set -e

CACHE_MARKER="/genvm/executor/${GENVM_TAG}/.cache/pc/.precompiled"

if [ -f "$CACHE_MARKER" ]; then
    echo "GenVM ${GENVM_TAG} already precompiled for this host, skipping."
else
    echo "Precompiling GenVM ${GENVM_TAG} for host CPU..."
    /genvm/bin/post-install.py --default-steps false --precompile true
    touch "$CACHE_MARKER"
    echo "Precompilation complete."
fi

exec python3 -m backend.consensus.run_worker
