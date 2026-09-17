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
if [[ ! "$GENVM_CLOSURE_REF" =~ ^([^:]+:)?[0-9a-fA-F]{7,40}$ ]]; then
    echo "ERROR: GenVM ref must be '<branch>:<commit>' or a bare commit SHA, got '$GENVM_CLOSURE_REF'" >&2
    exit 1
fi
commit="${GENVM_CLOSURE_REF##*:}"

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
dest="$GENVM_CLOSURE_DEST/runners-all-$commit.nar.gz"
tmp="$dest.tmp"
# shellcheck disable=SC2046 # store paths never contain whitespace
nix-store --export $(nix-store -qR "$out") \
    | gzip > "$tmp"
# Export via a temp file + rename so a Ctrl+C mid-write can't leave a
# truncated .nar.gz that a later build silently imports as if it were valid.
mv "$tmp" "$dest"
echo "Exported $out ($(du -h "$dest" | cut -f1))"
