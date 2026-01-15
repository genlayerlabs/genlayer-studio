# GenLayer Studio Load Testing Framework

## Table of Contents
- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Architecture](#architecture)
- [Scripts Documentation](#scripts-documentation)
- [Running Scripts with Arguments](#running-scripts-with-arguments)
- [Test Results Structure](#test-results-structure)
- [Usage Guide](#usage-guide)
- [Test Patterns](#test-patterns)
- [Analysis and Reporting](#analysis-and-reporting)
- [Remote Host Monitoring](#remote-host-monitoring)
- [Best Practices](#best-practices)
- [Troubleshooting](#troubleshooting)

## Overview

The GenLayer Studio Load Testing Framework is a comprehensive suite of tools designed to stress-test and benchmark the GenLayer blockchain platform. It simulates realistic load patterns including contract deployments, contract reads, and RPC endpoint calls to identify performance bottlenecks and system limits.

### Key Features
- **Mixed Load Testing**: Alternates between contract operations and endpoint calls
- **Multi-Instance Orchestration**: Spawn multiple parallel test instances
- **Resource Monitoring**: Track CPU, memory, and system load during tests
- **Comprehensive Reporting**: Detailed analysis with success rates and failure patterns
- **CI/CD Ready**: Can be integrated into GitHub Actions workflows

## Prerequisites

### Required Software
- **Docker & Docker Compose**: For running GenLayer services
- **Python 3.8+**: For contract deployment scripts
- **oha**: HTTP load testing tool ([Installation](https://github.com/hatoo/oha))
  ```bash
  cargo install oha
  ```
- **curl**: For API interactions
- **bash 4.0+**: For script execution

### Python Dependencies
```bash
pip install genlayer-py requests
```

### System Requirements
- **Memory**: Minimum 16GB RAM (32GB recommended for 20+ instances)
- **CPU**: 8+ cores recommended for parallel testing
- **Network**: Stable connection with low latency to target API

## Architecture

```
tests/load/
├── test_workflow_locally.sh      # Main workflow orchestrator
├── test_mixed_load.sh           # Single instance mixed load test
├── spawn_mixed_load_tests.sh    # Multi-instance spawner
├── monitor_resources.sh         # Resource monitoring utility
├── setup_validators.sh          # Validator setup script
├── deploy_contract/             # Contract deployment utilities
│   └── wizard_deploy.py         # WizardOfCoin contract deployer
├── spawn_logs_*/                # Test execution logs
├── test_results/                # Analysis reports and data
│   ├── 20_instances/           # 20-instance test results
│   ├── 25_instances/           # 25-instance test results
│   └── 30_instances/           # 30-instance test results
└── *.csv                       # Resource usage data files
```

## Scripts Documentation

### test_workflow_locally.sh
**Purpose**: Simulates the GitHub Actions workflow locally, providing a complete test pipeline.

**Features**:
- Chain ID verification with retries
- Validator setup (5 validators)
- Sequential or parallel contract deployments
- Contract read operations (sequential or parallel)
- Comprehensive endpoint testing
- Optional resource monitoring

### test_mixed_load.sh
**Purpose**: Executes a single instance of mixed load testing with alternating contract and endpoint operations.

**Test Pattern**:
- Deploy 5 contracts
- Execute 18 contract reads (2 parallel at a time)
- Test 17 different RPC endpoints (2 parallel at a time)
- 5-second delays between test groups

### spawn_mixed_load_tests.sh
**Purpose**: Orchestrates multiple parallel instances of test_mixed_load.sh for stress testing.

**Features**:
- Spawn N instances in parallel
- Individual log files per instance
- Process management with cleanup on exit
- Real-time status monitoring
- Summary report generation

## Running Scripts with Arguments

### test_workflow_locally.sh

**Syntax**:
```bash
./test_workflow_locally.sh [API_URL] [MODE] [monitor]
```

**Arguments**:
- `API_URL` (optional): Target API endpoint
  - Default: `http://localhost:4000/api`
  - Example: `https://studio-stress.genlayer.com/api`
- `MODE` (optional): Test execution mode
  - `read-parallel`: Enables parallel contract reads (default: sequential)
- `monitor` (optional): Enable resource monitoring
  - Creates CSV file with CPU, memory, and load metrics

**Environment Variables**:
- `REQUESTS`: Number of requests per endpoint (default: 1000)
- `CONCURRENCY`: Concurrent connections (default: 100)

**Examples**:
```bash
# Local test with default settings
./test_workflow_locally.sh

# Remote API with monitoring
./test_workflow_locally.sh https://studio-stress.genlayer.com/api monitor

# Parallel reads with high load
./test_workflow_locally.sh http://localhost:4000/api read-parallel monitor

# Custom request configuration
REQUESTS=5000 CONCURRENCY=200 ./test_workflow_locally.sh monitor
```

### test_mixed_load.sh

**Syntax**:
```bash
./test_mixed_load.sh [API_URL] [monitor]
```

**Arguments**:
- `API_URL` (optional): Target API endpoint
  - Default: `http://localhost:4000/api`
- `monitor` (optional): Enable resource monitoring

**Environment Variables**:
- `REQUESTS`: Requests per endpoint test (default: 1000)
- `CONCURRENCY`: Concurrent connections (default: 100)

**Examples**:
```bash
# Basic mixed load test
./test_mixed_load.sh

# Test remote API with monitoring
./test_mixed_load.sh https://studio-stress.genlayer.com/api monitor

# High concurrency test
REQUESTS=10000 CONCURRENCY=500 ./test_mixed_load.sh monitor
```

### spawn_mixed_load_tests.sh

**Syntax**:
```bash
./spawn_mixed_load_tests.sh [NUM_INSTANCES] [API_URL] [monitor]
```

**Arguments**:
- `NUM_INSTANCES` (optional): Number of parallel test instances
  - Default: 10
  - Recommended maximum: 20 (system degrades at 25+)
- `API_URL` (optional): Target API endpoint
  - Default: `http://localhost:4000/api`
- `monitor` (optional): Enable monitoring for first instance only

**Examples**:
```bash
# Spawn 10 instances (default)
./spawn_mixed_load_tests.sh

# Spawn 20 instances against remote API
./spawn_mixed_load_tests.sh 20 https://studio-stress.genlayer.com/api

# 15 instances with monitoring
./spawn_mixed_load_tests.sh 15 http://localhost:4000/api monitor

# Maximum recommended load
REQUESTS=5000 CONCURRENCY=200 ./spawn_mixed_load_tests.sh 20 https://studio-stress.genlayer.com/api monitor
```

### Combined Usage Scenarios

**Scenario 1: Progressive Load Testing**
```bash
# Start with single instance baseline
./test_mixed_load.sh monitor

# Test with 10 instances
./spawn_mixed_load_tests.sh 10 monitor

# Increase to 20 instances (recommended maximum)
./spawn_mixed_load_tests.sh 20 monitor
```

**Scenario 2: CI/CD Integration**
```bash
# Simulate GitHub Actions workflow
./test_workflow_locally.sh $CI_API_URL read-parallel

# Run stress test in CI
./spawn_mixed_load_tests.sh 15 $CI_API_URL
```

**Scenario 3: Performance Benchmarking**
```bash
# Baseline test
REQUESTS=1000 CONCURRENCY=100 ./test_mixed_load.sh monitor

# Stress test
REQUESTS=5000 CONCURRENCY=500 ./test_mixed_load.sh monitor

# Maximum load test
REQUESTS=10000 CONCURRENCY=1000 ./spawn_mixed_load_tests.sh 20 monitor
```

## Test Results Structure

### Directory Organization
```
test_results/
├── 20_instances/
│   ├── 20_instances_report.md          # Comprehensive analysis report
│   ├── resource_usage_20_instances.csv # Resource monitoring data
│   └── spawn_logs_20_instances/        # Individual instance logs
│       ├── instance_1.log
│       ├── instance_2.log
│       └── ...
├── 25_instances/
│   └── (similar structure)
└── 30_instances/
    └── (similar structure)
```

### Report Contents
Each `*_report.md` file contains:
- **Executive Summary**: High-level success rates and findings
- **Overall Statistics**: Contract deployments, reads, endpoint tests
- **Instance-by-Instance Analysis**: Individual performance metrics
- **Endpoint Performance**: Success rates per RPC endpoint
- **Contract Read Failure Analysis**: Failure patterns and distributions
- **Resource Usage Analysis**: CPU, memory, and load statistics
- **Key Findings**: Strengths, weaknesses, and observations
- **Recommendations**: Actionable improvements

### Resource Usage CSV Format
```csv
timestamp,cpu_percent,memory_percent,memory_used_mb,memory_available_mb,load_1min,load_5min,load_15min
2025-09-09 09:13:04,2.8,15.7,10126,42898,0.27,0.33,0.64
```

## Usage Guide

### Quick Start
1. **Setup GenLayer**:
   ```bash
   genlayer up  # or docker compose up
   ```

2. **Run Basic Test**:
   ```bash
   cd tests/load
   ./test_workflow_locally.sh
   ```

3. **Run Load Test**:
   ```bash
   ./spawn_mixed_load_tests.sh 10
   ```

4. **Analyze Results**:
   ```bash
   cat spawn_logs_*/instance_*.log | grep "✅\|❌" | sort | uniq -c
   ```

### Common Test Scenarios

#### Scenario 1: Development Testing
```bash
# Quick validation test
./test_mixed_load.sh

# Check specific endpoint under load
REQUESTS=100 CONCURRENCY=10 ./test_mixed_load.sh
```

#### Scenario 2: Pre-Production Testing
```bash
# Progressive load increase
for i in 5 10 15 20; do
    echo "Testing with $i instances..."
    ./spawn_mixed_load_tests.sh $i
    sleep 60
done
```

#### Scenario 3: Capacity Planning
```bash
# Find breaking point (careful - this will stress the system)
./spawn_mixed_load_tests.sh 20 monitor  # Should work
./spawn_mixed_load_tests.sh 25 monitor  # Expected to degrade
```

## Test Patterns

### Mixed Load Pattern
The default test pattern alternates between:
1. **2 Parallel Contract Reads**: Stress contract execution
2. **2 Parallel Endpoint Tests**: Stress RPC handling
3. **5-Second Delay**: Allow system recovery

### Endpoints Tested
- Basic: `ping`, `eth_blockNumber`, `eth_gasPrice`, `eth_chainId`
- Network: `net_version`
- Simulator: `sim_getFinalityWindowTime`, `sim_countValidators`, `sim_getAllValidators`
- Account: `eth_getBalance`, `eth_getTransactionCount`
- Blocks: `eth_getBlockByNumber`, `eth_getBlockByHash`
- Transactions: `eth_getTransactionByHash`, `eth_getTransactionReceipt`
- Validator: `sim_getValidator`, `sim_getTransactionsForAddress`, `sim_getConsensusContract`

## Analysis and Reporting

### Key Metrics to Monitor

#### Success Rates
- **Good**: >95% success rate
- **Acceptable**: 85-95% success rate
- **Poor**: <85% success rate
- **Critical**: <50% success rate

#### Performance Thresholds
Based on extensive testing:
- **20 instances**: System remains stable (90.6% contract reads, 99.7% endpoints)
- **25 instances**: Critical degradation (19.8% contract reads, 21.2% endpoints)
- **Recommendation**: Do not exceed 20 concurrent instances

#### Resource Usage Guidelines
- **CPU**: Should stay below 80% peak
- **Memory**: Should not exceed 80% of available RAM
- **Load Average**: Should not exceed 2x number of CPU cores

### Identifying Bottlenecks

1. **Connection Pool Exhaustion**:
   - Symptom: Low CPU/memory but high failure rate
   - Look for: 30-second timeouts, connection refused errors

2. **Rate Limiting**:
   - Symptom: Specific endpoints failing consistently
   - Look for: 429 status codes, "rate limit" messages

3. **Resource Exhaustion**:
   - Symptom: High CPU or memory usage
   - Look for: System load >2x cores, OOM errors

4. **Database Bottlenecks**:
   - Symptom: Contract reads failing, endpoints working
   - Look for: Database connection errors, slow queries

## Remote Host Monitoring

### Monitoring Target Hosts Directly

When testing against remote APIs or distributed systems, you may want to monitor resources directly on the target host rather than on the client machine running the tests. The `monitor_resources.sh` script can be deployed and run independently on any target host.

#### Setup Remote Monitoring

1. **Copy the monitoring script to the target host**:
   ```bash
   scp monitor_resources.sh user@target-host:/path/to/monitoring/
   ```

2. **Start monitoring on the target host**:
   ```bash
   # SSH into the target host
   ssh user@target-host

   # Navigate to monitoring directory
   cd /path/to/monitoring/

   # Start monitoring with 1-second intervals
   ./monitor_resources.sh start resource_usage.csv 1

   # Or run in background with nohup
   nohup ./monitor_resources.sh start resource_usage.csv 1 &
   ```

3. **Run your load tests** from the client machine:
   ```bash
   # On your local machine
   ./spawn_mixed_load_tests.sh 20 https://target-host/api
   ```

4. **Stop monitoring on the target host**:
   ```bash
   # On the target host
   ./monitor_resources.sh stop
   ```

5. **Retrieve the monitoring data**:
   ```bash
   # From your local machine
   scp user@target-host:/path/to/monitoring/resource_usage.csv ./test_results/
   ```

#### Automated Remote Monitoring Workflow

Create a script to automate the entire process:

```bash
#!/bin/bash
# remote_load_test.sh

TARGET_HOST="user@target-host"
MONITORING_PATH="/home/user/monitoring"
API_URL="https://target-host/api"
INSTANCES=20

# Start remote monitoring
ssh $TARGET_HOST "cd $MONITORING_PATH && ./monitor_resources.sh start resource_usage.csv 1 &"

# Run load test
./spawn_mixed_load_tests.sh $INSTANCES $API_URL

# Wait for test completion
wait

# Stop remote monitoring
ssh $TARGET_HOST "cd $MONITORING_PATH && ./monitor_resources.sh stop"

# Retrieve monitoring data
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
scp $TARGET_HOST:$MONITORING_PATH/resource_usage.csv ./test_results/resource_usage_${TIMESTAMP}.csv

echo "Remote monitoring data saved to test_results/resource_usage_${TIMESTAMP}.csv"
```

#### Multiple Host Monitoring

For distributed systems, monitor multiple hosts simultaneously:

```bash
#!/bin/bash
# multi_host_monitoring.sh

declare -a HOSTS=("user@api-server" "user@db-server" "user@cache-server")
MONITORING_PATH="/tmp/monitoring"

# Start monitoring on all hosts
for host in "${HOSTS[@]}"; do
    echo "Starting monitoring on $host"
    ssh $host "mkdir -p $MONITORING_PATH && cd $MONITORING_PATH && \
               curl -O https://raw.githubusercontent.com/.../monitor_resources.sh && \
               chmod +x monitor_resources.sh && \
               ./monitor_resources.sh start resource_usage.csv 1 &" &
done

# Run your load test
echo "Starting load test..."
./spawn_mixed_load_tests.sh 20 https://api-server/api

# Stop monitoring on all hosts and collect data
for host in "${HOSTS[@]}"; do
    echo "Stopping monitoring on $host"
    ssh $host "cd $MONITORING_PATH && ./monitor_resources.sh stop"

    # Extract hostname for file naming
    hostname=$(echo $host | cut -d'@' -f2)
    scp $host:$MONITORING_PATH/resource_usage.csv ./test_results/resource_usage_${hostname}.csv
done

echo "All monitoring data collected in test_results/"
```

#### Analyzing Remote Monitoring Data

After collecting remote monitoring data, you can analyze it alongside your test results:

```bash
# Combine local test logs with remote monitoring data
python3 analyze_results.py \
    --logs ./spawn_logs_*/*.log \
    --resources ./test_results/resource_usage_*.csv \
    --output ./test_results/combined_analysis.md
```

#### Best Practices for Remote Monitoring

1. **Time Synchronization**: Ensure all hosts have synchronized clocks (use NTP)
   ```bash
   # Check time on all hosts
   ssh user@host "date"
   ```

2. **Monitoring Interval**: Use 1-second intervals for detailed analysis, 5-seconds for longer tests
   ```bash
   # High resolution (more data, larger files)
   ./monitor_resources.sh start resource_usage.csv 1

   # Lower resolution (less data, suitable for long tests)
   ./monitor_resources.sh start resource_usage.csv 5
   ```

3. **Disk Space**: Ensure sufficient space for monitoring data
   - 1-second interval: ~3MB per hour
   - 5-second interval: ~600KB per hour

4. **Permissions**: Ensure the monitoring script has necessary permissions
   ```bash
   chmod +x monitor_resources.sh
   # May need sudo for some system metrics
   ```

5. **Cleanup**: Remove old monitoring files periodically
   ```bash
   # On remote host
   find /path/to/monitoring -name "*.csv" -mtime +7 -delete
   ```

#### Security Considerations

- Use SSH keys for authentication instead of passwords
- Consider using a dedicated monitoring user with limited permissions
- Restrict monitoring script to read-only system metrics
- Use secure copy (scp) or rsync over SSH for file transfers
- Clean up monitoring data after retrieval if it contains sensitive information

## Best Practices

### Recommended Configuration
```bash
# Optimal for stability
INSTANCES=15
REQUESTS=1000
CONCURRENCY=100

# Maximum stable load
INSTANCES=20
REQUESTS=2000
CONCURRENCY=200
```

### Pre-Test Checklist
1. ✅ Ensure sufficient system resources
2. ✅ Check network connectivity
3. ✅ Verify services are running
4. ✅ Clear old logs and results
5. ✅ Install all dependencies
6. ✅ Set appropriate ulimits:
   ```bash
   ulimit -n 65536  # Increase file descriptors
   ```

### Test Environment Preparation
```bash
# Clean environment
docker compose down -v
docker compose up -d

# Wait for services
sleep 30

# Verify services
curl -X POST -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
  http://localhost:4000/api
```

### CI/CD Integration
```yaml
# Example GitHub Actions workflow
- name: Run Load Tests
  run: |
    cd tests/load
    ./test_workflow_locally.sh ${{ secrets.API_URL }} read-parallel

- name: Stress Test
  run: |
    ./spawn_mixed_load_tests.sh 15 ${{ secrets.API_URL }}

- name: Collect Results
  uses: actions/upload-artifact@v2
  with:
    name: load-test-results
    path: tests/load/spawn_logs_*
```

## Troubleshooting

### Common Issues and Solutions

#### Issue: "oha is not installed"
**Solution**:
```bash
cargo install oha
# or
brew install oha  # macOS
```

#### Issue: "RPC server is not running"
**Solution**:
```bash
genlayer up
# or
docker compose up
# Wait 30 seconds for services to start
```

#### Issue: High failure rate with low resource usage
**Cause**: Connection pool exhaustion or rate limiting
**Solution**:
- Reduce number of instances
- Increase delays between operations
- Check API rate limits
- Increase connection pool size in configuration

#### Issue: "Failed to read chain ID"
**Cause**: Blockchain not initialized
**Solution**:
```bash
genlayer init
# or manually initialize the chain
```

#### Issue: Contract deployment failures
**Cause**: Validators not set up properly
**Solution**:
```bash
./setup_validators.sh 5
# Wait for validators to stabilize
sleep 30
```

#### Issue: Resource monitoring not working
**Solution**:
```bash
chmod +x monitor_resources.sh
# Ensure you have proper permissions to run ps and system commands
```

### Debug Mode
Enable verbose output for troubleshooting:
```bash
# Add -x to see command execution
bash -x ./test_workflow_locally.sh

# Check individual instance logs
tail -f spawn_logs_*/instance_1.log

# Monitor resource usage in real-time
watch -n 1 'ps aux | grep -E "python|node|docker" | head -20'
```

### Log Analysis Commands
```bash
# Count successes and failures
grep -h "✅\|❌" spawn_logs_*/*.log | sort | uniq -c

# Find slowest operations
grep -oE "\([0-9]+ms\)" spawn_logs_*/*.log | sort -rn | head -20

# Identify failing endpoints
grep "❌" spawn_logs_*/*.log | grep "Endpoint" | cut -d' ' -f4 | sort | uniq -c

# Check for timeout patterns
grep -E "30[0-9]{3}ms" spawn_logs_*/*.log
```

## Performance Recommendations

Based on extensive testing:

1. **Optimal Setup**: 15-20 instances with REQUESTS=1000, CONCURRENCY=100
2. **Do Not Exceed**: 20 concurrent instances (system degrades severely at 25+)
3. **Resource Requirements**:
   - Minimum 16GB RAM
   - 8+ CPU cores
   - SSD storage recommended
4. **Network**: Low latency (<10ms) to API endpoint for best results
5. **Database**: Ensure connection pool size ≥ 2 * number of instances

## Contributing

To add new test patterns or improve the framework:

1. Test scripts should follow the existing naming convention
2. Include proper error handling and cleanup
3. Add resource monitoring support where applicable
4. Document new arguments and environment variables
5. Update this README with new features

## License

This testing framework is part of the GenLayer Studio project. See the main project LICENSE file for details.