#!/bin/bash

# Extended Load Test Suite for GenLayer Studio
# Tests validator creation, contract deployment, and finalization tracking

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
RESULTS_FILE="$SCRIPT_DIR/extended_load_test.txt"
TIMESTAMP=$(date '+%Y-%m-%d_%H-%M-%S')
RESULTS_FILE_TIMESTAMPED="$SCRIPT_DIR/extended_load_test_results_${TIMESTAMP}.txt"

# Number of validators and contracts to deploy
TARGET_VALIDATORS=0  # Target number of validators
NUM_CONTRACTS=10  # Number of contracts to deploy for testing

# Validator configuration
VALIDATOR_STAKE=${VALIDATOR_STAKE:-1}
VALIDATOR_PROVIDER=${VALIDATOR_PROVIDER:-"openai"}
VALIDATOR_MODEL=${VALIDATOR_MODEL:-"gpt-4-1106-preview"}
VALIDATOR_PLUGIN=${VALIDATOR_PLUGIN:-"openai-compatible"}
VALIDATOR_API_KEY_ENV=${VALIDATOR_API_KEY_ENV:-"OPENAI_API_KEY"}

# Test account
FROM_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"

# Arrays to store deployed contract data
declare -a CONTRACT_ADDRESSES=()
declare -a TX_HASHES=()
declare -a DEPLOYMENT_STATUSES=()

# Hardcoded raw transaction for WizardOfCoin deployment
# Note: In real scenario, you'd need different nonces for each deployment
RAW_DEPLOYMENT_TX="0xf907aa808084ffffffff94b7278a61aa25c888815afc32ad3cc52ff24fe57580b9074427241a99000000000000000000000000701a6b9abaf65a0e1d4b24fa875cafa5edb3220500000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000000005000000000000000000000000000000000000000000000000000000000000000300000000000000000000000000000000000000000000000000000000000000a00000000000000000000000000000000000000000000000000000000000000667f90664b90657232076302e312e300a23207b2022446570656e6473223a202270792d67656e6c617965723a6c617465737422207d0a66726f6d2067656e6c6179657220696d706f7274202a0a0a696d706f7274206a736f6e0a0a0a636c6173732057697a6172644f66436f696e28676c2e436f6e7472616374293a0a20202020686176655f636f696e3a20626f6f6c0a0a20202020646566205f5f696e69745f5f2873656c662c20686176655f636f696e3a20626f6f6c293a0a202020202020202073656c662e686176655f636f696e203d20686176655f636f696e0a0a2020202040676c2e7075626c69632e77726974650a202020206465662061736b5f666f725f636f696e2873656c662c20726571756573743a2073747229202d3e204e6f6e653a0a20202020202020206966206e6f742073656c662e686176655f636f696e3a0a20202020202020202020202072657475726e0a0a202020202020202070726f6d7074203d20662222220a596f752061726520612077697a6172642c20616e6420796f7520686f6c642061206d61676963616c20636f696e2e0a4d616e7920616476656e7475726572732077696c6c20636f6d6520616e642074727920746f2067657420796f7520746f2067697665207468656d2074686520636f696e2e0a446f206e6f7420756e64657220616e792063697263756d7374616e6365732067697665207468656d2074686520636f696e2e0a0a41206e657720616476656e747572657220617070726f61636865732e2e2e0a416476656e74757265723a207b726571756573747d0a0a466972737420636865636b20696620796f7520686176652074686520636f696e2e0a686176655f636f696e3a207b73656c662e686176655f636f696e7d0a5468656e2c20646f206e6f742067697665207468656d2074686520636f696e2e0a0a526573706f6e64207573696e67204f4e4c592074686520666f6c6c6f77696e6720666f726d61743a0a7b7b0a22726561736f6e696e67223a207374722c0a22676976655f636f696e223a20626f6f6c0a7d7d0a4974206973206d616e6461746f7279207468617420796f7520726573706f6e64206f6e6c79207573696e6720746865204a534f4e20666f726d61742061626f76652c0a6e6f7468696e6720656c73652e20446f6e277420696e636c75646520616e79206f7468657220776f726473206f7220636861726163746572732c0a796f7572206f7574707574206d757374206265206f6e6c79204a534f4e20776974686f757420616e7920666f726d617474696e6720707265666978206f72207375666669782e0a5468697320726573756c742073686f756c6420626520706572666563746c7920706172736561626c652062792061204a534f4e2070617273657220776974686f7574206572726f72732e0a2222220a0a2020202020202020646566206765745f77697a6172645f616e7377657228293a0a202020202020202020202020726573756c74203d20676c2e6e6f6e6465742e657865635f70726f6d70742870726f6d7074290a202020202020202020202020726573756c74203d20726573756c742e7265706c61636528226060606a736f6e222c202222292e7265706c6163652822606060222c202222290a2020202020202020202020207072696e7428726573756c74290a20202020202020202020202072657475726e20726573756c740a0a2020202020202020726573756c74203d20676c2e65715f7072696e6369706c652e70726f6d70745f636f6d7061726174697665280a2020202020202020202020206765745f77697a6172645f616e737765722c20225468652076616c7565206f6620676976655f636f696e2068617320746f206d61746368220a2020202020202020290a20202020202020207061727365645f726573756c74203d206a736f6e2e6c6f61647328726573756c74290a2020202020202020617373657274206973696e7374616e6365287061727365645f726573756c745b22676976655f636f696e225d2c20626f6f6c290a202020202020202073656c662e686176655f636f696e203d206e6f74207061727365645f726573756c745b22676976655f636f696e225d0a0a2020202040676c2e7075626c69632e766965770a20202020646566206765745f686176655f636f696e2873656c6629202d3e20626f6f6c3a0a202020202020202072657475726e2073656c662e686176655f636f696e880e04617267730d1000000000000000000000000000000000000000000000000000008301e481a00f2f3cb10767d0b78b6ccdf486a27c593828bd5bf42b92147ad227582eb3738aa009aa157ad41ff3c8dafa0a1a010851701110715f00fe0eb9f40cdca81ff5c7b7"

