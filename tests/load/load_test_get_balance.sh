#!/bin/bash

# Load Test Script for eth_getBalance endpoint
# Focused testing of the read-only balance check operation

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from $SCRIPT_DIR/.env"
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Configuration
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
RESULTS_FILE="$SCRIPT_DIR/load_test_get_balance_results_${TIMESTAMP}.txt"

# Test parameters (can be overridden via environment)
REQUESTS=${REQUESTS:-100}
CONCURRENCY=${CONCURRENCY:-10}

# Test address (Hardhat default account #2)
TEST_ADDRESS=${TEST_ADDRESS:-"0x70997970C51812dc3A010C7d01b50e0d17dc79C8"}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    shift
    echo -e "${color}$@${NC}"
}

# Function to check if test passed
check_test_result() {
    local TEST_OUTPUT="$1"

    # Check for connection errors
    if echo "$TEST_OUTPUT" | grep -q "connection closed before message completed"; then
        return 1
    fi

    # Check for other error patterns
    if echo "$TEST_OUTPUT" | grep -q "Error distribution:"; then
        # If there are errors listed, check if count is non-zero
        local ERROR_COUNT=$(echo "$TEST_OUTPUT" | grep -oP '\[\d+\]' | grep -oP '\d+' | head -1)
        if [ -n "$ERROR_COUNT" ] && [ "$ERROR_COUNT" -gt 0 ]; then
            return 1
        fi
    fi

    # Check if we got successful responses
    if echo "$TEST_OUTPUT" | grep -q "\[200\]"; then
        return 0
    fi

    # Default to failure if no success indicators found
    return 1
}

# Function to run load test with specific parameters
run_load_test() {
    local requests=$1
    local concurrency=$2

    echo ""
    echo "Running load test: $requests requests with $concurrency concurrent connections"
    echo "----------------------------------------"

    # Build the JSON-RPC request
    REQUEST_JSON=$(cat <<EOF
{
    "jsonrpc": "2.0",
    "method": "eth_getBalance",
    "params": ["$TEST_ADDRESS", "latest"],
    "id": 1
}
EOF
)

    # Run the load test using oha
    TEST_OUTPUT=$(oha -n $requests -c $concurrency -m POST \
        -d "$REQUEST_JSON" \
        -H "Content-Type: application/json" \
        --no-tui "$BASE_URL" 2>&1)

    echo "$TEST_OUTPUT"

    # Check if test passed
    if check_test_result "$TEST_OUTPUT"; then
        print_color "$GREEN" "✅ Test PASSED"
        return 0
    else
        print_color "$RED" "❌ Test FAILED"
        return 1
    fi
}

# Function to run progressive load tests
run_progressive_tests() {
    local configs=("10:1" "50:5" "100:10" "200:20" "500:50" "1000:100")
    local passed=0
    local failed=0

    echo "Progressive Load Test Suite for eth_getBalance"
    echo "==============================================="

    for config in "${configs[@]}"; do
        IFS=':' read -r req con <<< "$config"

        echo ""
        echo "Test Configuration: $req requests / $con concurrent"
        echo "-----------------------------------------------------"

        if run_load_test "$req" "$con"; then
            ((passed++))
        else
            ((failed++))
            print_color "$YELLOW" "System limit potentially reached at $req requests / $con concurrent"
            break
        fi

        # Small delay between tests
        sleep 2
    done

    echo ""
    echo "Summary"
    echo "======="
    echo "Tests Passed: $passed"
    echo "Tests Failed: $failed"

    if [ $failed -eq 0 ]; then
        print_color "$GREEN" "All tests completed successfully!"
    else
        print_color "$YELLOW" "Performance limit detected. Consider scaling or optimization."
    fi
}

# Main script
main() {
    # Header
    echo "=====================================================" | tee "$RESULTS_FILE"
    echo "eth_getBalance Load Test" | tee -a "$RESULTS_FILE"
    echo "=====================================================" | tee -a "$RESULTS_FILE"
    echo "Timestamp: $(date)" | tee -a "$RESULTS_FILE"
    echo "Base URL: $BASE_URL" | tee -a "$RESULTS_FILE"
    echo "Test Address: $TEST_ADDRESS" | tee -a "$RESULTS_FILE"
    echo "" | tee -a "$RESULTS_FILE"

    # Check if oha is installed
    if ! command -v oha &> /dev/null; then
        print_color "$RED" "Error: oha is not installed. Please install it first."
        echo "Visit: https://github.com/hatoo/oha"
        exit 1
    fi

    # Check if jq is installed
    if ! command -v jq &> /dev/null; then
        print_color "$RED" "Error: jq is not installed. Please install it first."
        exit 1
    fi

    # Parse command line arguments
    case "${1:-}" in
        "progressive")
            run_progressive_tests | tee -a "$RESULTS_FILE"
            ;;
        "custom")
            if [ -z "$2" ] || [ -z "$3" ]; then
                echo "Usage: $0 custom <requests> <concurrency>"
                exit 1
            fi
            run_load_test "$2" "$3" | tee -a "$RESULTS_FILE"
            ;;
        *)
            # Default single test with environment variables
            echo "Running single test configuration" | tee -a "$RESULTS_FILE"
            echo "Requests: $REQUESTS" | tee -a "$RESULTS_FILE"
            echo "Concurrency: $CONCURRENCY" | tee -a "$RESULTS_FILE"
            run_load_test "$REQUESTS" "$CONCURRENCY" | tee -a "$RESULTS_FILE"
            ;;
    esac

    echo "" | tee -a "$RESULTS_FILE"
    echo "Results saved to: $RESULTS_FILE" | tee -a "$RESULTS_FILE"
}

# Run main function
main "$@"