---
name: integration-tests
description: Setup Python virtual environment and run integration tests with gltest
---

# Run Integration Tests

Setup the Python environment and run integration tests for GenLayer Studio.

## Prerequisites

- Python 3.12 installed
- virtualenv installed (`pip install virtualenv`)
- Docker containers running (`docker compose up -d`)

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

## Ensure Services Are Running

Integration tests require the backend services:

```bash
# Start all services
docker compose up -d

# Verify services are healthy
docker compose ps
curl -s http://localhost:4000/health | jq .
```

## Run Tests

```bash
# Activate venv (if not already)
source .venv/bin/activate
export PYTHONPATH="$(pwd)"

# Run all integration tests
gltest --contracts-dir . tests/integration

# Run specific test file
gltest --contracts-dir . tests/integration/test_specific.py

# Run with verbose output
gltest --contracts-dir . tests/integration -svv

# Run specific test function
gltest --contracts-dir . tests/integration/test_file.py::test_function_name
```

## Quick One-Liner (after initial setup)

```bash
source .venv/bin/activate && export PYTHONPATH="$(pwd)" && gltest --contracts-dir . tests/integration
```

## Test Contracts

Integration test contracts are located in:
```
tests/integration/test_contracts/
```

## Troubleshooting

### Connection Refused Errors
```bash
# Ensure Docker services are running
docker compose ps

# Restart services if needed
docker compose restart
```

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

### Timeout Errors
```bash
# Check backend logs for issues
docker compose logs -f backend

# Increase timeout if needed (in test or via env)
```
