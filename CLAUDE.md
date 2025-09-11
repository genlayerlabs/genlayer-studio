# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Frontend Development
```bash
cd frontend/
npm run dev          # Start development server (port 5173)
npm run build        # Production build
npm run test         # Run unit tests with Vitest
npm run test:e2e     # End-to-end tests with Mocha/Selenium
npm run lint         # ESLint with auto-fix
npm run format       # Prettier formatting
```

### Backend & Full Stack
```bash
genlayer up          # Start all services via Docker Compose
genlayer init        # Initialize project database
genlayer down        # Stop all services
docker compose up    # Alternative: start services directly
docker compose down -v  # Stop and clear data
```

### Testing
```bash
# Backend tests (Python)
pytest tests/unit/
pytest tests/integration/
pytest tests/e2e/
pytest -xvs tests/unit/test_specific.py  # Run single test file

# Frontend tests
cd frontend && npm run test  # Unit tests
cd frontend && npm run test:e2e  # E2E tests
```

### Database Operations
```bash
# Run from backend container
docker exec -it genlayer-studio-backend-1 bash
alembic upgrade head  # Apply migrations
alembic revision --autogenerate -m "description"  # Create migration
```

## Architecture

GenLayer Studio is a blockchain development sandbox for "Intelligent Contracts" - smart contracts that can interact with LLMs. It uses a microservices architecture with Docker Compose orchestration.

### Service Architecture
- **Frontend** (Vue 3 + TypeScript): SPA on port 8080, uses Pinia stores, TanStack Query, Socket.io for real-time updates
- **Backend** (Python Flask): JSON-RPC API on port 4000, handles consensus, contract execution
- **Database**: PostgreSQL with SQLAlchemy ORM, Alembic migrations
- **WebDriver**: Selenium service for contract execution sandboxing
- **GenVM**: Custom Python VM for executing intelligent contracts with LLM capabilities

### Key Directories
- `frontend/src/stores/`: Pinia state management (simulator, contracts, accounts)
- `frontend/src/components/Simulator/`: Contract execution UI components
- `backend/consensus/`: Custom PoS consensus with LLM validators
- `backend/node/genvm/`: GenLayer Virtual Machine implementation
- `backend/protocol_rpc/`: JSON-RPC endpoint handlers
- `backend/validators/`: LLM provider integrations (OpenAI, Anthropic, etc.)
- `examples/contracts/`: Sample intelligent contracts

### Contract Development
Intelligent contracts are Python classes inheriting from `gl.Contract`:
- Use `@gl.public.view` for read-only methods
- Use `@gl.public.write` for state-changing methods
- Use `@gl.public.llm_query` for non-deterministic LLM queries
- Contracts can make web requests and interact with multiple LLM providers

### Consensus System
- Uses multiple LLM providers as validators in a custom consensus mechanism
- Supports deterministic execution (all validators agree) and non-deterministic (majority vote)
- Validator rotation using VRF (Verifiable Random Function)
- Configurable validator sets in `backend/node/base/leader_election.py`

### Frontend State Management
- **SimulatorStore**: Main store for contract execution and state
- **ContractsStore**: Manages deployed contracts and their code
- **AccountsStore**: Handles user accounts and balances
- All stores persist to localStorage via pinia-plugin-persistedstate

### API Communication
- Primary: JSON-RPC over HTTP (port 4000)
- Real-time: Socket.io for consensus updates
- Frontend uses TanStack Query for caching and state synchronization
- API client: `frontend/src/services/api.ts`

### Environment Configuration
Key environment variables (see `.env.example`):
- `POSTGRES_*`: Database connection
- `PROVIDERS_*`: LLM provider API keys
- `ENABLE_DEBUG_ENDPOINTS`: Development features
- `DEFAULT_VALIDATORS_COUNT`: Consensus participants

### Testing Approach
- Backend: pytest with fixtures in `tests/common/`
- Frontend: Vitest for unit tests, Mocha/Selenium for E2E
- Integration tests use Docker Compose test profiles
- Contract tests in `tests/integration/test_contracts/`

### Code Style
- Python: Black formatter, type hints required
- TypeScript: ESLint + Prettier, strict mode enabled
- Pre-commit hooks enforce formatting (see `.pre-commit-config.yaml`)
- Vue: Composition API with `<script setup>` syntax