#!/bin/bash

# Simplified Read-Only Endpoint Test Script for GenLayer Studio
# Tests only the specified read-only endpoints with load testing

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Configuration
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}
TEST_ENDPOINT_SCRIPT="$SCRIPT_DIR/test_endpoint.sh"

# Test configuration
REQUESTS=${REQUESTS:-1000}
CONCURRENCY=${CONCURRENCY:-100}

# Test data
TEST_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
TEST_BLOCK_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"
TEST_TX_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    shift
    printf "${color}%s${NC}\n" "$*"
}

# Function to run a single endpoint test
run_test() {
    local method=$1
    shift
    local params="$*"

    # Run the test using test_endpoint.sh
    if REQUESTS=$REQUESTS CONCURRENCY=$CONCURRENCY "$TEST_ENDPOINT_SCRIPT" "$method" $params >/dev/null 2>&1; then
        print_color "$GREEN" "  ✅ PASS"
        return 0
    else
        print_color "$RED" "  ❌ FAIL"
        return 1
    fi
}

# Main execution for single endpoint test
main() {
    # Check if being called with a specific endpoint
    if [ $# -ge 1 ]; then
        METHOD=$1
        shift

        # Check if test_endpoint.sh exists
        if [ ! -f "$TEST_ENDPOINT_SCRIPT" ]; then
            print_color "$RED" "Error: test_endpoint.sh not found at $TEST_ENDPOINT_SCRIPT"
            exit 1
        fi

        # Make sure test_endpoint.sh is executable
        chmod +x "$TEST_ENDPOINT_SCRIPT"

        # Run single endpoint test
        run_test "$METHOD" "$@"
        exit $?
    fi

    # Otherwise run all read-only endpoint tests
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    print_color "$BLUE" "         GenLayer Studio Read-Only Endpoint Test Suite"
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    echo ""
    print_color "$YELLOW" "Configuration:"
    echo "  Base URL: $BASE_URL"
    echo "  Requests per test: $REQUESTS"
    echo "  Concurrency: $CONCURRENCY"
    echo ""

    # Check if test_endpoint.sh exists
    if [ ! -f "$TEST_ENDPOINT_SCRIPT" ]; then
        print_color "$RED" "Error: test_endpoint.sh not found at $TEST_ENDPOINT_SCRIPT"
        exit 1
    fi

    # Make sure test_endpoint.sh is executable
    chmod +x "$TEST_ENDPOINT_SCRIPT"

    # Initialize counters
    total_tests=0
    passed_tests=0
    failed_tests=0

    print_color "$BLUE" "╔════════════════════════════════════════════════════════════════╗"
    print_color "$BLUE" "║                    READ-ONLY ENDPOINTS                        ║"
    print_color "$BLUE" "╚════════════════════════════════════════════════════════════════╝"
    echo ""

    # Test read-only endpoints without parameters
    print_color "$YELLOW" "Testing endpoints without parameters..."
    echo ""

    # Test each endpoint
    endpoints_no_params=(
        "ping"
        "eth_blockNumber"
        "eth_gasPrice"
        "eth_chainId"
        "net_version"
        "sim_getFinalityWindowTime"
        "sim_countValidators"
        "sim_getAllValidators"
    )

    for endpoint in "${endpoints_no_params[@]}"; do
        print_color "$CYAN" "Testing: $endpoint"
        printf "  Status: "
        total_tests=$((total_tests + 1))
        if run_test "$endpoint"; then
            passed_tests=$((passed_tests + 1))
        else
            failed_tests=$((failed_tests + 1))
        fi
        sleep 0.5  # Small delay between tests
    done

    echo ""
    print_color "$YELLOW" "Testing endpoints with parameters..."
    echo ""

    # Define endpoints with their parameters
    declare -A endpoints_with_params
    endpoints_with_params["eth_getBalance"]="$TEST_ADDRESS latest"
    endpoints_with_params["eth_getTransactionCount"]="$TEST_ADDRESS latest"
    endpoints_with_params["eth_getBlockByNumber"]="0x1 true"
    endpoints_with_params["eth_getBlockByHash"]="$TEST_BLOCK_HASH true"
    endpoints_with_params["eth_getTransactionByHash"]="$TEST_TX_HASH"
    endpoints_with_params["eth_getTransactionReceipt"]="$TEST_TX_HASH"
    endpoints_with_params["sim_getValidator"]="$TEST_ADDRESS"
    endpoints_with_params["sim_getTransactionsForAddress"]="$TEST_ADDRESS"
    endpoints_with_params["sim_getConsensusContract"]=""

    # Test endpoints with parameters
    for endpoint in "eth_getBalance" "eth_getTransactionCount" "eth_getBlockByNumber" \
                    "eth_getBlockByHash" "eth_getTransactionByHash" "eth_getTransactionReceipt" \
                    "sim_getValidator" "sim_getTransactionsForAddress" "sim_getConsensusContract"; do
        params="${endpoints_with_params[$endpoint]}"
        print_color "$CYAN" "Testing: $endpoint"
        printf "  Parameters: [%s]\n" "$params"
        printf "  Status: "
        total_tests=$((total_tests + 1))
        if run_test "$endpoint" $params; then
            passed_tests=$((passed_tests + 1))
        else
            failed_tests=$((failed_tests + 1))
        fi
        sleep 0.5  # Small delay between tests
    done

    # Print summary
    echo ""
    print_color "$GREEN" "╔════════════════════════════════════════════════════════════════╗"
    print_color "$GREEN" "║                      TEST SUMMARY                             ║"
    print_color "$GREEN" "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  Total Tests: $total_tests"
    print_color "$GREEN" "  Passed: $passed_tests"
    print_color "$RED" "  Failed: $failed_tests"
    success_rate=$(awk "BEGIN {printf \"%.2f\", ($passed_tests/$total_tests)*100}")
    echo "  Success Rate: ${success_rate}%"
    echo ""

    if [ $failed_tests -eq 0 ]; then
        print_color "$GREEN" "  ✅ All tests passed successfully!"
    else
        print_color "$YELLOW" "  ⚠️  Some tests failed."
    fi
    echo ""

    # Exit with appropriate code
    if [ $failed_tests -gt 0 ]; then
        exit 1
    fi
}

# Run main function
main "$@"