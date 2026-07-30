#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $0 <branch:commit|commit> <destination>" >&2
    exit 1
fi

GENVM_CLOSURE_REF=$1
GENVM_CLOSURE_DEST=$2

if [[ -z "$GENVM_CLOSURE_DEST" ]]; then
    echo "ERROR: destination directory must not be empty" >&2
    exit 1
fi

# `<branch>:<commit>` and a bare commit both resolve to the commit.
commit="${GENVM_CLOSURE_REF##*:}"
if [[ -z "$commit" ]]; then
    echo "ERROR: empty GenVM ref passed to genvm-runners-closure" >&2
    exit 1
fi

# A caller may disable sandbox fallback separately. Checking the effective
# config here also catches nix.conf not being applied at all.
sandbox=$(nix config show sandbox)
if [[ "$sandbox" != "true" ]]; then
    echo "ERROR: Nix sandbox is '$sandbox', expected 'true'; runner derivations built without a sandbox miss their pinned hashes" >&2
    exit 1
fi

# Nix's own git fetcher clones (and caches) the repo and its submodules; no
# need to `git clone` it ourselves first.
out=$(nix build --no-link --print-out-paths \
    "git+https://github.com/genlayerlabs/genvm-manager?rev=$commit&submodules=1#runners-all")
mkdir -p "$GENVM_CLOSURE_DEST"
# shellcheck disable=SC2046 # store paths never contain whitespace
nix-store --export $(nix-store -qR "$out") \
    | gzip > "$GENVM_CLOSURE_DEST/runners-all.nar.gz"
echo "Exported $out ($(du -h "$GENVM_CLOSURE_DEST/runners-all.nar.gz" | cut -f1))"