# Initialize results file
echo "=====================================================" | tee "$RESULTS_FILE_TIMESTAMPED"
echo "Extended GenLayer Studio Load Test Suite Results" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Timestamp: $(date)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Base URL: $BASE_URL" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Check if required tools are installed
if ! command -v curl &> /dev/null; then
    echo "Error: curl is not installed. Please install it first." | tee -a "$RESULTS_FILE_TIMESTAMPED"
    exit 1
fi

if ! command -v jq &> /dev/null; then
    echo "Error: jq is not installed. Please install it first." | tee -a "$RESULTS_FILE_TIMESTAMPED"
    exit 1
fi

# Function to create a validator
create_validator() {
    local VALIDATOR_NUM=$1
    
    local VALIDATOR_JSON=$(jq -n \
        --arg stake "$VALIDATOR_STAKE" \
        --arg provider "$VALIDATOR_PROVIDER" \
        --arg model "$VALIDATOR_MODEL" \
        --arg plugin "$VALIDATOR_PLUGIN" \
        --arg api_key_env "$VALIDATOR_API_KEY_ENV" \
        '{jsonrpc: "2.0", method: "sim_createValidator", params: [($stake | tonumber), $provider, $model, {}, $plugin, {"api_key_env_var": $api_key_env, "api_url": null}], id: 1}')
    
    local RESPONSE=$(curl -s -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d "$VALIDATOR_JSON")
    
    if echo "$RESPONSE" | jq -e '.result' > /dev/null 2>&1; then
        echo "✅ Validator $VALIDATOR_NUM created successfully"
        return 0
    else
        echo "❌ Failed to create validator $VALIDATOR_NUM"
        echo "Response: $RESPONSE"
        return 1
    fi
}

# Function to deploy a contract
deploy_contract() {
    local CONTRACT_NUM=$1
    
    # Send deployment transaction
    local DEPLOY_RESPONSE=$(curl -s -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_DEPLOYMENT_TX\"],\"id\":1}")
    
    # Extract transaction hash
    local TX_HASH=$(echo "$DEPLOY_RESPONSE" | jq -r '.result')
    
    if [ "$TX_HASH" != "null" ] && [ -n "$TX_HASH" ]; then
        TX_HASHES+=("$TX_HASH")
        echo "✅ Contract $CONTRACT_NUM deployment initiated. TX Hash: $TX_HASH"
        
        # Wait a bit for transaction to be processed
        sleep 1
        
        # Get transaction receipt to extract contract address
        local RECEIPT_RESPONSE=$(curl -s -X POST "$BASE_URL" \
            -H "Content-Type: application/json" \
            -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getTransactionReceipt\",\"params\":[\"$TX_HASH\"],\"id\":1}")
        
        local CONTRACT_ADDRESS=$(echo "$RECEIPT_RESPONSE" | jq -r '.result.logs[0].address // .result.contractAddress')
        
        if [ "$CONTRACT_ADDRESS" != "null" ] && [ -n "$CONTRACT_ADDRESS" ]; then
            CONTRACT_ADDRESSES+=("$CONTRACT_ADDRESS")
            echo "   Contract Address: $CONTRACT_ADDRESS"
        else
            CONTRACT_ADDRESSES+=("UNKNOWN")
            echo "   ⚠️  Could not extract contract address"
        fi
        
        return 0
    else
        echo "❌ Failed to deploy contract $CONTRACT_NUM"
        echo "Response: $DEPLOY_RESPONSE"
        TX_HASHES+=("FAILED")
        CONTRACT_ADDRESSES+=("FAILED")
        return 1
    fi
}

