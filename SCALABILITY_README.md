# GenLayer Studio Scalability Implementation Guide

## Overview

This implementation adds horizontal scalability to GenLayer Studio using ZeroMQ for distributed message passing, Redis for state synchronization, and support for multiple consensus workers. The system can scale from a single developer machine to a multi-VM production environment.

## Key Features

- **Zero Database Migrations**: Only adds one `transaction_results` table
- **Backward Compatible**: Hybrid mode allows gradual migration
- **Horizontal Scaling**: Scale readers and writers independently
- **Fault Tolerance**: Automatic recovery from worker failures
- **Real-time Updates**: Socket.IO integration preserved

## Quick Start

### 1. Enable Scalability Features

Add to your `.env` file:

```bash
# Consensus mode: legacy (default), hybrid, or zmq
CONSENSUS_MODE=hybrid

# Number of workers (0 means service won't start)
READER_WORKERS=1
WRITER_WORKERS=2

# GenVM instances per worker
MAX_GENVM_PER_WORKER=5
```

### 2. Start Services with Scalability Profile

```bash
# Start all scalability services
docker-compose --profile scalability up -d

# Or start specific services
docker-compose up -d zmq-broker redis
docker-compose up -d --scale consensus-worker=3
```

### 3. Run Database Migration

```bash
# Apply the transaction_results table migration
docker-compose exec jsonrpc alembic upgrade head
```

## Architecture Components

### ZeroMQ Broker (`backend/services/zmq_broker.py`)
- Manages transaction queues per contract
- Tracks worker health with heartbeats
- Persists state to Redis for recovery
- Emits Socket.IO events for frontend

### Consensus Worker (`backend/workers/zmq_consensus_worker.py`)
- Pulls transactions from ZeroMQ queue
- Manages WebDriver pool for GenVM
- Executes leader/validator consensus
- Stores results in database

### State Manager (`backend/services/state_manager.py`)
- Redis-based contract state caching
- Distributed locking for contract modifications
- Cache invalidation across workers

### ZeroMQ Client (`backend/services/zmq_client.py`)
- Used by jsonrpc service to queue transactions
- Supports async and sync operations
- Automatic fallback in hybrid mode

## Deployment Modes

The system auto-determines its role based on READER_WORKERS and WRITER_WORKERS:

### Infrastructure Mode (VM1)
```bash
CONSENSUS_MODE=zmq
READER_WORKERS=0       # No GenVM readers
WRITER_WORKERS=0       # No consensus workers
# Result: Simple RPC only, handles non-GenVM endpoints
```

### Read Farm Mode (VM2)
```bash
CONSENSUS_MODE=zmq
READER_WORKERS=15      # Scale GenVM readers
WRITER_WORKERS=0       # No consensus workers
# Result: Handles GenVM read operations (call, staticcall)
```

### Write Farm Mode (VM3)
```bash
CONSENSUS_MODE=zmq
READER_WORKERS=0       # No GenVM readers
WRITER_WORKERS=10      # Scale consensus workers
# Result: Simple RPC + consensus workers via ZeroMQ
```

### Development Mode (All-in-One)
```bash
CONSENSUS_MODE=hybrid
READER_WORKERS=2       # Few readers
WRITER_WORKERS=2       # Few writers
# Result: Full functionality on single machine
```

## Multi-VM Deployment

### VM1: Infrastructure
```bash
# .env
READER_WORKERS=1
WRITER_WORKERS=0
```

### VM2: Read Farm
```bash
# .env
READER_WORKERS=10
WRITER_WORKERS=0
ZMQ_BROKER_HOST=vm1.internal
REDIS_HOST=vm1.internal
```

### VM3: Write Farm
```bash
# .env
READER_WORKERS=0
WRITER_WORKERS=5
ZMQ_BROKER_HOST=vm1.internal
REDIS_HOST=vm1.internal
```

## Monitoring

### Health Check
```bash
curl http://localhost:4000/api/health/scalability
```

