#!/bin/bash

# Simulate GitHub Actions workflow locally
# This script mimics what the workflow does

set -e

echo "==================================================="
echo "       LOCAL GITHUB ACTIONS WORKFLOW TEST"
echo "==================================================="

# Check if services are running
echo "Checking if services are up..."
if curl -X POST -H "Content-Type: application/json" \
     -d '{"jsonrpc":"2.0","method":"ping","params":[],"id":1}' \
     http://0.0.0.0:4000/api 2>/dev/null | grep -q "OK"; then
    echo "✅ RPC server is running"
else
    echo "❌ RPC server is not running. Please run: genlayer up"
    exit 1
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "=== Setting up Validators ==="
cd "$SCRIPT_DIR"
chmod +x setup_validators.sh
if ./setup_validators.sh 5; then
    echo "✅ Validators setup completed"
else
    echo "❌ Validator setup failed"
    exit 1
fi

echo ""
echo "=== Task 1: Run Load Test - Contract Deploy and Read ==="
chmod +x load_test_contract_deploy_and_read.sh
echo "Running with 5 deployments and 1 parallel job..."
if ./load_test_contract_deploy_and_read.sh 5 1; then
    echo "✅ Task 1 completed successfully"
else
    echo "❌ Task 1 failed"
    exit 1
fi

echo ""
echo "=== Task 2: Run Load Test - All Read Setup Endpoints ==="
chmod +x load_test_all_read_setup_endpoints.sh
echo "Running with REQUESTS=500 CONCURRENCY=100..."
if REQUESTS=500 CONCURRENCY=100 ./load_test_all_read_setup_endpoints.sh; then
    echo "✅ Task 2 completed successfully"
else
    echo "❌ Task 2 failed"
    exit 1
fi

echo ""
echo "==================================================="
echo "       ALL WORKFLOW TASKS COMPLETED"
echo "==================================================="