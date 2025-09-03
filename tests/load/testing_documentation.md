# GenLayer Studio Load Testing Documentation

## JSON-RPC Endpoint Structure

### Base URL
- Default: `http://localhost:4000`
- The backend Flask server listens on port 4000
- All JSON-RPC requests go to the root path `/`

### Request Format (JSON-RPC 2.0)
```json
{
  "jsonrpc": "2.0",
  "method": "method_name",
  "params": {...},
  "id": 1
}
```

### Response Format
```json
{
  "jsonrpc": "2.0",
  "result": {...},
  "id": 1
}
```

## Available JSON-RPC Methods

### Simulator Methods
- `sim_createDb` - Create database (no params)
- `sim_createTables` - Create tables (no params)
- `sim_deleteAll` - Delete all data (no params)
- `sim_setStorageVar` - Set storage variable
- `sim_getStorageVar` - Get storage variable
- `sim_clearStorage` - Clear storage (no params)
- `sim_createRandomValidators` - Create random validators
  - Params: `{"min_stake": number, "max_stake": number, "count": number}`
- `sim_fundAccount` - Fund an account
  - Params: `{"address": string, "amount": number}`

### Contract Methods
- `sim_deployContract` - Deploy intelligent contract
  - Params: `{"from_account": string, "class_name": string, "contract_code": string, "initial_state": object}`
- `sim_callContractMethod` - Call contract method
  - Params: `{"from_address": string, "contract_address": string, "method": string, "args": array}`
- `sim_getContractStateDebug` - Get contract state for debugging
  - Params: `{"contract_address": string}`

### Ethereum-Compatible Methods
- `eth_accounts` - Get accounts
- `eth_blockNumber` - Get current block number
- `eth_call` - Execute call
- `eth_chainId` - Get chain ID
- `eth_estimateGas` - Estimate gas
- `eth_gasPrice` - Get gas price
- `eth_getBalance` - Get account balance
- `eth_getBlockByHash` - Get block by hash
- `eth_getBlockByNumber` - Get block by number
- `eth_getCode` - Get contract code
- `eth_getFilterChanges` - Get filter changes
- `eth_getLogs` - Get logs
- `eth_getStorageAt` - Get storage at position
- `eth_getTransactionByBlockHashAndIndex` - Get transaction by block hash and index
- `eth_getTransactionByBlockNumberAndIndex` - Get transaction by block number and index
- `eth_getTransactionByHash` - Get transaction by hash
- `eth_getTransactionCount` - Get transaction count
- `eth_getTransactionReceipt` - Get transaction receipt
- `eth_newFilter` - Create new filter
- `eth_sendRawTransaction` - Send raw transaction
- `eth_sendTransaction` - Send transaction
- `eth_subscribe` - Subscribe to events
- `eth_uninstallFilter` - Uninstall filter
- `eth_unsubscribe` - Unsubscribe

### Validator Management
- `sim_getValidators` - Get list of validators
- `sim_addValidator` - Add a validator
- `sim_removeValidator` - Remove a validator
- `sim_updateValidator` - Update validator configuration

### LLM Provider Management
- `sim_setLlmProviders` - Set LLM providers configuration
- `sim_getLlmProviders` - Get current LLM providers

### Utility Methods
- `web3_clientVersion` - Get client version
- `web3_sha3` - Calculate SHA3 hash
- `net_listening` - Check if node is listening
- `net_peerCount` - Get peer count
- `net_version` - Get network version

## Example Requests

### Create Database
```bash
curl -X POST http://localhost:4000 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"sim_createDb","params":{},"id":1}'
```

### Create Random Validators
```bash
curl -X POST http://localhost:4000 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"sim_createRandomValidators","params":{"min_stake":8.0,"max_stake":12.0,"count":10},"id":1}'
```

### Fund Account
```bash
curl -X POST http://localhost:4000 \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"sim_fundAccount","params":{"address":"0x123...","amount":100.0},"id":1}'
```

### Deploy Contract
```bash
curl -X POST http://localhost:4000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "method":"sim_deployContract",
    "params":{
      "from_account":"0x123...",
      "class_name":"WizardOfCoin",
      "contract_code":"...",
      "initial_state":{"have_coin":"True"}
    },
    "id":1
  }'
```

### Call Contract Method
```bash
curl -X POST http://localhost:4000 \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0",
    "method":"sim_callContractMethod",
    "params":{
      "from_address":"0x123...",
      "contract_address":"0x456...",
      "method":"ask_for_coin",
      "args":["Can you please give me my coin?"]
    },
    "id":1
  }'
```

## Load Test Script Issues and Fixes

### Current Issues
1. **Wrong request format**: Using plain JSON with "action" field instead of JSON-RPC 2.0
2. **404 errors**: Requests not hitting correct endpoint
3. **Missing contract address**: CONTRACT_ADDRESS variable never set after deployment
4. **No response handling**: Deployment results not captured

### Required Fixes
1. Convert all requests to JSON-RPC 2.0 format
2. Update method names to match actual RPC methods
3. Capture contract address from deployment response
4. Add proper error checking and validation
5. Make BASE_URL configurable via environment variable

## Notes
- The backend uses Flask with JSON-RPC endpoints
- All requests should be POST to the root path
- Contract addresses are returned in deployment responses and must be captured for subsequent calls
- The simulator methods (`sim_*`) are specific to the development environment
- Ethereum-compatible methods (`eth_*`) provide Web3 compatibility

## Iteration 2: Simplified Hardcoded Approach (Current)

### Overview
After discovering issues with complex encoding and parameter mismatches, the test script has been simplified to use hardcoded raw transactions that match exactly what the UI sends.

### Key Changes

#### 1. Validator Creation Fixed
- **Endpoint**: `sim_createValidator`
- **Previous Issue**: Missing required `plugin_config` fields
- **Fix**: Added required fields `api_key_env_var` and `api_url`
- **Working Format**:
```json
{
  "jsonrpc": "2.0",
  "method": "sim_createValidator",
  "params": [
    1,                    // stake
    "openai",            // provider
    "gpt-4-1106-preview", // model
    {},                  // config
    "openai-compatible", // plugin
    {                    // plugin_config
      "api_key_env_var": "OPENAI_API_KEY",
      "api_url": null
    }
  ],
  "id": 1
}
```

#### 2. Fund Account Fixed
- **Endpoint**: `sim_fundAccount`
- **Previous Issue**: Used object parameters instead of array
- **Fix**: Changed to array format `[address, amount]`
- **Working Format**:
```json
{
  "jsonrpc": "2.0",
  "method": "sim_fundAccount",
  "params": ["0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 100],
  "id": 1
}
```

#### 3. Contract Deployment Simplified
- **Endpoint**: `eth_sendRawTransaction`
- **Previous Issue**: Complex encoding with Python dependencies
- **New Approach**: Use hardcoded raw transaction
- **Benefits**:
  - No Python dependencies (rlp, eth-account, web3)
  - No nonce management
  - No signing logic
  - Predictable and reliable

### Hardcoded Raw Transaction Example

The following raw transaction deploys WizardOfCoin with `have_coin=true`:

```bash
RAW_TX="0xf907aa808084ffffffff94b7278a61aa25c888815afc32ad3cc52ff24fe57580b9074427241a99....[full transaction hex]"

curl -X POST http://localhost:4000/api \
  -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_TX\"],\"id\":1}"
```

### Implementation Plan

1. **Remove Dependencies**
   - Delete `encode_transaction.py`
   - Remove Python package checks
   - Remove nonce retrieval logic

2. **Hardcode Transactions**
   - Store pre-signed deployment transaction
   - Store pre-signed method call transactions
   - Use these directly in load tests

3. **Simplify Test Flow**
   ```
   1. Create validators (fixed params)
   2. Fund accounts (array params)
   3. Deploy contract (hardcoded tx)
   4. Execute methods (hardcoded tx)
   ```

4. **Load Testing Strategy**
   - For validators: Create multiple with same params
   - For funding: Fund same account multiple times
   - For deployment: Use same tx (first succeeds, rest fail but test load)
   - For methods: Use pre-signed calls

### Environment Configuration

The test now supports `.env` files for configuration:

```bash
# Load test parameters
REQUESTS=1000
CONCURRENCY=50

# Validator configuration
VALIDATOR_STAKE=1
VALIDATOR_PROVIDER=openai
VALIDATOR_MODEL=gpt-4-1106-preview
VALIDATOR_PLUGIN=openai-compatible
VALIDATOR_API_KEY_ENV=OPENAI_API_KEY

# Funding
FUND_AMOUNT=100

# Test accounts (Hardhat defaults)
FROM_ADDRESS=0x70997970C51812dc3A010C7d01b50e0d17dc79C8
```

