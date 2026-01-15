---
name: use-precommit-not-black
enabled: true
event: bash
pattern: \.venv/bin/black\s
action: block
---

🚫 **Don't run Black directly!**

The venv Black version (25.x) differs from pre-commit config version (24.4.2).
This causes formatting differences that fail CI.

**Instead, run:**
```bash
.venv/bin/pre-commit run --all-files
```

Or for just Black:
```bash
.venv/bin/pre-commit run black --all-files
```
