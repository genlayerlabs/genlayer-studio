---
name: linear-issue-fixer
description: Fetch the most urgent Linear issue with tag "studio" and size XS, then fix it
invocation: user
argument-hint: Optional issue ID to work on a specific issue
---

# Linear Issue Fixer

Automatically fetch the most urgent Linear issue with the "studio" label and "XS" size estimate, analyze it, implement a fix, verify with tests, and create a pull request.

## Prerequisites

- Linear MCP server connected
- GitHub CLI (`gh`) authenticated
- Docker running (for integration tests)
- Python 3.12 with virtualenv
- Checkout `main` branch and pull the latest changes

## Linear MCP Server Setup

If Linear MCP is not configured, set it up first:

```bash
claude mcp add --transport sse linear https://mcp.linear.app/sse
```

Then restart Claude Code and run `/mcp` to authenticate with Linear via OAuth.

Alternatively, add via JSON:
```bash
claude mcp add-json linear '{"command": "npx", "args": ["-y","mcp-remote","https://mcp.linear.app/sse"]}'
```

To troubleshoot auth issues:
```bash
rm -rf ~/.mcp-auth
```

## Workflow

### Step 1: Fetch Candidate XS Studio Issues

Use Linear MCP tools to find candidate issues:

```
1. First, check Linear MCP is connected:
   Run /mcp to verify "linear" server is connected

2. Search for issues with "studio" label and XS size:
   Use list_issues with:
   - label: "studio"
   - Sort by priority (highest first)
   - limit: 10

3. Filter results to find XS-sized issues:
   - Look for issues with estimate/size "XS" or 1 point
   - Prioritize by urgency: Urgent > High > Medium > Low

4. If a specific issue ID was provided as argument, fetch that instead:
   $ARGUMENTS
```

**Priority Levels in Linear:**
- 1 = Urgent
- 2 = High
- 3 = Medium
- 4 = Low
- 0 = No priority

### Step 2: User Selection

**IMPORTANT: Always ask the user to select which issue to work on.**

Present the candidate issues to the user with:
- Issue identifier (e.g., DXP-123)
- Title
- Priority level
- Size estimate
- Brief description summary

Use the AskUserQuestion tool to let the user choose which issue to work on. Do NOT proceed with an issue automatically - always wait for user confirmation.

Example prompt:
```
I found the following XS studio issues:

1. DXP-456 [High] "Fix address validation" - XS
2. DXP-789 [Medium] "Update error message" - XS
3. DXP-012 [Low] "Typo in config" - XS

Which issue would you like me to work on?
```

### Step 3: Analyze the Selected Issue

Once the user has selected an issue:

```
1. Get full issue details including:
   - Title and description
   - Current status
   - Any linked issues or dependencies
   - Comments with context
   - Acceptance criteria

2. Understand the scope:
   - What files are likely affected?
   - Is this a bug fix, feature, or improvement?
   - Are there any blocking dependencies?
```

Gather from analysis:
- **Issue type** (bug, feature, task, improvement)
- **Affected area** (backend, frontend, infrastructure)
- **Expected behavior** vs current behavior
- **Acceptance criteria**
- **Related files** mentioned in description

### Step 4: Explore the Codebase

Before implementing, understand the relevant code:

```
1. Launch an Explore agent to find relevant code:
   - Search for files/functions mentioned in the issue
   - Understand the existing implementation
   - Identify patterns and conventions used

2. Read key files that will need modification

3. Check for existing tests covering the affected area
```

### Step 5: Plan the Fix

Create a clear implementation plan:

1. **Root Cause / Current State**
   - What is the current behavior?
   - Why does it need to change?

2. **Proposed Solution**
   - What changes are needed?
   - Which files need modification?
   - Are there related areas to consider?

3. **Risk Assessment**
   - Could this fix break other functionality?
   - Does it need backward compatibility?

4. **Testing Strategy**
   - What unit tests should be added/modified?
   - What integration tests are relevant?

**Present the plan to the user and get approval before implementing.**

### Step 6: Implement the Fix

1. **Create a Feature Branch**
   ```bash
   git checkout main
   git pull origin main
   git checkout -b <issue-identifier>-<short-description>
   ```

   Use the Linear issue identifier (e.g., `DXP-123`) as branch prefix.

2. **Make Code Changes**
   - Implement the planned fix
   - Follow codebase conventions
   - Add appropriate error handling
   - Add or update tests