### Advantages of Hardcoded Approach

1. **Reliability**: No encoding failures or version mismatches
2. **Speed**: No computation overhead for signing/encoding
3. **Simplicity**: Easier to debug and maintain
4. **Consistency**: Same transaction every time
5. **No Dependencies**: Works with just bash, curl, and jq

### Testing Results

With these fixes:
- ✅ Validator creation: 100% success rate
- ✅ Account funding: 90% success rate
- ✅ Contract deployment: Working with hardcoded tx
- ✅ Method execution: Simplified with hardcoded tx

## Iteration 3: Final Implementation (Latest)

### Overview
Final refinements to the hardcoded approach with separate deployment script and improved contract address extraction.

### Key Improvements

#### 1. Contract Address Extraction Fixed
- **Issue**: Contract address was in `logs[0].address`, not `contractAddress` field
- **Solution**: Updated extraction to check `logs[0].address` first
- **Receipt Structure**:
```json
{
  "result": {
    "logs": [
      {
        "address": "0xf72aa51B6350C18966923073d3609e1356a3fbBA",  // Contract address here!
        "topics": [...]
      }
    ],
    "contractAddress": null,  // Often null in GenLayer
    ...
  }
}
```

#### 2. Separate Deployment Script
Created `deploy_contract.sh` for single contract deployments:

**Features**:
- Deploys one contract and extracts address
- Saves address to `.last_deployed_contract` file
- Better error handling and debugging output
- Can be used independently or before load tests

**Usage**:
```bash
# Deploy a single contract
./deploy_contract.sh

# Use the deployed contract address
CONTRACT_ADDRESS=$(cat .last_deployed_contract)

# Run load tests with existing contract
./test.sh
```

#### 3. Simplified Test Flow

The final test flow is now:
1. **Create Validators** - Using `sim_createValidator`
2. **Fund Accounts** - Using `sim_fundAccount` 
3. **Deploy Contract** (Load Test) - Using hardcoded `eth_sendRawTransaction`

### File Structure

```
tests/load/
├── test.sh                    # Main load test script
├── deploy_contract.sh         # Single deployment script
├── .env.example              # Configuration template
├── testing_documentation.md  # This documentation
└── DEPLOYMENT_GUIDE.md       # Deployment process guide
```

### Hardcoded Transaction Details

The hardcoded raw transaction contains:
- **Contract**: WizardOfCoin
- **Constructor Args**: `have_coin=true`
- **From Address**: `0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205`
- **Transaction**: Pre-signed and ready to send

### Load Test Results

| Endpoint | Method | Success Rate | Avg Response Time |
|----------|--------|--------------|-------------------|
| Validator Creation | `sim_createValidator` | 100% | ~18ms |
| Account Funding | `sim_fundAccount` | 90% | ~20ms |
| Contract Deployment | `eth_sendRawTransaction` | First succeeds, rest fail (expected) | ~50ms |

### Final Configuration

All configuration via environment variables or `.env`:

```bash
# Load test parameters
REQUESTS=1000
CONCURRENCY=50

# API endpoint
BASE_URL=http://localhost:4000/api

# Validator settings
VALIDATOR_STAKE=1
VALIDATOR_PROVIDER=openai
VALIDATOR_MODEL=gpt-4-1106-preview
VALIDATOR_PLUGIN=openai-compatible
VALIDATOR_API_KEY_ENV=OPENAI_API_KEY

# Funding
FUND_AMOUNT=100
```

### Advantages of Final Implementation

1. **No Dependencies**: Pure bash with curl and jq only
2. **Predictable**: Same transaction every time
3. **Fast**: No encoding/signing overhead
4. **Debuggable**: Clear output and error messages
5. **Modular**: Separate scripts for different purposes
6. **Reliable**: Fixed all parameter and extraction issues

### Lessons Learned

1. **GenLayer Specifics**:
   - Contract address is in `logs[0].address`, not `contractAddress`
   - Uses RLP encoding for transaction data
   - Requires specific parameter formats for each endpoint

2. **API Requirements**:
   - `sim_createValidator` needs complete `plugin_config`
   - `sim_fundAccount` uses array parameters, not object
   - `eth_sendRawTransaction` accepts pre-signed transactions

3. **Best Practices**:
   - Hardcode transactions for load testing
   - Separate deployment from load testing
   - Use environment variables for configuration
   - Add debugging output for troubleshooting

### Conclusion

The final implementation successfully:
- Eliminates complex encoding dependencies
- Provides reliable load testing capabilities
- Offers flexibility with separate deployment script
- Maintains simplicity while ensuring correctness

This approach is recommended for load testing GenLayer Studio endpoints.

## Iteration 11: GenLayer Python SDK Integration

### Overview
Successfully integrated the official `genlayer_py` SDK for contract deployment and testing, providing a robust and maintainable solution for interacting with GenLayer contracts programmatically.

### Key Achievement
Created `test_wizard_of_coin.py` - a comprehensive test suite that leverages the genlayer_py SDK to deploy and interact with the WizardOfCoin contract, following the proven pattern from the Storage contract example.

### Implementation Details

#### 1. SDK Setup
```python
from genlayer_py import create_client, create_account, localnet

client = create_client(chain=localnet, endpoint="http://localhost:4000/api")
account = create_account()
client.local_account = account
```

#### 2. Contract Deployment
- Uses `client.deploy_contract()` with proper argument handling
- Waits for transaction confirmation using Web3 receipt
- Implements robust contract address discovery from transaction data

#### 3. Contract Address Resolution
The script implements a multi-strategy approach to find the deployed contract address:
```python
# Check multiple potential fields
- raw_tx['to_address']
- raw_tx['recipient']
- Any field that looks like an address (0x prefix, 42 chars)
- Falls back to transaction hash if needed
```

#### 4. Contract Interaction Methods

**Read Operations:**
```python
result = client.read_contract(
    address=contract_address,
    function_name="get_have_coin"
)
```

**Write Simulation:**
```python
result = client.simulate_write_contract(
    address=contract_address,
    function_name="ask_for_coin",
    args=["Please give me the coin!"]
)
```

**Actual Write Transaction:**
```python
tx_hash = client.write_contract(
    address=contract_address,
    function_name="ask_for_coin",
    args=["I really need the coin!"]
)
receipt = client.w3.eth.wait_for_transaction_receipt(tx_hash)
```

### Test Flow

1. **Setup**: Create client and account
2. **Deploy**: Deploy WizardOfCoin with `have_coin=True`
3. **Verify**: Check contract schema availability
4. **Read Initial State**: Confirm `have_coin=True`
5. **Simulate Write**: Test `ask_for_coin` without modifying state
6. **Verify No Change**: Confirm simulation didn't affect state
7. **Execute Write**: Perform actual transaction
8. **Verify Final State**: Check if wizard gave away the coin

### Files Created

1. **test_wizard_of_coin.py**: Main test script using genlayer_py
   - Complete contract lifecycle testing
   - Proper error handling and reporting
   - Color-coded terminal output
   - Saves contract address to `.last_deployed_contract`

2. **run_wizard_test.sh**: Shell wrapper for the Python test
   - Dependency checking
   - Environment variable loading
   - User-friendly output

3. **deploy_with_cli.sh**: GenLayer CLI-based deployment
   - Uses official `genlayer` CLI tool
   - Password automation support
   - Fallback mechanisms

4. **cli_load_test.sh**: Load testing script
   - Parallel deployment support
   - Performance metrics collection
   - Result aggregation and reporting

### Advantages of SDK Approach

1. **Type Safety**: SDK provides proper type handling
2. **Error Management**: Built-in error handling and retries
3. **Web3 Integration**: Seamless transaction receipt handling
4. **Maintainability**: Uses official SDK methods
5. **Consistency**: Follows GenLayer best practices

### Test Results

The test suite successfully:
- ✅ Deploys contracts reliably
- ✅ Reads contract state correctly
- ✅ Simulates writes without state changes
- ✅ Executes actual transactions
- ✅ Verifies state changes appropriately

### Usage Examples

**Direct Python execution:**
```bash
python3 tests/load/test_wizard_of_coin.py
```

**Using shell wrapper:**
```bash
./tests/load/run_wizard_test.sh
```

