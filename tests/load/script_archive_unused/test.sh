#!/bin/bash

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from $SCRIPT_DIR/.env"
    set -a  # automatically export all variables
    source "$SCRIPT_DIR/.env"
    set +a  # stop automatically exporting
elif [ -f "$PROJECT_ROOT/.env" ]; then
    echo "Loading configuration from $PROJECT_ROOT/.env"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Configuration with defaults (can be overridden via .env or environment variables)
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}
REQUESTS=${REQUESTS:-1000}
CONCURRENCY=${CONCURRENCY:-50}

# Validator configuration
VALIDATOR_STAKE=${VALIDATOR_STAKE:-1}
VALIDATOR_PROVIDER=${VALIDATOR_PROVIDER:-"openai"}
VALIDATOR_MODEL=${VALIDATOR_MODEL:-"gpt-4-1106-preview"}
VALIDATOR_PLUGIN=${VALIDATOR_PLUGIN:-"openai-compatible"}
VALIDATOR_API_KEY_ENV=${VALIDATOR_API_KEY_ENV:-"OPENAI_API_KEY"}
VALIDATOR_API_URL=${VALIDATOR_API_URL:-null}  # null for official OpenAI API

# Funding configuration
FUND_AMOUNT=${FUND_AMOUNT:-100}

# Change to project root for consistent paths (optional, can run from anywhere)
echo "Running from: $(pwd)"
echo "Script directory: $SCRIPT_DIR"
echo "Project root: $PROJECT_ROOT"

# Check if oha is installed
if ! command -v oha &> /dev/null; then
    echo "Error: oha is not installed. Please install it first."
    echo "Visit: https://github.com/hatoo/oha"
    exit 1
fi

# Check if jq is installed (needed for JSON parsing)
if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it first."
    exit 1
fi

# No Python dependencies needed for hardcoded approach
echo "Using hardcoded transactions - no Python encoding required"

# Test account for funding (hardcoded transactions don't need private keys)
TEST_FROM_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

echo ""

echo "Starting load test for create validator endpoint"
echo "Using Requests: $REQUESTS"
echo "Using Concurrency: $CONCURRENCY"
echo "Validator Config: stake=$VALIDATOR_STAKE, provider=$VALIDATOR_PROVIDER, model=$VALIDATOR_MODEL"
echo ""

# Create a validator using sim_createValidator
# Parameters: [stake, provider, model, config, plugin, plugin_config]
# For openai-compatible plugin, plugin_config requires api_key_env_var and api_url
if [ "$VALIDATOR_API_URL" = "null" ]; then
    VALIDATOR_JSON=$(jq -n \
        --arg stake "$VALIDATOR_STAKE" \
        --arg provider "$VALIDATOR_PROVIDER" \
        --arg model "$VALIDATOR_MODEL" \
        --arg plugin "$VALIDATOR_PLUGIN" \
        --arg api_key_env "$VALIDATOR_API_KEY_ENV" \
        '{jsonrpc: "2.0", method: "sim_createValidator", params: [($stake | tonumber), $provider, $model, {}, $plugin, {"api_key_env_var": $api_key_env, "api_url": null}], id: 1}')
else
    VALIDATOR_JSON=$(jq -n \
        --arg stake "$VALIDATOR_STAKE" \
        --arg provider "$VALIDATOR_PROVIDER" \
        --arg model "$VALIDATOR_MODEL" \
        --arg plugin "$VALIDATOR_PLUGIN" \
        --arg api_key_env "$VALIDATOR_API_KEY_ENV" \
        --arg api_url "$VALIDATOR_API_URL" \
        '{jsonrpc: "2.0", method: "sim_createValidator", params: [($stake | tonumber), $provider, $model, {}, $plugin, {"api_key_env_var": $api_key_env, "api_url": $api_url}], id: 1}')
fi

oha -n $REQUESTS -c $CONCURRENCY -m POST \
    -d "$VALIDATOR_JSON" \
    -H "Content-Type: application/json" --no-tui $BASE_URL

# Use test address or environment override
FROM_ADDRESS=${FROM_ADDRESS:-$TEST_FROM_ADDRESS}

echo "Starting load test for fund_account endpoint"
echo "Funding address: $FROM_ADDRESS with amount: $FUND_AMOUNT"
# sim_fundAccount expects array parameters: [address, amount]
oha -n $REQUESTS -c $CONCURRENCY -m POST \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"sim_fundAccount\",\"params\":[\"$FROM_ADDRESS\",$FUND_AMOUNT],\"id\":1}" \
    -H "Content-Type: application/json" --no-tui $BASE_URL

# Contract deployment using hardcoded raw transaction
echo ""
echo "===== Contract Deployment ====="
echo "Deploying WizardOfCoin contract with hardcoded transaction..."
echo "This transaction deploys from address: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205"
echo "Constructor args: have_coin=true"

