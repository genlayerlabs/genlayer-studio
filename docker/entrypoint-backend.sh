#!/bin/bash
set -e

CACHE_MARKER="/genvm-cache/pc/.precompiled"

if [ -f "$CACHE_MARKER" ]; then
    echo "GenVM already precompiled for this host, skipping."
else
    echo "Precompiling GenVM for host CPU..."
    /genvm/bin/post-install.py --default-steps false --precompile true
    mkdir -p "$(dirname "$CACHE_MARKER")"
    touch "$CACHE_MARKER"
    echo "Precompilation complete."
fi

exec "$@"
