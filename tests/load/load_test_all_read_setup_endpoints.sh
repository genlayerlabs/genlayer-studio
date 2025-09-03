#!/bin/bash

# Comprehensive API Test Suite for GenLayer Studio
# Tests read-only and setup endpoints, generates JSON and HTML reports

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Load environment variables from .env file if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Configuration
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}
TEST_ENDPOINT_SCRIPT="$SCRIPT_DIR/test_endpoint.sh"

# Test configuration
REQUESTS=${REQUESTS:-10}
CONCURRENCY=${CONCURRENCY:-5}

# Report files (always overwritten)
JSON_REPORT="$SCRIPT_DIR/api_test_report.json"
HTML_REPORT="$SCRIPT_DIR/api_test_report.html"

# Test data
TEST_ADDRESS="0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
TEST_ADDRESS_2="0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC"
TEST_PRIVATE_KEY="0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d"
TEST_BLOCK_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"
TEST_TX_HASH="0x0000000000000000000000000000000000000000000000000000000000000000"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Function to print colored output
print_color() {
    local color=$1
    shift
    printf "${color}%s${NC}\n" "$*"
}

# Initialize test results arrays
declare -a test_results
test_index=0
total_tests=0
passed_tests=0
failed_tests=0
start_time=$(date '+%Y-%m-%d %H:%M:%S')
start_timestamp=$(date +%s)

# Function to run a single endpoint test
run_test() {
    local category=$1
    local method=$2
    shift 2
    local params="$*"
    
    total_tests=$((total_tests + 1))
    
    print_color "$CYAN" "Testing: $method"
    printf "  Parameters: [%s]\n" "$params"
    printf "  Status: "
    
    # Escape params for JSON
    local escaped_params=$(echo "$params" | sed 's/\\/\\\\/g' | sed 's/"/\\"/g')
    
    # Run the test using test_endpoint.sh
    if REQUESTS=$REQUESTS CONCURRENCY=$CONCURRENCY "$TEST_ENDPOINT_SCRIPT" "$method" $params >/dev/null 2>&1; then
        print_color "$GREEN" "✅ PASS"
        passed_tests=$((passed_tests + 1))
        test_results[$test_index]=$(cat <<EOF
{
  "category": "$category",
  "method": "$method",
  "params": "$escaped_params",
  "status": "pass",
  "requests": $REQUESTS,
  "concurrency": $CONCURRENCY
}
EOF
)
    else
        print_color "$RED" "❌ FAIL"
        failed_tests=$((failed_tests + 1))
        test_results[$test_index]=$(cat <<EOF
{
  "category": "$category",
  "method": "$method",
  "params": "$escaped_params",
  "status": "fail",
  "requests": $REQUESTS,
  "concurrency": $CONCURRENCY
}
EOF
)
    fi
    
    test_index=$((test_index + 1))
    sleep 0.5  # Small delay between tests
}

