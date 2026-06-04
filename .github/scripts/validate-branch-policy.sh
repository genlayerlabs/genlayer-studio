#!/usr/bin/env bash
set -euo pipefail

failed=0

error() {
  echo "::error::$*"
  failed=1
}

warning() {
  echo "::warning::$*"
}

is_release_branch() {
  [[ "$1" =~ ^v[0-9]+\.[0-9]+$ ]]
}

is_deprecated_branch() {
  case "$1" in
    v0.122|main|master) return 0 ;;
    *) return 1 ;;
  esac
}

default_branch="${GITHUB_DEFAULT_BRANCH:-}"
event_name="${GITHUB_EVENT_NAME:-local}"
base_ref="${GITHUB_BASE_REF:-}"
head_ref="${GITHUB_HEAD_REF:-}"
ref_name="${GITHUB_REF_NAME:-}"

if [[ "$default_branch" == "main" || "$default_branch" == "master" ]]; then
  error "Repository default branch must be a real version branch, not ${default_branch}."
fi

if [[ -n "$base_ref" ]] && is_deprecated_branch "$base_ref"; then
  error "PRs must not target deprecated or ambiguous branch ${base_ref}."
fi

if [[ -n "$head_ref" && ( "$head_ref" == "main" || "$head_ref" == "master" ) ]]; then
  error "PR head branch must not be ${head_ref}; use a feature branch or the matching version dev branch."
fi

if [[ -n "$base_ref" ]] && is_release_branch "$base_ref"; then
  expected_head="${base_ref}-dev"
  if [[ "${ALLOW_DIRECT_RELEASE_PR:-false}" != "true" && "$head_ref" != "$expected_head" ]]; then
    error "PRs into ${base_ref} must come from ${expected_head}. Merge feature work into ${expected_head}, then promote ${expected_head} -> ${base_ref}."
  fi
fi

if [[ "$event_name" == "push" && ( "$ref_name" == "main" || "$ref_name" == "master" ) ]]; then
  error "Push CI should not run from ambiguous branch ${ref_name}; use version branches."
fi

if [[ -f ".github/workflows/release-from-main.yml" ]]; then
  error ".github/workflows/release-from-main.yml is forbidden. Studio releases must be version-tag driven."
fi

if [[ -f "release.config.js" ]]; then
  error "release.config.js is forbidden in Studio version branches; semantic-release-on-main must not be restored."
fi

if [[ ! -f ".github/workflows/release-from-tag.yml" ]]; then
  error ".github/workflows/release-from-tag.yml is required for tag-driven releases."
else
  if ! grep -Fq 'v*.*.*' .github/workflows/release-from-tag.yml; then
    error "release-from-tag.yml must trigger only from version tags matching v*.*.*."
  fi
  if ! grep -Fq 'refs/remotes/origin/${version_branch}' .github/workflows/release-from-tag.yml || \
     ! grep -Fq 'tag_commit' .github/workflows/release-from-tag.yml || \
     ! grep -Fq 'branch_head' .github/workflows/release-from-tag.yml; then
    error "release-from-tag.yml must verify the tag commit is the current matching version branch head."
  fi
fi

if [[ -f ".github/workflows/manual-docker-release.yml" ]]; then
  if ! grep -Fq 'expected_branch=' .github/workflows/manual-docker-release.yml; then
    error "manual-docker-release.yml must derive and enforce the expected version branch from the tag."
  fi
  if ! grep -Fq './.github/workflows/release-from-tag.yml' .github/workflows/manual-docker-release.yml; then
    error "manual-docker-release.yml must delegate image promotion to release-from-tag.yml."
  fi
else
  warning "manual-docker-release.yml is missing; manual releases should use the same tag validation path."
fi

if [[ "$failed" -ne 0 ]]; then
  exit 1
fi

if [[ -n "$base_ref" ]]; then
  echo "Branch policy ok for PR ${head_ref} -> ${base_ref}."
else
  echo "Branch policy ok for ${event_name} on ${ref_name:-detached ref}."
fi