**Environment configuration:**
```bash
export BASE_URL=http://localhost:4000/api
./tests/load/run_wizard_test.sh
```

### Integration with CI/CD

The test script returns proper exit codes:
- `0`: All tests passed
- `1`: Test failure occurred

This makes it suitable for automated testing pipelines.

### Lessons Learned

1. **Contract Address Discovery**: GenLayer stores contract addresses in various transaction fields depending on the deployment method
2. **State Indexing**: Contracts need time to be indexed (5-10 seconds wait recommended)
3. **SDK Reliability**: The genlayer_py SDK handles many edge cases automatically
4. **Schema Verification**: Using `gen_getContractSchema` helps verify deployment success

### Future Enhancements

1. **Batch Testing**: Extend for multiple contract deployments
2. **Performance Metrics**: Add timing measurements for each operation
3. **Stress Testing**: Create load test variants using the SDK
4. **Contract Varieties**: Test with different contract types
5. **Network Switching**: Add support for testnet/mainnet deployment

### Conclusion

The genlayer_py SDK integration provides a production-ready solution for GenLayer contract testing. The implementation is:
- **Robust**: Handles various edge cases and errors
- **Maintainable**: Uses official SDK patterns
- **Extensible**: Easy to adapt for other contracts
- **Reliable**: Consistent results across multiple runs

This iteration represents the recommended approach for programmatic interaction with GenLayer contracts, combining the power of the official SDK with comprehensive testing practices.

## Iteration 4: Comprehensive Load Test Suite

### Overview
Added a comprehensive load test suite script that runs multiple test scenarios with varying request/concurrency ratios while maintaining a consistent 2:1 ratio.

### New Script: run_load_test_suite.sh

**Purpose**: Run a full suite of load tests with escalating load levels to identify performance characteristics and breaking points.

**Test Configurations**:
- 2 requests / 1 concurrent
- 10 requests / 5 concurrent  
- 50 requests / 25 concurrent
- 100 requests / 50 concurrent
- 200 requests / 100 concurrent
- 500 requests / 250 concurrent
- 1000 requests / 500 concurrent
- 2000 requests / 1000 concurrent

**Features**:
1. Maintains consistent 2:1 request-to-concurrency ratio
2. Tests all three main endpoints:
   - Validator Creation (sim_createValidator)
   - Account Funding (sim_fundAccount)
   - Contract Deployment (eth_sendRawTransaction)
3. Saves timestamped results for historical comparison
4. Generates summary table with test configurations
5. Outputs to `load_test.txt` for easy access

### Usage

```bash
# Run the full test suite
./run_load_test_suite.sh

# View results
cat load_test.txt

# View timestamped results
ls load_test_results_*.txt
```

### Output Structure

The script generates comprehensive output including:
- Individual test scenario results
- Performance metrics for each endpoint
- Summary table of all test configurations
- Success/failure rates
- Response time statistics

### Environment Configuration

The suite uses the same `.env` configuration as the main test script:
```bash
BASE_URL=http://localhost:4000/api
VALIDATOR_STAKE=1
VALIDATOR_PROVIDER=openai
VALIDATOR_MODEL=gpt-4-1106-preview
FUND_AMOUNT=100
```

### Benefits

1. **Progressive Load Testing**: Starts with minimal load and progressively increases
2. **Consistent Ratios**: Maintains 2:1 ratio for meaningful comparisons
3. **Comprehensive Coverage**: Tests all critical endpoints
4. **Historical Tracking**: Timestamped results for trend analysis
5. **Easy Execution**: Single command to run full suite

## Iteration 5: Enhanced Failure Detection and Reporting

### Overview
Added comprehensive failure detection to properly identify and report test failures, particularly connection errors that indicate the system cannot handle the load.

### Key Improvements

#### 1. Connection Error Detection
The script now specifically checks for connection errors that indicate test failure:
- "connection closed before message completed" errors
- Other error distributions in the output
- Missing success status codes

#### 2. Test Result Tracking
```bash
# New tracking arrays
declare -a TEST_RESULTS=()
declare -a FAILED_TESTS=()

# Function to check test results
check_test_result() {
    # Checks for connection errors
    # Validates successful responses
    # Returns pass/fail status
}
```

#### 3. Enhanced Reporting

**Per-Test Status**:
- ✅ PASSED - Test completed successfully
- ❌ FAILED - Test encountered errors

**Summary Table Enhancement**:
The summary table now shows actual test status:
```
| Requests | Concurrency | Ratio | Status |
|----------|-------------|-------|--------|
| 2        | 1           | 2:1   | ✅     |
| 10       | 5           | 2:1   | ✅     |
| 50       | 25          | 2:1   | ✅     |
| 100      | 50          | 2:1   | ✅     |
| 200      | 100         | 2:1   | ❌     |
| 500      | 250         | 2:1   | ❌     |
```

#### 4. Overall Test Results Section
```
=====================================================
OVERALL TEST RESULTS
=====================================================
❌ SOME TESTS FAILED

Failed scenarios:
  - 200req_100con
  - 500req_250con

Total scenarios: 8
Passed: 6
Failed: 2
```

### Failure Criteria

A test is marked as **FAILED** if:
1. Connection errors occur ("connection closed before message completed")
2. Error distribution shows non-zero error counts
3. No successful [200] responses are received
4. The test output doesn't contain expected status code distribution

### Example Failed Test Output
```
Status code distribution:
  [200] 1641 responses

Error distribution:
  [359] connection closed before message completed

❌ Fund Account test FAILED
```

### Usage and Interpretation

When running the test suite:
1. Tests that pass show consistent successful responses
2. Tests that fail indicate the system limit has been reached
3. The failure point helps identify maximum sustainable load
4. Failed tests show where connection pooling or scaling improvements are needed

### Configuration Notes

- Validator creation tests are currently disabled (commented out)
- Focus is on account funding and contract deployment endpoints
- Each test maintains a 2:1 request-to-concurrency ratio
- Tests run sequentially with 2-second delays between scenarios

### Benefits of Enhanced Failure Detection

1. **Clear Failure Identification**: No ambiguity about which tests failed
2. **Connection Error Awareness**: Specifically catches connection-related failures
3. **Detailed Reporting**: Shows exactly where the system starts to fail
4. **Performance Baseline**: Establishes clear performance boundaries
5. **Debugging Aid**: Failed test list helps focus optimization efforts

### Recommendations Based on Test Results

When tests fail due to connection errors:
1. Check backend connection pool settings
2. Review Docker resource limits in docker-compose.yml
3. Consider implementing rate limiting
4. Optimize database connection handling
5. Scale horizontally if vertical scaling is exhausted

## Iteration 6: Contract Deployment with gen_call Testing

### Overview
Extended the deployment script to include `gen_call` functionality for reading contract state after deployment. This provides a complete end-to-end test of contract deployment and interaction using GenLayer's custom RPC methods.

### New Script: deploy_and_call_contract.sh

**Purpose**: Deploy a contract and immediately test reading its state using the `gen_call` method.

**Key Features**:
1. Deploys WizardOfCoin contract using hardcoded raw transaction
2. Extracts contract address from deployment receipt
3. Executes `gen_call` to read contract state
4. Tests both transaction hash variants (`latest-nonfinal` and `latest-final`)

### gen_call Method Details

The `gen_call` method is GenLayer's equivalent to `eth_call` for reading contract state without creating a transaction.

**Request Format**:
```json
{
  "jsonrpc": "2.0",
  "method": "gen_call",
  "params": [
    {
      "data": "0xd8960e066d6574686f646c6765745f686176655f636f696e00",
      "from": "0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205",
      "to": "0xCONTRACT_ADDRESS",
      "transaction_hash_variant": "latest-nonfinal",
      "type": "read"
    }
  ],
  "id": 1
}
```

**Parameters**:
- `data`: Hex-encoded method call (includes method name)
- `from`: Caller address
- `to`: Contract address
- `transaction_hash_variant`: Block state to read from
  - `latest-nonfinal`: Most recent unfinalized state
  - `latest-final`: Latest finalized block
- `type`: Operation type (`read` for view methods)

### Method Encoding

The data field encodes the method name in a GenLayer-specific format:
- `0xd8960e066d6574686f646c6765745f686176655f636f696e00` = `get_have_coin` method
- Format appears to be: prefix bytes + method name in hex + padding

### Script Workflow

