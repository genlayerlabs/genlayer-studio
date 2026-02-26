#!/usr/bin/env bash
# =============================================================================
# API Tier Levels Integration Test
#
# Creates custom test tiers with low limits, creates API keys for each,
# and confirms that each tier's rate limit is enforced independently.
#
# Usage:
#   ./scripts/test_tier_levels.sh
#
# Prerequisites:
#   - docker compose services running (genlayer up / docker compose up)
#   - Migration applied (api_tiers seeded)
# =============================================================================

set -o pipefail

API_URL="http://127.0.0.1:4000/api"
PASS=0
FAIL=0
ANON_LIMIT=3

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

# -- Helpers ------------------------------------------------------------------

rpc() {
  local method="$1"
  local params="${2:-null}"
  curl -s -w "\n%{http_code}" -X POST "$API_URL" \
    -H 'Content-Type: application/json' \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"$method\",\"params\":$params,\"id\":1}" 2>/dev/null
}

rpc_with_key() {
  local method="$1"
  local params="$2"
  local api_key="$3"
  curl -s -w "\n%{http_code}" -X POST "$API_URL" \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $api_key" \
    -d "{\"jsonrpc\":\"2.0\",\"method\":\"$method\",\"params\":$params,\"id\":1}" 2>/dev/null
}

parse_response() {
  local output="$1"
  HTTP_CODE=$(echo "$output" | tail -1)
  BODY=$(echo "$output" | sed '$d')
}

json_field() {
  # Extract a top-level field from JSON using python
  local json="$1" field="$2"
  echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d${field})" 2>/dev/null
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo -e "    ${GREEN}PASS${NC}: $desc (got $actual)"
    ((PASS++))
  else
    echo -e "    ${RED}FAIL${NC}: $desc (expected $expected, got $actual)"
    ((FAIL++))
  fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    echo -e "    ${GREEN}PASS${NC}: $desc"
    ((PASS++))
  else
    echo -e "    ${RED}FAIL${NC}: $desc (expected to find '$needle')"
    ((FAIL++))
  fi
}

wait_for_healthy() {
  echo -n "  Waiting for jsonrpc to be healthy"
  for i in $(seq 1 90); do
    if curl -sf http://127.0.0.1:4000/health > /dev/null 2>&1; then
      echo -e " ${GREEN}OK${NC} (${i}s)"
      return 0
    fi
    echo -n "."
    sleep 3
  done
  echo -e " ${RED}TIMEOUT${NC}"
  return 1
}

flush_ratelimit_keys() {
  docker compose exec -T redis redis-cli EVAL \
    "local keys = redis.call('keys', 'ratelimit:*') for i,k in ipairs(keys) do redis.call('del', k) end return #keys" \
    0 > /dev/null 2>&1 || true
}

# Send N requests, return how many got HTTP 200
send_n_requests() {
  local n="$1" method="$2" api_key="${3:-}"
  local ok=0
  for i in $(seq 1 "$n"); do
    local code
    if [ -n "$api_key" ]; then
      code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL" \
        -H 'Content-Type: application/json' \
        -H "X-API-Key: $api_key" \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"$method\",\"params\":null,\"id\":$i}" 2>/dev/null)
    else
      code=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$API_URL" \
        -H 'Content-Type: application/json' \
        -d "{\"jsonrpc\":\"2.0\",\"method\":\"$method\",\"params\":null,\"id\":$i}" 2>/dev/null)
    fi
    if [ "$code" = "200" ]; then
      ((ok++))
    fi
  done
  echo "$ok"
}

# Get the 429 error body for the next request (assumes limit already hit)
get_429_body() {
  local api_key="${1:-}"
  if [ -n "$api_key" ]; then
    curl -s -X POST "$API_URL" \
      -H 'Content-Type: application/json' \
      -H "X-API-Key: $api_key" \
      -d '{"jsonrpc":"2.0","method":"ping","params":null,"id":1}' 2>/dev/null
  else
    curl -s -X POST "$API_URL" \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","method":"ping","params":null,"id":1}' 2>/dev/null
  fi
}

create_api_key() {
  local tier_name="$1" desc="$2"
  RESP=$(rpc "admin_createApiKey" "{\"tier_name\":\"$tier_name\",\"description\":\"$desc\"}")
  parse_response "$RESP"
  if [ "$HTTP_CODE" != "200" ]; then
    echo ""
    return 1
  fi
  json_field "$BODY" "['result']['api_key']"
}

# =============================================================================
echo -e "${YELLOW}=== API Tier Levels Integration Test ===${NC}"
echo ""

# -- Step 0: Preflight check -------------------------------------------------
echo -e "${YELLOW}[Step 0] Preflight check${NC}"
RESP=$(curl -s -X POST "$API_URL" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"ping","id":1}')
assert_contains "Service responds to ping" '"OK"' "$RESP"
echo ""

