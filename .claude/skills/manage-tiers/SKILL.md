---
name: manage-tiers
description: Create, list, update, and delete API rate limiting tiers
invocation: user
---

# Manage API Rate Limiting Tiers

CRUD operations for API rate limiting tiers on GenLayer Studio deployments.

## Setup

Before any operation, determine the target environment:

1. **Ask the user** which environment they are targeting:
   - **Local dev**: `BASE_URL=http://localhost:4000/api`, no `admin_key` needed
   - **Hosted** (dev/stg/prd): `BASE_URL=https://<domain>/api`, requires `admin_key`

2. For hosted deployments, **ask the user for the `ADMIN_API_KEY`** (stored in k8s secrets as `ADMIN_API_KEY`).

## Operations

Ask the user which operation to perform: **Create**, **List**, **Update**, or **Delete**.

### List Tiers

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_listTiers","params":{"admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

For local dev (no admin_key):
```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"admin_listTiers","params":{},"id":1}' | python3 -m json.tool
```

### Create Tier

Ask the user for: `name`, `rate_limit_minute`, `rate_limit_hour`, `rate_limit_day`.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_createTier","params":{"name":"<NAME>","rate_limit_minute":<RPM>,"rate_limit_hour":<RPH>,"rate_limit_day":<RPD>,"admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

### Update Tier

**Note:** This endpoint may not exist yet. Check by listing tiers first. If `admin_updateTier` is not available, inform the user it needs to be implemented.

Ask the user for: `name` (existing tier) and which limits to change.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_updateTier","params":{"name":"<NAME>","rate_limit_minute":<RPM>,"rate_limit_hour":<RPH>,"rate_limit_day":<RPD>,"admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

Only include the fields that need changing.

### Delete Tier

**Note:** This endpoint may not exist yet. If `admin_deleteTier` is not available, inform the user it needs to be implemented.

Deletion fails if any API keys (active or inactive) reference the tier.

```bash
curl -s -X POST "$BASE_URL" -H "Content-Type: application/json" --data-binary @- <<'EOF' | python3 -m json.tool
{"jsonrpc":"2.0","method":"admin_deleteTier","params":{"name":"<NAME>","admin_key":"<ADMIN_KEY>"},"id":1}
EOF
```

## Default Seeded Tiers

These are created by the Alembic migration:

| Tier | Requests/min | Requests/hr | Requests/day |
|------|-------------|-------------|--------------|
| free | 30 | 500 | 5,000 |
| pro | 120 | 3,000 | 50,000 |
| unlimited | 999,999 | 999,999 | 999,999 |

## Common Errors

| Error | Cause |
|-------|-------|
| `-32000` "Admin access required" | Missing or invalid `admin_key` on hosted deployment |
| `-32602` "Duplicate tier name" | Tier with that name already exists (unique constraint) |
| `-32602` "Cannot delete tier: N API key(s) still reference it" | Deactivate/delete keys first |
| `-32001` "Tier not found: X" | Tier name doesn't exist |

## Important Notes

- Always use `--data-binary @-` with heredoc (`<<'EOF'`) to avoid shell expansion issues with special characters in the admin key (e.g., `+`, `=`).
- Tier names must be unique and max 50 characters.
- Rate limits are enforced per sliding window (minute, hour, day) using Redis sorted sets.
- When rate limiting is disabled (`RATE_LIMIT_ENABLED=false`), tiers can still be managed but limits are not enforced.

## Reference

- Models: `backend/database_handler/models.py` (ApiTier class)
- Endpoints: `backend/protocol_rpc/endpoints.py` (admin_create_tier, admin_list_tiers)
- Migration: `backend/database_handler/migration/versions/b1c3e5f7a902_add_api_tiers_and_api_keys.py`
