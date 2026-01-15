---
name: create-pr
description: Create GitHub pull requests by analyzing branch diff and following the project template
---

# Create Pull Request Skill

Create GitHub pull requests by analyzing branch diff and following the project template.

## Prerequisites

- On a feature branch (not main)
- Changes committed locally
- GitHub CLI (`gh`) authenticated

## Workflow

### 1. Gather Context

```bash
# Get current branch name
git branch --show-current

# Check if branch is pushed
git status

# Get diff against main
git diff main...HEAD --stat
git diff main...HEAD

# Get commit history for this branch
git log main..HEAD --oneline
```

### 2. Analyze Changes

Review the diff to understand:
- **What**: Files changed, functions modified, features added/removed
- **Why**: The purpose behind the changes (bug fix, feature, refactor)
- **Testing**: What tests were added or should be run

### 3. Create PR

Use `gh pr create` with the project template structure:

```bash
gh pr create --title "<type>: <description>" --body "$(cat <<'EOF'
Fixes #issue-number-here

# What

- [Describe specific changes made]
- [List modified components/files]

# Why

- [Explain the motivation]
- [Reference related issues]

# Testing done

- [List tests run]
- [Describe manual testing]

# Decisions made

- [Document any non-obvious choices]

# Checks

- [x] I have tested this code
- [x] I have reviewed my own PR
- [ ] I have created an issue for this PR
- [x] I have set a descriptive PR title compliant with conventional commits

# Reviewing tips

- [Guidance for reviewers]

# User facing release notes

- [Changes visible to end users, if any]
EOF
)"
```

## Conventional Commit Types

| Type | Use Case |
|------|----------|
| feat | New feature |
| fix | Bug fix |
| docs | Documentation only |
| style | Formatting, no code change |
| refactor | Code change, no feature/fix |
| perf | Performance improvement |
| test | Adding/fixing tests |
| chore | Maintenance, deps, config |

## Examples

### Feature PR
```bash
gh pr create --title "feat: add user authentication" --body "..."
```

### Bug Fix PR
```bash
gh pr create --title "fix: resolve timeout in consensus worker" --body "..."
```

### With Issue Link
```bash
gh pr create --title "fix: handle empty contract data" --body "Fixes #123..."
```

## Tips

- Keep PR title under 72 characters
- Reference issue numbers with `Fixes #N` or `Closes #N` for auto-close
- For WIP, use `gh pr create --draft`
- Push branch first if needed: `git push -u origin $(git branch --show-current)`
