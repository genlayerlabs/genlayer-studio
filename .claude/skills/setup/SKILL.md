---
name: setup
description: Setup and launch GenLayer Studio with Docker Compose
---

# Setup GenLayer Studio

Setup and launch the entire GenLayer Studio development environment.

## Prerequisites

- Docker installed and running

## Setup Steps

### 1. Verify Docker is Running

```bash
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### 2. Start All Services

```bash
docker compose up -d
```

This starts:
- **Frontend**: Vue 3 app on http://localhost:8080
- **Backend**: Flask JSON-RPC API on http://localhost:4000
- **PostgreSQL**: Database on port 5432
- **WebDriver**: Selenium service for contract sandboxing

### 3. Initialize Database (first time only)

```bash
genlayer init
```

Or manually:
```bash
docker exec -it genlayer-studio-backend-1 alembic upgrade head
```

### 4. Verify Services

```bash
# Check all containers are running
docker compose ps

# Test backend health
curl -s http://localhost:4000/health | jq .

# Open frontend
open http://localhost:8080
```

## Common Commands

| Command | Description |
|---------|-------------|
| `docker compose up -d` | Start all services (detached) |
| `docker compose down` | Stop all services |
| `docker compose down -v` | Stop and clear all data |
| `docker compose logs -f` | Tail all logs |
| `docker compose logs -f backend` | Tail backend logs |
| `docker compose restart backend` | Restart specific service |

## Alternative: GenLayer CLI

If you have the GenLayer CLI installed:

```bash
genlayer up      # Start all services
genlayer init    # Initialize database
genlayer down    # Stop all services
```

## Troubleshooting

### Port Already in Use
```bash
# Find what's using port 8080
lsof -i :8080

# Or use different ports in .env
```

### Database Connection Issues
```bash
# Check database is ready
docker compose logs db

# Reset database
docker compose down -v && docker compose up -d
```

### Container Won't Start
```bash
# Check logs for specific service
docker compose logs backend

# Rebuild containers
docker compose build --no-cache
docker compose up -d
```
