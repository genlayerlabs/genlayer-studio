#!/usr/bin/env bash
set -euo pipefail

# Adapted from genlayer-node's taskfiles/genvm/scripts/download_genvm.sh.
# GenVM release assets have had both combined and split layouts, so this script
# deliberately detects the layout from the extracted tree instead of the tag.

error_exit() {
    echo >&2
    echo "ERROR: $1" >&2
    exit 1
}

cleanup() {
    echo "Cleanup: removing downloaded GenVM assets..."
    rm -rf "$DOWNLOAD_DIR"
}

download_artifact() {
    local url=$1
    local asset_name=${url##*/}

    echo "Downloading $asset_name from $url..."
    if ! curl --retry 3 --retry-delay 2 --retry-max-time 60 \
        --fail --show-error --location --continue-at - \
        -H "Accept: application/octet-stream" \
        -o "$DOWNLOAD_DIR/$asset_name" "$url"; then
        error_exit "Download of $asset_name failed. Check that the release and asset exist."
    fi
}

unpack_artifact() {
    local file_path=$1
    local dest_dir=$2

    echo "Unpacking $file_path to $dest_dir..."
    if ! tar -xf "$file_path" -C "$(readlink -f "$dest_dir")"; then
        error_exit "Unpacking $file_path failed."
    fi
}

find_post_install() {
    if [[ -x "$INSTALL_DIR/bin/genvm-post-install" ]]; then
        printf '%s\n' "$INSTALL_DIR/bin/genvm-post-install"
    elif [[ -f "$INSTALL_DIR/bin/post-install.py" ]]; then
        printf '%s\n' "$INSTALL_DIR/bin/post-install.py"
    else
        error_exit "Neither bin/genvm-post-install nor bin/post-install.py exists in the GenVM bundle."
    fi
}

usage() {
    cat <<'EOF'
Usage: download_genvm.sh <os> <arch> <version> [options]

Options:
  --repo <owner/repo>    GitHub repository (default: genlayerlabs/genvm-manager)
  --out-dir <dir>        Extracted GenVM root (default: /genvm)
  --download-dir <dir>   Temporary download directory
  --repo-root <dir>      Studio checkout used to discover the pinned runner
  --runner-pins-file <file>
                         Precomputed unique Studio runner pins, one per line
  --precompile <bool>    Pass true/false to GenVM post-install (default: false)
EOF
}

REPO="genlayerlabs/genvm-manager"
OUT_DIR="/genvm"
DOWNLOAD_DIR="/tmp/genvm-download"
REPO_ROOT=""
RUNNER_PINS_FILE=""
PRECOMPILE=false
POSITIONAL=()

while [[ $# -gt 0 ]]; do
    case "$1" in
        --repo)
            REPO=${2:?"--repo requires a value"}
            shift 2
            ;;
        --out-dir)
            OUT_DIR=${2:?"--out-dir requires a value"}
            shift 2
            ;;
        --download-dir)
            DOWNLOAD_DIR=${2:?"--download-dir requires a value"}
            shift 2
            ;;
        --repo-root)
            REPO_ROOT=${2:?"--repo-root requires a value"}
            shift 2
            ;;
        --runner-pins-file)
            RUNNER_PINS_FILE=${2:?"--runner-pins-file requires a value"}
            shift 2
            ;;
        --precompile)
            PRECOMPILE=${2:?"--precompile requires true or false"}
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --)
            shift
            while [[ $# -gt 0 ]]; do
                POSITIONAL+=("$1")
                shift
            done
            ;;
        -*)
            error_exit "Unknown option: $1"
            ;;
        *)
            POSITIONAL+=("$1")
            shift
            ;;
    esac
done

