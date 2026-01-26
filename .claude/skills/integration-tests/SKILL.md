---
name: integration-tests
description: Setup Python virtual environment and run integration tests with gltest
---

# Run Integration Tests

Setup the Python environment, start the studio, and run integration tests for GenLayer Studio.

## Prerequisites

- Python 3.12 installed
- virtualenv installed (`pip install virtualenv`)
- Docker and Docker Compose installed

## Step 1: Start the Studio

The studio must be running before executing integration tests.

```bash
# Stop any existing containers and rebuild
docker-compose down && docker-compose up --build
```

Wait for all services to be healthy before proceeding.

## Step 2: Setup Virtual Environment (first time or reset)

In a separate terminal:

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

## Step 3: Run Integration Tests

```bash
# Activate venv (if not already)
source .venv/bin/activate
export PYTHONPATH="$(pwd)"

# Run all integration tests
gltest --contracts-dir . tests/integration

# Run faster with leader-only mode (skips validator consensus)
gltest --contracts-dir . tests/integration --leader-only

# Run specific test file
gltest --contracts-dir . tests/integration/test_specific.py

# Run with verbose output
gltest --contracts-dir . tests/integration -v

# Run specific test function
gltest --contracts-dir . tests/integration/test_file.py::test_function_name
```

## Quick Commands

### Full Setup (first time)
```bash
# Terminal 1: Start studio
docker-compose down && docker-compose up --build

# Terminal 2: Setup and run tests
rm -rf .venv && \
virtualenv -p python3.12 .venv && \
source .venv/bin/activate && \
pip install --upgrade pip && \
pip install -r requirements.txt && \
pip install -r requirements.test.txt && \
pip install -r backend/requirements.txt && \
export PYTHONPATH="$(pwd)" && \
gltest --contracts-dir . tests/integration
```

### Quick Run (after initial setup, studio already running)
```bash
source .venv/bin/activate && export PYTHONPATH="$(pwd)" && gltest --contracts-dir . tests/integration
```

### Fast Run (leader-only mode)
```bash
source .venv/bin/activate && export PYTHONPATH="$(pwd)" && gltest --contracts-dir . tests/integration --leader-only
```

## Troubleshooting

### Studio Not Running
```bash
# Check if containers are up
docker-compose ps

# Check container logs
docker-compose logs -f backend
```

### Connection Refused Errors
The studio needs time to initialize. Wait for all services to be healthy:
```bash
# Watch container status
docker-compose ps

# Check backend health
curl http://localhost:4000/health
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

### Test Timeouts
Integration tests may timeout if the studio is under load. Try:
```bash
# Run with leader-only for faster execution
gltest --contracts-dir . tests/integration --leader-only

# Run a single test file to isolate issues
gltest --contracts-dir . tests/integration/test_specific.py -v
```