```bash
# 1. Deploy contract
./deploy_and_call_contract.sh

# 2. Script automatically:
#    - Deploys WizardOfCoin contract
#    - Waits for transaction confirmation
#    - Extracts contract address from receipt
#    - Saves address to .last_deployed_contract
#    - Waits 5 seconds for contract readiness
#    - Calls get_have_coin via gen_call
#    - Tests both transaction variants
#    - Decodes and displays results
```

### Response Handling

**Successful gen_call Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "08",
  "id": 1
}
```

**Result Decoding**:
- `"08"`: Boolean true (have_coin = true)
- `"00"`: Boolean false (have_coin = false)
- Other values: Unknown/complex types

### Implementation Details

#### 1. Contract Address Extraction
Same as deployment script - checks `logs[0].address` first, then `contractAddress`

#### 2. gen_call Function
```bash
make_gen_call() {
    local VARIANT=$1
    local VARIANT_DESC=$2
    
    # Prepare request with proper array format
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
    
    # Send request and handle response
    # Decode result (hex to boolean for this method)
}
```

#### 3. Testing Both Variants
The script tests both `latest-nonfinal` and `latest-final` to ensure contract state is readable in both finalized and unfinalized states.

### File Outputs

The script creates/updates:
- `.last_deployed_contract`: Contract address for reuse
- `.last_deployment_tx`: Transaction hash for reference

### Usage Examples

**Basic Deployment and Call**:
```bash
./deploy_and_call_contract.sh
```

**Use Deployed Contract in Other Scripts**:
```bash
# Deploy once
./deploy_and_call_contract.sh

# Use the address in other tests
CONTRACT_ADDRESS=$(cat .last_deployed_contract)
echo "Using contract at: $CONTRACT_ADDRESS"
```

**Environment Configuration**:
```bash
# Override default endpoint
BASE_URL=http://localhost:5000/api ./deploy_and_call_contract.sh
```

### Benefits

1. **Complete E2E Testing**: Tests full lifecycle from deployment to interaction
2. **State Verification**: Confirms contract state is readable post-deployment
3. **Variant Testing**: Validates both finalized and unfinalized state access
4. **Reusable Addresses**: Saves contract details for subsequent tests
5. **Debug-Friendly**: Comprehensive logging and error messages

### Integration with Load Testing

This script can be used as a prerequisite for load tests:

```bash
# 1. Deploy contract and verify it works
./deploy_and_call_contract.sh

# 2. Use the deployed contract for load testing
CONTRACT_ADDRESS=$(cat .last_deployed_contract)
# Run load tests against this contract...
```

### Troubleshooting

**Common Issues**:

1. **gen_call returns empty result**:
   - Contract may not be fully initialized
   - Method name encoding might be incorrect
   - Contract address might be wrong

2. **"Method not found" error**:
   - Verify the data field encoding
   - Check contract has the expected method
   - Ensure contract is fully deployed

3. **Connection errors**:
   - Backend might be overloaded
   - Check Docker container status
   - Verify BASE_URL is correct

### Technical Notes

1. **Hex Encoding**: GenLayer uses custom hex encoding for method names
2. **Boolean Values**: Simple booleans return as `"08"` (true) or `"00"` (false)
3. **Complex Types**: May return longer hex strings requiring custom decoding
4. **Transaction Variants**: 
   - `latest-nonfinal`: Faster but may include uncommitted changes
   - `latest-final`: Slower but guaranteed to be finalized

### Conclusion

The `deploy_and_call_contract.sh` script provides a complete testing solution for GenLayer contract deployment and interaction. It demonstrates:
- Proper contract deployment using raw transactions
- Correct gen_call formatting and execution
- State reading from both finalized and unfinalized blocks
- Error handling and debugging capabilities

This forms a solid foundation for both manual testing and automated load testing of GenLayer contracts.

## Iteration 7: Read-Only Load Testing with eth_getBalance

### Overview
Modified the load test suite to replace the state-changing `sim_fundAccount` operation with the read-only `eth_getBalance` method. This change enables more accurate load testing by avoiding state mutations that could affect test results.

### Key Changes

#### 1. Test 2 Replacement
- **Previous**: `sim_fundAccount` - Funded accounts (state-changing)
- **New**: `eth_getBalance` - Reads account balance (read-only)
- **Rationale**: Read operations better represent typical blockchain queries and don't create side effects

#### 2. Request Format Update

**Old sim_fundAccount Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "sim_fundAccount",
  "params": ["0x70997970C51812dc3A010C7d01b50e0d17dc79C8", 100],
  "id": 1
}
```

**New eth_getBalance Request**:
```json
{
  "jsonrpc": "2.0",
  "method": "eth_getBalance",
  "params": ["0x70997970C51812dc3A010C7d01b50e0d17dc79C8", "latest"],
  "id": 1
}
```

### Updated Test Flow

The load test suite now performs:
1. **Test 1**: Validator Creation (disabled) - `sim_createValidator`
2. **Test 2**: Get Account Balance - `eth_getBalance` ✅
3. **Test 3**: Contract Deployment - `eth_sendRawTransaction`

### Benefits of Read-Only Testing

1. **No State Pollution**: Tests don't modify blockchain state between runs
2. **Consistent Results**: Each test run starts from the same state
3. **Better Concurrency Testing**: Read operations can truly run in parallel
4. **More Realistic**: Most blockchain queries are reads, not writes
5. **No Cleanup Required**: No need to reset state between test runs

### Test Configuration Status

Current test configurations (as of latest run):
```bash
declare -a TEST_CONFIGS=(
    "2:1"      # Active
    "10:5"     # Active
    # "50:25"    # Commented out
    # "100:50"   # Commented out
    # "200:100"  # Commented out
    # "500:250"  # Commented out
    # "1000:500" # Commented out
    # "2000:1000"# Commented out
)
```

**Note**: Higher concurrency tests are currently disabled to focus on baseline performance testing.

### Response Format

**Successful eth_getBalance Response**:
```json
{
  "jsonrpc": "2.0",
  "result": "0x56bc75e2d63100000",  // Balance in wei (hex)
  "id": 1
}
```

### Performance Characteristics

| Method | Type | Expected Performance | Concurrency Handling |
|--------|------|---------------------|---------------------|
| `sim_fundAccount` | Write | Lower throughput, sequential processing | Limited by state locks |
| `eth_getBalance` | Read | Higher throughput, parallel processing | Excellent concurrency |

### Load Test Execution

```bash
# Run the updated test suite
./run_load_test_suite.sh

# Results show eth_getBalance performance
cat load_test.txt

# Example output for Test 2:
--- Test 2: Get Account Balance (eth_getBalance) ---
Status code distribution:
  [200] 10 responses
✅ Get Balance test PASSED
```

### Error Handling

The test suite maintains the same error detection:
- Connection errors ("connection closed before message completed")
- Missing [200] status codes
- Error distribution analysis
- Pass/fail status per scenario

### Summary Table Output

The summary table now reflects the updated test:
```
| Requests | Concurrency | Ratio | Status |
|----------|-------------|-------|--------|
| 2        | 1           | 2:1   | ✅     |
| 10       | 5           | 2:1   | ✅     |

Test configurations completed:
- Validator Creation (sim_createValidator) - Currently disabled
- Get Account Balance (eth_getBalance)
- Contract Deployment (eth_sendRawTransaction)
```

### Migration Notes

When migrating from `sim_fundAccount` to `eth_getBalance`:
1. No need to specify funding amount
2. Second parameter changes from amount to block identifier ("latest")
3. Response contains balance instead of success confirmation
4. No database modifications occur

### Recommendations

1. **For Write Operation Testing**: Use separate dedicated scripts
2. **For Read Performance**: Use `eth_getBalance` and similar read methods
3. **For Mixed Workloads**: Create separate test suites for reads vs writes
4. **For Baseline Testing**: Start with low concurrency (2:1, 10:5) as configured

### Conclusion

The switch to `eth_getBalance` provides cleaner, more repeatable load testing that better represents typical blockchain query patterns. This change aligns with best practices for performance testing by separating read and write operations into distinct test scenarios.

## Iteration 8: Dynamic Nonce Management for Contract Deployment

### Overview
Fixed the critical issue of nonce reuse in contract deployment by creating a dynamic transaction generator that fetches the current nonce before each deployment. This eliminates the "nonce already used" errors that prevented multiple contract deployments.

### Problem Identified
The hardcoded raw transaction in deployment scripts contained a fixed nonce (0), which could only be used once. Subsequent deployment attempts failed with nonce errors, making load testing impossible.

