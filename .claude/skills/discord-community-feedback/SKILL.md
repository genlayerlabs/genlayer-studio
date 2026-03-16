---
name: discord-community-feedback
description: Monitor Discord community channel for user-reported bugs and issues
---

# Discord Community Feedback Skill

Monitor the GenLayer Discord community feedback channel for user-reported bugs, issues, and problems.

## Prerequisites

- Discord MCP server configured and running ([discordmcp](https://github.com/v-3/discordmcp))
- Bot added to the GenLayer Discord server with read permissions

## Channel Information

| Field | Value |
|-------|-------|
| Server ID | 1237055789441487021 |
| Channel ID | 1237114454877929482 |

## Quick Commands

Use the Discord MCP `read-messages` tool to fetch recent messages:

```
Tool: read-messages
Channel: 1237114454877929482
Limit: 100  (max allowed)
```

## Identifying User Problems

When reviewing messages, look for these indicators of issues:

### Problem Keywords
- **Errors**: error, bug, broken, crash, fail, exception
- **Functionality**: not working, doesn't work, can't, unable, stuck
- **Help requests**: help, issue, problem, wrong, weird
- **Performance**: slow, timeout, hang, freeze, unresponsive

### Common User-Reported Issues

| Category | Example Patterns | Related Component |
|----------|------------------|-------------------|
| Transaction failures | "tx failed", "transaction stuck" | consensus-worker |
| Contract errors | "contract not deploying", "execution error" | genvm, consensus-worker |
| UI issues | "page not loading", "button doesn't work" | frontend |
| API errors | "RPC error", "connection failed" | jsonrpc |
| Wallet issues | "can't connect wallet", "balance wrong" | frontend, jsonrpc |

## Workflow

1. **Fetch recent messages** from the feedback channel (up to 100)
2. **Scan for problem indicators** using keywords above
3. **Categorize issues** by component (frontend, backend, consensus, etc.)
4. **Cross-reference with logs** using the `hosted-studio-debug` skill if needed
5. **Summarize findings** for the team

## Triage Priority

| Priority | Indicators |
|----------|------------|
| High | Multiple users reporting same issue, "everything broken", data loss |
| Medium | Single user with reproducible issue, feature not working |
| Low | Questions, minor UI glitches, feature requests |

## Integration with Hosted Studio Debug

If a user reports an issue that might be related to the hosted environment:

1. Note the approximate time of the user's report
2. Use `hosted-studio-debug` skill to check logs around that time
3. Look for correlating errors in consensus-worker or jsonrpc logs

```bash
# Example: Check for errors around the time of a user report
argocd app logs studio-prd-workload --name studio-consensus-worker --tail 500 2>&1 | grep -iE "(error|exception|timeout)"
```

## Response Template

When summarizing community feedback:

```
## Community Feedback Summary

**Period**: [time range of messages reviewed]
**Total Messages**: [count]
**Issues Identified**: [count]

### High Priority
- [Issue description] - Reported by [count] users

### Medium Priority
- [Issue description]

### Low Priority / Questions
- [Description]

### No Issues
[If no problems found, note this]
```
