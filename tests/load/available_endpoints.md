# GenLayer Studio Available JSON-RPC Endpoints

## Complete List of Registered Endpoints

### Simulator Methods (sim_*)
- `sim_addProvider` - Add a new LLM provider
- `sim_call` - Call a contract method (simulator mode)
- `sim_clearDbTables` - Clear database tables
- `sim_countValidators` - Count total validators
- `sim_createRandomValidator` - Create a single random validator
- `sim_createRandomValidators` - Create multiple random validators
- `sim_createSnapshot` - Create a database snapshot
- `sim_createValidator` - Create a specific validator
- `sim_deleteAllSnapshots` - Delete all snapshots
- `sim_deleteAllValidators` - Delete all validators
- `sim_deleteProvider` - Delete an LLM provider
- `sim_deleteValidator` - Delete a specific validator
- `sim_fundAccount` - Fund an account with tokens
- `sim_getAllValidators` - Get list of all validators
- `sim_getConsensusContract` - Get consensus contract details
- `sim_getFinalityWindowTime` - Get finality window time
- `sim_getProvidersAndModels` - Get LLM providers and models
- `sim_getTransactionsForAddress` - Get transactions for address
- `sim_getValidator` - Get specific validator details
- `sim_resetDefaultsLlmProviders` - Reset LLM providers to defaults
- `sim_restoreSnapshot` - Restore from snapshot
- `sim_setFinalityWindowTime` - Set finality window time
- `sim_updateProvider` - Update LLM provider configuration
- `sim_updateValidator` - Update validator details

### GenLayer Methods (gen_*)
- `gen_call` - Call a GenLayer contract
- `gen_getContractSchema` - Get contract schema
- `gen_getContractSchemaForCode` - Get schema for contract code

### Ethereum-Compatible Methods (eth_*)
- `eth_blockNumber` - Get current block number
- `eth_call` - Execute a call without creating a transaction
- `eth_chainId` - Get chain ID
- `eth_estimateGas` - Estimate gas for transaction
- `eth_gasPrice` - Get current gas price
- `eth_getBalance` - Get account balance
- `eth_getBlockByHash` - Get block by hash
- `eth_getBlockByNumber` - Get block by number
- `eth_getTransactionByHash` - Get transaction by hash
- `eth_getTransactionCount` - Get transaction count (nonce)
- `eth_getTransactionReceipt` - Get transaction receipt
- `eth_sendRawTransaction` - Send raw signed transaction

### Network Methods (net_*)
- `net_version` - Get network version

### Other Methods
- `ping` - Health check endpoint

## Important Notes

1. **No eth_sendTransaction**: The system uses `eth_sendRawTransaction` instead
2. **No sim_deployContract**: Contract deployment is done through `eth_sendRawTransaction` or `gen_call`
3. **No sim_createDb/sim_createTables**: Only `sim_clearDbTables` exists
4. **No sim_callContractMethod**: Use `sim_call` or `gen_call` instead

## Methods for Load Testing

### Working Endpoints for Testing:
1. `sim_fundAccount` - Fund test accounts
2. `sim_createRandomValidators` - Create validators
3. `sim_getAllValidators` - List validators
4. `eth_getBalance` - Check balances
5. `eth_blockNumber` - Get block number
6. `eth_sendRawTransaction` - Deploy contracts and send transactions
7. `gen_call` or `sim_call` - Call contract methods

### Deployment Process:
Contracts are deployed using `eth_sendRawTransaction` with encoded transaction data.

### Contract Interaction:
Use `gen_call` or `sim_call` for contract method calls.