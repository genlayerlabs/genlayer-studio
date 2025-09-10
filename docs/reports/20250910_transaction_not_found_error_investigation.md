# Transaction Not Found Error Investigation Report

**Date:** 2025-09-10  
**Author:** Investigation Team  
**Issue:** Transactions marked as PENDING but fail with TransactionNotFoundError

## Executive Summary

Investigation into why transactions are recorded as PENDING but subsequently fail with `TransactionNotFoundError` when the transaction tracker attempts to retrieve them. The root cause is that transactions are being recorded in the database even when they fail to be forwarded to the consensus layer, resulting in locally-generated transaction hashes that don't exist on the network.

## Problem Description

### Symptoms
- Transactions appear as PENDING in the database
- Transaction tracker receives `TransactionNotFoundError` when attempting to check status
- Error message: "Transaction with hash [hash] could not be found"
- Users see transactions stuck in PENDING state that never resolve

### Impact
- Poor user experience with stuck transactions
- Database pollution with invalid transaction records
- Confusion about transaction state
- Potential for users to retry transactions unnecessarily

## Investigation Findings

### Transaction Flow Analysis

1. **Client Submission (genlayer-js)**
   - User initiates transaction through dKOL app
   - `writeContract()` method in genlayer-js is called
   - Transaction is signed and sent to Ethereum/Hardhat node
   - `NewTransaction` event is emitted with GenLayer transaction ID
   - This ID is returned to the frontend

2. **Backend Processing (genlayer-studio)**
   - `send_raw_transaction` endpoint receives the transaction
   - Attempts to forward to consensus layer via `consensus_service.add_transaction()`
   - Transaction is inserted into database regardless of forwarding success

### Root Cause

The critical issue occurs in `backend/protocol_rpc/endpoints.py` at the `send_raw_transaction` function:

```python
# Lines 768-770
rollup_transaction_details = consensus_service.add_transaction(
    signed_rollup_transaction, from_address
)

# Lines 806-809
if rollup_transaction_details and "tx_id_hex" in rollup_transaction_details:
    transaction_hash = rollup_transaction_details["tx_id_hex"]
else:
    transaction_hash = None

# Lines 812-824
transaction_hash = transactions_processor.insert_transaction(
    genlayer_transaction.from_address,
    to_address,
    transaction_data,
    value,
    genlayer_transaction.type.value,
    nonce,
    leader_only,
    genlayer_transaction.max_rotations,
    None,
    transaction_hash,  # This can be None!
    genlayer_transaction.num_of_initial_validators,
)
```

When `consensus_service.add_transaction()` fails or returns `None`:
1. The `transaction_hash` parameter becomes `None`
2. `insert_transaction` is still called
3. Inside `insert_transaction`, if hash is `None`, a local hash is generated
4. This locally-generated hash doesn't correspond to any real transaction on the network

### Failure Scenarios

`consensus_service.add_transaction()` returns `None` when:

1. **Not connected to Hardhat node** (lines 159-163):
   ```python
   if not self.web3.is_connected():
       print("[CONSENSUS_SERVICE]: Not connected to Hardhat node, skipping transaction forwarding")
       return None
   ```

2. **Exception during forwarding** (lines 169-202):
   - Network errors
   - Invalid transaction data
   - Consensus layer rejection
   - Any unexpected error that isn't a nonce issue

### Database Behavior

In `backend/database_handler/transactions_processor.py`:

```python
def insert_transaction(self, ...):
    # Lines 239-242
    if transaction_hash is None:
        transaction_hash = self._generate_transaction_hash(
            from_address, to_address, data, value, type, current_nonce
        )
    
    # Lines 283-285
    self.session.add(new_transaction)
    self.session.flush()  # Transaction is persisted
    
    return new_transaction.hash
```

The transaction is always added to the database, even when consensus forwarding fails.

## Recommended Solutions

### Option 1: Fail Fast (Recommended)
Don't insert transactions into the database if consensus forwarding fails:

```python
def send_raw_transaction(...):
    # ... existing code ...
    
    if genlayer_transaction.type != TransactionType.SEND:
        rollup_transaction_details = consensus_service.add_transaction(
            signed_rollup_transaction, from_address
        )
        
        # Fail if consensus forwarding failed
        if rollup_transaction_details is None:
            raise JSONRPCError(
                code=-32000,
                message="Failed to forward transaction to consensus layer",
                data={"reason": "Network unavailable or transaction rejected"}
            )
    
    # ... continue with insertion only if successful ...
```

### Option 2: Mark as Failed
Insert the transaction but immediately mark it as failed:

```python
def send_raw_transaction(...):
    # ... existing code ...
    
    if rollup_transaction_details is None:
        # Insert as failed transaction
        transaction_hash = transactions_processor.insert_failed_transaction(
            genlayer_transaction.from_address,
            to_address,
            transaction_data,
            value,
            genlayer_transaction.type.value,
            nonce,
            error="Failed to forward to consensus layer"
        )
        return transaction_hash
```

### Option 3: Retry Mechanism
Implement automatic retry for failed consensus forwarding:

```python
def send_raw_transaction(...):
    max_retries = 3
    retry_count = 0
    
    while retry_count < max_retries:
        rollup_transaction_details = consensus_service.add_transaction(
            signed_rollup_transaction, from_address
        )
        
        if rollup_transaction_details is not None:
            break
            
        retry_count += 1
        time.sleep(1)  # Wait before retry
    
    if rollup_transaction_details is None:
        raise JSONRPCError(...)
```

## Testing Recommendations

1. **Unit Tests**
   - Test `send_raw_transaction` with mocked `consensus_service` returning `None`
   - Verify proper error handling and no database pollution

2. **Integration Tests**
   - Test with Hardhat node disconnected
   - Test with invalid transaction data
   - Test with network interruptions

3. **End-to-End Tests**
   - Simulate user transaction flow with consensus failures
   - Verify user receives appropriate error messages
   - Ensure no stuck PENDING transactions

## Immediate Mitigation

For existing stuck transactions in production:

1. **Identify affected transactions**:
   ```sql
   SELECT hash, from_address, created_at 
   FROM transactions 
   WHERE status = 'PENDING' 
   AND created_at < NOW() - INTERVAL '1 hour';
   ```

2. **Mark as failed with appropriate error**:
   ```sql
   UPDATE transactions 
   SET status = 'FAILED', 
       error = 'Transaction not found on network' 
   WHERE status = 'PENDING' 
   AND created_at < NOW() - INTERVAL '1 hour';
   ```

## Conclusion

The root cause is that transactions are unconditionally inserted into the database even when they fail to be forwarded to the consensus layer. This results in transaction records with locally-generated hashes that don't exist on the network. The recommended solution is to implement proper error handling that prevents database insertion when consensus forwarding fails, providing clear feedback to users about the failure.

## Related Issues

- Transaction tracker error handling in dKOL app has been updated to properly handle `TransactionNotFoundError`
- Consider implementing health checks for Hardhat node connectivity
- Review other areas where similar optimistic database insertions might occur