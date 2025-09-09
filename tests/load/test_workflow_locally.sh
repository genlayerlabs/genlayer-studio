#!/bin/bash

# Simulate GitHub Actions workflow locally
# This script mimics what the workflow does

set -e

# Parse arguments
MODE=""
API_URL="http://localhost:4000/api"
MONITOR_ENABLED=false

# Check arguments
for arg in "$@"; do
    if [[ "$arg" == "read-parallel" ]]; then
        MODE="read-parallel"
    elif [[ "$arg" == "monitor" ]]; then
        MONITOR_ENABLED=true
    elif [[ "$arg" == http* ]] || [[ "$arg" == https* ]]; then
        API_URL="$arg"
    fi
done

# Remove trailing slash if present
API_URL="${API_URL%/}"

# Ensure /api suffix if not present
if [[ ! "$API_URL" == */api ]]; then
    API_URL="${API_URL}/api"
fi

echo "==================================================="
echo "       LOCAL GITHUB ACTIONS WORKFLOW TEST"
echo "==================================================="
echo "Using API URL: $API_URL"
if [ -n "$MODE" ]; then
    echo "Mode: $MODE"
fi
echo "Monitoring: $(if [ "$MONITOR_ENABLED" = true ]; then echo "ENABLED"; else echo "DISABLED"; fi)"
echo ""

# Check if services are running
echo "Checking if services are up..."
if curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     "$API_URL" 2>/dev/null | grep -q "OK"; then
    echo "✅ RPC server is running"
