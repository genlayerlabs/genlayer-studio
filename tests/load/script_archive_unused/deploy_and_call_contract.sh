#!/bin/bash

# Script to deploy a WizardOfCoin contract and then call it using gen_call
# Based on deploy_contract.sh with added gen_call functionality

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

echo "====================================="
echo "GenLayer Contract Deploy & Call Script"
echo "====================================="
echo ""
echo "Base URL: $BASE_URL"
echo ""

# Check if Python is available
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: Python 3 is required but not installed"
    exit 1
fi

# Check which transaction generator to use
if [ -f "$SCRIPT_DIR/deploy_with_genlayer_sdk.py" ]; then
    GENERATOR_SCRIPT="deploy_with_genlayer_sdk.py"
    echo "Using GenLayer SDK-style deployment"
elif [ -f "$SCRIPT_DIR/generate_correct_deployment_tx.py" ]; then
    GENERATOR_SCRIPT="generate_correct_deployment_tx.py"
    echo "Using corrected deployment transaction generator"
elif [ -f "$SCRIPT_DIR/generate_ui_compatible_tx.py" ]; then
    GENERATOR_SCRIPT="generate_ui_compatible_tx.py"
    echo "Using UI-compatible transaction generator"
elif [ -f "$SCRIPT_DIR/generate_deployment_tx_ui_format.py" ]; then
    GENERATOR_SCRIPT="generate_deployment_tx_ui_format.py"
    echo "Using UI-format transaction generator"
elif [ -f "$SCRIPT_DIR/generate_raw_transaction.py" ]; then
    GENERATOR_SCRIPT="generate_raw_transaction.py"
    echo "Using standard transaction generator"
else
    echo "❌ Error: No transaction generator found in $SCRIPT_DIR"
    exit 1
fi

# Generate a new raw transaction with current nonce
echo "Generating raw transaction with current nonce..."
if [ "$GENERATOR_SCRIPT" = "deploy_with_genlayer_sdk.py" ]; then
    # Use RAW_TX_ONLY mode for SDK script
    RAW_DEPLOYMENT_TX=$(RAW_TX_ONLY=1 python3 "$SCRIPT_DIR/$GENERATOR_SCRIPT" 2>/dev/null)
else
    RAW_DEPLOYMENT_TX=$(python3 "$SCRIPT_DIR/$GENERATOR_SCRIPT" 2>/dev/null)
fi

if [ -z "$RAW_DEPLOYMENT_TX" ]; then
    echo "❌ Error: Failed to generate raw transaction"
    echo "Debug output:"
    python3 "$SCRIPT_DIR/$GENERATOR_SCRIPT"
    exit 1
fi

echo "✅ Raw transaction generated successfully"
echo ""

# Send deployment transaction
echo "Step 1: Deploying Contract"
echo "============================="
echo "Sending deployment transaction..."
DEPLOY_RESPONSE=$(curl -s -X POST $BASE_URL \
    -H "Content-Type: application/json" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_DEPLOYMENT_TX\"],\"id\":1}")

echo "Raw response: $DEPLOY_RESPONSE"
echo ""

# Extract transaction hash from response
TX_HASH=$(echo "$DEPLOY_RESPONSE" | jq -r '.result // empty')
echo "Transaction hash: $TX_HASH"

if [ -z "$TX_HASH" ] || [ "$TX_HASH" = "empty" ]; then
    echo "❌ Error: Could not get transaction hash from deployment"
    echo "Response: $DEPLOY_RESPONSE"
    
    # Check if it's an error response
    ERROR_MSG=$(echo "$DEPLOY_RESPONSE" | jq -r '.error.message // empty')
    if [ -n "$ERROR_MSG" ] && [ "$ERROR_MSG" != "empty" ]; then
        echo "Error message: $ERROR_MSG"
    fi
    
    exit 1
else
    echo "✅ Deployment transaction submitted successfully"
    echo ""
    
    # Wait for transaction to be processed
    echo "Waiting for transaction to be processed..."
    sleep 3
    
    # Get transaction receipt
    echo "Getting transaction receipt..."
    RECEIPT_RESPONSE=$(curl -s -X POST $BASE_URL \
        -H "Content-Type: application/json" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getTransactionReceipt\",\"params\":[\"$TX_HASH\"],\"id\":1}")
    
    # Pretty print the receipt for debugging
    echo "Receipt response:"
    echo "$RECEIPT_RESPONSE" | jq '.'
    echo ""
    
    # Extract the actual deployed contract address from the NewTransaction event
    # The event signature is NewTransaction(bytes32,address,address)
    # Topics: [event_signature, txId, recipient, activator]
    # The recipient (topics[2]) is the deployed contract address
    CONTRACT_ADDRESS=$(echo "$RECEIPT_RESPONSE" | jq -r '.result.logs[0].topics[2] // empty')
    
    # Remove leading zeros and format as proper address if found
    if [ -n "$CONTRACT_ADDRESS" ] && [ "$CONTRACT_ADDRESS" != "null" ] && [ "$CONTRACT_ADDRESS" != "empty" ]; then
        # Convert from 32-byte hex to address (remove 0x prefix, take last 40 chars, add 0x prefix back)
        CONTRACT_ADDRESS="0x${CONTRACT_ADDRESS: -40}"
    else
        # Fallback: try the contractAddress field
        CONTRACT_ADDRESS=$(echo "$RECEIPT_RESPONSE" | jq -r '.result.contractAddress // empty')
    fi
    
    if [ -z "$CONTRACT_ADDRESS" ] || [ "$CONTRACT_ADDRESS" = "null" ] || [ "$CONTRACT_ADDRESS" = "empty" ]; then
        echo "❌ Error: Could not extract contract address from receipt"
        echo "Please check the receipt response above for the contract address"
        exit 1
    else
        echo "====================================="
        echo "✅ Contract successfully deployed!"
        echo "====================================="
        echo ""
        echo "Contract Address: $CONTRACT_ADDRESS"
        echo "Transaction Hash: $TX_HASH"
        echo ""
        
        # Export for use in other scripts
        export DEPLOYED_CONTRACT_ADDRESS="$CONTRACT_ADDRESS"
        export DEPLOYMENT_TX_HASH="$TX_HASH"
        
        # Save to file for later use
        echo "$CONTRACT_ADDRESS" > "$SCRIPT_DIR/.last_deployed_contract"
        echo "$TX_HASH" > "$SCRIPT_DIR/.last_deployment_tx"
        
        echo "Contract address saved to: $SCRIPT_DIR/.last_deployed_contract"
        echo ""
    fi
