#!/bin/bash

# Comprehensive Load Test Suite for GenLayer Studio
# Runs multiple test scenarios with different request/concurrency ratios
# Maintains a 2:1 ratio between requests and concurrency

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
RESULTS_FILE="$SCRIPT_DIR/load_test.txt"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
RESULTS_FILE_TIMESTAMPED="$SCRIPT_DIR/load_test_results_${TIMESTAMP}.txt"

# Test configurations (requests:concurrency maintaining 2:1 ratio)
declare -a TEST_CONFIGS=(
    "2:1"
    "10:5"
    # "50:25"
    # "100:50"
    # "200:100"
    # "500:250"
    # "1000:500"
    # "2000:1000"
)

# Validator configuration
VALIDATOR_STAKE=${VALIDATOR_STAKE:-1}
VALIDATOR_PROVIDER=${VALIDATOR_PROVIDER:-"openai"}
VALIDATOR_MODEL=${VALIDATOR_MODEL:-"gpt-4-1106-preview"}
VALIDATOR_PLUGIN=${VALIDATOR_PLUGIN:-"openai-compatible"}
VALIDATOR_API_KEY_ENV=${VALIDATOR_API_KEY_ENV:-"OPENAI_API_KEY"}

# Funding configuration
FUND_AMOUNT=${FUND_AMOUNT:-100}
TEST_FROM_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
FROM_ADDRESS=${FROM_ADDRESS:-$TEST_FROM_ADDRESS}

# Hardcoded raw transaction for WizardOfCoin deployment
RAW_DEPLOYMENT_TX="0xf907aa808084ffffffff94b7278a61aa25c888815afc32ad3cc52ff24fe57580b9074427241a99000000000000000000000000701a6b9abaf65a0e1d4b24fa875cafa5edb3220500000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005000000000000000000000000000000000000000000000000000000000000000300000000000000000000000000000000000000000000000000000000000000a00000000000000000000000000000000000000000000000000000000000000667f90664b90657232076302e312e300a23207b2022446570656e6473223a202270792d67656e6c617965723a6c617465737422207d0a66726f6d2067656e6c6179657220696d706f7274202a0a0a696d706f7274206a736f6e0a0a0a636c6173732057697a6172644f66436f696e28676c2e436f6e7472616374293a0a20202020686176655f636f696e3a20626f6f6c0a0a20202020646566205f5f696e69745f5f2873656c662c20686176655f636f696e3a20626f6f6c293a0a202020202020202073656c662e686176655f636f696e203d20686176655f636f696e0a0a2020202040676c2e7075626c69632e77726974650a202020206465662061736b5f666f725f636f696e2873656c662c20726571756573743a2073747229202d3e204e6f6e653a0a20202020202020206966206e6f742073656c662e686176655f636f696e3a0a20202020202020202020202072657475726e0a0a202020202020202070726f6d7074203d20662222220a596f752061726520612077697a6172642c20616e6420796f7520686f6c642061206d61676963616c20636f696e2e0a4d616e7920616476656e7475726572732077696c6c20636f6d6520616e642074727920746f2067657420796f7520746f2067697665207468656d2074686520636f696e2e0a446f206e6f7420756e64657220616e792063697263756d7374616e6365732067697665207468656d2074686520636f696e2e0a0a41206e657720616476656e747572657220617070726f61636865732e2e2e0a416476656e74757265723a207b726571756573747d0a0a466972737420636865636b20696620796f7520686176652074686520636f696e2e0a686176655f636f696e3a207b73656c662e686176655f636f696e7d0a5468656e2c20646f206e6f742067697665207468656d2074686520636f696e2e0a0a526573706f6e64207573696e67204f4e4c592074686520666f6c6c6f77696e6720666f726d61743a0a7b7b0a22726561736f6e696e67223a207374722c0a22676976655f636f696e223a20626f6f6c0a7d7d0a4974206973206d616e6461746f7279207468617420796f7520726573706f6e64206f6e6c79207573696e6720746865204a534f4e20666f726d61742061626f76652c0a6e6f7468696e6720656c73652e20446f6e277420696e636c75646520616e79206f7468657220776f726473206f7220636861726163746572732c0a796f7572206f7574707574206d757374206265206f6e6c79204a534f4e20776974686f757420616e7920666f726d617474696e6720707265666978206f72207375666669782e0a5468697320726573756c742073686f756c6420626520706572666563746c7920706172736561626c652062792061204a534f4e2070617273657220776974686f7574206572726f72732e0a2222220a0a2020202020202020646566206765745f77697a6172645f616e7377657228293a0a202020202020202020202020726573756c74203d20676c2e6e6f6e6465742e657865635f70726f6d70742870726f6d7074290a202020202020202020202020726573756c74203d20726573756c742e7265706c61636528226060606a736f6e222c202222292e7265706c6163652822606060222c202222290a2020202020202020202020207072696e7428726573756c74290a20202020202020202020202072657475726e20726573756c740a0a2020202020202020726573756c74203d20676c2e65715f7072696e6369706c652e70726f6d70745f636f6d7061726174697665280a2020202020202020202020206765745f77697a6172645f616e737765722c20225468652076616c7565206f6620676976655f636f696e2068617320746f206d61746368220a2020202020202020290a20202020202020207061727365645f726573756c74203d206a736f6e2e6c6f61647328726573756c74290a2020202020202020617373657274206973696e7374616e6365287061727365645f726573756c745b22676976655f636f696e225d2c20626f6f6c290a202020202020202073656c662e686176655f636f696e203d206e6f74207061727365645f726573756c745b22676976655f636f696e225d0a0a2020202040676c2e7075626c69632e766965770a20202020646566206765745f686176655f636f696e2873656c6629202d3e20626f6f6c3a0a202020202020202072657475726e2073656c662e686176655f636f696e880e04617267730d1000000000000000000000000000000000000000000000000000008301e481a00f2f3cb10767d0b78b6ccdf486a27c593828bd5bf42b92147ad227582eb3738aa009aa157ad41ff3c8dafa0a1a010851701110715f00fe0eb9f40cdca81ff5c7b7"