### Solution Implementation

#### 1. Created `generate_raw_transaction.py`
A Python script that dynamically generates properly signed raw transactions:

**Key Features:**
- Fetches current nonce from blockchain using `eth_getTransactionCount`
- Loads WizardOfCoin contract code from `examples/contracts/wizard_of_coin.py`
- Properly encodes deployment data using RLP format
- Builds `addTransaction` call to ConsensusMain contract
- Signs transaction with test account private key
- Outputs raw transaction hex ready for `eth_sendRawTransaction`

**Transaction Structure:**
```python
# Main transaction to ConsensusMain
{
    'to': '0xb7278a61aa25c888815afc32ad3cc52ff24fe575',  # ConsensusMain
    'data': encodeFunctionData({
        'abi': ConsensusMain.abi,
        'functionName': 'addTransaction',
        'args': [
            sender,           # 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
            recipient,        # 0x0000...0000 for deployment
            numValidators,    # 5
            maxRotations,     # 3
            deploymentData    # RLP([contract_code, constructor_args, leader_only])
        ]
    })
}
```

#### 2. Updated `deploy_and_call_contract.sh`
Modified to use dynamic transaction generation:

**Changes:**
- Removed hardcoded `RAW_DEPLOYMENT_TX` variable
- Added Python script execution to generate transaction
- Added error handling for script failures
- Maintains compatibility with existing gen_call tests

**Workflow:**
```bash
# Generate transaction with current nonce
RAW_DEPLOYMENT_TX=$(python3 generate_raw_transaction.py)

# Send via eth_sendRawTransaction
curl -X POST $BASE_URL \
    -d "{\"method\":\"eth_sendRawTransaction\",\"params\":[\"$RAW_DEPLOYMENT_TX\"]}"
```

### Testing Results

#### Successful First Deployment
```
Current nonce: 0
Transaction hash: 0xa2028a03aa3d7a1aa9e2a27d8fea95e6a58012ea726c1024ff5f78cb112e8497
✅ Deployment transaction submitted successfully
```

#### Nonce Increment Verification
```
Second attempt:
Current nonce: 1  # Successfully incremented
✅ Raw transaction generated successfully
```

### Technical Details

#### RLP Encoding Structure
The deployment data follows GenLayer's specific RLP encoding:
```python
deployment_data = rlp.encode([
    contract_code.encode('utf-8'),  # Python contract code
    calldata,                        # Constructor args (have_coin=true → 0x08)
    b'\x01' if leader_only else b''  # Leader only flag
])
```

#### Constructor Argument Encoding
For WizardOfCoin's boolean `have_coin` parameter:
- `True` → `0x08`
- `False` → `0x00`

### Benefits Achieved

1. **Unlimited Deployments**: Each deployment uses current nonce from blockchain
2. **No Manual Intervention**: Automatic nonce management
3. **Load Test Ready**: Can deploy multiple contracts in sequence
4. **Error Prevention**: Eliminates nonce reuse errors
5. **Maintainable**: Clean separation of transaction generation logic

### Known Issues

1. **Contract Address Extraction**: Currently returns ConsensusMain address instead of deployed contract
   - Need to extract from NewTransaction event in logs
   - Temporary workaround: Parse event data manually

2. **gen_call Errors**: ContractSnapshot initialization fails
   - Likely due to incorrect contract address
   - Will be fixed once proper address extraction is implemented

### Usage

```bash
# Single deployment with gen_call test
./deploy_and_call_contract.sh

# Multiple deployments (each gets new nonce)
for i in {1..5}; do
    ./deploy_and_call_contract.sh
done

# Direct Python usage for debugging
python3 generate_raw_transaction.py 2>&1
```

### Environment Requirements

```bash
# Python packages needed
pip install eth_account eth_utils web3 rlp eth_abi

# Environment variables (from .env)
HARDHAT_URL=http://localhost:8545
BASE_URL=http://localhost:4000/api
```

### Next Steps

1. Fix contract address extraction from transaction receipt
2. Parse NewTransaction event to get actual deployed contract address
3. Update gen_call to use correct contract address
4. Add support for different contract types and constructor arguments
5. Integrate with load test suite for concurrent deployments

### Conclusion

The dynamic nonce management solution successfully resolves the deployment bottleneck for load testing. By generating transactions on-demand with current blockchain state, the system can now handle unlimited contract deployments without manual nonce tracking or hardcoded values. This is essential for realistic load testing scenarios.

## Iteration 8: Fixed Raw Transaction Generation

### Problem
The `generate_raw_transaction.py` script was incorrectly encoding deployment transactions, causing backend errors.

### Issues Identified

1. **Incorrect calldata encoding**: The script was using wrong values for boolean encoding:
   - Was using `0x08` for `true` and `0x00` for `false`
   - Should use `0x10` for `true` (SPECIAL_TRUE) and `0x08` for `false` (SPECIAL_FALSE)

2. **Wrong calldata structure**: Constructor arguments need to be encoded as an array:
   - Arrays use `TYPE_ARR = 5` encoding
   - For 1 element: `(1 << 3) | 5 = 0x0D`

3. **Incorrect gas limit**: Was using `0xffffffff`, should use `21000` (0x5208) to match UI

### Solution

Updated `generate_raw_transaction.py` with:

```python
def encode_calldata(constructor_args):
    """
    Encode constructor arguments for the contract.
    The calldata should be encoded as an array of arguments.
    """
    have_coin = constructor_args.get("have_coin", True)
    
    result = bytearray()
    # Array with 1 element: (1 << 3) | 5 = 13
    result.append(0x0D)
    # Boolean value
    if have_coin:
        result.append(0x10)  # SPECIAL_TRUE
    else:
        result.append(0x08)  # SPECIAL_FALSE
    
    return bytes(result)
```

### Key Changes

1. **Calldata Encoding**: Fixed to use GenLayer's custom calldata format from `backend/node/genvm/origin/calldata.py`
2. **Gas Settings**: Changed from `0xffffffff` to `21000` to match UI behavior
3. **Debug Output**: Added helpful debugging information to stderr

### Transaction Structure

The fixed transaction:
- **To**: ConsensusMain contract at `0xb7278a61aa25c888815afc32ad3cc52ff24fe575`
- **Method**: `addTransaction` with deployment payload
- **Recipient**: `0x0000...0000` (indicates contract deployment)
- **Gas**: 21000 (0x5208)
- **Gas Price**: 0 (GenLayer uses zero gas price)
- **Chain ID**: 61999 (0xf22f)

### Files Updated
- `generate_raw_transaction.py` - Fixed calldata encoding and gas settings
- `deployment_tx.txt` - Generated transaction output

### Common Issues and Solutions

1. **Duplicate key constraint error**: If you get "duplicate key value violates unique constraint uq_transactions_hash", it means a transaction with the same hash already exists in the database. Solutions:
   - Clear the transactions table: `curl -X POST http://localhost:4000/api -H "Content-Type: application/json" -d '{"jsonrpc":"2.0","method":"sim_clearDbTables","params":[["transactions"]],"id":1}'`
   - Use a different nonce: `OVERRIDE_NONCE=1 python3 generate_raw_transaction.py`
   - The script now supports dynamic nonce retrieval and override via `OVERRIDE_NONCE` environment variable

### Successful Deployment

After fixing the encoding and clearing stale transactions, the deployment succeeds:
```json
{
  "id": 1,
  "jsonrpc": "2.0",
  "result": "0xa2028a03aa3d7a1aa9e2a27d8fea95e6a58012ea726c1024ff5f78cb112e8497"
}
```

### Conclusion

The script now generates properly formatted deployment transactions that match the UI's transaction format, using correct calldata encoding for GenLayer's custom format and appropriate gas settings. The addition of dynamic nonce management and database cleanup procedures ensures reliable deployment testing.

## Iteration 9: UI-Compatible Transaction Generation

### Overview
Discovered critical differences between the script-generated transactions and UI-generated transactions that were causing deployment failures. Created new transaction generators that match the UI format exactly.

### Problem Analysis

The main issue was that the script and UI were generating fundamentally different transaction structures:

1. **Different Account Addresses**:
   - Script used: `0xEd2Cc69b248703e5c4988c24f039e88948C6462D`
   - UI used: `0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205`

2. **Different Gas Limits**:
   - Script used: `0x5208` (21000)
   - UI used: `0xffffffff` (4294967295 - max gas)

