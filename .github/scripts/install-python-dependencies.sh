#!/usr/bin/env bash
set -euo pipefail

python -m pip install --only-binary :all: -r requirements.txt
python -m pip install --only-binary :all: -r requirements.test.txt
python -m pip install --only-binary :all: -r backend/requirements.txt