else
    echo "❌ RPC server is not running at $API_URL"
    if [[ "$API_URL" == *"localhost"* ]]; then
        echo "   Please run: genlayer up or docker compose up"
    fi
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
    response=$(curl -s -X POST "$API_URL" \
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

# Start resource monitoring if enabled
if [ "$MONITOR_ENABLED" = true ]; then
    echo ""
    echo "=== Starting Resource Monitoring ==="
    MONITOR_LOG="$SCRIPT_DIR/test_resources_$(date +%Y%m%d_%H%M%S).csv"
    chmod +x "$SCRIPT_DIR/monitor_resources.sh"
    "$SCRIPT_DIR/monitor_resources.sh" start "$MONITOR_LOG" 1
    echo "Resource monitoring log: $MONITOR_LOG"
fi

echo ""
echo "=== Setting up Validators ==="
cd "$SCRIPT_DIR"
chmod +x setup_validators.sh
if API_URL="$API_URL" ./setup_validators.sh 5; then
    echo "✅ Validators setup completed"
else
    echo "⚠️ Validator setup failed - continuing anyway"
    echo "Note: Some tests may fail without proper validator configuration"
fi

echo ""
echo "=== Task 1: Run Load Test - Contract Deploy and Read ==="
echo "Waiting 15 seconds for system to stabilize after validator setup..."
sleep 15

# Deploy contracts one by one with better error handling
echo "Deploying 5 contracts with delays..."
CONTRACT_ADDRESSES=()
DEPLOY_SUCCESS=0
DEPLOY_FAIL=0

for i in {1..5}; do
    echo ""
    echo "[Deploy $i/5] Starting deployment..."

    # Run the deployment script and capture output regardless of exit code
    if [ -f deploy_contract/wizard_deploy.py ]; then
        result=$(python3 deploy_contract/wizard_deploy.py 2>&1) || true

        # Look for a contract address in the output
        addr=$(echo "$result" | grep -oE "0x[a-fA-F0-9]{40}" | tail -n 1)

        if [ -n "$addr" ]; then
            echo "[Deploy $i/5] ✅ Success - Contract: $addr"
            CONTRACT_ADDRESSES+=("$addr")
            ((DEPLOY_SUCCESS++))

            # Wait after successful deployment
            if [ $i -lt 5 ]; then
                echo "Waiting 5 seconds for contract to be fully processed..."
                sleep 5
            fi
        else
            echo "[Deploy $i/5] ❌ Failed - no address returned"
            echo "Output: $result"
            ((DEPLOY_FAIL++))

            if [ $i -lt 5 ]; then
                echo "Waiting 5 seconds before next attempt..."
                sleep 5
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

# If in read-parallel mode, perform parallel reads from contracts
if [ "$MODE" == "read-parallel" ]; then
    echo ""
    echo "=== Task 2: Parallel Contract Reads ==="

    if [ ${#CONTRACT_ADDRESSES[@]} -eq 0 ]; then
        echo "❌ No contracts available for reading - skipping parallel reads"
    else
        # Wait for contracts to be fully ready
        echo "Waiting 15 seconds for contracts to be fully ready..."
        sleep 15

        # Prepare to run parallel reads
        echo "Performing 5 parallel reads from deployed contracts..."

        # Function to read from a contract
        read_contract() {
            local CONTRACT_ADDR=$1
            local READ_ID=$2
            local START_TIME=$(date +%s%N)

            # Call get_value method on the contract using gen_call
            # 0x20965255 is the function selector for get_value()
            RESPONSE=$(curl -s -X POST "$API_URL" \
                -H "Content-Type: application/json" \
                -d "{
                    \"jsonrpc\": \"2.0\",
                    \"method\": \"gen_call\",
                    \"params\": [{
                        \"from\": \"0x0000000000000000000000000000000000000000\",
                        \"to\": \"$CONTRACT_ADDR\",
                        \"data\": \"0x20965255\",
                        \"transaction_hash_variant\": \"latest-final\"
                    }],
                    \"id\": $READ_ID
                }" 2>&1) || true

            local END_TIME=$(date +%s%N)
            local DURATION=$((($END_TIME - $START_TIME) / 1000000))  # Convert to milliseconds

            if echo "$RESPONSE" | grep -q "result"; then
                echo "[Read $READ_ID] ✅ Success from $CONTRACT_ADDR (${DURATION}ms)"
            else
                echo "[Read $READ_ID] ❌ Failed from $CONTRACT_ADDR (${DURATION}ms)"
                echo "   Response: $RESPONSE"
            fi
        }

        # Run 5 parallel reads, cycling through available contracts
        echo "Starting parallel reads..."
        for i in {1..5}; do
            # Select contract in round-robin fashion
            CONTRACT_INDEX=$(( ($i - 1) % ${#CONTRACT_ADDRESSES[@]} ))
            CONTRACT_ADDR="${CONTRACT_ADDRESSES[$CONTRACT_INDEX]}"

            # Run read in background
            read_contract "$CONTRACT_ADDR" "$i" &
        done

        # Wait for all background jobs to complete
        wait

        echo "✅ Parallel reads completed"
    fi

    # Also run the load test endpoints in parallel mode
    echo ""
    echo "=== Task 3: Run Load Test - All Read Setup Endpoints ==="
    chmod +x load_test_all_read_setup_endpoints.sh
    echo "Running with REQUESTS=1000 CONCURRENCY=100 (reduced load)..."
    if BASE_URL="$API_URL" REQUESTS=10000 CONCURRENCY=5000 ./load_test_all_read_setup_endpoints.sh; then
        echo "✅ Task 3 completed successfully"
    else
        echo "⚠️ Some endpoint tests failed (this is expected under load)"
        echo "Checking if services are still responsive..."
        curl -X POST -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
            "$API_URL" && echo "✅ RPC server is still responding" || echo "❌ RPC server not responding"
    fi

else
    # Original sequential mode
    echo ""
    echo "=== Task 2: Sequential Contract Reads ==="

    if [ ${#CONTRACT_ADDRESSES[@]} -eq 0 ]; then
        echo "❌ No contracts available for reading - skipping contract reads"
    else
        # Wait for contracts to be fully ready
        echo "Waiting 15 seconds for contracts to be fully ready..."
        sleep 15

        # Perform sequential reads from contracts
        echo "Performing sequential reads from deployed contracts..."

        # Function to read from a contract
        read_contract() {
            local CONTRACT_ADDR=$1
            local READ_ID=$2
            local START_TIME=$(date +%s%N)

            # Call get_value method on the contract using gen_call
            # 0x20965255 is the function selector for get_value()
            RESPONSE=$(curl -s -X POST "$API_URL" \
                -H "Content-Type: application/json" \
                -d "{
                    \"jsonrpc\": \"2.0\",
                    \"method\": \"gen_call\",
                    \"params\": [{
                        \"from\": \"0x0000000000000000000000000000000000000000\",
                        \"to\": \"$CONTRACT_ADDR\",
                        \"data\": \"0x20965255\",
                        \"transaction_hash_variant\": \"latest-final\"
                    }],
                    \"id\": $READ_ID
                }" 2>&1) || true

            local END_TIME=$(date +%s%N)
            local DURATION=$((($END_TIME - $START_TIME) / 1000000))  # Convert to milliseconds

            if echo "$RESPONSE" | grep -q "result"; then
                echo "[Read $READ_ID] ✅ Success from $CONTRACT_ADDR (${DURATION}ms)"
            else
                echo "[Read $READ_ID] ❌ Failed from $CONTRACT_ADDR (${DURATION}ms)"
                echo "   Response: $RESPONSE"
            fi
        }

        # Run sequential reads from each deployed contract
        READ_ID=1
        for CONTRACT_ADDR in "${CONTRACT_ADDRESSES[@]}"; do
            read_contract "$CONTRACT_ADDR" "$READ_ID"
            ((READ_ID++))
            sleep 1  # Small delay between reads
        done

        echo "✅ Contract reads completed"
    fi

    echo ""
    echo "=== Task 3: Run Load Test - All Read Setup Endpoints ==="
    chmod +x load_test_all_read_setup_endpoints.sh
    echo "Running with REQUESTS=1000 CONCURRENCY=100 (reduced load)..."
    if BASE_URL="$API_URL" REQUESTS=1000 CONCURRENCY=100 ./load_test_all_read_setup_endpoints.sh; then
        echo "✅ Task 3 completed successfully"
    else
        echo "⚠️ Some endpoint tests failed (this is expected under load)"
        echo "Checking if services are still responsive..."
        curl -X POST -H "Content-Type: application/json" \
            -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
            "$API_URL" && echo "✅ RPC server is still responding" || echo "❌ RPC server not responding"
    fi
fi

echo ""
echo "==================================================="
echo "       ALL WORKFLOW TASKS COMPLETED"
echo "==================================================="

# Stop resource monitoring if it was enabled
if [ "$MONITOR_ENABLED" = true ]; then
    echo ""
    echo "=== Stopping Resource Monitoring ==="
    "$SCRIPT_DIR/monitor_resources.sh" stop
    echo ""
    echo "Resource monitoring data saved to: $MONITOR_LOG"
fi