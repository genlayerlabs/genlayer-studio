#!/bin/bash

# Script to clear the transactions table in GenLayer Studio database
# This is useful when you get duplicate key constraint errors

# Base URL (can be overridden via environment variable)
BASE_URL=${BASE_URL:-"http://localhost:4000/api"}

echo "Clearing transactions table at $BASE_URL..."

curl -X POST "$BASE_URL" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"sim_clearDbTables","params":[["transactions"]],"id":1}'

echo ""
echo "Transactions table cleared."