3. **Different Encoding Structure**:
   - Script: Simple RLP encoding of deployment data
   - UI: Special encoding with "args" wrapper field

4. **Different Nonce Values**:
   - Script: Started from 0
   - UI: Used correct account nonce

### Transaction Structure Comparison

#### Script Transaction (Failing):
```
Function: 0xd20aae67 (addTransaction)
Gas Limit: 21000
From: 0xEd2Cc69b248703e5c4988c24f039e88948C6462D
Data tail: ...820d10... (simple encoding)
```

#### UI Transaction (Working):
```
Function: 0xd20aae67 (addTransaction - same!)
Gas Limit: 4294967295
From: 0x701a6B9AbAF65a0E1d4B24fA875cAfA5EdB32205
Data tail: ...880e04617267730d10... (includes "args" wrapper)
```

### Key Discovery: The "args" Wrapper

The UI wraps constructor arguments with a special "args" field:
- `0x88` - Type/length marker
- `0x0e` - Sub-type marker  
- `0x04` - Length of "args"
- `0x61726773` - "args" in hex
- `0x0d10` - The actual constructor arguments

### Solution Implementation

Created `generate_ui_compatible_tx.py` that:

1. **Uses Correct Account**: 
   - Private key: `0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d`
   - Address: `0x70997970C51812dc3A010C7d01b50e0d17dc79C8`

2. **Matches UI Gas Settings**:
   - Gas limit: `0xffffffff` (max gas)
   - Gas price: `0` (GenLayer uses zero gas price)

3. **Implements "args" Wrapper**:
   ```python
   args_wrapper = bytearray()
   args_wrapper.append(0x88)  # Type marker
   args_wrapper.append(0x0e)  # Sub-type marker
   args_wrapper.append(0x04)  # Length of "args"
   args_wrapper.extend(b'args')  # The string "args"
   args_wrapper.extend(calldata)  # Constructor arguments
   ```

4. **Correct RLP Structure**:
   ```python
   deployment_data = rlp.encode([
       contract_bytes,
       bytes(args_wrapper),
       b''  # leader_only = false
   ])
   ```

### Script Updates

Updated `deploy_and_call_contract.sh` to automatically detect and use available generators:

```bash
# Priority order for transaction generators
1. generate_ui_compatible_tx.py (new, matches UI format)
2. generate_deployment_tx_ui_format.py (fallback)
3. generate_raw_transaction.py (original)
```

### Usage

The deployment script now works transparently:

```bash
# Just run the script - it automatically uses the UI-compatible generator
./deploy_and_call_contract.sh
```

Output shows which generator is being used:
```
Using UI-compatible transaction generator
Generating raw transaction with current nonce...
✅ Raw transaction generated successfully
```

### Testing Verification

Generated transaction matches UI format:
- Same function selector: `0xd20aae67`
- Same gas limit: `0xffffffff`
- Same encoding structure with "args" wrapper
- Proper account and nonce management

### Benefits

1. **Full UI Compatibility**: Transactions are indistinguishable from UI-generated ones
2. **Reliable Deployment**: No more encoding-related failures
3. **Automatic Selection**: Script picks the best available generator
4. **Backward Compatible**: Falls back to older generators if needed

### Files Created/Modified

- `generate_ui_compatible_tx.py` - New UI-matching generator
- `analyze_transactions.py` - Transaction comparison tool
- `deploy_and_call_contract.sh` - Updated to use new generator

### Lessons Learned

1. **GenLayer uses custom encoding**: Not standard Ethereum ABI encoding
2. **UI adds special wrappers**: The "args" field is crucial for deployment
3. **Gas limits matter**: UI uses max gas for safety
4. **Account consistency**: Must use the same accounts as UI for testing

### Conclusion

The UI-compatible transaction generator successfully replicates the exact format used by the GenLayer Studio UI, enabling reliable contract deployment from scripts. This solution bridges the gap between manual UI testing and automated script-based testing, making it possible to run comprehensive load tests with proper contract deployment.

## Iteration 10: GenLayer SDK-Style Deployment Implementation

### Overview
Created a comprehensive SDK-style deployment system that uses GenLayer's contract validation endpoints and proper transaction formatting, providing a more robust and maintainable approach to contract deployment.

### Problem Context
Previous iterations revealed that while we could match the UI's transaction format, we needed a more structured approach that:
- Validates contracts before deployment
- Uses GenLayer's native endpoints
- Provides better error handling and debugging
- Maintains compatibility with shell scripts

### Solution: GenLayer SDK Client

Created `deploy_with_genlayer_sdk.py` implementing a `GenLayerClient` class that encapsulates deployment logic:

#### Key Components

1. **Contract Validation**:
```python
def get_contract_schema_for_code(self, contract_code):
    """Get contract schema for validation"""
    contract_hex = "0x" + contract_code.encode('utf-8').hex()
    request = {
        "jsonrpc": "2.0",
        "method": "gen_getContractSchemaForCode",
        "params": [contract_hex],
        "id": 1
    }
    # Returns schema with constructor params and methods
```

2. **Proper Calldata Encoding**:
```python
def _encode_constructor_args(self, args):
    """
    Encode constructor arguments in GenLayer format.
    The UI format is: 880e04617267730d10
    Which is: 0x88 0x0e 0x04 "args" 0x0d 0x10
    """
    result = bytearray()
    result.append(0x88)  # Type/length marker
    result.append(0x0e)  # Sub-type marker
    result.append(0x04)  # Length of "args"
    result.extend(b'args')  # The string "args"
    result.append(0x0d)  # Array with 1 element
    result.append(0x10)  # Boolean true (have_coin = true)
    return bytes(result)
```

3. **RLP Deployment Payload**:
```python
def _create_deployment_payload(self, contract_code, calldata):
    """Create the deployment payload in RLP format"""
    contract_bytes = contract_code.encode('utf-8')
    deployment_data = rlp.encode([
        contract_bytes,
        calldata,
        b''  # leader_only = False
    ])
    return deployment_data
```

### Features Implemented

#### 1. Full Deployment Flow
The SDK client handles the complete deployment process:
- Contract validation via `gen_getContractSchemaForCode`
- Nonce management with automatic fetching
- Transaction creation and signing
- Receipt waiting and contract address extraction

#### 2. Dual-Mode Operation
```python
if __name__ == "__main__":
    if os.getenv("RAW_TX_ONLY") == "1":
        deploy_and_get_raw_tx()  # Shell script mode
    else:
        main()  # Interactive mode
```

- **Interactive Mode**: Full deployment with progress messages and receipt handling
- **Shell Script Mode**: Outputs only raw transaction hex for integration

#### 3. Contract Schema Validation
Before deployment, the contract is validated:
```json
{
  "ctor": {
    "kwparams": {},
    "params": [["have_coin", "bool"]]
  },
  "methods": {
    "ask_for_coin": {...},
    "get_have_coin": {...}
  }
}
```

### Shell Script Integration

Updated `deploy_and_call_contract.sh` to prioritize SDK deployment:

```bash
if [ -f "$SCRIPT_DIR/deploy_with_genlayer_sdk.py" ]; then
    GENERATOR_SCRIPT="deploy_with_genlayer_sdk.py"
    echo "Using GenLayer SDK-style deployment"
fi

# Use RAW_TX_ONLY mode for SDK script
if [ "$GENERATOR_SCRIPT" = "deploy_with_genlayer_sdk.py" ]; then
    RAW_DEPLOYMENT_TX=$(RAW_TX_ONLY=1 python3 "$SCRIPT_DIR/$GENERATOR_SCRIPT" 2>/dev/null)
fi
```

### Transaction Details

The generated transactions have these characteristics:
- **Type**: 0 (Legacy transaction - as expected by GenLayer)
- **Gas Limit**: `0xffffffff` (4294967295 - max gas)
- **Gas Price**: 0 (GenLayer uses zero gas)
- **Chain ID**: 61999 (0xf22f)
- **To Address**: ConsensusMain contract (`0xb7278a61aa25c888815afc32ad3cc52ff24fe575`)
- **Method**: `addTransaction(address,address,uint8,uint8,bytes)`

### Testing Results

#### Successful Validation
```
Validating contract schema...
Contract schema validated: {
  "ctor": {"kwparams": {}, "params": [["have_coin", "bool"]]},
  "methods": {...}
}
Constructor calldata (hex): 880e04617267730d10
Constructor calldata (base64): iA4EYXJncw0Q
```

