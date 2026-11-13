#!/bin/bash
set -euo pipefail

GENVM_RESOLVED_PIN=""
if [[ -s /genvm/version ]]; then
    GENVM_RESOLVED_PIN="$(head -n 1 /genvm/version)"
fi
GENVM_CACHE_VERSION="${GENVM_RESOLVED_PIN:-${GENVM_EXECUTOR_VERSION_NAME:-${GENVM_TAG:-unknown}}}"
CACHE_MARKER_NAME=".precompiled-${GENVM_CACHE_VERSION}-$(uname -m)"

# One cache_dir per executor version; older Studios' shared pc/ dir is never touched.
mapfile -t GENVM_CACHE_DIRS < <(find /genvm -name genvm.yaml -exec sed -n 's|^cache_dir:[[:space:]]*||p' {} + | sort -u)

needs_precompile=0
if [[ ${#GENVM_CACHE_DIRS[@]} -eq 0 ]]; then
    needs_precompile=1
fi
for cache_dir in "${GENVM_CACHE_DIRS[@]}"; do
    mkdir -p "$cache_dir"
    if [[ ! -f "$cache_dir/$CACHE_MARKER_NAME" ]]; then
        needs_precompile=1
    fi
done

if [[ "$needs_precompile" == "0" ]]; then
    echo "GenVM ${GENVM_CACHE_VERSION} already precompiled for this host, skipping."
else
    echo "Precompiling GenVM ${GENVM_CACHE_VERSION} for host CPU..."
    if [[ -x /genvm/bin/genvm-post-install ]]; then
        /genvm/bin/genvm-post-install --default-steps false --precompile true
    else
        /genvm/bin/post-install.py --default-steps false --precompile true
    fi
    for cache_dir in "${GENVM_CACHE_DIRS[@]}"; do
        touch "$cache_dir/$CACHE_MARKER_NAME"
    done
    echo "Precompilation complete."
fi

exec "$@"
