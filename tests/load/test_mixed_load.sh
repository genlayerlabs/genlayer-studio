#!/bin/bash

# Mixed Load Test Script for GenLayer Studio
# Alternates between contract reads and endpoint tests for realistic load simulation

set -e

# Parse arguments
API_URL="http://localhost:4000/api"
MONITOR_ENABLED=false
REQUESTS=${REQUESTS:-1000}
CONCURRENCY=${CONCURRENCY:-100}

# Check arguments
for arg in "$@"; do
    if [[ "$arg" == "monitor" ]]; then
        MONITOR_ENABLED=true
    elif [[ "$arg" == http* ]] || [[ "$arg" == https* ]]; then
        API_URL="$arg"
    fi
done

# Remove trailing slash and ensure /api suffix
API_URL="${API_URL%/}"
if [[ ! "$API_URL" == */api ]]; then
    API_URL="${API_URL}/api"
fi

echo "==================================================="
echo "       MIXED LOAD TEST - CONTRACT & ENDPOINT"
echo "==================================================="
echo "API URL: $API_URL"
echo "Monitoring: $(if [ "$MONITOR_ENABLED" = true ]; then echo "ENABLED"; else echo "DISABLED"; fi)"
echo "Load per endpoint: $REQUESTS requests / $CONCURRENCY concurrent"
echo ""

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Check if oha is installed
if ! command -v oha &> /dev/null; then
    echo "❌ Error: oha is not installed. Please install it first."
    echo "   Visit: https://github.com/hatoo/oha"
    echo "   Install: cargo install oha"
    exit 1
fi

# Check if services are running
echo "Checking if services are up..."
if curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     "$API_URL" 2>/dev/null | grep -q "OK"; then
    echo "✅ RPC server is running"
else
    echo "❌ RPC server is not running at $API_URL"
    echo "   Please run: genlayer up or docker compose up"
    exit 1
fi

# Start resource monitoring if enabled
if [ "$MONITOR_ENABLED" = true ]; then
    echo ""
    echo "=== Starting Resource Monitoring ==="
    MONITOR_LOG="$SCRIPT_DIR/mixed_load_resources_$(date +%Y%m%d_%H%M%S).csv"
    chmod +x "$SCRIPT_DIR/monitor_resources.sh"
    "$SCRIPT_DIR/monitor_resources.sh" start "$MONITOR_LOG" 1
    echo "Resource monitoring log: $MONITOR_LOG"
fi

echo ""
echo "=== Phase 1: Setting up 5 Validators ==="
cd "$SCRIPT_DIR"
chmod +x setup_validators.sh
if API_URL="$API_URL" ./setup_validators.sh 5; then
    echo "✅ Validators setup completed"
else
    echo "⚠️ Validator setup failed - continuing anyway"
fi

echo ""
echo "=== Phase 2: Deploying 5 Contracts ==="
echo "Waiting 10 seconds for validators to stabilize..."
sleep 10

CONTRACT_ADDRESSES=()
DEPLOY_SUCCESS=0
DEPLOY_FAIL=0

for i in {1..5}; do
    echo ""
    echo "[Deploy $i/5] Starting deployment..."

    if [ -f deploy_contract/wizard_deploy.py ]; then
        result=$(python3 deploy_contract/wizard_deploy.py 2>&1) || true
        addr=$(echo "$result" | grep -oE "0x[a-fA-F0-9]{40}" | tail -n 1)

        if [ -n "$addr" ]; then
            echo "[Deploy $i/5] ✅ Success - Contract: $addr"
            CONTRACT_ADDRESSES+=("$addr")
            ((DEPLOY_SUCCESS++))

            if [ $i -lt 5 ]; then
                echo "Waiting 3 seconds before next deployment..."
                sleep 3
            fi
        else
            echo "[Deploy $i/5] ❌ Failed - no address returned"
            ((DEPLOY_FAIL++))

            if [ $i -lt 5 ]; then
                echo "Waiting 3 seconds before next attempt..."
                sleep 3
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
    echo "❌ All deployments failed - cannot proceed with mixed load test"
    exit 1
fi

echo ""
echo "Waiting 20 seconds for contracts to be fully ready..."
sleep 20

echo ""
echo "=== Phase 3: Mixed Load Testing ==="
echo "Alternating between contract reads and endpoint tests..."
echo ""

# Test addresses for endpoint tests
TEST_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
TEST_BLOCK_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"
TEST_TX_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"

# Function to read from a contract (for background execution)
read_contract_async() {
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
        echo "[Contract Read $READ_ID] ✅ Success from $CONTRACT_ADDR (${DURATION}ms)"
    else
        echo "[Contract Read $READ_ID] ❌ Failed from $CONTRACT_ADDR (${DURATION}ms)"
    fi
}