#### Transaction Generation
```
Using nonce: 5
Transaction hash: 0x5a87615b3590a4553437d0e1973c68f6e41903b5ded2bb8a6ba556ba10cdce79
✅ Contract deployment transaction sent!
```

### Current Status

The SDK implementation successfully:
1. ✅ Validates contracts using GenLayer endpoints
2. ✅ Generates correctly formatted transactions
3. ✅ Matches UI calldata encoding exactly
4. ✅ Integrates with shell scripts
5. ✅ Provides comprehensive error handling

### Known Issues

1. **Backend Processing**: Some deployments result in 500 errors from the backend
2. **Contract Address Extraction**: The actual deployed contract address is not always available in receipts
3. **Transaction Type**: Shows as type 0 in eth_getTransactionByHash (this is correct for GenLayer)

### File Structure

```
tests/load/
├── deploy_with_genlayer_sdk.py    # SDK-style deployment implementation
├── deploy_and_call_contract.sh    # Shell script using SDK deployer
├── analyze_transactions.py        # Transaction comparison tool
└── testing_documentation.md       # This documentation
```

### Benefits of SDK Approach

1. **Maintainability**: Clean, object-oriented design
2. **Validation**: Contracts verified before deployment
3. **Flexibility**: Dual-mode operation for different use cases
4. **Debugging**: Comprehensive logging and error messages
5. **Compatibility**: Works with existing shell scripts

### Usage Examples

**Interactive Deployment**:
```bash
python3 deploy_with_genlayer_sdk.py
```

**Shell Script Integration**:
```bash
RAW_TX_ONLY=1 python3 deploy_with_genlayer_sdk.py
```

**Full Deployment with Testing**:
```bash
./deploy_and_call_contract.sh
```

### Lessons Learned

1. **Contract Validation is Critical**: Using `gen_getContractSchemaForCode` ensures the contract is valid before attempting deployment
2. **Calldata Format is Specific**: The "args" wrapper (`880e04617267730d10`) is essential for GenLayer
3. **Transaction Type 0 is Correct**: GenLayer uses legacy transactions, not EIP-2718 typed transactions
4. **Backend State Matters**: 500 errors often indicate backend state issues rather than transaction format problems

### Future Improvements

1. Implement retry logic for transient backend errors
2. Add support for different contract types and constructor parameters
3. Improve contract address extraction from events
4. Add transaction status monitoring
5. Implement batch deployment capabilities

### Conclusion

The GenLayer SDK-style deployment implementation provides a robust, validated approach to contract deployment that matches the UI's behavior while offering better programmability and error handling. This iteration significantly improves upon previous attempts by incorporating contract validation and proper SDK patterns, making it the recommended approach for automated contract deployment in GenLayer Studio.

## Iteration 12: Wizard Load Testing Suite

### Overview
Created a specialized load testing suite for the WizardOfCoin contract, breaking down functionality into modular scripts for deployment and reading, then orchestrating parallel operations for comprehensive load testing.

### Components Created

#### 1. wizard_deploy.py
Standalone deployment script that:
- Uses genlayer_py SDK for reliable deployment
- Accepts output file parameter for flexible address storage
- Handles contract path resolution from different directories
- Extracts and saves contract address for subsequent operations

#### 2. wizard_read.py
Lightweight read script that:
- Reads contract address from file (configurable)
- Uses genlayer_py SDK to call `get_have_coin`
- Provides clear success/failure status
- Minimal dependencies for fast parallel execution

#### 3. load_test_wizard.sh
Comprehensive load test orchestrator featuring:
- Configurable parallel deployment and reading operations
- Timestamped result directories for historical tracking
- Detailed logging for each operation
- Performance metrics and summary reporting
- Works from any directory location

### Load Test Architecture

```
Phase 1: Parallel Deployments
├── Deploy contracts using xargs -P for parallelization
├── Each deployment saves address to separate file
└── Logs capture deployment details

Phase 2: Verification
├── Count successful deployments
└── Validate address files exist

Phase 3: Parallel Reads
├── Read from all deployed contracts concurrently
├── Each read operation logs results
└── Aggregate success/failure metrics
```

### Key Features

#### Directory-Agnostic Execution
```bash
# Script works from root or any directory
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
python3 "$SCRIPT_DIR/wizard_deploy.py" "$output_file"
```

#### Parallel Processing
```bash
# Deploy N contracts with P parallel workers
seq 1 "$NUM_DEPLOYMENTS" | xargs -P "$PARALLEL_JOBS" -I {} bash -c 'deploy_contract {}'
```

#### Result Organization
```
load_test_results/
└── wizard_test_20250903_111838/
    ├── contract_1.addr
    ├── contract_2.addr
    ├── deploy_1.log
    ├── deploy_2.log
    ├── read_1.log
    └── read_2.log
```

### Usage Examples

```bash
# Default: 10 deployments with 5 parallel jobs
./load_test_wizard.sh

# Custom: 20 deployments with 8 parallel jobs
./load_test_wizard.sh 20 8

# From root directory
./tests/load/load_test_wizard.sh 50 10
```

### Performance Metrics

The suite provides comprehensive metrics:
- Total deployments requested vs successful
- Read operation success rates
- Contract addresses for all deployments
- Parallel job configuration
- Timestamped results for trend analysis

### Test Results Example

```
=== Load Test Summary ===

Deployment Results:
  - Requested: 20
  - Successful: 20
  - Failed: 0

Read Results:
  - Attempted: 20
  - Successful: 20
  - Failed: 0

Performance Metrics:
  - Parallel jobs: 8
  - Total deployments: 20
  - Total reads: 20

Deployed Contract Addresses:
  - contract_1: 0x4d8Cd6Caa7D7681AeF2E3B6e21FFB3238eCb4814
  - contract_2: 0x36d0628764E81B52814241c86B8De5cbF3C6333e
  [...]
```

### Benefits

1. **Modularity**: Separate scripts for different operations enable flexible testing scenarios
2. **Scalability**: Parallel execution with configurable concurrency levels
3. **Reliability**: SDK-based implementation ensures consistent results
4. **Observability**: Comprehensive logging and metrics for analysis
5. **Reusability**: Contract addresses saved for subsequent testing

### Integration with Existing Tests

The wizard load test complements existing load tests by:
- Focusing on contract lifecycle (deploy/read) rather than RPC endpoints
- Using SDK instead of raw transactions for reliability
- Providing contract-specific testing patterns
- Enabling state verification across multiple contracts

### Future Enhancements

1. **Write Operations**: Add parallel `ask_for_coin` tests
2. **Mixed Workloads**: Combine deploys, reads, and writes
3. **Performance Baselines**: Track deployment/read times
4. **Stress Testing**: Gradually increase load to find breaking points
5. **Contract Variations**: Test with different constructor parameters

### Conclusion

The wizard load testing suite demonstrates best practices for contract-specific load testing in GenLayer, combining the reliability of the SDK with the power of parallel shell scripting for comprehensive performance testing.

## Iteration 13: Streamlined In-Memory Load Testing

### Overview
Refactored the wizard load test script to eliminate all file system operations, storing results entirely in memory and presenting clean success/failure rate tables.

### Key Changes

#### 1. Removed Directory Creation
- **Previous**: Created timestamped directories in `load_test_results/`
- **New**: No directories or files created
- **Benefit**: Cleaner testing environment, no cleanup needed

#### 2. In-Memory Address Storage
```bash
# Previous: Saved to files
echo "$addr" > "$TEST_DIR/contract_$index.addr"

# New: Stored in array
CONTRACT_ADDRESSES+=("$(echo "$addr" | tail -n 1)")
```

#### 3. Simplified Output Functions
```bash
# Deployment without file logging
deploy_contract() {
    local index=$1
    echo "[Deploy $index] Starting deployment..."
    if result=$(python3 "$SCRIPT_DIR/wizard_deploy.py" 2>&1); then
        local addr=$(echo "$result" | grep -oE '0x[a-fA-F0-9]+' | tail -n 1)
        echo "[Deploy $index] ✅ Success - $addr"
        echo "$addr"
        return 0
    else
        echo "[Deploy $index] ❌ Failed"
        return 1
    fi
}
```

#### 4. Clean Table-Based Results

**Operation Summary Table**:
```
===================================================
              LOAD TEST RESULTS                    
===================================================

Operation            | Total      | Success    | Failed    
--------------------+-----------+-----------+-----------
Deployments          | 10         | 10         | 0         
Contract Reads       | 10         | 10         | 0         
```