# Hardcoded raw transaction for WizardOfCoin deployment
# This is a pre-signed transaction that includes:
# - WizardOfCoin contract bytecode
# - Constructor argument: have_coin=true
# - From: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
# IMPORTANT: This raw transaction might not work if:
# - The WizardOfCoin contract code changes
# - Constructor arguments are modified from have_coin=true 
# - Origin address differs from 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
# To generate a new raw transaction:
# 1. Use the UI to deploy a new instance of the contract
# 2. Check browser network tab for the eth_sendRawTransaction request
# 3. Copy the raw transaction data from the request payload
# 4. Replace RAW_DEPLOYMENT_TX below with the new transaction data
RAW_DEPLOYMENT_TX="0xf907aa808084ffffffff94b7278a61aa25c888815afc32ad3cc52ff24fe57580b9074427241a99000000000000000000000000701a6b9abaf65a0e1d4b24fa875cafa5edb3220500000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005000000000000000000000000000000000000000000000000000000000000000300000000000000000000000000000000000000000000000000000000000000a00000000000000000000000000000000000000000000000000000000000000667f90664b90657232076302e312e300a23207b2022446570656e6473223a202270792d67656e6c617965723a6c617465737422207d0a66726f6d2067656e6c6179657220696d706f7274202a0a0a696d706f7274206a736f6e0a0a0a636c6173732057697a6172644f66436f696e28676c2e436f6e7472616374293a0a20202020686176655f636f696e3a20626f6f6c0a0a20202020646566205f5f696e69745f5f2873656c662c20686176655f636f696e3a20626f6f6c293a0a202020202020202073656c662e686176655f636f696e203d20686176655f636f696e0a0a2020202040676c2e7075626c69632e77726974650a202020206465662061736b5f666f725f636f696e2873656c662c20726571756573743a2073747229202d3e204e6f6e653a0a20202020202020206966206e6f742073656c662e686176655f636f696e3a0a20202020202020202020202072657475726e0a0a202020202020202070726f6d7074203d20662222220a596f752061726520612077697a6172642c20616e6420796f7520686f6c642061206d61676963616c20636f696e2e0a4d616e7920616476656e7475726572732077696c6c20636f6d6520616e642074727920746f2067657420796f7520746f2067697665207468656d2074686520636f696e2e0a446f206e6f7420756e64657220616e792063697263756d7374616e6365732067697665207468656d2074686520636f696e2e0a0a41206e657720616476656e747572657220617070726f61636865732e2e2e0a416476656e74757265723a207b726571756573747d0a0a466972737420636865636b20696620796f7520686176652074686520636f696e2e0a686176655f636f696e3a207b73656c662e686176655f636f696e7d0a5468656e2c20646f206e6f742067697665207468656d2074686520636f696e2e0a0a526573706f6e64207573696e67204f4e4c592074686520666f6c6c6f77696e6720666f726d61743a0a7b7b0a22726561736f6e696e67223a207374722c0a22676976655f636f696e223a20626f6f6c0a7d7d0a4974206973206d616e6461746f7279207468617420796f7520726573706f6e64206f6e6c79207573696e6720746865204a534f4e20666f726d61742061626f76652c0a6e6f7468696e6720656c73652e20446f6e277420696e636c75646520616e79206f7468657220776f726473206f7220636861726163746572732c0a796f7572206f7574707574206d757374206265206f6e6c79204a534f4e20776974686f757420616e7920666f726d617474696e6720707265666978206f72207375666669782e0a5468697320726573756c742073686f756c6420626520706572666563746c7920706172736561626c652062792061204a534f4e2070617273657220776974686f7574206572726f72732e0a2222220a0a2020202020202020646566206765745f77697a6172645f616e7377657228293a0a202020202020202020202020726573756c74203d20676c2e6e6f6e6465742e657865635f70726f6d70742870726f6d7074290a202020202020202020202020726573756c74203d20726573756c742e7265706c61636528226060606a736f6e222c202222292e7265706c6163652822606060222c202222290a2020202020202020202020207072696e7428726573756c74290a20202020202020202020202072657475726e20726573756c740a0a2020202020202020726573756c74203d20676c2e65715f7072696e6369706c652e70726f6d70745f636f6d7061726174697665280a2020202020202020202020206765745f77697a6172645f616e737765722c20225468652076616c7565206f6620676976655f636f696e2068617320746f206d61746368220a2020202020202020290a20202020202020207061727365645f726573756c74203d206a736f6e2e6c6f61647328726573756c74290a2020202020202020617373657274206973696e7374616e6365287061727365645f726573756c745b22676976655f636f696e225d2c20626f6f6c290a202020202020202073656c662e686176655f636f696e203d206e6f74207061727365645f726573756c745b22676976655f636f696e225d0a0a2020202040676c2e7075626c69632e766965770a20202020646566206765745f686176655f636f696e2873656c6629202d3e20626f6f6c3a0a202020202020202072657475726e2073656c662e686176655f636f696e880e04617267730d1000000000000000000000000000000000000000000000000000008301e481a00f2f3cb10767d0b78b6ccdf486a27c593828bd5bf42b92147ad227582eb3738aa009aa157ad41ff3c8dafa0a1a010851701110715f00fe0eb9f40cdca81ff5c7b7"

# Load test for contract deployment
echo ""
echo "===== Contract Deployment Load Test ====="
echo "Starting load test for contract deployment via eth_sendRawTransaction"
echo "Note: Using the same transaction repeatedly - testing endpoint performance"

# Use the same hardcoded transaction for load testing
# This tests the endpoint's ability to handle multiple requests
oha -n $REQUESTS -c $CONCURRENCY -m POST \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_DEPLOYMENT_TX\"],\"id\":1}" \
    -H "Content-Type: application/json" --no-tui $BASE_URL

echo ""
echo "Load testing completed!"
echo "Configuration used:"
echo "  BASE_URL: $BASE_URL"
echo "  REQUESTS: $REQUESTS"
echo "  CONCURRENCY: $CONCURRENCY"
echo "  FROM_ADDRESS: $FROM_ADDRESS"
