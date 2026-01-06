# Cloudflare CLI Skill

Debug and manage Cloudflare DNS, cache, and proxy settings using flarectl.

## Prerequisites

- flarectl installed: `brew install cloudflare/cloudflare/flarectl`
- API token set: `export CF_API_TOKEN=<token>`

## Commands

### Zones
```bash
# List all zones
flarectl zone list

# Get zone details
flarectl zone info --zone genlayer.com
```

### DNS Records
```bash
# List DNS records
flarectl dns list --zone genlayer.com

# Create record
flarectl dns create --zone genlayer.com --name subdomain --type A --content 1.2.3.4

# Update record (toggle proxy)
flarectl dns update --zone genlayer.com --id <record-id> --proxy=true

# Delete record
flarectl dns delete --zone genlayer.com --id <record-id>
```

### Cache
```bash
# Purge all cache
flarectl zone purge --zone genlayer.com --everything

# Purge specific URLs
flarectl zone purge --zone genlayer.com --files "https://studio.genlayer.com/api"
```

## Common Debug Patterns

### Check if Cloudflare Proxy is Enabled
```bash
flarectl dns list --zone genlayer.com | grep studio
# Look for "proxied: true" in output
```

### Test Direct to Origin (bypass Cloudflare)
```bash
# Get origin IP
dig +short studio.genlayer.com

# Test direct (if proxy disabled)
curl -v https://studio.genlayer.com/health

# Test with Host header to specific IP
curl -H "Host: studio.genlayer.com" https://<origin-ip>/health -k
```

### Diagnose 502/503 Errors
```bash
# Check if error is from Cloudflare or origin
# Cloudflare errors have cf-ray header
curl -I https://studio.genlayer.com/api 2>&1 | grep -i "cf-ray"

# Fast response (30-60ms) = Cloudflare WAF/rate limit
# Slow response (5s+) = Origin timeout
curl -w "%{time_total}s\n" -o /dev/null -s https://studio.genlayer.com/api
```

### Rate Limiting Check
```bash
# 429 with fast response = Cloudflare rate limiting
for i in {1..10}; do
  curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" https://studio.genlayer.com/api \
    -X POST -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_chainId","params":[],"id":1}'
done
```

## GenLayer Domains

| Domain | Purpose |
|--------|---------|
| studio.genlayer.com | Production studio |
| studio-dev.genlayer.com | Dev studio |
| studio-stage.genlayer.com | Staging studio |
| rally-testnet.genlayer.com | Rally production |
| rally-devnet.genlayer.com | Rally dev |

## SSL Modes

- **Full**: Cloudflare → Origin with any cert (allows self-signed)
- **Full (Strict)**: Cloudflare → Origin with valid cert
- **Flexible**: Cloudflare terminates SSL, HTTP to origin

GenLayer uses Cloudflare Origin Certificates - requires proxy enabled and Full (Strict) mode.