# Function to run endpoint test in background with full load
run_endpoint_async() {
    local METHOD=$1
    shift
    local PARAMS="$*"

    echo "[Endpoint Test] Starting $METHOD with $REQUESTS requests / $CONCURRENCY concurrent..."

    # Build params for the endpoint
    local PARAM_STRING=""
    for p in $PARAMS; do
        PARAM_STRING="$PARAM_STRING $p"
    done

    # Build the JSON-RPC request
    local REQUEST_JSON
    if [ -z "$PARAMS" ]; then
        REQUEST_JSON="{\"jsonrpc\":\"2.0\",\"method\":\"$METHOD\",\"params\":[],\"id\":1}"
    else
        # Build params array
        local PARAMS_JSON="["
        local FIRST=true
        for p in $PARAMS; do
            if [ "$FIRST" = true ]; then
                FIRST=false
            else
                PARAMS_JSON+=","
            fi
            # Check if param starts with 0x or is "latest", "true", "false"
            if [[ "$p" =~ ^0x ]] || [ "$p" = "latest" ] || [ "$p" = "true" ] || [ "$p" = "false" ]; then
                PARAMS_JSON+="\"$p\""
            else
                PARAMS_JSON+="\"$p\""
            fi
        done
        PARAMS_JSON+="]"
        REQUEST_JSON="{\"jsonrpc\":\"2.0\",\"method\":\"$METHOD\",\"params\":$PARAMS_JSON,\"id\":1}"
    fi

    # Run OHA load test directly
    oha -n $REQUESTS -c $CONCURRENCY -m POST \
        -d "$REQUEST_JSON" \
        -H "Content-Type: application/json" \
        -t 60s \
        --no-tui "$API_URL" >/dev/null 2>&1 && \
        echo "[Endpoint Test] ✅ $METHOD completed ($REQUESTS reqs / $CONCURRENCY concurrent)" || \
        echo "[Endpoint Test] ❌ $METHOD failed"
}

# Function to run endpoint test
run_endpoint_test() {
    local METHOD=$1
    shift
    local PARAMS="$*"

    echo "[Endpoint Test] Testing $METHOD with load ($REQUESTS requests / $CONCURRENCY concurrent)"

    # Run the simplified endpoint test script
    if [ -f "$SCRIPT_DIR/load_test_readonly_endpoints.sh" ]; then
        BASE_URL="$API_URL" REQUESTS=$REQUESTS CONCURRENCY=$CONCURRENCY \
            "$SCRIPT_DIR/load_test_readonly_endpoints.sh" "$METHOD" $PARAMS
    else
        echo "  ⚠️ load_test_readonly_endpoints.sh not found, using test_endpoint.sh"
        chmod +x "$SCRIPT_DIR/test_endpoint.sh"
        REQUESTS=$REQUESTS CONCURRENCY=$CONCURRENCY \
            "$SCRIPT_DIR/test_endpoint.sh" "$METHOD" $PARAMS >/dev/null 2>&1 && \
            echo "  ✅ Endpoint test completed" || \
            echo "  ❌ Endpoint test failed"
    fi

    echo ""
}

# Counter for contract reads
CONTRACT_READ_ID=1

# Define all endpoints to test
echo "=== Starting Mixed Load Pattern ==="
echo "Pattern: 2 parallel contract reads + 2 parallel endpoint tests"
echo ""

# Test endpoints without parameters
echo "--- Testing: ping + eth_blockNumber (parallel) ---"
# Launch 2 contract reads in parallel
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "ping" &
run_endpoint_async "eth_blockNumber" &

# Wait for all parallel operations to complete
wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: eth_gasPrice + eth_chainId (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "eth_gasPrice" &
run_endpoint_async "eth_chainId" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: net_version + sim_getFinalityWindowTime (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "net_version" &
run_endpoint_async "sim_getFinalityWindowTime" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: sim_countValidators + sim_getAllValidators (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "sim_countValidators" &
run_endpoint_async "sim_getAllValidators" &

wait
echo ""
sleep 5  # Wait before next test group

# Test endpoints with parameters
echo "--- Testing: eth_getBalance + eth_getTransactionCount (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "eth_getBalance" "$TEST_ADDRESS" "latest" &
run_endpoint_async "eth_getTransactionCount" "$TEST_ADDRESS" "latest" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: eth_getBlockByNumber + eth_getBlockByHash (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "eth_getBlockByNumber" "0x1" "true" &
run_endpoint_async "eth_getBlockByHash" "$TEST_BLOCK_HASH" "true" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: eth_getTransactionByHash + eth_getTransactionReceipt (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "eth_getTransactionByHash" "$TEST_TX_HASH" &
run_endpoint_async "eth_getTransactionReceipt" "$TEST_TX_HASH" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: sim_getValidator + sim_getTransactionsForAddress (parallel) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 2 endpoint tests in parallel
run_endpoint_async "sim_getValidator" "$TEST_ADDRESS" &
run_endpoint_async "sim_getTransactionsForAddress" "$TEST_ADDRESS" &

wait
echo ""
sleep 5  # Wait before next test group

echo "--- Testing: sim_getConsensusContract (with 2 contract reads) ---"
# Launch 2 contract reads with delay
CONTRACT_INDEX1=$(( ($CONTRACT_READ_ID - 1) % ${#CONTRACT_ADDRESSES[@]} ))
CONTRACT_INDEX2=$(( $CONTRACT_READ_ID % ${#CONTRACT_ADDRESSES[@]} ))
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX1]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))
sleep 5  # Wait between contract reads
read_contract_async "${CONTRACT_ADDRESSES[$CONTRACT_INDEX2]}" "$CONTRACT_READ_ID" &
((CONTRACT_READ_ID++))

# Launch 1 endpoint test (odd number, so we just do one)
run_endpoint_async "sim_getConsensusContract" &

wait
echo ""

echo ""
echo "==================================================="
echo "       MIXED LOAD TEST COMPLETED"
echo "==================================================="
echo "Total contract reads: $((CONTRACT_READ_ID - 1))"
echo "Total endpoint tests: 17"
echo "Pattern: 2 parallel contract calls + 2 parallel endpoint calls"

# Stop resource monitoring if it was enabled
if [ "$MONITOR_ENABLED" = true ]; then
    echo ""
    echo "=== Stopping Resource Monitoring ==="
    "$SCRIPT_DIR/monitor_resources.sh" stop
    echo ""
    echo "Resource monitoring data saved to: $MONITOR_LOG"
fi

echo ""
echo "✅ Mixed load test completed successfully!"