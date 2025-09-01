# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GenLayer Studio is an interactive sandbox for the GenLayer Protocol - a blockchain platform focused on "Intelligent Contracts" that use AI validators for consensus. The project consists of a Python Flask backend, Vue.js 3 TypeScript frontend, PostgreSQL database, and Hardhat for Ethereum local development.

## Essential Development Commands

### Quick Start
```bash
# Install and run the studio
npm install -g genlayer
genlayer init
genlayer up
```

### Frontend Development
```bash
cd frontend
npm run dev          # Start Vite dev server with hot reload
npm run build        # Production build
npm run test         # Run unit tests with Vitest
npm run test:e2e     # End-to-end tests with Mocha
npm run lint         # ESLint checking
npm run format       # Prettier formatting
npm run type-check   # TypeScript checking
```

### Backend Testing
```bash
# Run all tests
pytest

# Specific test categories
pytest tests/unit/              # Unit tests only
pytest tests/integration/       # Integration tests with AI validators
pytest tests/db-sqlalchemy/     # Database layer tests
pytest tests/e2e/               # End-to-end scenarios
```

### Docker Services
```bash
docker-compose up               # Start all services
docker-compose up --build       # Rebuild and start
docker-compose down             # Stop all services
docker-compose logs -f jsonrpc  # Follow backend logs
```

### Code Quality
```bash
# Run all pre-commit hooks
pre-commit run --all-files

# Python formatting
black backend/

# Frontend linting and formatting
cd frontend && npm run lint && npm run format
```

## Architecture Overview

### Backend Structure (`/backend`)
- **protocol_rpc/**: JSON-RPC server implementation with Flask-JSONRPC
- **consensus/**: AI-powered consensus algorithm for transaction validation
- **database_handler/**: SQLAlchemy models and PostgreSQL handlers
- **validators/**: LLM provider integrations (OpenAI, Anthropic, Google, Ollama)
- **node/**: Network node functionality and peer communication
- **rollup/**: Transaction processing and rollup mechanisms

Key patterns:
- JSON-RPC API for all client-server communication
- WebSocket support via Flask-SocketIO for real-time updates
- Modular validator system supporting multiple LLM providers
- Database migrations managed with Alembic

### Frontend Structure (`/frontend`)
- Vue 3 with Composition API and TypeScript
- Vite build system for fast development
- Pinia for state management
- Monaco Editor for code editing interface
- TailwindCSS for styling
- Socket.IO client for real-time backend communication

### Testing Infrastructure
- Backend: pytest with fixtures for database and validator mocking
- Frontend: Vitest for unit tests, Mocha + Selenium for E2E tests
- Integration tests simulate full AI validator consensus rounds
- Load tests available in `/tests/load/`

### Environment Configuration
Essential `.env` variables:
```bash
# Database
DBHOST=postgres
DBNAME=genlayer_state
DBUSER=postgres
DBPASSWORD=postgres

# Database Pool Configuration
DB_POOL_SIZE=20              # Base pool size
DB_MAX_OVERFLOW=30           # Maximum overflow connections
DB_POOL_RECYCLE=3600         # Recycle connections after seconds
DB_POOL_TIMEOUT=30           # Timeout for getting connection from pool
DB_CONNECT_TIMEOUT=10        # Connection timeout in seconds
DB_STATEMENT_TIMEOUT=300000  # Statement timeout in milliseconds (5 minutes)
DB_POOL_PRE_PING=true        # Check connection health before use

# Service Ports
RPCPORT=4000
FRONTEND_PORT=8080
HARDHAT_PORT=8545

# LLM API Keys (at least one required)
OPENAIKEY=<your_key>
ANTHROPIC_API_KEY=<your_key>
GEMINI_API_KEY=<your_key>

# Build Targets
BACKEND_BUILD_TARGET=debug    # 'debug' for development, 'prod' for production
FRONTEND_BUILD_TARGET=final   # 'dev' for development, 'final' for production

# Optional: Enable Hardhat
COMPOSE_PROFILES=hardhat     # Add to enable Hardhat service
```

## Key Development Workflows

### Adding New Intelligent Contract Features
1. Implement contract logic in `/examples/contracts/`
2. Add RPC endpoints in `/backend/protocol_rpc/`
3. Update frontend components in `/frontend/src/components/`
4. Write integration tests in `/tests/integration/`

### Modifying Consensus Algorithm
1. Core logic in `/backend/consensus/`
2. Validator implementations in `/backend/validators/`
3. Test with different LLM configurations in integration tests

### Database Schema Changes
1. Modify models in `/backend/database_handler/models/`
2. Create Alembic migration
3. Update related RPC endpoints
4. Test database operations in `/tests/db-sqlalchemy/`

### Frontend Feature Development
1. Components in `/frontend/src/components/`
2. State management in `/frontend/src/stores/`
3. API calls via `/frontend/src/services/`
4. Unit tests alongside components
5. E2E tests in `/frontend/tests/e2e/`

## Database Connection Pool Management

The application uses SQLAlchemy connection pooling optimized for managed databases:

### Pool Configuration Features
- **Health Checks**: `pool_pre_ping=True` verifies connections before use
- **Connection Recycling**: Connections automatically recycled every hour (configurable)
- **Timeout Management**: 30-second timeout for pool acquisition prevents hanging
- **Optimized Limits**: 20 base connections + 30 overflow for balanced performance

### Session Management
Use the `SessionManager` for safe database transactions:
```python
from backend.database_handler.session_manager import SessionManager

with SessionManager(session_factory) as session:
    # Database operations
    # Automatic commit on success, rollback on error
```

### Testing Database Pools
- Unit tests: `/tests/db-sqlalchemy/test_database_pool_config.py`
- Integration tests: `/tests/integration/test_pool_with_services.py`
- Load tests: `/tests/load/test_database_pool_load.py`

## Important Notes

- The project uses pre-commit hooks for code formatting - commits may be modified automatically
- Integration tests require at least one LLM API key configured
- Hardhat service is optional but required for Ethereum transaction processing
- Frontend development server proxies API calls to backend on port 4000
- Database migrations run automatically on backend startup
- WebSocket connections required for real-time contract execution updates
- Database pool configuration is critical for production deployments with managed databases