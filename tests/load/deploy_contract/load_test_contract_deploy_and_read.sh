#!/bin/bash

# Shell wrapper for WizardOfCoin contract test
# This script runs the Python test and can be used in load testing scenarios

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    echo "Loading configuration from $SCRIPT_DIR/.env"
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Base URL (can be overridden via environment variable)
export BASE_URL=${BASE_URL:-"http://localhost:4000/api"}

echo "======================================"
echo "WizardOfCoin Contract Test Runner"
echo "======================================"
echo ""
echo "Base URL: $BASE_URL"
echo ""

# Check if Python 3 is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is required but not installed"
    exit 1
fi

# Check if genlayer_py is installed
python3 -c "import genlayer_py" 2>/dev/null
if [ $? -ne 0 ]; then
    echo "⚠️  Warning: genlayer_py not installed"
    echo "Installing genlayer_py..."
    pip3 install genlayer-py 2>/dev/null || pip install genlayer-py 2>/dev/null
    
    # Check again
    python3 -c "import genlayer_py" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo "❌ Error: Could not install genlayer_py"
        echo "Please install it manually with: pip install genlayer-py"
        exit 1
    fi
fi

echo "✅ Dependencies verified"
echo ""

# Run the Python test script
echo "Starting WizardOfCoin contract test..."
echo "======================================"

# Check for the script in the new location
if [ -f "$SCRIPT_DIR/deploy_contract/test_wizard_of_coin.py" ]; then
    python3 "$SCRIPT_DIR/deploy_contract/test_wizard_of_coin.py"
    EXIT_CODE=$?
else
    echo "❌ Error: test_wizard_of_coin.py not found"
    echo "Expected location: $SCRIPT_DIR/deploy_contract/test_wizard_of_coin.py"
    exit 1
fi

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test completed successfully"
    
    # Check if contract address was saved (could be in either location)
    if [ -f "$SCRIPT_DIR/.last_deployed_contract" ]; then
        CONTRACT_ADDRESS=$(cat "$SCRIPT_DIR/.last_deployed_contract")
        echo "Contract deployed at: $CONTRACT_ADDRESS"
    elif [ -f "$SCRIPT_DIR/deploy_contract/.last_deployed_contract" ]; then
        CONTRACT_ADDRESS=$(cat "$SCRIPT_DIR/deploy_contract/.last_deployed_contract")
        echo "Contract deployed at: $CONTRACT_ADDRESS"
        echo ""
        echo "You can interact with the contract using:"
        echo "  genlayer call $CONTRACT_ADDRESS get_have_coin --rpc $BASE_URL"
        echo "  genlayer write $CONTRACT_ADDRESS ask_for_coin \"Your request\" --rpc $BASE_URL"
    fi
else
    echo "❌ Test failed with exit code: $EXIT_CODE"
fi

exit $EXIT_CODE