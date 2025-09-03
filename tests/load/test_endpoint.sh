#!/bin/bash

# Generic Load Test Script for any JSON-RPC endpoint
# Usage: ./test_endpoint.sh <method> [param1] [param2] ...
# Example: ./test_endpoint.sh eth_getBalance 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 latest

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

# Test parameters (can be overridden via environment)
REQUESTS=${REQUESTS:-100}
CONCURRENCY=${CONCURRENCY:-10}

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    shift
    printf "${color}%s${NC}\n" "$*"
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

# Function to build JSON-RPC params array from arguments
build_params_json() {
    # If first argument starts with '{', treat it as a JSON object
    if [ $# -eq 1 ] && [[ "$1" =~ ^\{ ]]; then
        echo "$1"
        return
    fi

    local params="["
    local first=true

    for param in "$@"; do
        if [ "$first" = true ]; then
            first=false
        else
            params+=","
        fi

        # Check if param starts with '{' (JSON object)
        if [[ "$param" =~ ^\{ ]]; then
            params+="$param"
        # Check if param starts with '0x' (Ethereum address or hex value) - always treat as string
        elif [[ "$param" =~ ^0x ]]; then
            params+="\"$param\""
        # Check if param is a large number (more than 15 digits) - treat as string for precision
        elif [[ "$param" =~ ^[0-9]{16,}$ ]]; then
            params+="\"$param\""
        # Check if param looks like a small number (less than 16 digits)
        elif [[ "$param" =~ ^[0-9]+$ ]] && [ ${#param} -lt 16 ]; then
            params+="$param"
        # Check if param looks like a boolean
        elif [ "$param" = "true" ] || [ "$param" = "false" ]; then
            params+="$param"
        # Check if param looks like null
        elif [ "$param" = "null" ]; then
            params+="null"
        # Otherwise treat as string
        else
            params+="\"$param\""
        fi
    done

    params+="]"
    echo "$params"
}

# Function to run load test for a specific endpoint
run_endpoint_test() {
    local method=$1
    shift
    local params=$(build_params_json "$@")

    # Build the JSON-RPC request
    REQUEST_JSON=$(cat <<EOF
{
    "jsonrpc": "2.0",
    "method": "$method",
    "params": $params,
    "id": 1
}
EOF
)

    # Run the load test using oha with timeout
    TEST_OUTPUT=$(oha -n $REQUESTS -c $CONCURRENCY -m POST \
        -d "$REQUEST_JSON" \
        -H "Content-Type: application/json" \
        -t 60s \
        --no-tui "$BASE_URL" 2>&1)

    # Print full oha output including histogram
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Load Test Results for: $method"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    # Display the entire oha output (includes histogram, response time stats, etc.)
    echo "$TEST_OUTPUT"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo ""

    # Return success/failure based on test result
    if check_test_result "$TEST_OUTPUT"; then
        return 0
    else
        return 1
    fi
}

# Function to run progressive load tests
run_progressive_tests() {
    local method=$1
    shift
    local configs=("10:1" "50:5" "100:10" "200:20" "500:50" "1000:100")

    print_color "$BLUE" "\n════════════════════════════════════════════════════════════════════"
    print_color "$BLUE" "  Progressive Load Test: $method"
    print_color "$BLUE" "  Parameters: [$*]"
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════\n"

    # Table header
    printf "%-20s %-20s %-10s\n" "Requests" "Concurrency" "Result"
    printf "%-20s %-20s %-10s\n" "────────" "───────────" "──────"

    for config in "${configs[@]}"; do
        IFS=':' read -r req con <<< "$config"

        # Update global variables for this test
        REQUESTS=$req
        CONCURRENCY=$con

        printf "%-20s %-20s " "$req" "$con"

        if run_endpoint_test "$method" "$@"; then
            print_color "$GREEN" "✅ PASS"
        else
            print_color "$RED" "❌ FAIL"
            print_color "$YELLOW" "\nSystem limit reached at $req requests / $con concurrent"
            break
        fi

        # Small delay between tests
        sleep 1
    done

    echo ""
}

# Function to run single test with current configuration
run_single_test() {
    local method=$1
    shift

    print_color "$BLUE" "\n════════════════════════════════════════════════════════════════════"
    print_color "$BLUE" "  Single Test: $method"
    print_color "$BLUE" "  Parameters: [$*]"
    print_color "$BLUE" "  Configuration: $REQUESTS requests / $CONCURRENCY concurrent"
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════\n"

    # Table header
    printf "%-20s %-20s %-10s\n" "Requests" "Concurrency" "Result"
    printf "%-20s %-20s %-10s\n" "────────" "───────────" "──────"

    printf "%-20s %-20s " "$REQUESTS" "$CONCURRENCY"

    if run_endpoint_test "$method" "$@"; then
        print_color "$GREEN" "✅ PASS"
    else
        print_color "$RED" "❌ FAIL"
    fi

    echo ""
}

# Main script
main() {
    # Check if oha is installed
    if ! command -v oha &> /dev/null; then
        print_color "$RED" "Error: oha is not installed. Please install it first."
        echo "Visit: https://github.com/hatoo/oha"
        exit 1
    fi

    # Check for method argument
    if [ $# -lt 1 ]; then
        print_color "$RED" "Error: No method specified"
        echo ""
        echo "Usage: $0 <method> [param1] [param2] ..."
        echo ""
        echo "Examples:"
        echo "  $0 eth_getBalance 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 latest"
        echo "  $0 eth_blockNumber"
        echo "  $0 eth_getTransactionCount 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 latest"
        echo ""
        echo "Run modes:"
        echo "  Default: Single test with REQUESTS=$REQUESTS and CONCURRENCY=$CONCURRENCY"
        echo "  PROGRESSIVE=1 $0 <method> [params]: Run progressive load tests"
        echo ""
        echo "Configuration via environment:"
        echo "  REQUESTS=500 CONCURRENCY=50 $0 eth_blockNumber"
        echo "  BASE_URL=http://localhost:8545 $0 eth_getBalance 0x123... latest"
        exit 1
    fi

    METHOD=$1
    shift

    # Check for progressive mode
    if [ "${PROGRESSIVE:-0}" = "1" ]; then
        run_progressive_tests "$METHOD" "$@"
    else
        run_single_test "$METHOD" "$@"
    fi
}

# Run main function
main "$@"