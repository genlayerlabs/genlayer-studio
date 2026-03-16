---
name: manage-api-keys
description: Create, list, deactivate, and reactivate API keys for rate limiting
invocation: user
---

# Manage API Keys

CRUD operations for API keys used in rate limiting on GenLayer Studio deployments.

## Setup

Before any operation, determine the target environment:

1. **Ask the user** which environment they are targeting:
   - **Local dev**: `BASE_URL=http://localhost:4000/api`, no `admin_key` needed
   - **Hosted** (dev/stg/prd): `BASE_URL=https://<domain>/api`, requires `admin_key`

2. For hosted deployments, **ask the user for the `ADMIN_API_KEY`** (stored in k8s secrets as `ADMIN_API_KEY`).

## Operations

Ask the user which operation to perform: **Create**, **List**, **Deactivate**, or **Reactivate**.

### List API Keys

**Note:** This endpoint may not exist yet. If `admin_listApiKeys` is not available, query the database directly using the `studio-db` skill, or inform the user it needs to be implemented.

```bash
# List all keys
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_listApiKeys","params":{"admin_key":"<ADMIN_KEY>"},"id":1}
EOF

# Filter by tier
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_listApiKeys","params":{"tier_name":"free","admin_key":"<ADMIN_KEY>"},"id":1}
EOF

# Filter by active status
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_listApiKeys","params":{"is_active":false,"admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

### Create API Key

Ask the user for: `tier_name` (suggest listing tiers first with `/manage-tiers`) and optional `description`.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_createApiKey","params":{"tier_name":"<TIER_NAME>","description":"<DESCRIPTION>","admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

**IMPORTANT:** The full API key (e.g., `glk_abcdef1234...`) is **only returned once** at creation time. Remind the user to store it securely. Only the `key_prefix` (first 8 chars) is stored for identification.

Response includes:
- `api_key`: Full key (store this!)
- `key_prefix`: First 8 characters (e.g., `glk_ab12`)
- `tier`: Tier name
- `description`: Optional description

### Deactivate API Key

Ask the user for the `key_prefix` (8 characters, e.g., `glk_ab12`). If they don't know it, list keys first.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_deactivateApiKey","params":{"key_prefix":"<KEY_PREFIX>","admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

Deactivation takes effect immediately (Redis cache is invalidated).

### Reactivate API Key

**Note:** This endpoint may not exist yet. If `admin_reactivateApiKey` is not available, inform the user it needs to be implemented.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_reactivateApiKey","params":{"key_prefix":"<KEY_PREFIX>","admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

## API Key Usage

Clients send the API key via the `X-API-Key` HTTP header:

```bash
curl -X POST "$BASE_URL" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: glk_<full_key>" \
  -d '{"jsonrpc":"2.0","method":"<method>","params":{...},"id":1}'
```

- Requests without `X-API-Key` are subject to anonymous rate limits (default: 10/min, 100/hr, 1000/day).
- Invalid or deactivated keys return `-32029 "Invalid API key"`.
- When the rate limit is exceeded, the response is HTTP 429 with a JSON-RPC error including `window`, `limit`, `current`, and `retry_after_seconds`.

## Common Errors

| Error | Cause |
|-------|-------|
| `-32000` "Admin access required" | Missing or invalid `admin_key` on hosted deployment |
| `-32602` "Tier not found: X" | Specified `tier_name` doesn't exist |
| `-32001` "Active API key with prefix X not found" | Key doesn't exist or is already deactivated |
| `-32029` "Invalid API key" | Key is invalid or deactivated (when rate limiting is enabled) |
| `-32029` "Rate limit exceeded: N requests per minute" | Key has exceeded its tier's rate limit |

## Key Format

- Full key: `glk_` + 64 hex characters (68 chars total)
- Key prefix: first 8 characters (e.g., `glk_ab12`)
- Storage: only the SHA-256 hash is stored in the database; the full key cannot be recovered

## Important Notes

- Always use `--data-binary @-` with heredoc (`<<'EOF'`) to avoid shell expansion issues with special characters in the admin key.
- When rate limiting is disabled (`RATE_LIMIT_ENABLED=false`), keys can still be created and managed, but rate limits are not enforced and invalid keys are not rejected.
- Cache invalidation happens automatically on deactivate/reactivate (5-minute TTL otherwise).

## Reference

- Models: `backend/database_handler/models.py` (ApiKey class)
- Endpoints: `backend/protocol_rpc/endpoints.py` (admin_create_api_key, admin_deactivate_api_key)
- Rate limiter: `backend/protocol_rpc/rate_limiter.py`
- Middleware: `backend/protocol_rpc/rate_limit_middleware.py`
