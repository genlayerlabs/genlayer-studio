#!/usr/bin/env bash
set -euo pipefail

python -m pip install --only-binary :all: -r requirements.txt
# The prerelease qualification cut pins the SDK and test harness to immutable
# Git commits until their coordinated package versions are published.
python -m pip install -r requirements.test.txt
python -m pip install --only-binary :all: -r backend/requirements.txt