# -- Step 1: Enable rate limiting with low anonymous limit --------------------
echo -e "${YELLOW}[Step 1] Enable rate limiting (anon=$ANON_LIMIT/min)${NC}"
export RATE_LIMIT_ENABLED=true
export RATE_LIMIT_ANON_PER_MINUTE=$ANON_LIMIT
export RATE_LIMIT_ANON_PER_HOUR=100
export RATE_LIMIT_ANON_PER_DAY=1000

docker compose up -d jsonrpc > /dev/null 2>&1
wait_for_healthy
flush_ratelimit_keys
echo ""

# -- Step 2: Create test tiers with low limits --------------------------------
echo -e "${YELLOW}[Step 2] Create test tiers with low per-minute limits${NC}"

# test_bronze: 4/min, test_silver: 8/min, test_gold: 15/min
flush_ratelimit_keys
RESP=$(rpc "admin_createTier" '{"name":"test_bronze","rate_limit_minute":4,"rate_limit_hour":1000,"rate_limit_day":10000}')
parse_response "$RESP"
assert_eq "Create test_bronze tier" "200" "$HTTP_CODE"

flush_ratelimit_keys
RESP=$(rpc "admin_createTier" '{"name":"test_silver","rate_limit_minute":8,"rate_limit_hour":1000,"rate_limit_day":10000}')
parse_response "$RESP"
assert_eq "Create test_silver tier" "200" "$HTTP_CODE"

flush_ratelimit_keys
RESP=$(rpc "admin_createTier" '{"name":"test_gold","rate_limit_minute":15,"rate_limit_hour":1000,"rate_limit_day":10000}')
parse_response "$RESP"
assert_eq "Create test_gold tier" "200" "$HTTP_CODE"

# Verify all tiers exist
flush_ratelimit_keys
RESP=$(rpc "admin_listTiers" '{}')
parse_response "$RESP"
assert_contains "Tier list includes test_bronze" 'test_bronze' "$BODY"
assert_contains "Tier list includes test_silver" 'test_silver' "$BODY"
assert_contains "Tier list includes test_gold" 'test_gold' "$BODY"
echo ""

# -- Step 3: Create API keys for each tier ------------------------------------
echo -e "${YELLOW}[Step 3] Create API keys for each tier${NC}"

flush_ratelimit_keys
KEY_BRONZE=$(create_api_key "test_bronze" "tier-test-bronze")
if [ -n "$KEY_BRONZE" ]; then
  echo -e "    ${GREEN}PASS${NC}: Created bronze key: ${KEY_BRONZE:0:12}..."
  ((PASS++))
else
  echo -e "    ${RED}FAIL${NC}: Could not create bronze key"
  ((FAIL++))
fi

flush_ratelimit_keys
KEY_SILVER=$(create_api_key "test_silver" "tier-test-silver")
if [ -n "$KEY_SILVER" ]; then
  echo -e "    ${GREEN}PASS${NC}: Created silver key: ${KEY_SILVER:0:12}..."
  ((PASS++))
else
  echo -e "    ${RED}FAIL${NC}: Could not create silver key"
  ((FAIL++))
fi

flush_ratelimit_keys
KEY_GOLD=$(create_api_key "test_gold" "tier-test-gold")
if [ -n "$KEY_GOLD" ]; then
  echo -e "    ${GREEN}PASS${NC}: Created gold key: ${KEY_GOLD:0:12}..."
  ((PASS++))
else
  echo -e "    ${RED}FAIL${NC}: Could not create gold key"
  ((FAIL++))
fi

# Also create a key on the seeded "free" tier for comparison
flush_ratelimit_keys
KEY_FREE=$(create_api_key "free" "tier-test-free")
if [ -n "$KEY_FREE" ]; then
  echo -e "    ${GREEN}PASS${NC}: Created free key:   ${KEY_FREE:0:12}..."
  ((PASS++))
else
  echo -e "    ${RED}FAIL${NC}: Could not create free key"
  ((FAIL++))
fi
echo ""

# -- Step 4: Test anonymous (IP-based) rate limiting --------------------------
echo -e "${YELLOW}[Step 4] Anonymous tier (IP-based, limit=${ANON_LIMIT}/min)${NC}"
flush_ratelimit_keys

ALLOWED=$(send_n_requests $((ANON_LIMIT + 2)) "ping")
assert_eq "Anonymous: exactly $ANON_LIMIT of $((ANON_LIMIT + 2)) requests allowed" "$ANON_LIMIT" "$ALLOWED"

