---
name: sentry-issue-fixer
description: Fetch, analyze, fix Sentry issues, run tests, and create PRs
invocation: user
---

# Sentry Issue Fixer

Automatically fetch the most important open Sentry issue, analyze it, implement a fix, verify with tests, and create a pull request.

## Prerequisites

- Sentry MCP server connected (configured in `~/.claude/settings.json`)
- GitHub CLI (`gh`) authenticated
- Docker running (for integration tests)
- Python 3.12 with virtualenv
- Checkout `main` branch and pull the latest changes

## MCP Server Configuration

The Sentry MCP server should be configured in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "Sentry": {
      "url": "https://mcp.sentry.dev/mcp"
    }
  }
}
```

On first use, you'll be prompted to authenticate with Sentry via OAuth.

## Workflow

### Step 1: Fetch Most Important Open Issue

Use Sentry MCP tools to get the highest priority unresolved issue:

```
1. First, list available organizations:
   mcp__Sentry__list_organizations

2. List projects in the organization:
   mcp__Sentry__list_projects with organization_slug

3. Search for unresolved issues sorted by priority/frequency:
   mcp__Sentry__search_issues with:
   - organization_slug
   - project_slug
   - query: "is:unresolved"
   - sort: "freq" or "priority"

4. Get detailed issue information:
   mcp__Sentry__get_issue with issue_id
```

### Step 2: Analyze the Issue

Use Sentry's analysis tools:

```
1. Get issue details and stack trace:
   mcp__Sentry__get_issue_details

2. Find errors in specific files:
   mcp__Sentry__find_errors_in_file with filename from stack trace

3. Use Seer AI for root cause analysis (if available):
   mcp__Sentry__invoke_seer_agent for AI-powered fix suggestions
```

Gather from analysis:
- **Exception type and message**
- **Stack trace** (file, function, line number)
- **Error frequency** and affected users
- **Environment context** (tags, release, etc.)
- **Pattern of occurrences**

### Step 3: Plan the Fix

Before implementing, create a plan:

1. **Root Cause Analysis**
   - What is causing the error?
   - Is it a logic error, missing validation, race condition, etc.?

2. **Proposed Solution**
   - What changes are needed?
   - Which files need modification?
   - Are there related issues to consider?

3. **Risk Assessment**
   - Could this fix break other functionality?
   - Does it need backward compatibility?

4. **Testing Strategy**
   - What unit tests should be added/modified?
   - What integration tests are relevant?

### Step 4: Implement the Fix

1. **Create a Feature Branch**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b fix/sentry-<issue-id>-<short-description>
   ```

2. **Make Code Changes**
   - Implement the planned fix
   - Add appropriate error handling
   - Add or update tests

3. **Commit Changes**
   ```bash
   git add <files>
   git commit -m "fix: <description>

   Fixes Sentry issue <issue-id>

   Co-Authored-By: Claude <noreply@anthropic.com>"
   ```

### Step 5: Run ALL Test Suites

**IMPORTANT: You MUST run ALL of the following test suites before creating a PR. Do NOT skip any.**

#### 5.1 DB/SQLAlchemy Tests (Primary Backend Tests - Dockerized)

```bash
docker compose -f tests/db-sqlalchemy/docker-compose.yml --project-directory . run --build --rm tests
```

#### 5.2 Backend Unit Tests

```bash
.venv/bin/pytest tests/unit/ -v --tb=short --ignore=tests/unit/test_rpc_endpoint_manager.py
```

#### 5.3 Frontend Unit Tests

```bash
cd frontend && npm run test
```

**If any tests fail:**
- Analyze the failure
- Fix the issue
- Re-run ALL test suites until they pass

### Step 6: Run Integration Tests (Optional - if Docker services are running)

Follow the integration-tests skill:

```bash
# Ensure Docker is running with the studio
docker compose ps  # Verify services are up

source .venv/bin/activate
export PYTHONPATH="$(pwd)"

# Run integration tests in parallel (excluding test_validators.py)
gltest --contracts-dir . tests/integration -n 4 --ignore=tests/integration/test_validators.py

# Run validator tests separately
gltest --contracts-dir . tests/integration/test_validators.py
```