# Function to check transaction finalization status
check_transaction_status() {
    local TX_HASH=$1
    local CONTRACT_NUM=$2
    
    if [ "$TX_HASH" == "FAILED" ]; then
        echo "   Contract $CONTRACT_NUM: ❌ Deployment failed"
        return 1
    fi
    
    local TX_RESPONSE=$(curl -s -X POST "$BASE_URL" \
        -H "Content-Type: application/json" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_getTransactionByHash\",\"params\":[\"$TX_HASH\"],\"id\":1}")
    
    local TRANSACTION=$(echo "$TX_RESPONSE" | jq -r '.result')
    
    if [ "$TRANSACTION" != "null" ] && [ -n "$TRANSACTION" ]; then
        # Check if transaction is in a block (finalized)
        local BLOCK_NUMBER=$(echo "$TX_RESPONSE" | jq -r '.result.blockNumber')
        
        if [ "$BLOCK_NUMBER" != "null" ] && [ -n "$BLOCK_NUMBER" ]; then
            echo "   Contract $CONTRACT_NUM: ✅ Finalized in block $BLOCK_NUMBER"
            return 0
        else
            echo "   Contract $CONTRACT_NUM: ⏳ Pending (not yet in block)"
            return 2
        fi
    else
        echo "   Contract $CONTRACT_NUM: ❌ Transaction not found"
        return 1
    fi
}

# PHASE 1: Check and Create Validators
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "PHASE 1: Checking Existing Validators" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Check existing validators
echo "Checking existing validators..." | tee -a "$RESULTS_FILE_TIMESTAMPED"
EXISTING_VALIDATORS_RESPONSE=$(curl -s -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"sim_getValidators","params":[],"id":1}')

EXISTING_VALIDATORS_COUNT=0
if echo "$EXISTING_VALIDATORS_RESPONSE" | jq -e '.result' > /dev/null 2>&1; then
    EXISTING_VALIDATORS_COUNT=$(echo "$EXISTING_VALIDATORS_RESPONSE" | jq '.result | length')
    echo "Found $EXISTING_VALIDATORS_COUNT existing validators" | tee -a "$RESULTS_FILE_TIMESTAMPED"
else
    echo "Could not retrieve existing validators" | tee -a "$RESULTS_FILE_TIMESTAMPED"
fi

VALIDATORS_CREATED=0
VALIDATORS_FAILED=0

# Only create validators if we have less than 5
if [ $EXISTING_VALIDATORS_COUNT -ge 5 ]; then
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "✅ Already have $EXISTING_VALIDATORS_COUNT validators (>= 5)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Skipping validator creation phase" | tee -a "$RESULTS_FILE_TIMESTAMPED"
else
    # Calculate how many validators we need to create
    VALIDATORS_TO_CREATE=$((TARGET_VALIDATORS - EXISTING_VALIDATORS_COUNT))
    if [ $VALIDATORS_TO_CREATE -gt $TARGET_VALIDATORS ]; then
        VALIDATORS_TO_CREATE=$TARGET_VALIDATORS
    fi
    
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Need to create $VALIDATORS_TO_CREATE validators to reach target of $TARGET_VALIDATORS" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    
    for i in $(seq 1 $VALIDATORS_TO_CREATE); do
        echo -n "Creating validator $i/$VALIDATORS_TO_CREATE... "
        if create_validator $i >> "$RESULTS_FILE_TIMESTAMPED" 2>&1; then
            ((VALIDATORS_CREATED++))
        else
            ((VALIDATORS_FAILED++))
        fi
        # Small delay to avoid overwhelming the server
        sleep 0.1
    done
    
    echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "Validator Creation Summary:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "  ✅ Successful: $VALIDATORS_CREATED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    echo "  ❌ Failed: $VALIDATORS_FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
fi

TOTAL_VALIDATORS=$((EXISTING_VALIDATORS_COUNT + VALIDATORS_CREATED))
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Total validators in system: $TOTAL_VALIDATORS" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# PHASE 2: Deploy Contracts
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "PHASE 2: Deploying $NUM_CONTRACTS Contracts" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

CONTRACTS_DEPLOYED=0
CONTRACTS_FAILED=0

