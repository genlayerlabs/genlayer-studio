# Load Test Report: 25 Instances Analysis

## Test Overview

- **Test Type**: Mixed Load Test - Contract & Endpoint
- **API URL**: https://studio-stress.genlayer.com/api
- **Number of Instances**: 25 parallel test instances
- **Load per Endpoint**: 1,000 requests / 100 concurrent connections
- **Test Pattern**: 2 parallel contract reads + 2 parallel endpoint tests
- **Date**: September 9, 2025

## Executive Summary

The load test with 25 parallel instances revealed significant system degradation compared to the 20-instance test. The system showed a **19.8% success rate for contract reads** (down from 90.6% with 20 instances) and **21.2% success rate for endpoint tests** (down from 99.7%). This indicates the system reached a critical threshold between 20 and 25 concurrent instances where performance dramatically deteriorates.

## Overall Statistics

### Contract Operations
- **Total Contract Deployments**: 125 (5 per instance)
- **Deployment Success Rate**: 100% (125/125)
- **Total Contract Read Attempts**: 450 (18 per instance)
- **Contract Read Successes**: 89
- **Contract Read Failures**: 361
- **Contract Read Success Rate**: 19.8%

### Endpoint Operations
- **Total Endpoint Tests**: 425 (17 endpoints × 25 instances)
- **Endpoint Test Successes**: 90
- **Endpoint Test Failures**: 335
- **Endpoint Success Rate**: 21.2%

## Instance-by-Instance Analysis

| Instance | Successes | Failures | Success Rate |
|----------|-----------|----------|--------------|
| instance_1 | 18 | 27 | 40.0% |
| instance_2 | 19 | 26 | 42.2% |
| instance_3 | 18 | 27 | 40.0% |
| instance_4 | 18 | 27 | 40.0% |
| instance_5 | 17 | 28 | 37.8% |
| instance_6 | 17 | 28 | 37.8% |
| instance_7 | 18 | 27 | 40.0% |
| instance_8 | 17 | 28 | 37.8% |
| instance_9 | 17 | 28 | 37.8% |
| instance_10 | 17 | 28 | 37.8% |
| instance_11 | 19 | 26 | 42.2% |
| instance_12 | 17 | 28 | 37.8% |
| instance_13 | 18 | 27 | 40.0% |
| instance_14 | 18 | 27 | 40.0% |
| instance_15 | 16 | 29 | 35.6% |
| instance_16 | 18 | 27 | 40.0% |
| instance_17 | 18 | 27 | 40.0% |
| instance_18 | 15 | 30 | 33.3% |
| instance_19 | 18 | 27 | 40.0% |
| instance_20 | 18 | 27 | 40.0% |
| instance_21 | 16 | 29 | 35.6% |
| instance_22 | 17 | 28 | 37.8% |
| instance_23 | 18 | 27 | 40.0% |
| instance_24 | 17 | 28 | 37.8% |
| instance_25 | 18 | 27 | 40.0% |

**Best Performers**: instances 2, 11 (42.2% success rate)
**Worst Performer**: instance 18 (33.3% success rate)

## Endpoint Performance

### Endpoint Success Rates

| Endpoint | Successes | Total | Success Rate |
|----------|-----------|-------|--------------|
| ping | 24 | 50 | 48.0% |
| eth_blockNumber | 24 | 50 | 48.0% |
| net_version | 14 | 50 | 28.0% |
| sim_getFinalityWindowTime | 14 | 50 | 28.0% |
| eth_gasPrice | 10 | 50 | 20.0% |
| eth_chainId | 10 | 50 | 20.0% |
| sim_countValidators | 1 | 50 | 2.0% |
| sim_getAllValidators | 1 | 50 | 2.0% |
| eth_getBalance | 0 | 50 | 0.0% |
| eth_getTransactionCount | 0 | 50 | 0.0% |
| eth_getBlockByNumber | 0 | 50 | 0.0% |
| eth_getBlockByHash | 0 | 50 | 0.0% |
| eth_getTransactionByHash | 0 | 50 | 0.0% |
| eth_getTransactionReceipt | 0 | 50 | 0.0% |
| sim_getValidator | 0 | 50 | 0.0% |
| sim_getTransactionsForAddress | 0 | 50 | 0.0% |
| sim_getConsensusContract | 0 | 50 | 0.0% |

