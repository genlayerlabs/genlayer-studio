#!/usr/bin/env bash
# =============================================================================
# Rate Limiting Integration Test Script
#
# Tests the API key rate limiting feature end-to-end against a running instance.
#
# Usage:
#   ./scripts/test_rate_limiting.sh
#
# Prerequisites:
#   - docker compose services running (genlayer up / docker compose up)
#   - Migration applied (api_tiers seeded)
# =============================================================================

set -o pipefail

API_URL="http://127.0.0.1:4000/api"
PASS=0
FAIL=0
ANON_PER_MINUTE=5  # Low limit for quick testing

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

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

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    echo -e "  ${GREEN}PASS${NC}: $desc (got $actual)"
    ((PASS++))
  else
    echo -e "  ${RED}FAIL${NC}: $desc (expected $expected, got $actual)"
    ((FAIL++))
  fi
}

assert_contains() {
  local desc="$1" needle="$2" haystack="$3"
  if echo "$haystack" | grep -qF -- "$needle"; then
    echo -e "  ${GREEN}PASS${NC}: $desc"
    ((PASS++))
  else
    echo -e "  ${RED}FAIL${NC}: $desc (expected to find '$needle')"
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

# =============================================================================
echo -e "${YELLOW}=== Rate Limiting Integration Test ===${NC}"
echo ""

# -- Step 0: Verify service is running --
echo -e "${YELLOW}[Step 0] Verify service is running${NC}"
RESP=$(curl -s -X POST "$API_URL" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"ping","id":1}')
assert_contains "Service responds to ping" '"OK"' "$RESP"
echo ""

# -- Step 1: Enable rate limiting --
echo -e "${YELLOW}[Step 1] Enable rate limiting (ANON_PER_MINUTE=$ANON_PER_MINUTE)${NC}"

export RATE_LIMIT_ENABLED=true
export RATE_LIMIT_ANON_PER_MINUTE=$ANON_PER_MINUTE
export RATE_LIMIT_ANON_PER_HOUR=100
export RATE_LIMIT_ANON_PER_DAY=1000

docker compose up -d jsonrpc > /dev/null 2>&1
wait_for_healthy
flush_ratelimit_keys
echo ""

# -- Step 2: Test admin endpoints --
echo -e "${YELLOW}[Step 2] Test admin endpoints${NC}"

# List tiers (flush first so this isn't rate limited)
flush_ratelimit_keys
RESP=$(rpc "admin_listTiers" '{}')
parse_response "$RESP"
assert_eq "admin_listTiers returns 200" "200" "$HTTP_CODE"
assert_contains "Has 'free' tier" '"free"' "$BODY"
assert_contains "Has 'pro' tier" '"pro"' "$BODY"
assert_contains "Has 'unlimited' tier" '"unlimited"' "$BODY"

# Create API key on 'free' tier (flush so we have a fresh window)
flush_ratelimit_keys
RESP=$(rpc "admin_createApiKey" '{"tier_name":"free","description":"test-key"}')
parse_response "$RESP"
assert_eq "admin_createApiKey returns 200" "200" "$HTTP_CODE"
assert_contains "Returns api_key starting with glk_" '"glk_' "$BODY"

# Extract the raw API key
API_KEY=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['api_key'])" 2>/dev/null || echo "")
if [ -z "$API_KEY" ]; then
  echo -e "  ${RED}FAIL${NC}: Could not extract API key from response"
  ((FAIL++))
else
  echo -e "  ${GREEN}PASS${NC}: Extracted API key: ${API_KEY:0:12}..."
  ((PASS++))
fi
echo ""

# -- Step 3: Test anonymous rate limiting --
echo -e "${YELLOW}[Step 3] Test anonymous rate limiting (limit=$ANON_PER_MINUTE/min)${NC}"

flush_ratelimit_keys

# Send requests up to the limit — all should succeed
echo "  Sending $ANON_PER_MINUTE requests (should all pass)..."
ALL_OK=true
for i in $(seq 1 "$ANON_PER_MINUTE"); do
  RESP=$(rpc "ping" 'null')
  parse_response "$RESP"
  if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}FAIL${NC}: Request $i returned $HTTP_CODE (expected 200)"
    ALL_OK=false
    ((FAIL++))
    break
  fi
done
if [ "$ALL_OK" = true ]; then
  echo -e "  ${GREEN}PASS${NC}: All $ANON_PER_MINUTE requests returned 200"
  ((PASS++))
fi

# Next request should be rate limited (429)
echo "  Sending request $(($ANON_PER_MINUTE + 1)) (should be rate limited)..."
RESP=$(rpc "ping" 'null')
parse_response "$RESP"
assert_eq "Request over limit returns 429" "429" "$HTTP_CODE"
assert_contains "Error contains rate limit message" "Rate limit exceeded" "$BODY"
assert_contains "Error code is -32029" "-32029" "$BODY"

