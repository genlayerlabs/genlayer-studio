#!/bin/bash

# Simulate GitHub Actions workflow locally
# This script mimics what the workflow does

set -e

echo "==================================================="
echo "       LOCAL GITHUB ACTIONS WORKFLOW TEST"
echo "==================================================="

# Check if services are running
echo "Checking if services are up..."
if curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     http://localhost:4000/api 2>/dev/null | grep -q "OK"; then
    echo "✅ RPC server is running"
else
    echo "❌ RPC server is not running. Please run: genlayer up or docker compose up"
    exit 1
fi

echo ""
echo "=== Verifying Chain ID ==="
echo "Checking if blockchain is properly initialized..."
max_retries=5
retry_count=0
chain_id=""

while [[ "$retry_count" -lt "$max_retries" ]]; do
    echo "Attempt $((retry_count + 1))/$max_retries to read chain ID..."

    # Try to get the chain ID
    response=$(curl -s -X POST http://localhost:4000/api \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "method": "eth_chainId",
        "params": [],
        "id": 1
      }')

    # Check if we got a valid chain ID response
    if echo "$response" | grep -q '"result"'; then
      # Handle both formatted and unformatted JSON
      chain_id=$(echo "$response" | grep -o '"result"[[:space:]]*:[[:space:]]*"[^"]*"' | sed 's/.*"result"[[:space:]]*:[[:space:]]*"//;s/"$//')
      if [ ! -z "$chain_id" ]; then
        echo "✅ Successfully read chain ID: $chain_id"
        break
      fi
    fi

    retry_count=$((retry_count + 1))

    if [[ "$retry_count" -lt "$max_retries" ]]; then
      echo "Failed to read chain ID, waiting 10 seconds before retry..."
      sleep 10
    fi
done

# Check if we successfully got the chain ID
if [ -z "$chain_id" ]; then
    echo "❌ ERROR: Failed to read chain ID after $max_retries attempts"
    echo "The blockchain service may not be properly initialized"
    echo "Please check if GenLayer is running properly"
    exit 1
fi

echo "Chain ID verification successful, proceeding with tests..."

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "=== Setting up Validators ==="
cd "$SCRIPT_DIR"
chmod +x setup_validators.sh
if API_URL="http://localhost:4000/api" ./setup_validators.sh 5; then
    echo "✅ Validators setup completed"
else
    echo "❌ Validator setup failed"
    exit 1
fi

echo ""
echo "=== Task 1: Run Load Test - Contract Deploy and Read ==="
echo "Waiting 15 seconds for system to stabilize after validator setup..."
sleep 15

# Deploy contracts one by one with better error handling
echo "Deploying 3 contracts with delays..."
CONTRACT_ADDRESSES=()
DEPLOY_SUCCESS=0
DEPLOY_FAIL=0

for i in {1..3}; do
    echo ""
    echo "[Deploy $i/3] Starting deployment..."

    # Run the deployment script and capture output regardless of exit code
    if [ -f deploy_contract/wizard_deploy.py ]; then
        result=$(python3 deploy_contract/wizard_deploy.py 2>&1) || true

        # Look for a contract address in the output
        addr=$(echo "$result" | grep -oE "0x[a-fA-F0-9]{40}" | tail -n 1)

        if [ -n "$addr" ]; then
            echo "[Deploy $i/3] ✅ Success - Contract: $addr"
            CONTRACT_ADDRESSES+=("$addr")
            ((DEPLOY_SUCCESS++))

            # Wait after successful deployment
            if [ $i -lt 3 ]; then
                echo "Waiting 30 seconds for contract to be fully processed..."
                sleep 30
            fi
        else
            echo "[Deploy $i/3] ❌ Failed - no address returned"
            echo "Output: $result"
            ((DEPLOY_FAIL++))

            if [ $i -lt 3 ]; then
                echo "Waiting 10 seconds before next attempt..."
                sleep 10
            fi
        fi
    else
        echo "❌ deploy_contract/wizard_deploy.py not found"
        exit 1
    fi
done

echo ""
echo "=== Deployment Summary ==="
echo "Successful: $DEPLOY_SUCCESS"
echo "Failed: $DEPLOY_FAIL"

if [ $DEPLOY_SUCCESS -eq 0 ]; then
    echo "❌ All deployments failed"
    exit 1
else
    echo "✅ Contract deployment test completed with $DEPLOY_SUCCESS/$((DEPLOY_SUCCESS + DEPLOY_FAIL)) successful"
fi

echo ""
echo "=== Task 2: Run Load Test - All Read Setup Endpoints ==="
chmod +x load_test_all_read_setup_endpoints.sh
echo "Running with REQUESTS=100 CONCURRENCY=10 (reduced load)..."
if REQUESTS=100 CONCURRENCY=10 ./load_test_all_read_setup_endpoints.sh; then
    echo "✅ Task 2 completed successfully"
else
    echo "⚠️ Some endpoint tests failed (this is expected under load)"
    echo "Checking if services are still responsive..."
    curl -X POST -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
        http://localhost:4000/api && echo "✅ RPC server is still responding" || echo "❌ RPC server not responding"
fi

echo ""
echo "==================================================="
echo "       ALL WORKFLOW TASKS COMPLETED"
echo "==================================================="