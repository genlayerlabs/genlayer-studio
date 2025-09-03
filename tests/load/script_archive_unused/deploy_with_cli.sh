#!/bin/bash

# Script to deploy WizardOfCoin contract using GenLayer CLI
# This is a cleaner alternative to raw transaction generation

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

echo "======================================"
echo "GenLayer CLI Contract Deployment"
echo "======================================"
echo ""
echo "Base URL: $BASE_URL"
echo ""

# Check if genlayer CLI is available
if ! command -v genlayer &> /dev/null; then
    echo "❌ Error: GenLayer CLI is not installed"
    echo "Please install it first"
    exit 1
fi

echo "✅ GenLayer CLI version: $(genlayer --version)"
echo ""

# Contract path
CONTRACT_PATH="$SCRIPT_DIR/../../examples/contracts/wizard_of_coin.py"

# Check if contract exists
if [ ! -f "$CONTRACT_PATH" ]; then
    echo "❌ Error: Contract file not found at $CONTRACT_PATH"
    exit 1
fi

echo "Contract path: $CONTRACT_PATH"
echo ""

# Deploy the contract with have_coin=true
echo "Step 1: Deploying WizardOfCoin Contract"
echo "========================================"
echo "Constructor argument: have_coin=true"
echo ""

echo "Executing deployment command..."

# Check if expect is available for password automation
if command -v expect &> /dev/null && [ -n "$GENLAYER_PASSWORD" ]; then
    echo "Using expect for password automation..."
    DEPLOY_OUTPUT=$(expect -c "
        set timeout 30
        spawn genlayer deploy --contract \"$CONTRACT_PATH\" --args true --rpc \"$BASE_URL\"
        expect \"Enter password to decrypt keystore:\"
        send \"$GENLAYER_PASSWORD\r\"
        expect eof
        catch wait result
        exit [lindex \$result 3]
    " 2>&1)
    DEPLOY_EXIT_CODE=$?
else
    echo "Note: Set GENLAYER_PASSWORD environment variable and install 'expect' for automation"
    echo "Or unlock your wallet first with: genlayer keygen unlock"
    DEPLOY_OUTPUT=$(genlayer deploy --contract "$CONTRACT_PATH" --args true --rpc "$BASE_URL" 2>&1)
    DEPLOY_EXIT_CODE=$?
fi

echo "$DEPLOY_OUTPUT"
echo ""

if [ $DEPLOY_EXIT_CODE -ne 0 ]; then
    echo "❌ Error: Deployment failed with exit code $DEPLOY_EXIT_CODE"
    exit 1
fi

# Extract contract address from output
# The CLI output typically includes the contract address
CONTRACT_ADDRESS=$(echo "$DEPLOY_OUTPUT" | grep -oE '0x[a-fA-F0-9]{40}' | head -1)

if [ -z "$CONTRACT_ADDRESS" ]; then
    echo "⚠️  Warning: Could not extract contract address from deployment output"
    echo "Please check the output above for the contract address"
else
    echo "✅ Contract successfully deployed!"
    echo "Contract Address: $CONTRACT_ADDRESS"
    echo ""
    
    # Save contract address for later use
    echo "$CONTRACT_ADDRESS" > "$SCRIPT_DIR/.last_deployed_contract"
    echo "Contract address saved to: $SCRIPT_DIR/.last_deployed_contract"
fi

echo ""
echo "Step 2: Getting Contract Schema"
echo "================================"

if [ -n "$CONTRACT_ADDRESS" ]; then
    echo "Getting schema for deployed contract..."
    genlayer schema "$CONTRACT_ADDRESS" --rpc "$BASE_URL" 2>&1
    echo ""
fi

echo ""
echo "Step 3: Reading Contract State"
echo "==============================="

if [ -n "$CONTRACT_ADDRESS" ]; then
    echo "Calling get_have_coin() method..."
    CALL_OUTPUT=$(genlayer call "$CONTRACT_ADDRESS" get_have_coin --rpc "$BASE_URL" 2>&1)
    CALL_EXIT_CODE=$?
    
    echo "$CALL_OUTPUT"
    
    if [ $CALL_EXIT_CODE -eq 0 ]; then
        echo "✅ Successfully read contract state"
    else
        echo "⚠️  Warning: Could not read contract state"
    fi
fi

echo ""
echo "======================================"
echo "✅ Script completed!"
echo "======================================"
echo ""

if [ -n "$CONTRACT_ADDRESS" ]; then
    echo "Summary:"
    echo "- Contract deployed at: $CONTRACT_ADDRESS"
    echo "- Constructor: have_coin=true"
    echo ""
    echo "To interact with the contract:"
    echo "  # Read state:"
    echo "  genlayer call $CONTRACT_ADDRESS get_have_coin --rpc $BASE_URL"
    echo ""
    echo "  # Write state (ask for coin):"
    echo "  genlayer write $CONTRACT_ADDRESS ask_for_coin \"Please give me the coin!\" --rpc $BASE_URL"
    echo ""
    echo "You can also export the contract address:"
    echo "  export CONTRACT_ADDRESS=$CONTRACT_ADDRESS"
fi