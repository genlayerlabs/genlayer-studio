#!/bin/bash

# Setup validators for load testing
# This script creates the required number of validators for testing

set -e

# Configuration
NUM_VALIDATORS=${1:-5}  # Default to 5 validators
API_URL=${API_URL:-"http://0.0.0.0:4000/api"}

echo "==================================================="
echo "         VALIDATOR SETUP FOR LOAD TESTING"
echo "==================================================="
echo "Target: $NUM_VALIDATORS validators"
echo "API URL: $API_URL"
echo ""

# Check if RPC server is running
echo "Checking RPC server..."
if ! curl -s -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     "$API_URL" | grep -q "OK"; then
    echo "❌ RPC server is not responding at $API_URL"
    echo "Please ensure GenLayer is running: genlayer up"
    exit 1
fi
echo "✅ RPC server is running"

# Get current validator count
echo ""
echo "Checking existing validators..."
response=$(curl -s -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -d '{
      "jsonrpc": "2.0",
      "method": "sim_countValidators",
      "params": [],
      "id": 1
    }')

# Debug: show raw response if in debug mode
if [ ! -z "$DEBUG" ]; then
    echo "DEBUG: Raw response: $response"
fi

current_count=$(echo "$response" | grep -o '"result":[[:space:]]*[0-9]*' | grep -o '[0-9]*' | tail -1 || echo "0")
# Ensure current_count is a valid number
if [ -z "$current_count" ] || ! [[ "$current_count" =~ ^[0-9]+$ ]]; then
    current_count=0
fi
echo "Current validators: $current_count"

if [ "$current_count" -ge "$NUM_VALIDATORS" ]; then
    echo "✅ Already have $current_count validators (>= $NUM_VALIDATORS required)"
    exit 0
fi

# Calculate how many validators to create
to_create=$((NUM_VALIDATORS - current_count))
echo "Need to create $to_create more validators"

# Create validators
echo ""
echo "Creating validators..."
success=0
failed=0

for i in $(seq 1 $to_create); do
    echo -n "Creating validator $i/$to_create... "

    response=$(curl -s -X POST "$API_URL" \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "method": "sim_createRandomValidator",
        "params": [1],
        "id": '"$i"'
      }')

    if echo "$response" | grep -q '"result"'; then
        echo "✅"
        ((success++))
    else
        echo "❌"
        ((failed++))
        echo "  Error: $response"
    fi

    # Small delay to avoid overwhelming the server
    sleep 0.2
done

echo ""
echo "Creation summary:"
echo "  Successful: $success"
echo "  Failed: $failed"

# Verify final count
echo ""
echo "Verifying final validator count..."
response=$(curl -s -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -d '{
      "jsonrpc": "2.0",
      "method": "sim_countValidators",
      "params": [],
      "id": 100
    }')

final_count=$(echo "$response" | grep -o '"result":[[:space:]]*[0-9]*' | grep -o '[0-9]*' | tail -1 || echo "0")
# Ensure final_count is a valid number
if [ -z "$final_count" ] || ! [[ "$final_count" =~ ^[0-9]+$ ]]; then
    final_count=0
fi
echo "Final validator count: $final_count"

if [ "$final_count" -ge "$NUM_VALIDATORS" ]; then
    echo "✅ Successfully set up $final_count validators"
else
    echo "❌ Failed to create enough validators (have $final_count, need $NUM_VALIDATORS)"
    exit 1
fi

# List validators
echo ""
echo "Listing all validators:"
response=$(curl -s -X POST "$API_URL" \
    -H "Content-Type: application/json" \
    -d '{
      "jsonrpc": "2.0",
      "method": "sim_getAllValidators",
      "params": [],
      "id": 101
    }')

# Pretty print validator info if jq is available
if command -v jq &> /dev/null; then
    echo "$response" | jq -r '.result[] | "  - \(.address) (\(.provider):\(.model)) stake: \(.stake)"' 2>/dev/null || echo "$response"
else
    echo "$response" | grep -o '"address":"[^"]*"' | sed 's/"address":"//g' | sed 's/"//g' | while read addr; do
        echo "  - $addr"
    done
fi

echo ""
echo "==================================================="
echo "✅ Validator setup complete!"
echo "==================================================="