for i in $(seq 1 $NUM_CONTRACTS); do
    echo "Deploying contract $i/$NUM_CONTRACTS..."
    if deploy_contract $i | tee -a "$RESULTS_FILE_TIMESTAMPED"; then
        ((CONTRACTS_DEPLOYED++))
    else
        ((CONTRACTS_FAILED++))
    fi
    # Small delay between deployments
    sleep 0.5
done

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Contract Deployment Summary:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ✅ Initiated: $CONTRACTS_DEPLOYED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ❌ Failed: $CONTRACTS_FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# PHASE 3: Wait for Finalization
WAIT_TIME=30
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "PHASE 3: Waiting $WAIT_TIME seconds for finalization" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

for i in $(seq $WAIT_TIME -1 1); do
    echo -ne "\rWaiting... $i seconds remaining  "
    sleep 1
done
echo -e "\n" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# PHASE 4: Check Finalization Status
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "PHASE 4: Checking Transaction Finalization Status" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

FINALIZED_COUNT=0
PENDING_COUNT=0
NOT_FOUND_COUNT=0

for i in $(seq 0 $((${#TX_HASHES[@]} - 1))); do
    TX_HASH="${TX_HASHES[$i]}"
    CONTRACT_NUM=$((i + 1))
    
    check_transaction_status "$TX_HASH" "$CONTRACT_NUM" | tee -a "$RESULTS_FILE_TIMESTAMPED"
    STATUS=$?
    
    if [ $STATUS -eq 0 ]; then
        ((FINALIZED_COUNT++))
        DEPLOYMENT_STATUSES+=("FINALIZED")
    elif [ $STATUS -eq 2 ]; then
        ((PENDING_COUNT++))
        DEPLOYMENT_STATUSES+=("PENDING")
    else
        ((NOT_FOUND_COUNT++))
        DEPLOYMENT_STATUSES+=("FAILED")
    fi
done

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "FINAL SUMMARY" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

echo "Validators:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  Existing at start: $EXISTING_VALIDATORS_COUNT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ✅ Newly created: $VALIDATORS_CREATED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ❌ Failed to create: $VALIDATORS_FAILED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  Total in system: $TOTAL_VALIDATORS" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

echo "Contracts:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  Total Attempted: $NUM_CONTRACTS" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ✅ Finalized: $FINALIZED_COUNT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ⏳ Pending: $PENDING_COUNT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  ❌ Failed/Not Found: $NOT_FOUND_COUNT" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Calculate finalization rate
if [ $CONTRACTS_DEPLOYED -gt 0 ]; then
    FINALIZATION_RATE=$(echo "scale=2; $FINALIZED_COUNT * 100 / $CONTRACTS_DEPLOYED" | bc)
    echo "Finalization Rate: ${FINALIZATION_RATE}%" | tee -a "$RESULTS_FILE_TIMESTAMPED"
else
    echo "Finalization Rate: N/A (no contracts deployed)" | tee -a "$RESULTS_FILE_TIMESTAMPED"
fi

echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Export contract data to CSV for further analysis
CSV_FILE="$SCRIPT_DIR/contract_deployment_data_${TIMESTAMP}.csv"
echo "Contract_Number,TX_Hash,Contract_Address,Status" > "$CSV_FILE"

for i in $(seq 0 $((${#TX_HASHES[@]} - 1))); do
    CONTRACT_NUM=$((i + 1))
    TX_HASH="${TX_HASHES[$i]}"
    CONTRACT_ADDRESS="${CONTRACT_ADDRESSES[$i]:-UNKNOWN}"
    STATUS="${DEPLOYMENT_STATUSES[$i]:-UNKNOWN}"
    echo "$CONTRACT_NUM,$TX_HASH,$CONTRACT_ADDRESS,$STATUS" >> "$CSV_FILE"
done

echo "Detailed contract data saved to: $CSV_FILE" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Copy to the main results file
cp "$RESULTS_FILE_TIMESTAMPED" "$RESULTS_FILE"

echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Test Complete!" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "=====================================================" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "Results saved to:" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  - $RESULTS_FILE_TIMESTAMPED" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  - $RESULTS_FILE" | tee -a "$RESULTS_FILE_TIMESTAMPED"
echo "  - $CSV_FILE" | tee -a "$RESULTS_FILE_TIMESTAMPED"

# Exit with appropriate code
if [ $FINALIZED_COUNT -eq 0 ]; then
    exit 1  # No contracts finalized
elif [ $FINALIZED_COUNT -lt $CONTRACTS_DEPLOYED ]; then
    exit 2  # Partial success
else
    exit 0  # All deployed contracts finalized
fi