**If tests fail:**
- Check Docker logs: `docker compose logs -f`
- Analyze test output
- Fix issues and re-run

### Step 7: Create Pull Request

Follow the create-pr skill:

```bash
# Push branch
git push -u origin $(git branch --show-current)

# Create PR with Sentry context
gh pr create --title "fix: <description from Sentry issue>" --body "$(cat <<'EOF'
Fixes Sentry issue: <link-to-sentry-issue>

# What

- [Describe the error that was occurring]
- [List the changes made to fix it]

# Why

- This error was affecting [X users / occurring Y times]
- Root cause: [explanation]

# Testing done

- [x] Unit tests pass
- [x] Integration tests pass
- [x] Verified fix resolves the Sentry error pattern

# Decisions made

- [Document any non-obvious choices]

# Checks

- [x] I have tested this code
- [x] I have reviewed my own PR
- [x] I have set a descriptive PR title compliant with conventional commits

# Reviewing tips

- Focus on [specific areas]
- The main change is in [file/function]

# User facing release notes

- Fixed: [user-visible description of what was broken]
EOF
)"
```

## Sentry MCP Tools Reference

Based on Sentry MCP documentation (https://docs.sentry.io/product/sentry-mcp/):

| Category | Tools |
|----------|-------|
| **Core** | Organizations, Projects, Teams, Issues, DSNs |
| **Analysis** | Error Searching, Issue Analysis, Seer Integration |
| **Advanced** | Release Management, Performance Monitoring, Custom Queries |

### Common Tool Patterns

```
# List organizations you have access to
mcp__Sentry__list_organizations

# List projects in an organization
mcp__Sentry__list_projects(organization_slug)

# Search for issues
mcp__Sentry__search_issues(organization_slug, query="is:unresolved")

# Get issue details
mcp__Sentry__get_issue(issue_id)

# Find errors in a specific file
mcp__Sentry__find_errors_in_file(organization_slug, project_slug, filename)

# Use Seer AI agent for analysis
mcp__Sentry__invoke_seer(issue_id)
```

## Common Issue Patterns

| Error Type | Typical Fix |
|------------|-------------|
| `NoneType` errors | Add null checks, validate inputs |
| `KeyError` | Check dict key existence, use `.get()` |
| Timeout errors | Add retry logic, increase timeout |
| Connection errors | Add error handling, retry with backoff |
| Validation errors | Improve input validation, add type hints |

## Example Session

```
1. List organizations:
   > mcp__Sentry__list_organizations
   Result: [{"slug": "genlayer", ...}]

2. Search unresolved issues:
   > mcp__Sentry__search_issues(org="genlayer", query="is:unresolved", sort="freq")
   Result: Top issue: "KeyError: 'validator_address' in consensus/worker.py"

3. Get issue details:
   > mcp__Sentry__get_issue(issue_id="12345")
   Result: Stack trace showing error at worker.py:145

4. Analyze:
   - Error in worker.py:145 accessing result['validator_address']
   - Occurs when validator response is incomplete
   - 47 events, affecting 12 users

5. Plan:
   - Add validation before accessing key
   - Log warning for incomplete responses
   - Add unit test for edge case

6. Implement:
   - Edit worker.py to add .get() with default
   - Add test_worker_missing_validator_address test

7. Test:
   - gltest --contracts-dir . tests/unit/test_worker*.py
   - All pass

8. Create PR:
   - gh pr create with Sentry context
```

## Troubleshooting

### Sentry MCP Not Connected
- Restart Claude Code to trigger OAuth flow
- Check `~/.claude/settings.json` has the mcpServers configuration
- Verify you have access to the Sentry organization

### Cannot Find Issues
- Check organization and project slugs are correct
- Verify you have the right permissions in Sentry
- Try different query filters (is:unresolved, is:unhandled)

### Cannot Reproduce Issue Locally
- Check environment differences (env vars, config)
- Use Sentry event data to understand exact conditions
- Add logging to capture more context

### Tests Timeout
- Use `--leader-only` for faster integration tests
- Run specific test files instead of full suite