# Verify the 429 body reports the correct limit
ERROR_BODY=$(get_429_body)
REPORTED_LIMIT=$(json_field "$ERROR_BODY" "['error']['data']['limit']")
assert_eq "Anonymous: error reports limit=$ANON_LIMIT" "$ANON_LIMIT" "$REPORTED_LIMIT"
assert_contains "Anonymous: error window is 'minute'" '"minute"' "$ERROR_BODY"
echo ""

# -- Step 5: Test test_bronze tier (4/min) ------------------------------------
echo -e "${YELLOW}[Step 5] test_bronze tier (limit=4/min)${NC}"
flush_ratelimit_keys

if [ -n "$KEY_BRONZE" ]; then
  ALLOWED=$(send_n_requests 6 "ping" "$KEY_BRONZE")
  assert_eq "Bronze: exactly 4 of 6 requests allowed" "4" "$ALLOWED"

  ERROR_BODY=$(get_429_body "$KEY_BRONZE")
  REPORTED_LIMIT=$(json_field "$ERROR_BODY" "['error']['data']['limit']")
  assert_eq "Bronze: error reports limit=4" "4" "$REPORTED_LIMIT"
  assert_contains "Bronze: error window is 'minute'" '"minute"' "$ERROR_BODY"
else
  echo -e "    ${YELLOW}SKIP${NC}: No bronze key"
fi
echo ""

# -- Step 6: Test test_silver tier (8/min) ------------------------------------
echo -e "${YELLOW}[Step 6] test_silver tier (limit=8/min)${NC}"
flush_ratelimit_keys

if [ -n "$KEY_SILVER" ]; then
  ALLOWED=$(send_n_requests 10 "ping" "$KEY_SILVER")
  assert_eq "Silver: exactly 8 of 10 requests allowed" "8" "$ALLOWED"

  ERROR_BODY=$(get_429_body "$KEY_SILVER")
  REPORTED_LIMIT=$(json_field "$ERROR_BODY" "['error']['data']['limit']")
  assert_eq "Silver: error reports limit=8" "8" "$REPORTED_LIMIT"
  assert_contains "Silver: error window is 'minute'" '"minute"' "$ERROR_BODY"
else
  echo -e "    ${YELLOW}SKIP${NC}: No silver key"
fi
echo ""

# -- Step 7: Test test_gold tier (15/min) -------------------------------------
echo -e "${YELLOW}[Step 7] test_gold tier (limit=15/min)${NC}"
flush_ratelimit_keys

if [ -n "$KEY_GOLD" ]; then
  ALLOWED=$(send_n_requests 17 "ping" "$KEY_GOLD")
  assert_eq "Gold: exactly 15 of 17 requests allowed" "15" "$ALLOWED"

  ERROR_BODY=$(get_429_body "$KEY_GOLD")
  REPORTED_LIMIT=$(json_field "$ERROR_BODY" "['error']['data']['limit']")
  assert_eq "Gold: error reports limit=15" "15" "$REPORTED_LIMIT"
  assert_contains "Gold: error window is 'minute'" '"minute"' "$ERROR_BODY"
else
  echo -e "    ${YELLOW}SKIP${NC}: No gold key"
fi
echo ""

# -- Step 8: Test seeded "free" tier (30/min) ---------------------------------
echo -e "${YELLOW}[Step 8] Seeded 'free' tier (limit=30/min)${NC}"
flush_ratelimit_keys

if [ -n "$KEY_FREE" ]; then
  # Send 32 requests — expect 30 pass, 2 blocked
  ALLOWED=$(send_n_requests 32 "ping" "$KEY_FREE")
  assert_eq "Free: exactly 30 of 32 requests allowed" "30" "$ALLOWED"

  ERROR_BODY=$(get_429_body "$KEY_FREE")
  REPORTED_LIMIT=$(json_field "$ERROR_BODY" "['error']['data']['limit']")
  assert_eq "Free: error reports limit=30" "30" "$REPORTED_LIMIT"
else
  echo -e "    ${YELLOW}SKIP${NC}: No free key"
fi
echo ""

# -- Step 9: Cross-tier isolation ---------------------------------------------
echo -e "${YELLOW}[Step 9] Cross-tier isolation${NC}"
echo -e "  ${CYAN}Exhaust bronze, verify silver still works${NC}"
flush_ratelimit_keys

if [ -n "$KEY_BRONZE" ] && [ -n "$KEY_SILVER" ]; then
  # Exhaust bronze (4/min)
  send_n_requests 5 "ping" "$KEY_BRONZE" > /dev/null

  # Silver should still work (separate identity)
  RESP=$(rpc_with_key "ping" "null" "$KEY_SILVER")
  parse_response "$RESP"
  assert_eq "Silver unaffected by bronze exhaustion" "200" "$HTTP_CODE"

  # Anonymous should also still work (separate identity)
  RESP=$(rpc "ping" "null")
  parse_response "$RESP"
  assert_eq "Anonymous unaffected by bronze exhaustion" "200" "$HTTP_CODE"
