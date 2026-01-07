# Logging Policy

This document defines the logging standards for GenLayer Studio backend services.

## Philosophy

**Logs should be actionable.** Every log entry should answer: "Who needs to see this, and what will they do with it?"

## Log Levels

| Level | Purpose | Who Reads | When to Use |
|-------|---------|-----------|-------------|
| **ERROR** | Something broke, needs attention | On-call, alerts | System failures, data integrity issues |
| **WARNING** | Degraded state, may need attention | Daily review | Retries, recoverable failures, resource pressure |
| **INFO** | Business events, audit trail | Product/debug | Final state changes, user-initiated actions |
| **DEBUG** | Developer troubleshooting | On-demand | Internal operations, intermediate states |

## What Belongs at Each Level

### ERROR - "Wake someone up"
```python
# System failures
logger.error("Database connection failed")
logger.error("GenVM crashed during execution")
logger.error("Failed to release transaction after 3 attempts")

# Data integrity issues
logger.error("Transaction in invalid state: expected X, got Y")
```

### WARNING - "Check this soon"
```python
# Degraded operation
logger.warning("No validators available, retry {n}/{max}")
logger.warning("Transaction timeout, recovering: {hash}")
logger.warning("Worker recovered {n} stuck transactions")

# Resource pressure
logger.warning("Database pool exhausted, waiting for connection")
logger.warning("Queue depth exceeds threshold: {depth}")
```

### INFO - "Business events" (audit trail)
```python
# Transaction lifecycle FINAL states only
logger.info("Transaction finalized: {hash}")
logger.info("Transaction canceled: {hash}")
logger.info("Contract deployed: {address}")
logger.info("Appeal submitted for: {hash}")
logger.info("Successfully processed transaction {hash}")

# Service lifecycle (startup/shutdown)
logger.info("Worker {id} started")
logger.info("Service shutting down")
```

### DEBUG - "Developer needs this"
```python
# All intermediate states
logger.debug("Claimed transaction {hash}")
logger.debug("Transaction state: PENDING -> PROPOSING")
logger.debug("Released transaction {hash}")

# Polling/heartbeat
logger.debug("Query returned no rows in 0.020s")

# Internal operations
logger.debug("Published to Redis: {channel}")
logger.debug("Spawning processor for contract {address}")
```

## RPC Method Logging

### Filtered from INFO logs (`DISABLE_INFO_LOGS_ENDPOINTS`)

High-frequency polling/read methods that generate noise:

```python
[
    "ping",
    "gen_getTransactionStatus",      # Constant polling
    "eth_getTransactionByHash",
    "eth_getTransactionReceipt",
    "eth_getTransactionCount",
    "gen_getContractSchemaForCode",
    "gen_getContractSchema",
    "eth_getCode",
    "eth_getBalance",
    "eth_call",                      # View calls
    "net_version",
    "eth_chainId",
    "sim_getTransactionsForAddress",
    "sim_getConsensusContract",
]
```

### Always log at INFO

State-changing operations (audit trail):
- `eth_sendTransaction`
- `sim_createValidator`
- Contract deployments

### Log errors at WARNING

Any RPC method that returns an error response.

## Access Logs

| Endpoint | Log Level | Rationale |
|----------|-----------|-----------|
| `/health`, `/ready`, `/status` | Filtered out | Use metrics instead |
| `/api` (RPC) | Filtered by uvicorn | High volume, rely on RPC-level logging |
| Error responses | WARNING | Actionable |

The `HealthCheckFilter` in `backend/protocol_rpc/logging_config.py` automatically filters health check access logs.

## Environment Configuration

| Environment | LOG_LEVEL | Rationale |
|-------------|-----------|-----------|
| dev | info | Full visibility (can set to debug locally) |
| stg | info | Balance debugging/cost |
| prd | info | Keep visible during alpha; later move to warning |

## Adding New Logs

When adding new log statements, ask:

1. **Is this a final state or intermediate?**
   - Final states (transaction completed, contract deployed) = INFO
   - Intermediate states (claimed, processing, retrying) = DEBUG

2. **Is this an error or degraded state?**
   - Failures = ERROR
   - Recoverable issues = WARNING

3. **Will this log be useful in 6 months?**
   - If it's only useful during development = DEBUG
   - If it's useful for audit/compliance = INFO

4. **How often will this log?**
   - Per-request/per-poll = DEBUG (or consider removing)
   - Per-transaction-lifecycle = INFO for final state only
   - On errors = ERROR/WARNING

## Implementation Details

### Health Check Filtering

Located in `backend/protocol_rpc/logging_config.py`:

```python
class HealthCheckFilter(logging.Filter):
    FILTERED_PATHS = {"/health", "/ready", "/status"}

    def filter(self, record: logging.LogRecord) -> bool:
        # Returns False to suppress health check logs
        ...
```

### Worker Logging

The consensus worker uses loguru with these patterns:

- Query polling results: DEBUG (logged every 60s, not per-poll)
- Claim operations: DEBUG
- Processing completion: INFO
- Errors: ERROR with full traceback

### RPC Method Filtering

Configured via `DISABLE_INFO_LOGS_ENDPOINTS` environment variable. Methods in this list are logged at DEBUG level instead of INFO.

## Monitoring vs Logging

Prefer metrics over logs for:
- Request throughput (counter)
- Request latency (histogram)
- Queue depth (gauge)
- Health check success rate (counter)

Logs are for:
- Debugging specific issues
- Audit trail of business events
- Error investigation

## References

- Logging config: `backend/protocol_rpc/logging_config.py`
- Worker logging: `backend/consensus/worker.py`
- Consensus logging: `backend/consensus/base.py`
- Deployment configs: `devexp-apps-workload/workload/*/deployment.yaml`
