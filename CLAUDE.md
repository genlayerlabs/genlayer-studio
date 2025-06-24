# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

GenLayer Studio is an interactive sandbox for developing and testing Intelligent Contracts on the GenLayer Protocol. It's a multi-service application with:
- **Backend**: Python Flask-based JSON-RPC server with async SQLAlchemy
- **Frontend**: Vue 3 + TypeScript with Vite
- **Smart Contracts**: Solidity contracts via Hardhat
- **Infrastructure**: Docker Compose orchestration with PostgreSQL, Ollama (LLM), and Traefik

## Key Commands

### Frontend Development
```bash
# From the frontend/ directory
npm install          # Install dependencies
npm run dev          # Start development server with hot reload
npm run build        # Build for production
npm run test         # Run all tests
npm run test:unit    # Run unit tests only
npm run test:e2e     # Run E2E tests
npm run lint         # Lint code
npm run format       # Format code with Prettier
npm run type-check   # Check TypeScript types
```

### Backend Development
```bash
# Backend uses Docker for development
docker compose up    # Start all services

# Run backend tests
pytest tests/unit                    # Unit tests
pytest tests/integration             # Integration tests
pytest tests/e2e                     # End-to-end tests

# Database migrations (from backend/database_handler/)
alembic revision --autogenerate -m "migration name"  # Create migration
docker compose up database-migration --build          # Apply migrations
alembic downgrade -1                                 # Revert last migration
```

### Smart Contract Development
```bash
# From hardhat/ directory
npm run node         # Start local Hardhat node
```

### Full Stack Development
```bash
# Quick start (using genlayer CLI)
genlayer up

# Manual start with Docker
cp .env.example .env
docker compose up

# Frontend dev mode with backend services
docker compose up jsonrpc webrequest ollama database-migration postgres
cd frontend && npm run dev
```

## Architecture Overview

### Service Architecture
The application uses Docker Compose to orchestrate multiple services:
- **jsonrpc**: Flask-based JSON-RPC API server (port 4000)
- **postgres**: PostgreSQL database for state persistence
- **database-migration**: Alembic migrations service
- **frontend**: Vue.js application (port 8080)
- **ollama**: Local LLM service for validator nodes
- **webdriver**: Selenium service for web validators
- **hardhat**: Ethereum node for transaction processing (optional)
- **traefik**: Reverse proxy for routing

### Backend Architecture (Flask + SQLAlchemy)
- **backend/consensus/**: Consensus algorithm implementation
- **backend/node/**: GenVM node implementation for contract execution
- **backend/protocol_rpc/**: JSON-RPC server handling API requests
- **backend/database_handler/**: SQLAlchemy models and database operations
- **backend/validators/**: LLM and web validator implementations
- **backend/rollup/**: Rollup consensus service

Key patterns:
- Async SQLAlchemy with PostgreSQL
- WebSocket support via Flask-SocketIO
- JSON-RPC protocol for API communication
- Pydantic for request/response validation

### Frontend Architecture (Vue 3 + TypeScript)
- **src/components/**: Reusable Vue components
- **src/stores/**: Pinia state management
- **src/views/**: Page-level components
- **src/hooks/**: Vue composables
- **src/services/**: API client and business logic

Key patterns:
- Vue 3 Composition API
- TypeScript for type safety
- Tanstack Query for data fetching
- Monaco Editor for code editing
- TailwindCSS for styling

### Database Schema
Uses PostgreSQL with SQLAlchemy ORM. Key models include:
- Contracts and contract state
- Transactions and receipts
- Validators and consensus data
- Account balances

Migrations managed via Alembic in `backend/database_handler/migration/`.

### Testing Strategy
- **Frontend**: Vitest for unit tests, Selenium for E2E tests
- **Backend**: pytest with pytest-asyncio for async tests
- **Smart Contracts**: Hardhat test suite
- Pre-commit hooks enforce code quality

## Development Tips

1. **LLM Configuration**: Set API keys in `.env` for OpenAI, Anthropic, Google, or XAI providers
2. **Hardhat Integration**: Enable by setting `COMPOSE_PROFILES=hardhat` in `.env`
3. **Debug Mode**: Set `VSCODEDEBUG=true` and `BACKEND_BUILD_TARGET=debug` for debugging
4. **Database Access**: PostgreSQL accessible at `postgresql://postgres:postgres@localhost:5432/genlayer_state`
5. **WebSocket Events**: Frontend connects to `ws://localhost:4000` for real-time updates

## Important Conventions

- Follow existing code patterns and styles in each service
- Use TypeScript strict mode in frontend
- Backend uses async/await patterns throughout
- Database operations should use async SQLAlchemy sessions
- All new features require tests
- Commit messages follow conventional commits format