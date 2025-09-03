# GenLayer Studio Scalability Testing Guide

This guide provides comprehensive instructions for launching and testing GenLayer Studio in different scalability modes.

## Table of Contents
- [Quick Start](#quick-start)
- [Deployment Modes](#deployment-modes)
- [Local Testing](#local-testing)
- [Multi-VM Production Setup](#multi-vm-production-setup)
- [Testing Procedures](#testing-procedures)
- [Monitoring](#monitoring)
- [Troubleshooting](#troubleshooting)

## Quick Start

### Prerequisites
```bash
# Install Docker and Docker Compose
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Clone repository
git clone https://github.com/genlayer/genlayer-studio.git
cd genlayer-studio

# Copy environment file
cp .env.example .env
```

### Quick Launch - Development Mode
```bash
# Launch with 1 reader and 1 writer (hybrid mode)
READER_WORKERS=1 WRITER_WORKERS=1 CONSENSUS_MODE=zmq docker-compose up -d

# Check status
docker-compose ps
curl http://localhost:4000/api/health
```

## Deployment Modes

| Mode | READER_WORKERS | WRITER_WORKERS | Use Case |
|------|---------------|----------------|----------|
| Simple RPC | 0 | 0 | Infrastructure node, no GenVM |
| Reader Farm | N > 0 | 0 | High-volume read operations |
| Writer Farm | 0 | N > 0 | High-volume transaction processing |
| Hybrid | N > 0 | M > 0 | Full production scaling |

## Local Testing

### Test Each Mode Sequentially

#### 1. Simple RPC Mode (Infrastructure Only)
```bash
# Clean start
docker-compose down -v
docker volume prune -f

# Configure
export READER_WORKERS=0
export WRITER_WORKERS=0
export CONSENSUS_MODE=legacy

# Launch
docker-compose up -d

# Verify services
docker-compose ps
# Should show: jsonrpc, postgres, traefik
# Should NOT show: webdriver, zmq-broker, redis, consensus-worker

# Test basic RPC
curl -X POST http://localhost:4000/api \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'

# Expected: {"jsonrpc":"2.0","id":1,"result":"0x0"}
```

#### 2. Reader Farm Mode (GenVM Reads)
```bash
# Reconfigure
docker-compose down
export READER_WORKERS=3
export WRITER_WORKERS=0
export CONSENSUS_MODE=legacy
export JSONRPC_REPLICAS=3

# Launch
docker-compose up -d

# Verify services
docker-compose ps
# Should show: 3x jsonrpc, webdriver (running), postgres, traefik
# Should NOT show: zmq-broker, redis, consensus-worker

# Test load balancing
for i in {1..10}; do
  echo "Request $i:"
  curl -s -X POST http://localhost:4000/api \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":'$i'}' \
    | jq -r '.result'
done

# Check distribution across replicas
docker-compose logs jsonrpc | grep "ping" | awk '{print $1}' | sort | uniq -c
```

#### 3. Writer Farm Mode (Consensus Processing)
```bash
# Reconfigure
docker-compose down
export READER_WORKERS=0
export WRITER_WORKERS=5
export CONSENSUS_MODE=zmq

# Launch
docker-compose up -d

# Verify services
docker-compose ps
# Should show: jsonrpc, zmq-broker, redis, 5x consensus-worker
# Should show: webdriver (for consensus validation)

# Monitor broker
curl http://localhost:5561/metrics | jq

# Test transaction routing
# First, deploy a contract (example)
DEPLOY_TX='{"jsonrpc":"2.0","method":"eth_sendRawTransaction","params":["0x..."],"id":1}'
curl -X POST http://localhost:4000/api \
  -H "Content-Type: application/json" \
  -d "$DEPLOY_TX"

# Monitor worker activity
docker-compose logs -f consensus-worker | grep "Processing"
```

#### 4. Hybrid Mode (Full Scaling)
```bash
# Reconfigure for production-like setup
docker-compose down
export READER_WORKERS=2
export WRITER_WORKERS=3
export CONSENSUS_MODE=zmq
export JSONRPC_REPLICAS=2
export MAX_GENVM_PER_WORKER=5
export MAX_GENVM_TOTAL=15

# Launch
docker-compose up -d

# Full verification
docker-compose ps

# Should see:
# - 2x jsonrpc replicas
# - 3x consensus-worker replicas  
# - 1x zmq-broker (running)
# - 1x redis (running)
# - 1x webdriver (running)
# - 1x postgres (running)
# - 1x traefik (running)
```

## Multi-VM Production Setup

### Architecture Overview
```
Internet → Load Balancer → VM1 (Simple RPC)
                        ↘ VM2 (Reader Farm)
                        ↘ VM3 (Writer Farm)
```

### VM Setup Scripts

#### VM1: Simple RPC Node
```bash
# vm1-setup.sh
#!/bin/bash
SERVER_IP=10.0.1.10
POSTGRES_IP=10.0.0.5
REDIS_IP=10.0.3.10

cat > .env << EOF
READER_WORKERS=0
WRITER_WORKERS=0
CONSENSUS_MODE=legacy
DBHOST=$POSTGRES_IP
REDIS_HOST=$REDIS_IP
SERVER_NAME=rpc.genlayer.com
EOF

docker-compose up -d
```

#### VM2: Reader Farm
```bash
# vm2-setup.sh
#!/bin/bash
SERVER_IP=10.0.2.10
POSTGRES_IP=10.0.0.5

cat > .env << EOF
READER_WORKERS=5
WRITER_WORKERS=0
CONSENSUS_MODE=legacy
DBHOST=$POSTGRES_IP
JSONRPC_REPLICAS=5
MAX_GENVM_PER_WORKER=10
MAX_GENVM_TOTAL=50
SERVER_NAME=readers.genlayer.com
EOF

docker-compose up -d
```

#### VM3: Writer Farm
```bash
# vm3-setup.sh
#!/bin/bash
SERVER_IP=10.0.3.10
POSTGRES_IP=10.0.0.5

cat > .env << EOF
READER_WORKERS=0
WRITER_WORKERS=10
CONSENSUS_MODE=zmq
DBHOST=$POSTGRES_IP
MAX_GENVM_PER_WORKER=5
MAX_GENVM_TOTAL=50
SERVER_NAME=writers.genlayer.com
EOF

docker-compose up -d
```

## Testing Procedures

### Create Test Script
```bash
cat > test_scalability.sh << 'EOF'
#!/bin/bash

API_URL=${1:-http://localhost:4000/api}
echo "Testing GenLayer at $API_URL"
echo "================================"

# Color codes
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

# Test health
echo -n "1. Health check... "
HEALTH=$(curl -s $API_URL/health 2>/dev/null | jq -r '.status' 2>/dev/null)
if [ "$HEALTH" = "healthy" ]; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED${NC}"
    exit 1
fi

# Test system metrics
echo -n "2. System metrics... "
METRICS=$(curl -s $API_URL/metrics/system 2>/dev/null)
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ PASSED${NC}"
    echo "   Workers: $(echo $METRICS | jq -r '.workers | "\(.readers) readers, \(.writers) writers"')"
else
    echo -e "${RED}✗ FAILED${NC}"
fi

# Test read operation
echo -n "3. Read operations... "
for i in {1..5}; do
    curl -s -X POST $API_URL \
        -H "Content-Type: application/json" \
        -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":'$i'}' \
        >/dev/null 2>&1
done
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ PASSED${NC}"
else
    echo -e "${RED}✗ FAILED${NC}"
fi

# Test consensus metrics (if available)
echo -n "4. Consensus metrics... "
CONSENSUS=$(curl -s $API_URL/metrics/consensus 2>/dev/null)
if [ $? -eq 0 ] && [ -n "$CONSENSUS" ]; then
    echo -e "${GREEN}✓ PASSED${NC}"
    echo "   Mode: $(echo $CONSENSUS | jq -r '.mode')"
else
    echo -e "${RED}✗ SKIPPED${NC} (not in consensus mode)"
fi

# Test ZMQ broker (if running)
echo -n "5. ZMQ Broker... "
if curl -s http://localhost:5561/metrics >/dev/null 2>&1; then
    BROKER=$(curl -s http://localhost:5561/metrics)
    echo -e "${GREEN}✓ PASSED${NC}"
    echo "   Active workers: $(echo $BROKER | jq -r '.workers | length')"
    echo "   Queued transactions: $(echo $BROKER | jq -r '.total_queued')"
else
    echo -e "${RED}✗ SKIPPED${NC} (broker not running)"
fi

echo "================================"
echo "Testing complete!"
EOF

chmod +x test_scalability.sh
./test_scalability.sh
```

### Load Testing
```bash
# Install Apache Bench
sudo apt-get install apache2-utils

# Simple load test
ab -n 1000 -c 10 -p request.json -T application/json \
   http://localhost:4000/api

# Create request.json
echo '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}' > request.json

# Monitor during load test
watch -n 1 'docker stats --no-stream'
```

## Monitoring

### Real-time Monitoring Dashboard
```bash
# Create monitoring script
cat > monitor.sh << 'EOF'
#!/bin/bash
while true; do
    clear
    echo "=== GenLayer Studio Monitor ==="
    echo "Time: $(date)"
    echo ""
    
    echo "=== Services Status ==="
    docker-compose ps --format "table {{.Name}}\t{{.Status}}\t{{.Ports}}"
    echo ""
    
    echo "=== Resource Usage ==="
    docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"
    echo ""
    
    if [ "$WRITER_WORKERS" -gt "0" ]; then
        echo "=== ZMQ Broker Status ==="
        curl -s http://localhost:5561/metrics 2>/dev/null | jq '.' 2>/dev/null || echo "Broker not available"
        echo ""
    fi
    
    echo "=== Recent Logs ==="
    docker-compose logs --tail=5 2>&1 | grep -E "ERROR|WARNING|Processing"
    
    sleep 5
done
EOF

chmod +x monitor.sh
./monitor.sh
```

### Metrics Endpoints
```bash
# System metrics
curl http://localhost:4000/api/metrics/system | jq

# Consensus metrics
curl http://localhost:4000/api/metrics/consensus | jq

# ZMQ broker metrics
curl http://localhost:5561/metrics | jq

# Worker health
curl http://localhost:4000/api/health | jq
```

## Troubleshooting

### Common Issues

#### Services Not Starting
```bash
# Check docker-compose configuration
docker-compose config

# Check individual service logs
docker-compose logs jsonrpc
docker-compose logs zmq-broker
docker-compose logs consensus-worker

# Verify environment variables
env | grep WORKERS
```

#### Transactions Not Processing
```bash
# Check consensus mode
echo $CONSENSUS_MODE

# Check pending transactions
docker-compose exec postgres psql -U genlayer -d genlayer \
  -c "SELECT COUNT(*), status FROM transactions GROUP BY status;"

# Check ZMQ broker queues
curl http://localhost:5561/status | jq '.contract_queues'

# Force reprocess stuck transactions
docker-compose restart consensus-worker
```

#### High Memory Usage
```bash
# Check WebDriver sessions
curl http://localhost:4444/status | jq '.value.nodes[].slots'

# Reduce sessions per worker
export MAX_GENVM_PER_WORKER=3
docker-compose up -d

# Clear Redis cache
docker-compose exec redis redis-cli FLUSHALL
```

#### Network Issues Between VMs
```bash
# Test connectivity
ping -c 3 <other-vm-ip>
telnet <other-vm-ip> 5432  # PostgreSQL
telnet <other-vm-ip> 6379  # Redis
telnet <other-vm-ip> 5557  # ZMQ

# Check firewall rules
sudo iptables -L -n
sudo ufw status
```

## Performance Tuning

### PostgreSQL Optimization
```sql
-- Connect to database
docker-compose exec postgres psql -U genlayer -d genlayer

-- Add performance indexes
CREATE INDEX CONCURRENTLY idx_tx_pending ON transactions(to_address, status) 
  WHERE status = 'pending';
CREATE INDEX CONCURRENTLY idx_tx_contract ON transactions(to_address);
CREATE INDEX CONCURRENTLY idx_tx_created ON transactions(created_at);

-- Tune settings
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
SELECT pg_reload_conf();
```

### Docker Resource Limits
```yaml
# docker-compose.override.yml
services:
  jsonrpc:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '1'
          memory: 1G
  
  consensus-worker:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 1G
```

## Production Checklist

Before deploying to production:

- [ ] Set `BACKEND_BUILD_TARGET=prod` in .env
- [ ] Set `FRONTEND_BUILD_TARGET=final` in .env  
- [ ] Configure SSL certificates
- [ ] Set up monitoring (Prometheus/Grafana)
- [ ] Configure log aggregation
- [ ] Set up PostgreSQL replication
- [ ] Enable Redis persistence
- [ ] Configure backups
- [ ] Test disaster recovery
- [ ] Load test with expected traffic
- [ ] Create runbooks
- [ ] Set up alerts

## Quick Reference

```bash
# Launch modes
READER_WORKERS=0 WRITER_WORKERS=0 docker-compose up -d  # Simple RPC
READER_WORKERS=3 WRITER_WORKERS=0 docker-compose up -d  # Reader farm
READER_WORKERS=0 WRITER_WORKERS=5 docker-compose up -d  # Writer farm
READER_WORKERS=3 WRITER_WORKERS=5 docker-compose up -d  # Hybrid

# Monitoring
docker-compose ps                          # Service status
docker-compose logs -f                     # Live logs
curl http://localhost:4000/api/health      # Health check
curl http://localhost:5561/metrics         # Broker metrics

# Management
docker-compose restart consensus-worker    # Restart workers
docker-compose scale consensus-worker=10   # Scale workers
docker-compose exec redis redis-cli        # Redis CLI
docker-compose exec postgres psql -U genlayer  # PostgreSQL CLI
```