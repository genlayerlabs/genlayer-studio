# VM Configuration Examples for GenLayer Studio Scalability

The system auto-determines its role based on `READER_WORKERS` and `WRITER_WORKERS` values.

## VM Role Determination

| READER_WORKERS | WRITER_WORKERS | Role | Description |
|----------------|----------------|------|-------------|
| 0 | 0 | Infrastructure | Simple RPC only, no GenVM, runs broker/redis |
| >0 | 0 | Read Farm | GenVM read operations via Traefik |
| 0 | >0 | Write Farm | Simple RPC + consensus workers via ZeroMQ |
| >0 | >0 | Hybrid/Dev | Both read and write capabilities |

## VM1: Infrastructure Layer (.env)
```bash
# Infrastructure VM - Runs core services + simple RPC
CONSENSUS_MODE=zmq
SERVER_NAME=api.genlayer.example.com

# No readers/writers - just simple RPC
READER_WORKERS=0
WRITER_WORKERS=0

# This makes jsonrpc act as simple-rpc (no GenVM)
# Handles non-GenVM endpoints like:
# - /api/accounts
# - /api/transactions
# - /api/blocks
# - etc.

# Network settings for other VMs
ZMQ_BROKER_HOST=0.0.0.0
REDIS_HOST=0.0.0.0
DBHOST=postgres

# Enable scalability services
COMPOSE_PROFILES=studio,scalability
```

## VM2: Read Farm (.env)
```bash
# Read Farm VM - Handles GenVM read operations
CONSENSUS_MODE=zmq
SERVER_NAME=api.genlayer.example.com

# Multiple readers, no writers
READER_WORKERS=15
WRITER_WORKERS=0

# This makes jsonrpc act as reader with GenVM
# Handles GenVM read endpoints via Traefik:
# - /api/call
# - /api/staticcall  
# - /api/eth_call
# - /api/eth_estimateGas

# Connect to infrastructure VM
ZMQ_BROKER_HOST=vm1.internal
REDIS_HOST=vm1.internal
DBHOST=vm1.internal

# WebDriver configuration for GenVM
WEBDRIVERHOST=webdriver
WEBDRIVERPORT=4444
MAX_GENVM_PER_WORKER=5

# Don't need broker/redis locally
COMPOSE_PROFILES=studio
```

## VM3: Write Farm (.env)
```bash
# Write Farm VM - Handles consensus/write operations
CONSENSUS_MODE=zmq
SERVER_NAME=api.genlayer.example.com

# No readers, multiple writers
READER_WORKERS=0
WRITER_WORKERS=10

# jsonrpc acts as simple-rpc (READER_WORKERS=0)
# consensus-worker service handles writes

# Connect to infrastructure VM
ZMQ_BROKER_HOST=vm1.internal
REDIS_HOST=vm1.internal
DBHOST=vm1.internal

# WebDriver configuration for consensus
WEBDRIVERHOST=webdriver
WEBDRIVERPORT=4444
MAX_GENVM_PER_WORKER=10

# Don't need broker/redis locally
COMPOSE_PROFILES=studio,scalability
```

## Development Machine (.env)
```bash
# Development - All services on one machine
CONSENSUS_MODE=hybrid
SERVER_NAME=localhost

# Small number of both types
READER_WORKERS=2
WRITER_WORKERS=2

# Local services
ZMQ_BROKER_HOST=zmq-broker
REDIS_HOST=redis
DBHOST=postgres

# Moderate resources
MAX_GENVM_PER_WORKER=3

# Enable all profiles
COMPOSE_PROFILES=studio,scalability
```

## Deployment Commands

### VM1 (Infrastructure)
```bash
# Start core services + simple RPC
docker-compose up -d traefik postgres zmq-broker redis jsonrpc
```

### VM2 (Read Farm)
```bash
# Start reader replicas with WebDriver
docker-compose up -d jsonrpc webdriver
# Scale readers
docker-compose up -d --scale jsonrpc=15
```

### VM3 (Write Farm)
```bash
# Start consensus workers with WebDriver
docker-compose up -d jsonrpc consensus-worker webdriver
# Scale writers
docker-compose up -d --scale consensus-worker=10
```

### Development
```bash
# Start everything with profiles
docker-compose --profile scalability up -d
```

## How Traefik Routes Requests

With this setup, Traefik automatically routes based on the available services:

1. **VM1 (Infrastructure)**: 
   - Gets all non-GenVM requests
   - Simple database queries, account info, etc.

2. **VM2 (Read Farm)**:
   - Gets GenVM read requests (call, staticcall)
   - Load balanced across 15 replicas

3. **VM3 (Write Farm)**:
   - Consensus workers pull from ZeroMQ
   - Not directly accessible via HTTP

The beauty is that each VM configures itself based on the READER_WORKERS and WRITER_WORKERS values, no need for complex configuration!