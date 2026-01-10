---
name: studio-db
description: Query Studio deployment PostgreSQL databases for transaction debugging and analytics
---

# Studio Database Debugging

Query and debug the `genlayer_state` database for transaction issues.

## Connection

For cluster access setup and connection workflow, see:
`devexp-apps-workload/.claude/skills/studio-db/SKILL.md`

Quick connect (after setup):
```bash
export PATH="/opt/homebrew/share/google-cloud-sdk/bin:/opt/homebrew/bin:$PATH"
DBHOST=$(kubectl get secret database-config -n <namespace> -o jsonpath='{.data.DBHOST}' | base64 -d)
DBPASSWORD=$(kubectl get secret database-password -n <namespace> -o jsonpath='{.data.DBPASSWORD}' | base64 -d)

echo "<SQL>" | kubectl run pg-query --rm -i --image=postgres:16 --restart=Never \
  --namespace=<namespace> --env="PGPASSWORD=$DBPASSWORD" -- \
  psql -h "$DBHOST" -p 5432 -U postgres -d genlayer_state -t
```

## Transaction Table Schema

Primary table for debugging (`transactions`):

| Column | Type | Description |
|--------|------|-------------|
| hash | VARCHAR(66) | Primary key, tx identifier |
| status | ENUM | Transaction lifecycle status |
| from_address | VARCHAR | Sender address |
| to_address | VARCHAR | Recipient/contract address |
| nonce | INTEGER | Tx sequence number |
| value | INTEGER | Transaction value |
| type | INTEGER | Tx type (0-3) |
| created_at | TIMESTAMP | When tx was created |
| input_data | JSONB | Contract call input |
| data | JSONB | Transaction data payload |
| consensus_data | JSONB | Final consensus results |
| consensus_history | JSONB | Full voting history |
| contract_snapshot | JSONB | Contract state at execution |
| appealed | BOOLEAN | Was tx appealed |
| appeal_failed | INTEGER | Appeal failure count |
| appeal_undetermined | BOOLEAN | Appeal resulted in undetermined |
| appeal_leader_timeout | BOOLEAN | Leader timeout during appeal |
| appeal_validators_timeout | BOOLEAN | Validators timeout during appeal |
| leader_timeout_validators | JSONB | Validators that timed out |
| worker_id | VARCHAR | Which worker processed tx |
| triggered_by_hash | VARCHAR(66) | Parent tx hash (for tx chains) |
| blocked_at | TIMESTAMP | When/if tx got blocked |

## Transaction Statuses

```
PENDING → ACTIVATED → PROPOSING → COMMITTING → REVEALING → ACCEPTED → FINALIZED
                                                        ↘ UNDETERMINED
                                                        ↘ LEADER_TIMEOUT
                                                        ↘ VALIDATORS_TIMEOUT
                                                        ↘ CANCELED
```

## Other Tables

- **current_state**: Global chain state (`id`, `data` JSONB, `balance`)
- **validators**: Validator configs (`id`, `address`, `stake`, `provider`, `model`, `plugin`)
- **llm_providers**: LLM provider configurations
- **snapshot**: Compressed state snapshots (`snapshot_id`, `state_data`, `transaction_data`)

## Common Debugging Queries

### Find transaction by hash
```sql
SELECT hash, status, from_address, to_address, created_at,
       consensus_data, worker_id
FROM transactions WHERE hash = '0x...';
```

### List stuck/problematic transactions
```sql
SELECT hash, status, created_at, worker_id
FROM transactions
WHERE status IN ('UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
ORDER BY created_at DESC LIMIT 20;
```

### Transaction status distribution
```sql
SELECT status, COUNT(*) as count
FROM transactions
GROUP BY status ORDER BY count DESC;
```

### Recent transactions
```sql
SELECT hash, status, from_address, created_at
FROM transactions ORDER BY created_at DESC LIMIT 10;
```

### Transactions with appeals
```sql
SELECT hash, status, appealed, appeal_undetermined, appeal_failed,
       appeal_leader_timeout, appeal_validators_timeout
FROM transactions WHERE appealed = true
ORDER BY created_at DESC LIMIT 20;
```

### Find triggered transactions (tx chains)
```sql
SELECT t1.hash as parent, t2.hash as child, t2.status
FROM transactions t1
JOIN transactions t2 ON t2.triggered_by_hash = t1.hash
WHERE t1.hash = '0x...';
```

### Transactions by worker
```sql
SELECT worker_id, status, COUNT(*)
FROM transactions
WHERE created_at > NOW() - INTERVAL '24 hours'
GROUP BY worker_id, status
ORDER BY worker_id, count DESC;
```

### Check consensus history for a tx
```sql
SELECT hash, status,
       jsonb_pretty(consensus_history) as history
FROM transactions WHERE hash = '0x...';
```

## Write Operations

**CAUTION**: Before any UPDATE/DELETE:
1. First SELECT to verify affected rows
2. Ask user for explicit confirmation
3. Prefer dev/stg for testing

### Reset stuck transaction
```sql
-- Verify first
SELECT hash, status FROM transactions
WHERE hash = '0x...' AND status = 'UNDETERMINED';

-- Then update (after confirmation)
UPDATE transactions SET status = 'PENDING' WHERE hash = '0x...';
```

## Full Schema

Models: `backend/database_handler/models.py`
Migrations: `backend/database_handler/migration/versions/`
Tx processor: `backend/database_handler/transactions_processor.py`
