#!/bin/bash

# Load testing script using GenLayer CLI for contract deployment
# This script performs multiple deployments to test system performance

# Base URL (can be overridden via environment variable)
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from $SCRIPT_DIR/.env"
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Test parameters
NUM_DEPLOYMENTS=${NUM_DEPLOYMENTS:-10}
PARALLEL_DEPLOYMENTS=${PARALLEL_DEPLOYMENTS:-1}
CONTRACT_PATH="$SCRIPT_DIR/../../examples/contracts/wizard_of_coin.py"
RESULTS_DIR="$SCRIPT_DIR/cli_load_test_results"

echo "=========================================="
echo "GenLayer CLI Load Testing Script"
echo "=========================================="
echo ""
echo "Configuration:"
echo "- Base URL: $BASE_URL"
echo "- Number of deployments: $NUM_DEPLOYMENTS"
echo "- Parallel deployments: $PARALLEL_DEPLOYMENTS"
echo "- Contract: $CONTRACT_PATH"
echo ""

# Check if genlayer CLI is available
if ! command -v genlayer &> /dev/null; then
    echo "❌ Error: GenLayer CLI is not installed"
    exit 1
fi

echo "✅ GenLayer CLI version: $(genlayer --version)"
echo ""

# Check if contract exists
if [ ! -f "$CONTRACT_PATH" ]; then
    echo "❌ Error: Contract file not found at $CONTRACT_PATH"
    exit 1
fi

# Create results directory
mkdir -p "$RESULTS_DIR"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
RESULTS_FILE="$RESULTS_DIR/load_test_${TIMESTAMP}.txt"
SUMMARY_FILE="$RESULTS_DIR/summary_${TIMESTAMP}.txt"

echo "Results will be saved to:"
echo "- $RESULTS_FILE"
echo "- $SUMMARY_FILE"
echo ""