3. **Commit Changes**
   ```bash
   git add <files>
   git commit -m "<type>: <description>

   Resolves <LINEAR-ISSUE-ID>

   Co-Authored-By: Claude <noreply@anthropic.com>"
   ```

### Step 7: Run ALL Test Suites

**IMPORTANT: You MUST run ALL of the following test suites before creating a PR. Do NOT skip any.**

#### 7.1 DB/SQLAlchemy Tests (Primary Backend Tests - Dockerized)

```bash
docker compose -f tests/db-sqlalchemy/docker-compose.yml --project-directory . run --build --rm tests
```

#### 7.2 Backend Unit Tests

```bash
.venv/bin/pytest tests/unit/ -v --tb=short --ignore=tests/unit/test_rpc_endpoint_manager.py
```

#### 7.3 Frontend Unit Tests

```bash
cd frontend && npm run test
```

**If any tests fail:**
- Analyze the failure
- Fix the issue
- Re-run ALL test suites until they pass

### Step 8: Run Integration Tests (if needed)

For changes that affect runtime behavior:

```bash
# Ensure Docker is running with the studio
docker compose up -d

source .venv/bin/activate
export PYTHONPATH="$(pwd)"

# Run integration tests
gltest --contracts-dir . tests/integration -n 4 --ignore=tests/integration/test_validators.py
```

### Step 9: Create Pull Request

**Use the `/create-pr` skill** to create the pull request.

This skill will:
1. Push the branch to origin
2. Analyze the diff against main
3. Create a PR with the proper template format

Make sure to include the Linear issue URL in the PR body with `Fixes <LINEAR-ISSUE-URL>` so the issue gets linked automatically.

### Step 10: Update Linear Issue

After PR is created:

```
1. Add a comment to the Linear issue with:
   - Link to the PR
   - Brief summary of the implementation

2. Update issue status to "In Review" or appropriate status

Use linear_add_comment and linear_update_issue tools
```

## Issue Size Reference

| Size | Points | Typical Scope |
|------|--------|---------------|
| XS   | 1      | Trivial fix, typo, config change |
| S    | 2      | Simple bug fix, small feature |
| M    | 3      | Moderate feature, multiple files |
| L    | 5      | Large feature, significant changes |
| XL   | 8+     | Epic-level work |

## Linear MCP Tools Reference

| Tool | Purpose |
|------|---------|
| `linear_search_issues` | Find issues with filters (labels, priority, status) |
| `linear_update_issue` | Update issue status, assignee, etc. |
| `linear_add_comment` | Add comment to an issue |
| `linear_create_issue` | Create new issues |
| `linear_get_user_issues` | Get issues assigned to a user |

### Common Search Patterns

```
# Find urgent studio issues
linear_search_issues(labels=["studio"], priority=1)

# Find all studio issues sorted by priority
linear_search_issues(labels=["studio"], limit=20)

# Find in-progress issues
linear_search_issues(status="In Progress", teamId="...")
```

## Troubleshooting

### Linear MCP Not Connected
- Run `claude mcp list` to check status
- Run `/mcp` to trigger authentication
- Clear auth with `rm -rf ~/.mcp-auth` and retry

### Cannot Find Issues
- Verify the "studio" label exists in your Linear workspace
- Check you have access to the relevant team
- Try searching without filters first

### Tests Failing
- Check Docker is running: `docker compose ps`
- View logs: `docker compose logs -f`
- Run specific test file to isolate issue

## Example Session

```
1. Check Linear MCP connection:
   > /mcp
   > linear: Connected

2. Search for XS studio issues:
   > list_issues(label="studio")
   > Found 3 XS issues

3. Ask user to select:
   > "Which issue would you like to work on?"
   > 1. DXP-456 [High] "Fix address validation" - XS
   > 2. DXP-789 [Medium] "Update error message" - XS
   > User selects: DXP-456

4. Analyze selected issue:
   - Bug: addresses without 0x prefix cause validation errors
   - Affected: backend validation logic
   - Acceptance: addresses like "abc123..." should be accepted

5. Explore codebase:
   - Find address validation code
   - Understand current validation logic

6. Plan fix:
   - Add normalization to accept addresses without prefix
   - Add unit test for edge case

7. Implement:
   - Edit validation.py
   - Add test_address_without_prefix.py

8. Test:
   - docker compose -f tests/db-sqlalchemy/docker-compose.yml run tests
   - All pass

9. Create PR:
   - Use /create-pr skill
   - Include "Fixes https://linear.app/genlayer/issue/DXP-456"

10. Update Linear:
    - Add PR link as comment
    - Move to "In Review"
```