# Function to generate JSON report
generate_json_report() {
    local end_time=$(date '+%Y-%m-%d %H:%M:%S')
    local end_timestamp=$(date +%s)
    local duration=$((end_timestamp - start_timestamp))
    local success_rate=$(awk "BEGIN {printf \"%.2f\", ($passed_tests/$total_tests)*100}")
    
    cat > "$JSON_REPORT" <<EOF
{
  "test_suite": "GenLayer Studio API Comprehensive Test",
  "timestamp": "$start_time",
  "end_time": "$end_time",
  "duration_seconds": $duration,
  "configuration": {
    "base_url": "$BASE_URL",
    "requests_per_test": $REQUESTS,
    "concurrency": $CONCURRENCY
  },
  "summary": {
    "total_tests": $total_tests,
    "passed": $passed_tests,
    "failed": $failed_tests,
    "success_rate": $success_rate
  },
  "results": [
EOF
    
    # Add test results
    for ((i=0; i<${#test_results[@]}; i++)); do
        if [ $i -gt 0 ]; then
            echo "," >> "$JSON_REPORT"
        fi
        echo "    ${test_results[$i]}" >> "$JSON_REPORT"
    done
    
    cat >> "$JSON_REPORT" <<EOF

  ]
}
EOF
}

# Function to generate HTML report
generate_html_report() {
    local end_time=$(date '+%Y-%m-%d %H:%M:%S')
    local success_rate=$(awk "BEGIN {printf \"%.2f\", ($passed_tests/$total_tests)*100}")
    
    cat > "$HTML_REPORT" <<'EOF'
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>GenLayer Studio API Test Report</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            background: white;
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        h1 {
            color: #2d3748;
            margin-bottom: 10px;
        }
        .timestamp {
            color: #718096;
            font-size: 14px;
        }
        .summary {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            border-radius: 12px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-value {
            font-size: 36px;
            font-weight: bold;
            margin-bottom: 5px;
        }
        .stat-label {
            color: #718096;
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .total { color: #4299e1; }
        .passed { color: #48bb78; }
        .failed { color: #f56565; }
        .rate { color: #805ad5; }
        .results-section {
            background: white;
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.1);
        }
        h2 {
            color: #2d3748;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 2px solid #e2e8f0;
        }
        .category-section {
            margin-bottom: 30px;
        }
        h3 {
            color: #4a5568;
            margin-bottom: 15px;
            font-size: 18px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th {
            background: #f7fafc;
            color: #2d3748;
            padding: 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #e2e8f0;
        }
        td {
            padding: 12px;
            border-bottom: 1px solid #e2e8f0;
            color: #4a5568;
        }
        tr:hover {
            background: #f7fafc;
        }
        .status-pass {
            color: #48bb78;
            font-weight: 600;
        }
        .status-fail {
            color: #f56565;
            font-weight: 600;
        }
        .config-info {
            background: #f7fafc;
            border-radius: 8px;
            padding: 15px;
            margin-bottom: 20px;
        }
        .config-item {
            display: inline-block;
            margin-right: 30px;
            color: #4a5568;
        }
        .config-item strong {
            color: #2d3748;
        }
        .no-params {
            color: #a0aec0;
            font-style: italic;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>GenLayer Studio API Test Report</h1>
            <div class="timestamp">Generated: <span id="timestamp"></span></div>
        </div>
        
        <div class="summary">
            <div class="stat-card">
                <div class="stat-value total" id="total-tests">0</div>
                <div class="stat-label">Total Tests</div>
            </div>
            <div class="stat-card">
                <div class="stat-value passed" id="passed-tests">0</div>
                <div class="stat-label">Passed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value failed" id="failed-tests">0</div>
                <div class="stat-label">Failed</div>
            </div>
            <div class="stat-card">
                <div class="stat-value rate" id="success-rate">0%</div>
                <div class="stat-label">Success Rate</div>
            </div>
        </div>
        
        <div class="results-section">
            <h2>Test Configuration</h2>
            <div class="config-info" id="config-info"></div>
            
            <h2>Detailed Results</h2>
            <div id="results-container"></div>
        </div>
    </div>
    
    <script>
        // Embedded test data
        const testData = 
EOF
    
    # Append JSON data
    cat "$JSON_REPORT" >> "$HTML_REPORT"
    
    cat >> "$HTML_REPORT" <<'EOF'
        ;
        
        // Populate the report
        document.getElementById('timestamp').textContent = testData.timestamp;
        document.getElementById('total-tests').textContent = testData.summary.total_tests;
        document.getElementById('passed-tests').textContent = testData.summary.passed;
        document.getElementById('failed-tests').textContent = testData.summary.failed;
        document.getElementById('success-rate').textContent = testData.summary.success_rate + '%';
        
        // Configuration info
        const configHtml = `
            <div class="config-item"><strong>Base URL:</strong> ${testData.configuration.base_url}</div>
            <div class="config-item"><strong>Requests per Test:</strong> ${testData.configuration.requests_per_test}</div>
            <div class="config-item"><strong>Concurrency:</strong> ${testData.configuration.concurrency}</div>
            <div class="config-item"><strong>Duration:</strong> ${testData.duration_seconds}s</div>
        `;
        document.getElementById('config-info').innerHTML = configHtml;
        
        // Group results by category
        const categories = {};
        testData.results.forEach(result => {
            if (!categories[result.category]) {
                categories[result.category] = [];
            }
            categories[result.category].push(result);
        });
        
        // Generate results HTML
        const container = document.getElementById('results-container');
        Object.keys(categories).sort().forEach(category => {
            const section = document.createElement('div');
            section.className = 'category-section';
            
            const title = document.createElement('h3');
            title.textContent = category;
            section.appendChild(title);
            
            const table = document.createElement('table');
            const thead = `
                <thead>
                    <tr>
                        <th>Method</th>
                        <th>Parameters</th>
                        <th>Status</th>
                    </tr>
                </thead>
            `;
            
            const tbody = categories[category].map(result => `
                <tr>
                    <td><code>${result.method}</code></td>
                    <td>${result.params || '<span class="no-params">none</span>'}</td>
                    <td class="status-${result.status}">${result.status.toUpperCase()}</td>
                </tr>
            `).join('');
            
            table.innerHTML = thead + '<tbody>' + tbody + '</tbody>';
            section.appendChild(table);
            container.appendChild(section);
        });
    </script>
</body>
</html>
EOF
}

# Main execution
main() {
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    print_color "$BLUE" "         GenLayer Studio API Comprehensive Test Suite"
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    echo ""
    print_color "$YELLOW" "Configuration:"
    echo "  Base URL: $BASE_URL"
    echo "  Requests per test: $REQUESTS"
    echo "  Concurrency: $CONCURRENCY"
    echo ""
    
    # Check if test_endpoint.sh exists
    if [ ! -f "$TEST_ENDPOINT_SCRIPT" ]; then
        print_color "$RED" "Error: test_endpoint.sh not found at $TEST_ENDPOINT_SCRIPT"
        exit 1
    fi
    
    # Make sure test_endpoint.sh is executable
    chmod +x "$TEST_ENDPOINT_SCRIPT"
    
    print_color "$BLUE" "╔════════════════════════════════════════════════════════════════╗"
    print_color "$BLUE" "║                    READ-ONLY ENDPOINTS                        ║"
    print_color "$BLUE" "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    
    # Test read-only endpoints without parameters
    print_color "$YELLOW" "Testing endpoints without parameters..."
    echo ""
    run_test "Read-Only" "ping"
    run_test "Read-Only" "eth_blockNumber"
    run_test "Read-Only" "eth_gasPrice"
    run_test "Read-Only" "eth_chainId"
    run_test "Read-Only" "net_version"
    run_test "Read-Only" "sim_getFinalityWindowTime"
    run_test "Read-Only" "sim_getProvidersAndModels"
    run_test "Read-Only" "sim_countValidators"
    run_test "Read-Only" "sim_getAllValidators"
    
    echo ""
    print_color "$YELLOW" "Testing endpoints with parameters..."
    echo ""
    run_test "Read-Only" "eth_getBalance" "$TEST_ADDRESS" "latest"
    run_test "Read-Only" "eth_getTransactionCount" "$TEST_ADDRESS" "latest"
    run_test "Read-Only" "eth_getBlockByNumber" "0x1" "true"
    run_test "Read-Only" "eth_getBlockByHash" "$TEST_BLOCK_HASH" "true"
    run_test "Read-Only" "eth_getTransactionByHash" "$TEST_TX_HASH"
    run_test "Read-Only" "eth_getTransactionReceipt" "$TEST_TX_HASH"
    run_test "Read-Only" "sim_getValidator" "$TEST_ADDRESS"
    run_test "Read-Only" "sim_getTransactionsForAddress" "$TEST_ADDRESS"
    run_test "Read-Only" "sim_getConsensusContract"
    
    echo ""
    print_color "$BLUE" "╔════════════════════════════════════════════════════════════════╗"
    print_color "$BLUE" "║                     SETUP OPERATIONS                          ║"
    print_color "$BLUE" "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    
    print_color "$YELLOW" "Testing validator management..."
    echo ""
    run_test "Setup" "sim_createRandomValidator"
    run_test "Setup" "sim_createRandomValidators" "3"
    
    # For sim_createValidator, we need to pass JSON
    VALIDATOR_CONFIG='{"stake":1,"provider":"openai","model":"gpt-4-1106-preview","config":{},"address":"'$TEST_ADDRESS_2'"}'
    run_test "Setup" "sim_createValidator" "$VALIDATOR_CONFIG"
    
    # For sim_updateValidator
    UPDATE_CONFIG='{"address":"'$TEST_ADDRESS_2'","stake":2,"provider":"openai","model":"gpt-4","config":{}}'
    run_test "Setup" "sim_updateValidator" "$UPDATE_CONFIG"
    
    run_test "Setup" "sim_deleteValidator" "$TEST_ADDRESS_2"
    
    echo ""
    print_color "$YELLOW" "Testing account operations..."
    echo ""
    # sim_fundAccount needs account_address and amount as named parameters
    FUND_PARAMS='{"account_address":"'$TEST_ADDRESS'","amount":1000000000000000000}'
    run_test "Setup" "sim_fundAccount" "$FUND_PARAMS"
    
    echo ""
    print_color "$YELLOW" "Testing system management..."
    echo ""
    run_test "Setup" "sim_setFinalityWindowTime" "10"
    run_test "Setup" "sim_createSnapshot" "test_snapshot"
    run_test "Setup" "sim_restoreSnapshot" "test_snapshot"
    run_test "Setup" "sim_deleteAllSnapshots"
    
    echo ""
    print_color "$YELLOW" "Testing provider management..."
    echo ""
    PROVIDER_CONFIG='{"provider":"openai","model":"gpt-4","config":{"api_key_env_var":"OPENAI_API_KEY"}}'
    run_test "Setup" "sim_addProvider" "$PROVIDER_CONFIG"
    
    UPDATE_PROVIDER_CONFIG='{"provider":"openai","model":"gpt-4-turbo","config":{"api_key_env_var":"OPENAI_API_KEY"}}'
    run_test "Setup" "sim_updateProvider" "openai" "$UPDATE_PROVIDER_CONFIG"
    
    run_test "Setup" "sim_deleteProvider" "openai"
    run_test "Setup" "sim_resetDefaultsLlmProviders"
    
    echo ""
    print_color "$YELLOW" "Testing cleanup operations..."
    echo ""
    run_test "Setup" "sim_deleteAllValidators"
    run_test "Setup" "sim_clearDbTables"
    
    echo ""
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    print_color "$BLUE" "                     GENERATING REPORTS"
    print_color "$BLUE" "════════════════════════════════════════════════════════════════════"
    echo ""
    
    # Generate reports
    generate_json_report
    generate_html_report
    
    # Print summary
    echo ""
    print_color "$GREEN" "╔════════════════════════════════════════════════════════════════╗"
    print_color "$GREEN" "║                      TEST SUMMARY                             ║"
    print_color "$GREEN" "╚════════════════════════════════════════════════════════════════╝"
    echo ""
    echo "  Total Tests: $total_tests"
    print_color "$GREEN" "  Passed: $passed_tests"
    print_color "$RED" "  Failed: $failed_tests"
    success_rate=$(awk "BEGIN {printf \"%.2f\", ($passed_tests/$total_tests)*100}")
    echo "  Success Rate: ${success_rate}%"
    echo ""
    echo "  Reports generated:"
    echo "  - JSON: $JSON_REPORT"
    echo "  - HTML: $HTML_REPORT"
    echo ""
    
    if [ $failed_tests -eq 0 ]; then
        print_color "$GREEN" "  ✅ All tests passed successfully!"
    else
        print_color "$YELLOW" "  ⚠️  Some tests failed. Check the reports for details."
    fi
    echo ""
}

# Run main function
main "$@"