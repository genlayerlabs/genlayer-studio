#!/bin/bash
set -euo pipefail

GENVM_RESOLVED_PIN=""
if [[ -s /genvm/version ]]; then
    GENVM_RESOLVED_PIN="$(head -n 1 /genvm/version)"
fi
GENVM_CACHE_VERSION="${GENVM_RESOLVED_PIN:-${GENVM_EXECUTOR_VERSION_NAME:-${GENVM_TAG:-unknown}}}"
CACHE_MARKER="/genvm-cache/pc/.precompiled-${GENVM_CACHE_VERSION}-$(uname -m)"

if [ -f "$CACHE_MARKER" ]; then
    echo "GenVM ${GENVM_CACHE_VERSION} already precompiled for this host, skipping."
else
    echo "Precompiling GenVM ${GENVM_CACHE_VERSION} for host CPU..."
    if [[ -x /genvm/bin/genvm-post-install ]]; then
        /genvm/bin/genvm-post-install --default-steps false --precompile true
    else
        /genvm/bin/post-install.py --default-steps false --precompile true
    fi
    mkdir -p "$(dirname "$CACHE_MARKER")"
    touch "$CACHE_MARKER"
    echo "Precompilation complete."
fi

exec "$@"