### Critical Failures
- **Complete Failures (0% success)**: 9 endpoints completely failed across all instances
- **Near-Complete Failures (<5% success)**: sim_countValidators, sim_getAllValidators
- **Partial Functionality**: Only simple endpoints (ping, eth_blockNumber) maintained reasonable success rates

## Contract Read Failure Analysis

### Failure Distribution by Read Number

| Read # | Failures | Percentage of Total Attempts |
|--------|----------|------------------------------|
| Read 7-18 | 25 each | 5.6% each |
| Read 8 | 24 | 5.3% |
| Read 5 | 18 | 4.0% |
| Read 2 | 18 | 4.0% |
| Read 6 | 16 | 3.6% |
| Read 1 | 8 | 1.8% |
| Read 3 | 1 | 0.2% |
| Read 4 | 1 | 0.2% |

### Notable Failure Patterns

1. **Systematic Failure After Read 6**:
   - Reads 7-18 failed almost universally (24-25 failures out of 25 attempts)
   - Indicates system reaches capacity limit early in the test sequence
   - Later reads have virtually no chance of success

2. **Timeout Patterns**:
   - Many failures show 30-second timeouts (30012ms - 30027ms)
   - Suggests requests are queuing and timing out rather than being rejected immediately
   - System appears to be overwhelmed and unable to process requests

3. **Early Success Window**:
   - Reads 1-4 show higher success rates
   - System performs adequately until resource exhaustion occurs
   - Performance cliff effect after initial operations

## Resource Usage Analysis

### Test Duration
- **Start Time**: 2025-09-09 09:13:04
- **End Time**: 2025-09-09 09:35:21
- **Total Duration**: 22 minutes 17 seconds
- **Monitoring Points**: 3,208 measurements

### Resource Consumption Statistics

| Metric | Average | Maximum | Minimum |
|--------|---------|---------|---------|
| CPU Usage | 4.22% | 26.1% | ~1.7% |
| Memory Usage | 15.98% | 17.0% | ~15.7% |
| Memory (MB) | 10,311 MB | ~10,950 MB | ~10,118 MB |
| Load (1 min) | 0.63 | 2.40 | 0.16 |
| Load (5 min) | 0.61 | 1.52 | 0.33 |
| Load (15 min) | 0.69 | 1.12 | 0.52 |

### Resource Usage Patterns

1. **Lower CPU Usage Than 20-Instance Test**:
   - Peak CPU only 26.1% (vs 30.6% with 20 instances)
   - Average CPU 4.22% (vs 8.68% with 20 instances)
   - Suggests the system is failing fast rather than processing requests
   - CPU is not the bottleneck - other resources are constraining performance

2. **Memory Consumption**:
   - Memory usage remained stable (15.7% - 17.0%)
   - Total memory usage ~10.3 GB average
   - Lower than 20-instance test (which peaked at 12.1 GB)
   - Memory not fully utilized due to other bottlenecks

3. **System Load**:
   - Peak 1-minute load of 2.40 (vs 3.00 with 20 instances)
   - Lower average load indicates less actual work being done
   - System is rejecting/timing out requests rather than processing them

### Critical Resource Events

| Timestamp | Event | CPU% | Memory% | Load |
|-----------|-------|------|---------|------|
| 09:15:54 | Peak CPU | 26.1% | 15.9% | 1.74 |
| 09:16:01 | Peak Load | 19.5% | 15.9% | 2.40 |
| 09:18:51 | Peak Memory | 7.4% | 17.0% | 0.82 |
| 09:35:21 | Test End | 2.8% | 15.8% | 0.16 |

### Resource-Performance Correlation

1. **Paradoxical Resource Usage**:
   - Lower resource usage with worse performance
   - System hitting artificial limits (connection pools, rate limits, etc.)
   - Not a hardware resource issue