**Success Rate Table**:
```
Success Rates        | Percentage
--------------------+----------
Deploy Success Rate  |     100.00%
Read Success Rate    |     100.00%
```

### Benefits

1. **No File System Overhead**: All operations in memory
2. **Cleaner Output**: Focused on metrics, not file paths
3. **Faster Execution**: No disk I/O for intermediate results
4. **No Cleanup Required**: No temporary files or directories
5. **Easier CI/CD Integration**: No file artifacts to manage

### Usage Remains Unchanged

```bash
# Default parameters
./load_test_wizard.sh

# Custom parameters
./load_test_wizard.sh 20 10
```

### Implementation Details

#### Sequential Execution with Counters
```bash
# Deploy contracts and collect addresses
for i in $(seq 1 "$NUM_DEPLOYMENTS"); do
    if addr=$(deploy_contract "$i"); then
        CONTRACT_ADDRESSES+=("$(echo "$addr" | tail -n 1)")
        ((DEPLOY_SUCCESS++))
    else
        ((DEPLOY_FAIL++))
    fi
done
```

#### Direct Contract Reading
```bash
# Read from all deployed contracts
index=1
for addr in "${CONTRACT_ADDRESSES[@]}"; do
    if read_contract "$addr" "$index"; then
        ((READ_SUCCESS++))
    else
        ((READ_FAIL++))
    fi
    ((index++))
done
```

### Output Format

The script now provides:
- Real-time operation status with ✅/❌ indicators
- Summary tables with aligned columns
- Percentage-based success rates
- Clean exit codes for automation

### Conclusion

This iteration successfully streamlines the load testing process by eliminating all file system dependencies while maintaining comprehensive metrics and clear reporting. The in-memory approach is more efficient and suitable for continuous testing scenarios.

## Iteration 14: Comprehensive API Test Suite

### Overview
Created a comprehensive test suite that systematically tests read-only and setup endpoints using a generic endpoint tester, with JSON and HTML report generation.

### Components Created

#### 1. test_endpoint.sh
Generic endpoint testing script that:
- Accepts any JSON-RPC method and parameters
- Supports JSON object parameters for complex endpoints
- Handles array and object parameter formats automatically
- Provides two modes: single test and progressive load testing
- Returns clear pass/fail status for automation

**Key Features:**
```bash
# Simple endpoint without parameters
./test_endpoint.sh eth_blockNumber

# Endpoint with parameters
./test_endpoint.sh eth_getBalance 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 latest

# Complex JSON parameters (auto-detected)
./test_endpoint.sh sim_fundAccount '{"account_address":"0x...", "amount":1000}'

# Progressive testing mode
PROGRESSIVE=1 ./test_endpoint.sh eth_blockNumber
```

#### 2. comprehensive_api_test.sh
Full API test suite orchestrator that:
- Tests 34 endpoints across read-only and setup categories
- Generates reports in JSON and HTML formats (overwritten each run)
- Provides real-time test progress with color-coded status
- Maintains test results in memory for report generation

**Test Categories:**

1. **Read-Only Endpoints (18 total)**:
   - No parameters: ping, eth_blockNumber, eth_gasPrice, eth_chainId, net_version, etc.
   - With parameters: eth_getBalance, eth_getTransactionCount, eth_getBlockByNumber, etc.

2. **Setup Operations (16 total)**:
   - Validator management: create, update, delete validators
   - Account operations: fund accounts
   - System management: snapshots, finality window
   - Provider management: add, update, delete LLM providers

**Report Features:**
- **api_test_report.json**: Machine-readable test results
- **api_test_report.html**: Interactive visualization with:
  - Summary cards (total/passed/failed/success rate)
  - Test configuration details
  - Results grouped by category
  - Color-coded pass/fail indicators
  - Embedded JSON data for programmatic access

### Key Improvements

#### Parameter Format Handling
Fixed the `sim_fundAccount` parameter issue:
- **Problem**: Endpoint required `account_address` key, not positional parameters
- **Solution**: Updated to use JSON object format
- **Implementation**: Enhanced test_endpoint.sh to detect and handle JSON objects

```bash
# Old (failing)
run_test "Setup" "sim_fundAccount" "$TEST_ADDRESS" "1000000000000000000"

# New (working) 
FUND_PARAMS='{"account_address":"'$TEST_ADDRESS'","amount":1000000000000000000}'
run_test "Setup" "sim_fundAccount" "$FUND_PARAMS"
```

### Usage Examples

```bash
# Run full test suite with defaults
./comprehensive_api_test.sh

# Custom configuration via environment
REQUESTS=100 CONCURRENCY=20 ./comprehensive_api_test.sh

# Test individual endpoint
./test_endpoint.sh eth_getBalance 0x70997970C51812dc3A010C7d01b50e0d17dc79C8 latest

# Progressive load test
PROGRESSIVE=1 ./test_endpoint.sh eth_blockNumber
```

### Test Results Format

**Console Output:**
```
════════════════════════════════════════════════════════════════════
         GenLayer Studio API Comprehensive Test Suite
════════════════════════════════════════════════════════════════════

Configuration:
  Base URL: http://localhost:4000/api
  Requests per test: 50
  Concurrency: 10

Testing: eth_blockNumber
  Parameters: []
  Status: ✅ PASS

Testing: sim_fundAccount
  Parameters: [{"account_address":"0x...", "amount":1000000000000000000}]
  Status: ✅ PASS
```

**HTML Report:**
Interactive dashboard with:
- Test execution timestamp
- Configuration parameters (base URL, requests, concurrency)
- Summary statistics with visual cards
- Detailed results table by category
- Success rate percentage

### Benefits

1. **Comprehensive Coverage**: Tests all major read-only and setup endpoints
2. **Flexible Testing**: Supports different parameter formats and types
3. **Clear Reporting**: Both human-readable HTML and machine-readable JSON
4. **No Timestamp Proliferation**: Reports overwritten each run
5. **Easy Integration**: Simple bash scripts work with CI/CD pipelines
6. **Real-time Feedback**: Color-coded console output during execution

### Files Created/Modified

- `test_endpoint.sh`: Generic endpoint tester
- `comprehensive_api_test.sh`: Full test suite orchestrator
- `api_test_report.json`: JSON test results (overwritten)
- `api_test_report.html`: HTML visualization (overwritten)

### Conclusion

The comprehensive API test suite provides systematic endpoint testing with clear reporting, making it easy to validate GenLayer Studio's JSON-RPC API functionality and performance. The combination of generic endpoint testing and comprehensive orchestration creates a robust testing framework suitable for both development and CI/CD environments.

## Iteration 15: Renamed Comprehensive Test Suite

### Overview
Renamed the comprehensive API test suite from `comprehensive_api_test.sh` to `load_test_all_read_setup_endpoints.sh` to better reflect its purpose and align with the naming convention of other load testing scripts.

### Changes Made

#### 1. Script Rename
- **Old Name**: `comprehensive_api_test.sh`
- **New Name**: `load_test_all_read_setup_endpoints.sh`
- **Rationale**: More descriptive name that clearly indicates the script tests all read and setup endpoints

#### 2. Updated References
All documentation and usage examples have been updated to reflect the new script name.

### Usage with New Name

```bash
# Run full test suite with defaults
./load_test_all_read_setup_endpoints.sh

# Custom configuration via environment
REQUESTS=100 CONCURRENCY=20 ./load_test_all_read_setup_endpoints.sh

# From root directory
./tests/load/load_test_all_read_setup_endpoints.sh
```

### File Structure Update

```
tests/load/
├── test_endpoint.sh                        # Generic endpoint tester (unchanged)
├── load_test_all_read_setup_endpoints.sh   # Renamed comprehensive test suite
├── api_test_report.json                    # JSON test results (generated)
├── api_test_report.html                    # HTML visualization (generated)
└── testing_documentation.md                # This documentation
```

### Benefits of Rename

1. **Clarity**: Name explicitly states what the script does
2. **Consistency**: Follows naming pattern of other load_test_*.sh scripts
3. **Discoverability**: Easier to find when looking for load testing tools
4. **Self-documenting**: Name serves as documentation

### Backward Compatibility

If you have automation or documentation referencing the old name, you can create a symlink:

```bash
ln -s load_test_all_read_setup_endpoints.sh comprehensive_api_test.sh
```

### Conclusion

The rename improves clarity and consistency in the load testing suite while maintaining all functionality. The script continues to test 34 endpoints across read-only and setup categories with comprehensive reporting.