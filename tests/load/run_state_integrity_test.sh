#!/bin/bash
#
# State integrity test — detects the lost update bug.
#
# Requires CONSENSUS_WORKERS >= 2 to trigger the race condition.
# With 1 worker, all transactions are serialized and the bug cannot manifest.
#
# Usage:
#   # Start with multiple workers:
#   CONSENSUS_WORKERS=3 docker compose up -d
#
#   # Run the test:
#   ./run_state_integrity_test.sh [API_URL] [NUM_TXS]
#
#   # Example with more transactions for higher detection probability:
#   ./run_state_integrity_test.sh http://localhost:4000/api 50

set -e

API_URL="${1:-http://localhost:4000/api}"
NUM_TXS="${2:-20}"

# Ensure /api suffix
API_URL="${API_URL%/}"
if [[ ! "$API_URL" == */api ]]; then
    API_URL="${API_URL}/api"
fi

echo "=== State Integrity Test ==="
echo "API: $API_URL"
echo "Transactions: $NUM_TXS"
echo ""

# Check if services are running
if ! curl -s -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     "$API_URL" 2>/dev/null | grep -q "OK"; then
    echo "ERROR: RPC server not running at $API_URL"
    echo "Start with: CONSENSUS_WORKERS=3 docker compose up -d"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$SCRIPT_DIR/test_state_integrity.py" "$API_URL" --txs "$NUM_TXS"