if [[ ${#POSITIONAL[@]} -ne 3 ]]; then
    usage >&2
    error_exit "Expected <os> <arch> <version>."
fi

OS=${POSITIONAL[0]}
ARCH=${POSITIONAL[1]}
VERSION=${POSITIONAL[2]}
INSTALL_DIR=$OUT_DIR

[[ "$OS" == "linux" || "$OS" == "macos" ]] \
    || error_exit "Invalid OS '$OS'; supported values are linux and macos."
[[ "$ARCH" == "amd64" || "$ARCH" == "arm64" ]] \
    || error_exit "Invalid architecture '$ARCH'; supported values are amd64 and arm64."
[[ "$PRECOMPILE" == "true" || "$PRECOMPILE" == "false" ]] \
    || error_exit "--precompile must be true or false."

if [[ "$VERSION" == *-dev ]]; then
    error_exit "Bare branch pin '$VERSION' is not supported. Pin an exact release tag (vX.Y.Z) or select source mode with '<branch>:<commit>'."
fi
if [[ ! "$VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$ ]]; then
    error_exit "Invalid release '$VERSION'. Expected vX.Y.Z, optionally followed by a prerelease suffix."
fi

echo "Validated GenVM release: OS=$OS ARCH=$ARCH VERSION=$VERSION REPO=$REPO"
mkdir -p "$DOWNLOAD_DIR"
trap cleanup EXIT

RELEASE_BASE="https://github.com/$REPO/releases/download/$VERSION"
ASSET_NAME="genvm-${ARCH}-${OS}.tar.xz"
UNIVERSAL_ASSET_NAME="genvm-universal.tar.xz"

download_artifact "$RELEASE_BASE/$ASSET_NAME"
echo "Preparing install directory: $INSTALL_DIR"
rm -rf "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
unpack_artifact "$DOWNLOAD_DIR/$ASSET_NAME" "$INSTALL_DIR"

# v0.6.0-rc0 is combined. Later releases may omit runners from the platform
# archive and publish them in genvm-universal.tar.xz. Probe the tree, not the tag.
BUNDLE_DESC=$ASSET_NAME
if [[ ! -d "$INSTALL_DIR/runners" ]]; then
    echo "No runners/ in $ASSET_NAME; fetching split-layout asset $UNIVERSAL_ASSET_NAME..."
    download_artifact "$RELEASE_BASE/$UNIVERSAL_ASSET_NAME"
    unpack_artifact "$DOWNLOAD_DIR/$UNIVERSAL_ASSET_NAME" "$INSTALL_DIR"
    BUNDLE_DESC="$ASSET_NAME + $UNIVERSAL_ASSET_NAME"
fi

# rc0 ships data/ read-only, while post-install must create files beneath it.
chmod -R u+w "$INSTALL_DIR"
printf '%s\n' "$VERSION" > "$INSTALL_DIR/version"

EXECUTOR_BINARY=$(find "$INSTALL_DIR/executor" -name genvm -type f 2>/dev/null | head -n 1 || true)
if [[ -z "$EXECUTOR_BINARY" || ! -s "$EXECUTOR_BINARY" ]]; then
    error_exit "No GenVM executor binary was found after extracting $ASSET_NAME."
fi

# Studio embeds py-genlayer hashes in its intelligent contracts. Assert every
# pin is present so an asset/pin mismatch fails during acquisition. Contracts
# targeting an older executor line pin an older runner, which ships inside that
# executor's legacy-runners/ tree rather than the shared runners/ tree.
RUNNER_PINS=""
if [[ -n "$RUNNER_PINS_FILE" ]]; then
    if [[ -f "$RUNNER_PINS_FILE" ]]; then
        RUNNER_PINS=$(sed '/^$/d' "$RUNNER_PINS_FILE" | sort -u)
    fi
elif [[ -n "$REPO_ROOT" ]]; then
    RUNNER_PINS=$(grep -rhoE 'py-genlayer:[a-z0-9]+' \
        "$REPO_ROOT/backend" "$REPO_ROOT/examples" "$REPO_ROOT/tests" 2>/dev/null \
        | sed 's/^py-genlayer://' | sort -u || true)
fi

runner_archive_for_pin() {
    local pin=$1 candidate ext
    # The shared tree moved from .tar to .zip in the v0.3.0-rc7 line; the
    # legacy-runners tree of an older executor still ships .tar.
    for ext in zip tar; do
        candidate="$INSTALL_DIR/runners/py-genlayer/${pin:0:2}/${pin:2}.$ext"
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    for candidate in "$INSTALL_DIR"/executor/*/legacy-runners/py-genlayer/"${pin:0:2}"/"${pin:2}".tar; do
        if [[ -f "$candidate" ]]; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    return 1
}

if [[ -n "$RUNNER_PINS_FILE" || -n "$REPO_ROOT" ]]; then
    RUNNER_PIN_COUNT=$(grep -c . <<<"$RUNNER_PINS" || true)
    if [[ "$RUNNER_PIN_COUNT" -eq 0 ]]; then
        # Studio always carries at least one pin, so zero means the context
        # filtering or the grep itself broke -- exactly what this assertion
        # exists to catch. Failing loudly beats silently skipping the check.
        error_exit "No py-genlayer pin found in Studio sources; the runner assertion cannot run. This usually means the pin inputs were not present in the build context."
    fi
    if [[ ! -d "$INSTALL_DIR/runners" ]]; then
        error_exit "No runners/ directory after extracting $BUNDLE_DESC at $VERSION; check the published release assets."
    fi
    while read -r RUNNER_PIN; do
        [[ -n "$RUNNER_PIN" ]] || continue
        if ! RUNNER_ARCHIVE=$(runner_archive_for_pin "$RUNNER_PIN"); then
            error_exit "Pinned py-genlayer runner is missing: py-genlayer:$RUNNER_PIN (looked in $INSTALL_DIR/runners and $INSTALL_DIR/executor/*/legacy-runners)."
        fi
        echo "Runner pin OK: py-genlayer:$RUNNER_PIN -> ${RUNNER_ARCHIVE#"$INSTALL_DIR"/}"
    done <<<"$RUNNER_PINS"
fi

# The executable was renamed in newer bundles. Both variants accept the same
# post-install flags. The release already contains executor and runner assets,
# so downloading during finalization must remain disabled.
POST_INSTALL=$(find_post_install)
"$POST_INSTALL" \
    --precompile "$PRECOMPILE" \
    --default-download false \
    --error-on-missing-executor false

# This marker is intentionally the final write: it records a completely
# acquired and finalized tree, never a partial extraction.
printf '%s\n' "${OS}-${ARCH}" > "$INSTALL_DIR/.complete"
echo "GenVM download, extraction, and finalization completed successfully."
