#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 <version-tag> <field>" >&2
  echo "Fields: version-branch, channel, push-latest, deployment-event" >&2
  exit 2
}

tag="${1:-}"
field="${2:-}"

if [[ -z "${tag}" || -z "${field}" ]]; then
  usage
fi

if [[ ! "${tag}" =~ ^v[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*)?$ ]]; then
  echo "Invalid release tag: ${tag}" >&2
  exit 1
fi

release_branch="$(sed -E 's/^((v[0-9]+)\.([0-9]+))\..*$/\1/' <<< "${tag}")"

if [[ "${tag}" == *-* ]]; then
  version_branch="${release_branch}-dev"
  channel="preview"
  push_latest="false"
  deployment_event="release-backend-preview"
else
  version_branch="${release_branch}"
  channel="stable"
  push_latest="true"
  deployment_event="release-backend"
fi

case "${field}" in
  version-branch)
    echo "${version_branch}"
    ;;
  channel)
    echo "${channel}"
    ;;
  push-latest)
    echo "${push_latest}"
    ;;
  deployment-event)
    echo "${deployment_event}"
    ;;
  *)
    usage
    ;;
esac