else
  echo -e "    ${YELLOW}SKIP${NC}: Missing keys"
fi
echo ""

# -- Step 10: Verify 429 response format --------------------------------------
echo -e "${YELLOW}[Step 10] Verify 429 JSON-RPC error format${NC}"
flush_ratelimit_keys

if [ -n "$KEY_BRONZE" ]; then
  # Exhaust bronze
  send_n_requests 5 "ping" "$KEY_BRONZE" > /dev/null

  ERROR_BODY=$(get_429_body "$KEY_BRONZE")
  assert_contains "Has jsonrpc field" '"jsonrpc"' "$ERROR_BODY"
  assert_contains "jsonrpc version is 2.0" '"2.0"' "$ERROR_BODY"
  assert_contains "Has error.code" '"code"' "$ERROR_BODY"
  assert_contains "Error code is -32029" '-32029' "$ERROR_BODY"
  assert_contains "Has error.message" '"message"' "$ERROR_BODY"
  assert_contains "Has error.data.window" '"window"' "$ERROR_BODY"
  assert_contains "Has error.data.limit" '"limit"' "$ERROR_BODY"
  assert_contains "Has error.data.current" '"current"' "$ERROR_BODY"
  assert_contains "Has error.data.retry_after_seconds" '"retry_after_seconds"' "$ERROR_BODY"
  assert_contains "Has id: null" '"id":' "$ERROR_BODY"

  # Check Retry-After header
  HEADERS=$(curl -s -D - -o /dev/null -X POST "$API_URL" \
    -H 'Content-Type: application/json' \
    -H "X-API-Key: $KEY_BRONZE" \
    -d '{"jsonrpc":"2.0","method":"ping","id":1}' 2>/dev/null)
  assert_contains "Retry-After header present" "retry-after" "$(echo "$HEADERS" | tr '[:upper:]' '[:lower:]')"
else
  echo -e "    ${YELLOW}SKIP${NC}: No bronze key"
fi
echo ""

# -- Step 11: Deactivation stops access --------------------------------------
echo -e "${YELLOW}[Step 11] Deactivated key is rejected${NC}"
flush_ratelimit_keys

if [ -n "$KEY_BRONZE" ]; then
  # Verify it works first
  RESP=$(rpc_with_key "ping" "null" "$KEY_BRONZE")
  parse_response "$RESP"
  assert_eq "Bronze key works before deactivation" "200" "$HTTP_CODE"

  # Deactivate
  PREFIX="${KEY_BRONZE:0:8}"
  flush_ratelimit_keys
  RESP=$(rpc "admin_deactivateApiKey" "{\"key_prefix\":\"$PREFIX\"}")
  parse_response "$RESP"
  assert_eq "Deactivation succeeds" "200" "$HTTP_CODE"

  # Flush cache so deactivation takes effect
  flush_ratelimit_keys
  sleep 1

  # Now it should be rejected
  RESP=$(rpc_with_key "ping" "null" "$KEY_BRONZE")
  parse_response "$RESP"
  assert_eq "Deactivated bronze key returns 429" "429" "$HTTP_CODE"
  assert_contains "Error says Invalid API key" "Invalid API key" "$BODY"
else
  echo -e "    ${YELLOW}SKIP${NC}: No bronze key"
fi
echo ""

# -- Step 12: Restore rate limiting to disabled --------------------------------
echo -e "${YELLOW}[Step 12] Restore RATE_LIMIT_ENABLED=false${NC}"
export RATE_LIMIT_ENABLED=false
unset RATE_LIMIT_ANON_PER_MINUTE RATE_LIMIT_ANON_PER_HOUR RATE_LIMIT_ANON_PER_DAY 2>/dev/null || true
docker compose up -d jsonrpc > /dev/null 2>&1
wait_for_healthy
flush_ratelimit_keys

# Quick sanity: many requests pass with rate limiting off
ALLOWED=$(send_n_requests 20 "ping")
assert_eq "All 20 requests pass with rate limiting disabled" "20" "$ALLOWED"
echo ""

# -- Summary ------------------------------------------------------------------
echo -e "${YELLOW}=== Results ===${NC}"
echo -e "  ${GREEN}Passed${NC}: $PASS"
echo -e "  ${RED}Failed${NC}: $FAIL"
TOTAL=$((PASS + FAIL))
echo -e "  Total:  $TOTAL"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}SOME TESTS FAILED${NC}"
  exit 1
else
  echo -e "${GREEN}ALL TESTS PASSED${NC}"
  exit 0
fi