# Function to deploy a contract and measure time
deploy_contract() {
    local index=$1
    local start_time=$(date +%s%N)
    
    # Deploy the contract
    if command -v expect &> /dev/null && [ -n "$GENLAYER_PASSWORD" ]; then
        local output=$(expect -c "
            set timeout 30
            log_user 0
            spawn genlayer deploy --contract \"$CONTRACT_PATH\" --args true --rpc \"$BASE_URL\"
            expect \"Enter password to decrypt keystore:\"
            send \"$GENLAYER_PASSWORD\r\"
            expect eof
            catch wait result
            puts [read [open /dev/stdin]]
        " 2>&1)
        local exit_code=$?
    else
        local output=$(genlayer deploy --contract "$CONTRACT_PATH" --args true --rpc "$BASE_URL" 2>&1)
        local exit_code=$?
    fi
    
    local end_time=$(date +%s%N)
    local duration_ns=$((end_time - start_time))
    local duration_ms=$((duration_ns / 1000000))
    
    # Extract contract address if successful
    local contract_address=$(echo "$output" | grep -oE '0x[a-fA-F0-9]{40}' | head -1)
    
    # Log result
    if [ $exit_code -eq 0 ] && [ -n "$contract_address" ]; then
        echo "[$(date +%H:%M:%S)] Deployment $index: SUCCESS - ${duration_ms}ms - $contract_address"
        echo "SUCCESS,$index,$duration_ms,$contract_address" >> "$RESULTS_FILE"
        return 0
    else
        echo "[$(date +%H:%M:%S)] Deployment $index: FAILED - ${duration_ms}ms"
        echo "FAILED,$index,$duration_ms," >> "$RESULTS_FILE"
        return 1
    fi
}

# Function to run deployments in parallel
run_parallel_deployments() {
    local batch_start=$1
    local batch_size=$2
    
    echo ""
    echo "Starting batch: deployments $batch_start to $((batch_start + batch_size - 1))"
    
    # Start deployments in background
    for ((i=0; i<batch_size; i++)); do
        local index=$((batch_start + i))
        if [ $index -le $NUM_DEPLOYMENTS ]; then
            deploy_contract $index &
        fi
    done
    
    # Wait for all background jobs to complete
    wait
}

echo "=========================================="
echo "Starting Load Test"
echo "=========================================="

# Write CSV header
echo "Status,Index,Duration_ms,Contract_Address" > "$RESULTS_FILE"

TOTAL_START=$(date +%s%N)
SUCCESS_COUNT=0
FAIL_COUNT=0

# Run deployments
if [ $PARALLEL_DEPLOYMENTS -eq 1 ]; then
    echo ""
    echo "Running sequential deployments..."
    echo ""
    
    for ((i=1; i<=NUM_DEPLOYMENTS; i++)); do
        deploy_contract $i
        if [ $? -eq 0 ]; then
            ((SUCCESS_COUNT++))
        else
            ((FAIL_COUNT++))
        fi
    done
else
    echo ""
    echo "Running parallel deployments (batch size: $PARALLEL_DEPLOYMENTS)..."
    echo ""
    
    # Process in batches
    for ((batch_start=1; batch_start<=NUM_DEPLOYMENTS; batch_start+=PARALLEL_DEPLOYMENTS)); do
        run_parallel_deployments $batch_start $PARALLEL_DEPLOYMENTS
    done
    
    # Count results
    SUCCESS_COUNT=$(grep -c "^SUCCESS" "$RESULTS_FILE")
    FAIL_COUNT=$(grep -c "^FAILED" "$RESULTS_FILE")
fi

TOTAL_END=$(date +%s%N)
TOTAL_DURATION_NS=$((TOTAL_END - TOTAL_START))
TOTAL_DURATION_S=$((TOTAL_DURATION_NS / 1000000000))

echo ""
echo "=========================================="
echo "Load Test Results"
echo "=========================================="
echo ""

# Calculate statistics
if [ -f "$RESULTS_FILE" ] && [ $SUCCESS_COUNT -gt 0 ]; then
    # Extract successful deployment times
    grep "^SUCCESS" "$RESULTS_FILE" | cut -d',' -f3 > "$RESULTS_DIR/temp_times.txt"
    
    # Calculate average, min, max using awk
    STATS=$(awk '{
        sum += $1;
        if (NR == 1 || $1 < min) min = $1;
        if (NR == 1 || $1 > max) max = $1;
        count++;
    }
    END {
        if (count > 0) {
            avg = sum / count;
            printf "%.2f,%.0f,%.0f", avg, min, max;
        }
    }' "$RESULTS_DIR/temp_times.txt")
    
    IFS=',' read -r AVG_TIME MIN_TIME MAX_TIME <<< "$STATS"
    rm -f "$RESULTS_DIR/temp_times.txt"
fi

# Display and save summary
{
    echo "Test Configuration:"
    echo "- Total deployments: $NUM_DEPLOYMENTS"
    echo "- Parallel deployments: $PARALLEL_DEPLOYMENTS"
    echo "- RPC URL: $BASE_URL"
    echo ""
    echo "Results:"
    echo "- Successful deployments: $SUCCESS_COUNT"
    echo "- Failed deployments: $FAIL_COUNT"
    echo "- Success rate: $(echo "scale=2; $SUCCESS_COUNT * 100 / $NUM_DEPLOYMENTS" | bc)%"
    echo "- Total duration: ${TOTAL_DURATION_S} seconds"
    echo ""
    if [ $SUCCESS_COUNT -gt 0 ]; then
        echo "Timing Statistics (successful deployments):"
        echo "- Average time: ${AVG_TIME}ms"
        echo "- Minimum time: ${MIN_TIME}ms"
        echo "- Maximum time: ${MAX_TIME}ms"
        echo "- Throughput: $(echo "scale=2; $SUCCESS_COUNT / $TOTAL_DURATION_S" | bc) deployments/second"
    fi
} | tee "$SUMMARY_FILE"

echo ""
echo "=========================================="
echo "Load test completed!"
echo "=========================================="
echo ""
echo "Detailed results saved to: $RESULTS_FILE"
echo "Summary saved to: $SUMMARY_FILE"
echo ""

# Show sample of deployed contracts
if [ $SUCCESS_COUNT -gt 0 ]; then
    echo "Sample of deployed contracts:"
    grep "^SUCCESS" "$RESULTS_FILE" | head -5 | while IFS=',' read -r status index duration address; do
        echo "  - $address (${duration}ms)"
    done
    
    if [ $SUCCESS_COUNT -gt 5 ]; then
        echo "  ... and $((SUCCESS_COUNT - 5)) more"
    fi
fi