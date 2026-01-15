---
name: unit-tests
description: Setup Python virtual environment and run unit tests with gltest
---

# Run Unit Tests

Setup the Python environment and run unit tests for GenLayer Studio.

## Prerequisites

- Python 3.12 installed
- virtualenv installed (`pip install virtualenv`)

## Setup Virtual Environment (first time or reset)

```bash
# Remove existing venv if present
rm -rf .venv

# Create new venv with Python 3.12
virtualenv -p python3.12 .venv

# Activate
source .venv/bin/activate

# Upgrade pip
pip install --upgrade pip

# Install all dependencies
pip install -r requirements.txt
pip install -r requirements.test.txt
pip install -r backend/requirements.txt

# Set Python path
export PYTHONPATH="$(pwd)"
```

## Run Tests

```bash
# Activate venv (if not already)
source .venv/bin/activate
export PYTHONPATH="$(pwd)"

# Run all unit tests
gltest --contracts-dir . tests/unit

# Run specific test file
gltest --contracts-dir . tests/unit/test_specific.py

# Run with verbose output
gltest --contracts-dir . tests/unit -v

# Run specific test function
gltest --contracts-dir . tests/unit/test_file.py::test_function_name
```

## Quick One-Liner (after initial setup)

```bash
source .venv/bin/activate && export PYTHONPATH="$(pwd)" && gltest --contracts-dir . tests/unit
```

## Troubleshooting

### Python 3.12 Not Found
```bash
# Check available Python versions
which python3.12

# On macOS with Homebrew
brew install python@3.12
```

### gltest Command Not Found
```bash
# Make sure venv is activated
source .venv/bin/activate

# Reinstall test dependencies
pip install -r requirements.test.txt
```

### Import Errors
```bash
# Ensure PYTHONPATH is set
export PYTHONPATH="$(pwd)"

# Verify from project root
pwd  # Should be genlayer-studio
```