2. **Early Resource Spike**:
   - Peak load occurs early (09:16:01) then decreases
   - System gives up on processing after initial overload
   - Self-limiting behavior to prevent complete failure

3. **Stable Memory**:
   - Memory remains consistent throughout
   - No memory leak evident
   - Memory not the limiting factor

## Comparison with 20-Instance Test

| Metric | 20 Instances | 25 Instances | Change |
|--------|--------------|--------------|--------|
| Contract Read Success | 90.6% | 19.8% | -70.8% |
| Endpoint Success | 99.7% | 21.2% | -78.5% |
| Peak CPU | 30.6% | 26.1% | -4.5% |
| Peak Memory | 18.8% | 17.0% | -1.8% |
| Peak Load | 3.00 | 2.40 | -0.60 |
| Test Duration | 31 min | 22 min | -9 min |

## Key Findings

### Critical Issues
1. **System Capacity Threshold Exceeded**: Performance cliff between 20 and 25 instances
2. **Cascade Failure Pattern**: Early failures lead to system-wide degradation
3. **Complete Endpoint Failure**: 9 out of 17 endpoints have 0% success rate
4. **Contract Read Collapse**: 80% failure rate indicates severe system stress

### Paradoxical Observations
1. **Lower Resource Usage with Worse Performance**: System is failing fast, not processing
2. **Shorter Test Duration**: Tests completing faster due to failures, not efficiency
3. **Connection/Rate Limiting**: Artificial limits preventing resource utilization

### System Behavior
1. **Early Success Window**: First few operations succeed before system overload
2. **Timeout Dominance**: 30-second timeouts indicate queuing issues
3. **Self-Protection**: System appears to have circuit breakers or rate limiters engaging

## Recommendations

### Immediate Actions
1. **Do Not Deploy with 25+ Instances**:
   - System clearly cannot handle this load
   - Stay below 20 concurrent instances for production

2. **Investigate Connection Limits**:
   - Check database connection pool settings
   - Review API rate limiting configurations
   - Analyze network connection limits

3. **Timeout Configuration**:
   - Reduce timeout from 30 seconds to fail fast
   - Implement exponential backoff
   - Add circuit breakers to prevent cascade failures

### System Tuning
1. **Connection Pool Optimization**:
   - Increase database connection pool size
   - Implement connection pooling for external services
   - Add connection pool monitoring

2. **Rate Limiting Review**:
   - Identify and adjust rate limits causing failures
   - Implement adaptive rate limiting
   - Add queue management for burst handling

3. **Load Balancing**:
   - Implement request queuing with fair scheduling
   - Add horizontal scaling capabilities
   - Consider microservice separation for critical endpoints

### Architecture Changes
1. **Caching Layer**:
   - Add Redis/Memcached for frequently accessed data
   - Implement query result caching
   - Cache contract read results

2. **Async Processing**:
   - Move heavy operations to background queues
   - Implement webhook callbacks for long operations
   - Add job status tracking

3. **Service Mesh**:
   - Implement service mesh for better traffic management
   - Add circuit breakers at service level
   - Enable gradual rollout and canary deployments

## Conclusion

The 25-instance load test reveals a critical performance threshold has been exceeded. The system experiences catastrophic failure with a 70.8% drop in contract read success and 78.5% drop in endpoint success compared to 20 instances. This is not due to hardware resource exhaustion (CPU peaked at only 26.1%, memory at 17.0%) but rather architectural limitations such as connection pools, rate limits, or other artificial constraints.

**Critical Recommendation**: The system should not be operated with more than 20 concurrent instances without significant architectural improvements. The performance degradation between 20 and 25 instances is severe enough to render the system effectively non-functional for most operations.

The lower resource utilization paradoxically paired with worse performance strongly indicates that the system is hitting soft limits (connections, rate limits, queues) rather than hardware constraints. This suggests that scaling horizontally or increasing hardware resources alone will not solve the problem - architectural changes are required to handle loads beyond 20 concurrent instances.