### Metrics
```bash
# Consensus metrics
curl http://localhost:4000/api/metrics/consensus

# ZeroMQ broker metrics
curl http://localhost:5561/metrics

# Queue status
curl http://localhost:5561/queues
```

### Logs
```bash
# View broker logs
docker-compose logs -f zmq-broker

# View worker logs
docker-compose logs -f consensus-worker

# View Redis logs
docker-compose logs -f redis
```

## Scaling Operations

### Scale Workers
```bash
# Scale up write workers
docker-compose up -d --scale consensus-worker=10

# Scale down
docker-compose up -d --scale consensus-worker=2
```

### Monitor Queue Depth
```bash
watch -n 1 'curl -s http://localhost:5561/metrics | jq .queues_by_contract'
```

### Check Worker Health
```bash
curl http://localhost:5561/metrics | jq .worker_health
```

## Troubleshooting

### Workers Not Processing
1. Check broker is running: `docker-compose ps zmq-broker`
2. Check Redis connection: `docker-compose exec redis redis-cli ping`
3. View worker logs: `docker-compose logs consensus-worker`

### Transaction Stuck
1. Check queue status: `curl http://localhost:5561/queues`
2. Check processing contracts: `curl http://localhost:5561/metrics`
3. Restart stuck worker: `docker-compose restart consensus-worker`

### High Memory Usage
1. Check Redis memory: `docker-compose exec redis redis-cli info memory`
2. Clear old cache: `docker-compose exec redis redis-cli FLUSHDB`
3. Adjust Redis max memory in docker-compose.yml

## Performance Tuning

### Optimize Worker Count
- **Read Workers**: 2-3x number of CPU cores
- **Write Workers**: 1-2x number of CPU cores
- **GenVM per Worker**: 3-5 for optimal resource usage

### Redis Optimization
```yaml
# docker-compose.yml
redis:
  command: redis-server --maxmemory 512mb --maxmemory-policy allkeys-lru
```

### ZeroMQ Tuning
```python
# Increase message buffer in zmq_broker.py
self.backend.setsockopt(zmq.SNDHWM, 5000)  # High water mark
```

## Migration Guide

### Phase 1: Test in Hybrid Mode
```bash
CONSENSUS_MODE=hybrid
WRITER_WORKERS=1
# Monitor for 24 hours
```

### Phase 2: Scale Workers
```bash
WRITER_WORKERS=5
# Monitor performance
```

### Phase 3: Switch to ZeroMQ Only
```bash
CONSENSUS_MODE=zmq
# Keep legacy code for rollback
```

### Rollback Procedure
```bash
# Immediate rollback
CONSENSUS_MODE=legacy
docker-compose restart jsonrpc

# Stop scalability services
docker-compose stop zmq-broker consensus-worker redis
```

## API Changes

The implementation is fully backward compatible. No API changes required.

## Database Changes

Only one new table is added:

```sql
CREATE TABLE transaction_results (
    tx_hash VARCHAR(66) PRIMARY KEY,
    contract_address VARCHAR(42) NOT NULL,
    result JSONB,
    error TEXT,
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    worker_id VARCHAR(100),
    consensus_mode VARCHAR(20)
);
```

## Security Considerations

1. **Internal Network**: Keep ZeroMQ ports (5557-5560) internal only
2. **Redis Security**: Use password authentication in production
3. **Worker Authentication**: Consider adding worker authentication tokens
4. **TLS**: Enable TLS for cross-VM communication

## Contributing

To extend the scalability features:

1. **Add New Worker Types**: Create in `backend/workers/`
2. **Extend Broker**: Modify `backend/services/zmq_broker.py`
3. **Add Metrics**: Update `backend/protocol_rpc/scalability_endpoints.py`
4. **Update Migrations**: Add to `backend/database_handler/migration/versions/`

## Support

For issues or questions:
- Check logs: `docker-compose logs [service-name]`
- Monitor metrics: `http://localhost:5561/metrics`
- Review this guide and `scalability_plan.md`