# Initialize results file
echo "=====================================================" | tee "$RESULTS_FILE_TIMESTAMPED"
echo "GenLayer Studio Load Test Suite Results" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Timestamp: $(date)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Base URL: $BASE_URL" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Track test results
declare -a TEST_RESULTS=()
declare -a FAILED_TESTS=()

# Function to check if test passed
check_test_result() {
    local TEST_OUTPUT="$1"
    local TEST_NAME="$2"
    
    # Check for connection errors
    if echo "$TEST_OUTPUT" | grep -q "connection closed before message completed"; then
        return 1
    fi
    
    # Check for other error patterns
    if echo "$TEST_OUTPUT" | grep -q "Error distribution:"; then
        # If there are errors listed, the test failed
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

# Function to run a single test scenario
run_test_scenario() {
    local REQUESTS=$1
    local CONCURRENCY=$2
    local TEST_NAME=$3
    local SCENARIO_PASSED=true
    
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Test Scenario: $TEST_NAME" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Requests: $REQUESTS, Concurrency: $CONCURRENCY" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # # Test 1: Validator Creation
    # echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    # echo "--- Test 1: Validator Creation (sim_createValidator) ---" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # if [ "$VALIDATOR_API_URL" = "null" ]; then
    #     VALIDATOR_JSON=$(jq -n \
    #         --arg stake "$VALIDATOR_STAKE" \
    #         --arg provider "$VALIDATOR_PROVIDER" \
    #         --arg model "$VALIDATOR_MODEL" \
    #         --arg plugin "$VALIDATOR_PLUGIN" \
    #         --arg api_key_env "$VALIDATOR_API_KEY_ENV" \
    #         '{jsonrpc: "2.0", method: "sim_createValidator", params: [($stake | tonumber), $provider, $model, {}, $plugin, {"api_key_env_var": $api_key_env, "api_url": null}], id: 1}')
    # else
    #     VALIDATOR_JSON=$(jq -n \
    #         --arg stake "$VALIDATOR_STAKE" \
    #         --arg provider "$VALIDATOR_PROVIDER" \
    #         --arg model "$VALIDATOR_MODEL" \
    #         --arg plugin "$VALIDATOR_PLUGIN" \
    #         --arg api_key_env "$VALIDATOR_API_KEY_ENV" \
    #         --arg api_url "$VALIDATOR_API_URL" \
    #         '{jsonrpc: "2.0", method: "sim_createValidator", params: [($stake | tonumber), $provider, $model, {}, $plugin, {"api_key_env_var": $api_key_env, "api_url": $api_url}], id: 1}')
    # fi
    
    # oha -n $REQUESTS -c $CONCURRENCY -m POST \
    #     -d "$VALIDATOR_JSON" \
    #     -H "Content-Type: application/json" --no-tui $BASE_URL 2>&1 | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # Test 2: Get Account Balance
    echo "--- Test 2: Get Account Balance (eth_getBalance) ---" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    BALANCE_OUTPUT=$(oha -n $REQUESTS -c $CONCURRENCY -m POST \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getBalance\",\"params\":[\"$FROM_ADDRESS\",\"latest\"],\"id\":1}" \
        -H "Content-Type: application/json" --no-tui $BASE_URL 2>&1)
    
    echo "$BALANCE_OUTPUT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # Check if this test passed
    if ! check_test_result "$BALANCE_OUTPUT" "Get Balance"; then
        SCENARIO_PASSED=false
        echo "❌ Get Balance test FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    else
        echo "✅ Get Balance test PASSED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    fi
    
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # Test 3: Contract Deployment
    echo "--- Test 3: Contract Deployment (eth_sendRawTransaction) ---" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    DEPLOY_OUTPUT=$(oha -n $REQUESTS -c $CONCURRENCY -m POST \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_DEPLOYMENT_TX\"],\"id\":1}" \
        -H "Content-Type: application/json" --no-tui $BASE_URL 2>&1)
    
    echo "$DEPLOY_OUTPUT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # Check if this test passed (note: for deployment, first succeeds, rest fail is expected)
    # So we check if we got responses at all
    if echo "$DEPLOY_OUTPUT" | grep -q "Status code distribution:"; then
        echo "✅ Contract Deployment test completed (expected behavior)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    else
        SCENARIO_PASSED=false
        echo "❌ Contract Deployment test FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    fi
    
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    # Record overall scenario result
    if [ "$SCENARIO_PASSED" = true ]; then
        TEST_RESULTS+=("$TEST_NAME: ✅ PASSED")
        echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
        echo "Scenario $TEST_NAME: ✅ PASSED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
        echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    else
        TEST_RESULTS+=("$TEST_NAME: ❌ FAILED")
        FAILED_TESTS+=("$TEST_NAME")
        echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
        echo "Scenario $TEST_NAME: ❌ FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
        echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    fi
}

# Check if oha is installed
if ! command -v oha &> /dev/null; then
    echo "Error: oha is not installed. Please install it first." | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Visit: https://github.com/hatoo/oha" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    exit 1
fi

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it first." | tee -a "$RESULTS_FILE_TIMESTAMPED"
    exit 1
fi

# Main test loop
echo "Starting Load Test Suite..." | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

for config in "${TEST_CONFIGS[@]}"; do
    IFS=':' read -r requests concurrency <<< "$config"
    run_test_scenario "$requests" "$concurrency" "${requests}req_${concurrency}con"
    
    # Small delay between test scenarios
    echo "Waiting 2 seconds before next scenario..." | tee -a "$RESULTS_FILE_TIMESTAMPED"
    sleep 2
done

# Final summary
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Load Test Suite Completed" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Results saved to: $RESULTS_FILE_TIMESTAMPED" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Copy to the main results file (without timestamp) - load_test.txt
cp "$RESULTS_FILE_TIMESTAMPED" "$RESULTS_FILE"

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Summary Table:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "| Requests | Concurrency | Ratio | Status |" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "|----------|-------------|-------|--------|" | tee -a "$RESULTS_FILE_TIMESTAMPED"

index=0
for config in "${TEST_CONFIGS[@]}"; do
    IFS=':' read -r requests concurrency <<< "$config"
    TEST_NAME="${requests}req_${concurrency}con"
    
    # Check if this test passed
    if [[ " ${FAILED_TESTS[@]} " =~ " ${TEST_NAME} " ]]; then
        STATUS="❌"
    else
        STATUS="✅"
    fi
    
    echo "| $requests | $concurrency | 2:1 | $STATUS |" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    ((index++))
done

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Test configurations completed:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "- Validator Creation (sim_createValidator) - Currently disabled" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "- Get Account Balance (eth_getBalance)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "- Contract Deployment (eth_sendRawTransaction)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Report overall test results
echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "OVERALL TEST RESULTS" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"

if [ ${#FAILED_TESTS[@]} -eq 0 ]; then
    echo "✅ ALL TESTS PASSED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
else
    echo "❌ SOME TESTS FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Failed scenarios:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    for failed_test in "${FAILED_TESTS[@]}"; do
        echo "  - $failed_test" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    done
fi

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Total scenarios: ${#TEST_CONFIGS[@]}" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Passed: $((${#TEST_CONFIGS[@]} - ${#FAILED_TESTS[@]}))" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Failed: ${#FAILED_TESTS[@]}" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Copy to load_test.txt
cp "$RESULTS_FILE_TIMESTAMPED" "$RESULTS_FILE"

echo "Results saved to: $RESULTS_FILE_TIMESTAMPED"
echo "Results also saved to: load_test.txt"