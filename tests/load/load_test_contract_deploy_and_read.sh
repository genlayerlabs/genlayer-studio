#!/bin/bash

# Load test for WizardOfCoin contract
# Deploys multiple contracts in parallel and then reads from them

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Configuration
NUM_DEPLOYMENTS=${1:-10}  # Default to 10 deployments
PARALLEL_JOBS=${2:-5}      # Default to 5 parallel jobs

echo "==================================================="
echo "WizardOfCoin Load Test"
echo "==================================================="
echo "Deployments: $NUM_DEPLOYMENTS"
echo "Parallel jobs: $PARALLEL_JOBS"
echo ""

# Counters
DEPLOY_SUCCESS=0
DEPLOY_FAIL=0
READ_SUCCESS=0
READ_FAIL=0
declare -a CONTRACT_ADDRESSES

# Function to deploy a contract
deploy_contract() {
    local index=$1

    echo "[Deploy $index] Starting deployment..."
    if result=$(python3 "$SCRIPT_DIR/deploy_contract/wizard_deploy.py" 2>&1); then
        local addr=$(echo "$result" | grep -oE '0x[a-fA-F0-9]+' | tail -n 1)
        echo "[Deploy $index] ✅ Success - $addr"
        echo "$addr"
        return 0
    else
        echo "[Deploy $index] ❌ Failed"
        return 1
    fi
}

# Function to read from a contract
read_contract() {
    local addr=$1
    local index=$2

    echo "[Read $index] Reading contract state..."
    # Create temporary file with contract address
    local temp_addr_file="/tmp/wizard_contract_$index.addr"
    echo "$addr" > "$temp_addr_file"

    if result=$(python3 "$SCRIPT_DIR/deploy_contract/wizard_read.py" "$temp_addr_file" 2>&1); then
        rm -f "$temp_addr_file"
        echo "[Read $index] ✅ Success"
        return 0
    else
        rm -f "$temp_addr_file"
        echo "[Read $index] ❌ Failed"
        return 1
    fi
}

# Export functions and variables for parallel execution
export -f deploy_contract
export -f read_contract
export SCRIPT_DIR

echo "=== Phase 1: Parallel Contract Deployments (Burst Mode) ==="
echo "Launching $NUM_DEPLOYMENTS deployments with $PARALLEL_JOBS parallel workers..."
echo ""

# Create temporary directory for deployment results
TEMP_DIR=$(mktemp -d)
trap "rm -rf $TEMP_DIR" EXIT

# Deploy contracts in parallel using xargs for fast burst
seq 1 "$NUM_DEPLOYMENTS" | xargs -P "$PARALLEL_JOBS" -I {} bash -c '
    index=$1
    temp_dir=$2
    script_dir=$3

    echo "[Deploy $index] Starting deployment..."
    if result=$(python3 "$script_dir/deploy_contract/wizard_deploy.py" 2>&1); then
        addr=$(echo "$result" | grep -oE "0x[a-fA-F0-9]+" | tail -n 1)
        if [ -n "$addr" ]; then
            echo "[Deploy $index] ✅ Success - $addr"
            echo "$addr" > "$temp_dir/deploy_$index.addr"
            exit 0
        else
            echo "[Deploy $index] ❌ Failed - no address returned"
            exit 1
        fi
    else
        echo "[Deploy $index] ❌ Failed"
        exit 1
    fi
' _ {} "$TEMP_DIR" "$SCRIPT_DIR"

echo ""
echo "Collecting deployment results..."

# Collect results from parallel deployments
for i in $(seq 1 "$NUM_DEPLOYMENTS"); do
    if [ -f "$TEMP_DIR/deploy_$i.addr" ]; then
        addr=$(cat "$TEMP_DIR/deploy_$i.addr")
        CONTRACT_ADDRESSES+=("$addr")
        ((DEPLOY_SUCCESS++))
    else
        ((DEPLOY_FAIL++))
    fi
done

echo ""
echo "Waiting 20 seconds for contracts to be fully indexed..."
sleep 20

echo ""
echo "=== Phase 2: Parallel Contract Reads (Burst Mode) ==="
echo "Reading from ${#CONTRACT_ADDRESSES[@]} contracts with $PARALLEL_JOBS parallel workers..."
echo ""

# Save addresses to files for parallel reading
index=0
for addr in "${CONTRACT_ADDRESSES[@]}"; do
    ((index++))
    echo "$index" > "$TEMP_DIR/index_$index.txt"
    echo "$addr" > "$TEMP_DIR/addr_$index.txt"
done

# Read contracts in parallel
seq 1 "${#CONTRACT_ADDRESSES[@]}" | xargs -P "$PARALLEL_JOBS" -I {} bash -c '
    index=$1
    script_dir=$2
    temp_dir=$3

    addr=$(cat "$temp_dir/addr_$index.txt")

    echo "[Read $index] Reading contract state..."
    temp_addr_file="$temp_dir/read_$index.addr"
    echo "$addr" > "$temp_addr_file"

    if python3 "$script_dir/deploy_contract/wizard_read.py" "$temp_addr_file" >/dev/null 2>&1; then
        echo "[Read $index] ✅ Success"
        touch "$temp_dir/read_$index.success"
    else
        echo "[Read $index] ❌ Failed"
    fi
    rm -f "$temp_addr_file"
' _ {} "$SCRIPT_DIR" "$TEMP_DIR"

echo ""
echo "Collecting read results..."

# Count read successes
for i in $(seq 1 "${#CONTRACT_ADDRESSES[@]}"); do
    if [ -f "$TEMP_DIR/read_$i.success" ]; then
        ((READ_SUCCESS++))
    else
        ((READ_FAIL++))
    fi
done

echo ""
echo "==================================================="
echo "              LOAD TEST RESULTS                    "
echo "==================================================="
echo ""
printf "%-20s | %-10s | %-10s | %-10s\n" "Operation" "Total" "Success" "Failed"
printf "%-20s-+-%-10s-+-%-10s-+-%-10s\n" "--------------------" "----------" "----------" "----------"
printf "%-20s | %-10d | %-10d | %-10d\n" "Deployments" "$NUM_DEPLOYMENTS" "$DEPLOY_SUCCESS" "$DEPLOY_FAIL"
printf "%-20s | %-10d | %-10d | %-10d\n" "Contract Reads" "${#CONTRACT_ADDRESSES[@]}" "$READ_SUCCESS" "$READ_FAIL"
echo ""
echo "==================================================="
echo ""

# Calculate success rates
if [ "$NUM_DEPLOYMENTS" -gt 0 ]; then
    DEPLOY_RATE=$(echo "scale=2; $DEPLOY_SUCCESS * 100 / $NUM_DEPLOYMENTS" | bc)
else
    DEPLOY_RATE=0
fi

if [ "${#CONTRACT_ADDRESSES[@]}" -gt 0 ]; then
    READ_RATE=$(echo "scale=2; $READ_SUCCESS * 100 / ${#CONTRACT_ADDRESSES[@]}" | bc)
else
    READ_RATE=0
fi

printf "%-20s | %-10s\n" "Success Rates" "Percentage"
printf "%-20s-+-%-10s\n" "--------------------" "----------"
printf "%-20s | %9.2f%%\n" "Deploy Success Rate" "$DEPLOY_RATE"
printf "%-20s | %9.2f%%\n" "Read Success Rate" "$READ_RATE"
echo ""
echo "==================================================="

# Return non-zero if any failures
if [ "$DEPLOY_FAIL" -gt 0 ] || [ "$READ_FAIL" -gt 0 ]; then
    exit 1
fi

exit 0