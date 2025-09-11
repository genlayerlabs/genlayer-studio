# Load Test Report: 20 Instances Analysis

## Test Overview

- **Test Type**: Mixed Load Test - Contract & Endpoint
- **API URL**: https://studio-stress.genlayer.com/api
- **Number of Instances**: 20 parallel test instances
- **Load per Endpoint**: 1,000 requests / 100 concurrent connections
- **Test Pattern**: 2 parallel contract reads + 2 parallel endpoint tests
- **Date**: September 9, 2025

## Executive Summary

The load test with 20 parallel instances revealed a system that performs well under stress with a **90.6% success rate for contract reads** and **99.7% success rate for endpoint tests**. The main issues are concentrated in specific contract read operations, particularly reads 2, 6, 8, 10, and 18, which account for the majority of failures.

## Overall Statistics

### Contract Operations
- **Total Contract Deployments**: 100 (5 per instance)
- **Deployment Success Rate**: 100% (100/100)
- **Total Contract Read Attempts**: 360 (18 per instance)
- **Contract Read Successes**: 326
- **Contract Read Failures**: 34
- **Contract Read Success Rate**: 90.6%

### Endpoint Operations
- **Total Endpoint Tests**: 340 (17 endpoints × 20 instances)
- **Endpoint Test Successes**: 339
- **Endpoint Test Failures**: 1
- **Endpoint Success Rate**: 99.7%

## Instance-by-Instance Analysis

| Instance | Successes | Failures | Success Rate |
|----------|-----------|----------|--------------|
| instance_1 | 44 | 1 | 97.8% |
| instance_2 | 45 | 0 | 100% |
| instance_3 | 45 | 0 | 100% |
| instance_4 | 43 | 2 | 95.6% |
| instance_5 | 43 | 2 | 95.6% |
| instance_6 | 45 | 0 | 100% |
| instance_7 | 43 | 2 | 95.6% |
| instance_8 | 45 | 0 | 100% |
| instance_9 | 42 | 3 | 93.3% |
| instance_10 | 44 | 1 | 97.8% |
| instance_11 | 41 | 4 | 91.1% |
| instance_12 | 43 | 2 | 95.6% |
| instance_13 | 41 | 4 | 91.1% |
| instance_14 | 44 | 1 | 97.8% |
| instance_15 | 42 | 3 | 93.3% |
| instance_16 | 43 | 2 | 95.6% |
| instance_17 | 43 | 2 | 95.6% |
| instance_18 | 41 | 4 | 91.1% |
| instance_19 | 44 | 1 | 97.8% |
| instance_20 | 44 | 1 | 97.8% |

**Best Performers**: instances 2, 3, 6, 8 (100% success rate)
**Worst Performers**: instances 11, 13, 18 (91.1% success rate)

## Endpoint Performance

### Successful Endpoints (100% Success Rate)
All endpoints except one achieved perfect success rates:
- `ping`
- `eth_blockNumber`
- `eth_gasPrice`
- `eth_chainId`
- `net_version`
- `sim_getFinalityWindowTime`
- `sim_countValidators`
- `sim_getAllValidators`
- `eth_getBalance`
- `eth_getTransactionCount`
- `eth_getBlockByNumber`
- `eth_getBlockByHash`
- `eth_getTransactionByHash`
- `sim_getValidator`
- `sim_getTransactionsForAddress`
- `sim_getConsensusContract`

### Failed Endpoint
- **`eth_getTransactionReceipt`**: 19/20 successful (95% success rate)
  - Single failure occurred in one instance
  - Likely due to timing or resource contention

## Contract Read Failure Analysis

### Failure Distribution by Read Number
| Read # | Failures | Percentage of Total Failures |
|--------|----------|------------------------------|
| Read 2 | 6 | 17.6% |
| Read 10 | 5 | 14.7% |
| Read 6 | 4 | 11.8% |
| Read 18 | 4 | 11.8% |
| Read 8 | 3 | 8.8% |
| Read 7 | 2 | 5.9% |
| Read 17 | 2 | 5.9% |
| Read 12 | 2 | 5.9% |
| Others | 6 | 17.6% |

### Notable Failure Patterns

1. **Timing-Related Failures**:
   - Several failures show extremely long response times before failure (100+ seconds)
   - Examples: Contract Read 18 from `0x39D0121D42CDaaC9dC9418FAEdc164716d6ec7f7` (100073ms)
   - Indicates potential timeout issues or resource exhaustion

2. **Quick Failures**:
   - Many failures occur within 300-500ms
   - Suggests immediate rejection rather than timeout
   - Likely due to resource limits or rate limiting

3. **Pattern Concentration**:
   - Contract Reads 2, 6, 8, 10, and 18 show higher failure rates
   - These appear to be stress points in the test sequence
   - May coincide with peak concurrent load periods

## Resource Usage Analysis

### Test Duration
- **Start Time**: 2025-09-09 08:38:15
- **End Time**: 2025-09-09 09:09:15
- **Total Duration**: 31 minutes
- **Monitoring Points**: 3,962 measurements

### Resource Consumption Statistics