fi

# Now call the contract using gen_call
echo ""
echo "Step 2: Calling Contract with gen_call"
echo "======================================="
echo ""

# Wait for contract to be fully ready
echo "Waiting 5 seconds for contract to be fully ready..."
sleep 5

# From address (same as deployment)
FROM_ADDRESS="0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205"

# The data field for get_have_coin method call
# Based on the UI example: 0xd8960e066d6574686f646c6765745f686176655f636f696e00
# This encodes the method name "get_have_coin"
CALL_DATA="0xd8960e066d6574686f646c6765745f686176655f636f696e00"

echo "Calling contract method: get_have_coin"
echo "Contract Address: $CONTRACT_ADDRESS"
echo "From Address: $FROM_ADDRESS"
echo ""

# Function to make a gen_call
make_gen_call() {
    local VARIANT=$1
    local VARIANT_DESC=$2
    
    echo "Making gen_call with transaction_hash_variant: $VARIANT_DESC"
    
    # Prepare the gen_call request
    # Note: params must be an array containing a single object
    GEN_CALL_REQUEST=$(cat <<EOF
{
    "jsonrpc": "2.0",
    "method": "gen_call",
    "params": [
        {
            "data": "$CALL_DATA",
            "from": "$FROM_ADDRESS",
            "to": "$CONTRACT_ADDRESS",
            "transaction_hash_variant": "$VARIANT",
            "type": "read"
        }
    ],
    "id": 1
}
EOF
)
    
    echo "Request:"
    echo "$GEN_CALL_REQUEST" | jq '.'
    echo ""
    
    # Send the gen_call request
    GEN_CALL_RESPONSE=$(curl -s -X POST $BASE_URL \
        -H "Content-Type: application/json" \
        -d "$GEN_CALL_REQUEST")
    
    echo "Response:"
    echo "$GEN_CALL_RESPONSE" | jq '.'
    echo ""
    
    # Extract and decode the result
    RESULT=$(echo "$GEN_CALL_RESPONSE" | jq -r '.result // empty')
    
    if [ -z "$RESULT" ] || [ "$RESULT" = "empty" ]; then
        echo "❌ Error: Could not get result from gen_call"
        ERROR_MSG=$(echo "$GEN_CALL_RESPONSE" | jq -r '.error.message // empty')
        if [ -n "$ERROR_MSG" ] && [ "$ERROR_MSG" != "empty" ]; then
            echo "Error message: $ERROR_MSG"
        fi
    else
        echo "✅ gen_call successful!"
        echo "Result (hex): $RESULT"
        
        # Try to decode the result (it appears to be hex-encoded)
        # The UI shows "08" which is likely a boolean value (true/false)
        if [ "$RESULT" = "08" ]; then
            echo "Result (decoded): true (have_coin = true)"
        elif [ "$RESULT" = "00" ]; then
            echo "Result (decoded): false (have_coin = false)"
        else
            echo "Result (decoded): Unknown value"
        fi
    fi
    
    echo "-------------------------------------"
    echo ""
}

# Make two gen_call requests as shown in the UI example
echo "Test 1: gen_call with latest-nonfinal"
echo "--------------------------------------"
make_gen_call "latest-nonfinal" "latest-nonfinal"

echo "Test 2: gen_call with latest-final"
echo "-----------------------------------"
make_gen_call "latest-final" "latest-final"

echo ""
echo "====================================="
echo "✅ Script completed successfully!"
echo "====================================="
echo ""
echo "Summary:"
echo "- Contract deployed at: $CONTRACT_ADDRESS"
echo "- Transaction hash: $TX_HASH"
echo "- Method called: get_have_coin"
echo "- gen_call executed with both latest-nonfinal and latest-final variants"
echo ""
echo "You can use the contract address for further testing:"
echo "  export CONTRACT_ADDRESS=$CONTRACT_ADDRESS"