# Check Retry-After header
RETRY_RESP=$(curl -s -D - -o /dev/null -X POST "$API_URL" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"ping","id":1}' 2>/dev/null)
assert_contains "Response includes Retry-After header" "retry-after" "$(echo "$RETRY_RESP" | tr '[:upper:]' '[:lower:]')"
echo ""

# -- Step 4: Test authenticated rate limiting --
echo -e "${YELLOW}[Step 4] Test API key rate limiting (free tier: 30/min)${NC}"

flush_ratelimit_keys

if [ -n "$API_KEY" ]; then
  # With an API key on the free tier (30/min), requests should pass
  RESP=$(rpc_with_key "ping" "null" "$API_KEY")
  parse_response "$RESP"
  assert_eq "Authenticated request returns 200" "200" "$HTTP_CODE"
  assert_contains "Authenticated ping returns OK" '"OK"' "$BODY"

  # Send more — should still pass (free tier = 30/min >> 10 requests)
  echo "  Sending 10 more authenticated requests..."
  AUTH_OK=true
  for i in $(seq 1 10); do
    RESP=$(rpc_with_key "ping" "null" "$API_KEY")
    parse_response "$RESP"
    if [ "$HTTP_CODE" != "200" ]; then
      echo -e "  ${RED}FAIL${NC}: Authenticated request $i returned $HTTP_CODE"
      AUTH_OK=false
      ((FAIL++))
      break
    fi
  done
  if [ "$AUTH_OK" = true ]; then
    echo -e "  ${GREEN}PASS${NC}: 10 authenticated requests all passed (within free tier limit)"
    ((PASS++))
  fi
else
  echo -e "  ${YELLOW}SKIP${NC}: No API key available, skipping authenticated tests"
fi
echo ""

# -- Step 5: Test invalid API key --
echo -e "${YELLOW}[Step 5] Test invalid API key${NC}"

flush_ratelimit_keys

RESP=$(rpc_with_key "ping" "null" "glk_invalid_key_that_doesnt_exist")
parse_response "$RESP"
assert_eq "Invalid API key returns 429" "429" "$HTTP_CODE"
assert_contains "Error says invalid API key" "Invalid API key" "$BODY"
echo ""

# -- Step 6: Test API key deactivation --
echo -e "${YELLOW}[Step 6] Test API key deactivation${NC}"

if [ -n "$API_KEY" ]; then
  flush_ratelimit_keys
  KEY_PREFIX="${API_KEY:0:8}"

  RESP=$(rpc "admin_deactivateApiKey" "{\"key_prefix\":\"$KEY_PREFIX\"}")
  parse_response "$RESP"
  assert_eq "admin_deactivateApiKey returns 200" "200" "$HTTP_CODE"
  assert_contains "Response confirms deactivation" '"deactivated"' "$BODY"

  # Flush ratelimit cache so the deactivated status is fetched fresh
  flush_ratelimit_keys
  sleep 1

  # Deactivated key should now be rejected
  RESP=$(rpc_with_key "ping" "null" "$API_KEY")
  parse_response "$RESP"
  assert_eq "Deactivated key returns 429" "429" "$HTTP_CODE"
  assert_contains "Error says invalid API key" "Invalid API key" "$BODY"
else
  echo -e "  ${YELLOW}SKIP${NC}: No API key available"
fi
echo ""

# -- Step 7: Restore rate limiting to disabled --
echo -e "${YELLOW}[Step 7] Restore RATE_LIMIT_ENABLED=false${NC}"

export RATE_LIMIT_ENABLED=false
unset RATE_LIMIT_ANON_PER_MINUTE RATE_LIMIT_ANON_PER_HOUR RATE_LIMIT_ANON_PER_DAY 2>/dev/null || true
docker compose up -d jsonrpc > /dev/null 2>&1
wait_for_healthy

# Verify rate limiting is disabled — many requests should all pass
echo "  Sending 20 rapid requests with rate limiting disabled..."
DISABLED_OK=true
for i in $(seq 1 20); do
  RESP=$(rpc "ping" 'null')
  parse_response "$RESP"
  if [ "$HTTP_CODE" != "200" ]; then
    echo -e "  ${RED}FAIL${NC}: Request $i returned $HTTP_CODE with rate limiting disabled"
    DISABLED_OK=false
    ((FAIL++))
    break
  fi
done
if [ "$DISABLED_OK" = true ]; then
  echo -e "  ${GREEN}PASS${NC}: All 20 requests passed with rate limiting disabled"
  ((PASS++))
fi
echo ""

# -- Summary --
echo -e "${YELLOW}=== Results ===${NC}"
echo -e "  ${GREEN}Passed${NC}: $PASS"
echo -e "  ${RED}Failed${NC}: $FAIL"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo -e "${RED}SOME TESTS FAILED${NC}"
  exit 1
else
  echo -e "${GREEN}ALL TESTS PASSED${NC}"
  exit 0
fi