| Metric | Average | Maximum | Minimum |
|--------|---------|---------|---------|
| CPU Usage | 8.68% | 30.6% | 0% |
| Memory Usage | 15.93% | 18.8% | 13.5% |
| Memory (MB) | 10,277 MB | 12,095 MB | 8,708 MB |
| Load (1 min) | 1.18 | 3.00 | 0.20 |
| Load (5 min) | 1.19 | 2.83 | 0.42 |
| Load (15 min) | 1.10 | 1.52 | 0.76 |

### Resource Usage Patterns

1. **CPU Spikes**:
   - Peak CPU usage reached 30.6% at 08:42:19 and 08:55:48
   - Multiple sustained periods above 25% CPU usage
   - Correlates with heavy concurrent contract operations
   - System maintained stability even during peak loads

2. **Memory Consumption**:
   - Memory usage remained relatively stable (13.5% - 18.8%)
   - Gradual increase from ~8.9 GB to ~12.1 GB peak
   - Memory leak not evident - usage stabilized after load
   - Approximately 3.4 GB memory increase during peak load

3. **System Load**:
   - 1-minute load average peaked at 3.00 (08:42:19)
   - Load remained manageable throughout the test
   - No signs of system overload or thrashing
   - Gradual decline in load average after peak periods

### Critical Resource Events

| Timestamp | Event | CPU% | Memory% | Load |
|-----------|-------|------|---------|------|
| 08:42:19 | Peak CPU & Load | 30.6% | 14.8% | 3.00 |
| 08:55:48 | Secondary CPU Peak | 30.6% | 18.5% | 1.25 |
| 08:55:54 | Sustained High CPU | 29.5% | 18.7% | 1.23 |
| 09:09:15 | Test End (Recovery) | 3.3% | 15.7% | 0.20 |

### Resource-Performance Correlation

1. **High CPU periods coincide with**:
   - Multiple concurrent contract read operations
   - Validator consensus processing
   - Heavy endpoint request processing

2. **Memory growth patterns**:
   - Steady increase during contract deployments
   - Plateaus during steady-state testing
   - No significant memory releases during test

3. **System remained stable**:
   - No out-of-memory conditions
   - CPU never reached critical levels (>80%)
   - Load averages indicate healthy system response

## Key Findings

### Strengths
1. **Excellent Endpoint Reliability**: 99.7% success rate demonstrates robust endpoint handling
2. **Perfect Deployment Success**: All 100 contract deployments succeeded
3. **Consistent Performance**: 4 instances achieved 100% success rate
4. **Validator Stability**: All validator setup operations completed successfully
5. **Resource Efficiency**: System maintained <31% CPU and <19% memory usage under heavy load
6. **System Stability**: No resource exhaustion or critical failures during 31-minute test

### Weaknesses
1. **Contract Read Reliability**: 9.4% failure rate needs investigation
2. **Inconsistent Instance Performance**: Success rates vary from 91.1% to 100%
3. **Specific Read Operations**: Certain contract read numbers consistently fail more often
4. **Long Timeout Failures**: Some operations fail after 100+ seconds, wasting resources
5. **Memory Growth**: 3.4 GB memory increase suggests potential optimization opportunities

## Recommendations

### Immediate Actions
1. **Investigate Contract Read Failures**:
   - Focus on reads 2, 6, 8, 10, and 18
   - Analyze what makes these operations different
   - Check for resource contention patterns

2. **Timeout Optimization**:
   - Implement shorter timeouts for contract reads (suggest 30-60 seconds max)
   - Add retry logic with exponential backoff

3. **Resource Monitoring**:
   - Monitor memory and CPU usage during peak failure periods
   - Check database connection pool exhaustion
   - Analyze network bandwidth utilization
   - Investigate memory growth pattern (3.4 GB increase)
   - Correlate CPU spikes (30.6%) with specific operations

### Long-term Improvements
1. **Load Balancing**:
   - Implement better distribution of contract read operations
   - Consider staggering the test pattern to avoid simultaneous peaks

2. **Circuit Breaker Pattern**:
   - Implement circuit breakers for failing operations
   - Prevent cascade failures and resource waste

3. **Performance Tuning**:
   - Optimize database queries for contract reads
   - Consider caching frequently accessed contract data
   - Review connection pooling configurations

4. **Monitoring Enhancement**:
   - Add detailed metrics for each contract read operation
   - Implement distributed tracing for failure analysis
   - Create alerts for failure rate thresholds

## Conclusion

The system demonstrates strong overall performance with excellent endpoint reliability and perfect deployment success. Resource utilization remained well within acceptable limits, with CPU peaking at 30.6% and memory at 18.8%, indicating the system has capacity for additional load. However, the 9.4% failure rate in contract reads indicates areas for improvement.

Key insights from the combined analysis:
- The concentration of failures in specific read operations (2, 6, 8, 10, 18) suggests systematic issues rather than resource exhaustion
- CPU spikes correlate with heavy concurrent operations but don't directly cause failures
- Memory growth of 3.4 GB during the test warrants investigation for potential optimization
- The system successfully handled 20 parallel instances executing thousands of operations without critical resource exhaustion

With the recommended improvements focusing on contract read reliability and memory optimization, the system should achieve even better performance and could potentially handle higher concurrent loads given the available resource headroom.