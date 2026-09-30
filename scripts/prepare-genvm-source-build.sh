#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

GENVM_REF=${GENVM_REF:-}
GENVM_SOURCE_MODE=${GENVM_SOURCE_MODE:-}
GENVM_TAG=${GENVM_TAG:-}

if [[ -z "$GENVM_TAG" && -z "$GENVM_REF" ]]; then
    GENVM_REF="$(< "$REPO_ROOT/third_party/genvm/version")"
fi

source_selected=0
if [[ "$GENVM_SOURCE_MODE" == "source" || ( -z "$GENVM_SOURCE_MODE" && "$GENVM_REF" =~ ^.+:[0-9a-fA-F]{7,40}$ ) ]]; then
    source_selected=1
fi

if [[ "$source_selected" != "1" ]]; then
    echo "ERROR: GenVM source mode is not selected; set GENVM_SOURCE_MODE=source and GENVM_REF, or use a <branch>:<commit> GENVM_REF" >&2
    exit 1
fi
if [[ -z "$GENVM_REF" ]]; then
    echo "ERROR: GENVM_SOURCE_MODE=source requires GENVM_REF" >&2
    exit 1
fi

"$SCRIPT_DIR/genvm-runners-closure.sh" \
    "$GENVM_REF" "$REPO_ROOT/.genvm-nix-closure"
