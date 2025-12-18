## GenLayer Studio Backend – Agent-Friendly Guide

### Purpose
A compact, reusable briefing for agents working on the backend. It explains how the FastAPI app is assembled, how JSON‑RPC requests flow, how consensus and workers process transactions, and where to extend or debug.

### High-Level Architecture
- **FastAPI entrypoint**: `backend/protocol_rpc/fastapi_server.py`
- **App assembly & lifecycle**: `backend/protocol_rpc/app_lifespan.py`
- **RPC routing**: `backend/protocol_rpc/fastapi_rpc_router.py` → `backend/protocol_rpc/rpc_endpoint_manager.py`
- **RPC methods**: registered in `backend/protocol_rpc/rpc_methods.py`, implemented in `backend/protocol_rpc/endpoints.py`
- **Dependencies (DI)**: `backend/protocol_rpc/dependencies.py`
- **WebSocket/broadcast**: `backend/protocol_rpc/websocket.py`, `backend/protocol_rpc/broadcast.py`, `backend/protocol_rpc/redis_subscriber.py`
- **Consensus/background**: `backend/consensus/base.py`, `backend/consensus/worker.py`, `backend/consensus/worker_service.py`
- **Execution engine**: `backend/node/base.py`, `backend/node/genvm/base.py`
- **Database/services**: `backend/database_handler/*` (models, sessions, processors, registries)

### App Startup & State
- `fastapi_server.lifespan()` loads env and delegates to `rpc_app_lifespan(app, settings)`.
- `rpc_app_lifespan` initializes:
  - SQLAlchemy via `DatabaseSessionManager` and verifies DB readiness/migrations.
  - `Broadcast` (in‑memory pub/sub) and `MessageHandler` for structured events.
  - `ConsensusService` (Web3/rollup abstraction) and `TransactionParser` (RLP/EIP‑2718 decode & validation).
  - `validators.Manager` and registry; optional bootstrap via `VALIDATORS_CONFIG_JSON`.
  - `ConsensusAlgorithm` instance (for RPC endpoint use only, no background loops).
  - **`RedisEventSubscriber`** (REQUIRED) - connects to Redis and forwards worker events to `Broadcast`.
  - RPC stack: builds `RPCEndpointManager`, imports `rpc_methods.py` (decorator registration), creates `FastAPIRPCRouter`.
- Applies all to `app.state` for DI (e.g., `broadcast`, `msg_handler`, `validators_manager/registry`, `consensus_service`, `transactions_parser`, `sqlalchemy_db`, `db_manager`, `rpc_router`).
- **No consensus background loops** - all transaction processing handled by separate worker services.

### Dependency Injection (DI)
- Resolvers in `dependencies.py` expose request‑scoped `Session` and services:
  - Core: `get_db_session`, `get_sqlalchemy_db`, `get_message_handler`, `websocket_broadcast`.
  - Business: `get_accounts_manager`, `get_transactions_processor`, `get_snapshot_manager`, `get_llm_provider_registry`, `get_validators_manager`, `get_validators_registry`, `get_consensus`, `get_consensus_service`, `get_transactions_parser`.
- Handlers declare these via FastAPI `Depends` and receive them at invocation time.

### JSON‑RPC Routing Pipeline
- HTTP endpoint: `POST /api` in `fastapi_server.py`.
- Router: `FastAPIRPCRouter.handle_http_request`
  - Parses single or batch JSON; validates `JSONRPCRequest` (Pydantic), handles `ping`, enforces batch size, maps errors to JSON‑RPC 2.0.
  - Delegates to `RPCEndpointManager.invoke(request, fastapi_request)`.
- Endpoint manager: binds params to handler signature, solves FastAPI dependencies (or fast‑path when none), then calls the implementation and returns a `JSONRPCResponse`.

### RPC Surface (Categories)
Implemented in `endpoints.py`, registered in `rpc_methods.py` via `@rpc.method(...)`:
- **Simulator (`sim_*`)**: DB reset, fund accounts, LLM provider CRUD, validator configuration, snapshot utilities, simulated calls.
- **GenLayer (`gen_*`)**: contract schema/code helpers, `gen_call` (readonly run through VM).
- **Ethereum‑compat**: `eth_getBalance`, `eth_getTransactionByHash`, `eth_call` (readonly execution), `eth_sendRawTransaction` (persist & queue), `eth_getTransactionCount`, chain/net info, block number.

### Admin Endpoints & Contract Upgrade
Admin endpoints are protected by `@require_admin_access` decorator with access modes:
- **Local dev** (no env vars): Open access
- **With `ADMIN_API_KEY` set**: Requires matching `admin_key` parameter (works in all modes including hosted)
- **Hosted cloud** (`VITE_IS_HOSTED=true`) without `ADMIN_API_KEY`: Blocked entirely

**Contract Upgrade (`sim_upgradeContractCode`)**:
Allows in-place upgrade of deployed contract code without losing state. Uses transaction type=3 (UPGRADE_CONTRACT).

**Access control**:
- **Local mode**: Open access (no auth required)
- **Hosted/self-hosted**: Requires `admin_key` (any contract) OR `signature` from deployer (own contracts only)

```python
# RPC signature: sim_upgradeContractCode(contract_address, new_code, signature?, admin_key?)

# Local mode (no auth)
result = rpc.call("sim_upgradeContractCode", [contract_address, new_code])

# Hosted mode with admin key (any contract)
result = rpc.call("sim_upgradeContractCode", [contract_address, new_code, None, admin_key])

# Hosted mode with deployer signature (own contracts)
# Signature scheme: sign(keccak256(contract_address + keccak256(new_code)))
result = rpc.call("sim_upgradeContractCode", [contract_address, new_code, signature])

# Returns: {"transaction_hash": "0x...", "message": "..."}
# Poll for completion
receipt = rpc.call("eth_getTransactionByHash", [result["transaction_hash"]])
# receipt["status"] == "FINALIZED" means success
```

**How it works**:
1. RPC validates auth (admin_key or deployer signature in hosted mode)
2. Creates upgrade transaction (type=3) and returns immediately with tx hash
3. Worker claims transaction naturally (queued behind any pending txs for that contract)
4. Worker detects type=3, skips consensus, updates contract code directly
5. Worker marks tx as FINALIZED and sends WebSocket notification

**Frontend**: "Upgrade code" button in ContractInfo.vue signs the request with user's private key and upgrades using current editor code.

**CLI script**: `scripts/upgrade_contract.py` supports `--private-key` (deployer signature) and `--admin-key` options.

### Execution Engine (Node/GenVM)
- `node/base.py` `Node` orchestrates deploy and run:
  - Decides action (deploy vs run) from tx type and decodes code/calldata (base64).
  - Creates `IGenVM` host (`genvm/base.py`), uses a `StateProxy` bridging contract/account storage to DB snapshots.
  - Readonly mode for `*_call` prevents writes.
- `genvm/base.py` `GenVMHost` manages the external GenVM host process and returns `ExecutionResult` (result bytes, logs, stdout/stderr, nondet disagreement, pending spawned txs, processing time).

### Database & Services
- Sessions: `database_handler/session_factory.DatabaseSessionManager` (engine + `SessionLocal`), DI supplies per‑request `Session`.
- Models: `database_handler/models.py` includes `CurrentState`, `Transactions` with `TransactionStatus`, `Validators`, `LLMProviderDBModel`, `Snapshot`, etc.
- Services:
  - `AccountsManager`: address validation/CRUD for `CurrentState`.
  - `TransactionsProcessor`: persist/index/query transactions and provide filters.
  - `SnapshotManager`: snapshot lifecycle.
  - `LLMProviderRegistry`: provider/model catalog used by validators.

### Consensus & Workers

**Distributed worker architecture** (always enabled):
- Separate worker service (`consensus/worker_service.py` + `consensus/worker.py`) handles all consensus processing.
- Workers claim transactions from DB, execute via Node/GenVM, validate, and update state.
- Workers publish events to Redis channels (`consensus:events`, `transaction:events`, `general:events`).
- RPC instances run `RedisEventSubscriber` to consume Redis events and forward to WebSocket clients.
- Fully decoupled: RPC handles API requests, workers handle consensus (no shared event loop).

**Redis is REQUIRED:**
- ✅ Always required for worker→RPC event communication
- ✅ Workers fail-fast on startup if `REDIS_URL` is not set
- ✅ RPC instances fail-fast on startup if `REDIS_URL` is not set
- No single-process mode - architecture is always distributed

**Scaling:**
- `CONSENSUS_WORKERS` (default: 1) - Number of worker replicas (minimum 1)
- `JSONRPC_REPLICAS` (default: 1) - Number of RPC instances (minimum 1)
- Workers coordinate via database locking (no shared state)
- RPC instances are stateless (load-balanced by Traefik)

**Architecture diagram:**

```
┌──────────────────┐
│ Worker 1         │──┐
│ ConsensusWorker  │  │
└──────────────────┘  │
┌──────────────────┐  │      ┌────────────────────┐
│ Worker 2         │──┼─────→│ Redis pub/sub      │
│ ConsensusWorker  │  │      │ (3 channels)       │
└──────────────────┘  │      └─────────┬──────────┘
┌──────────────────┐  │                │
│ Worker N         │──┘                │
│ ConsensusWorker  │                   │
└──────────────────┘                   │
    (Publish events)                   │
                                       ↓
           ┌────────────────────────────────────────┐
           │                                        │
    ┌──────▼──────────┐  ┌────────────────┐  ┌────▼──────────┐
    │ RPC 1           │  │ RPC 2          │  │ RPC M         │
    │ RedisSubscriber │  │ RedisSubscriber│  │ RedisSubscriber│
    │      ↓          │  │      ↓         │  │      ↓        │
    │  Broadcast      │  │  Broadcast     │  │  Broadcast    │
    │      ↓          │  │      ↓         │  │      ↓        │
    │  WebSockets     │  │  WebSockets    │  │  WebSockets   │
    └─────────────────┘  └────────────────┘  └───────────────┘
         (Load balanced by Traefik)

Database: PostgreSQL (shared by all workers and RPC instances)
```

### Events, Broadcast, and WebSockets
- **Event flow architecture:**
  1. Workers publish to Redis channels (`RedisWorkerMessageHandler`)
  2. RPC instances consume from Redis (`RedisEventSubscriber`)
  3. Redis events forwarded to in-memory `Broadcast` channels
  4. WebSocket clients subscribe to `Broadcast` channels and receive events

- **Broadcast** (`broadcast.py`): Lightweight in-memory pub/sub within each RPC instance
  - Maps Redis channels to local WebSocket subscriptions
  - Channels: transaction hash, `transactions`, `consensus`, `general`

- **WebSockets** (`/ws` and `/socket.io/`): Real-time event delivery to clients
  - Clients send `{event: "subscribe", data: "transaction_hash"}` to subscribe
  - Server emits `subscribed`/`unsubscribed` confirmations
  - Events forwarded from `Broadcast` to subscribed clients
  - Handled by `websocket.py` with automatic cleanup on disconnect

### Readiness & Health
- `health_router` and `/ready` ensure app state is fully initialized (DB, router, broadcast, consensus, etc.) before accepting traffic.

### Request Flows (Essential)
- Readonly call (`eth_call` / `gen_call` / `sim_call`):
  1) Client → `POST /api` JSON‑RPC.
  2) Router → Endpoint manager → Handler with DI (fresh `Session`).
  3) Build `Node` + readonly `GenVMHost`, execute calldata.
  4) Return hex‑encoded result/logs, no state mutation.
- State‑changing (`eth_sendRawTransaction`):
  1) Decode/validate signed tx via `TransactionParser` (type, from/to, signature).
  2) Persist to `Transactions` via `TransactionsProcessor` (status → pending) and emit event.
  3) `ConsensusAlgorithm` (or worker) claims and executes; validators validate; DB updated to success/failure; events emitted to rooms.
  4) WebSocket clients in `transactions` or `transaction:{hash}` receive lifecycle updates.

### Configuration & Running
- Environment:
  - DB: `DBUSER`, `DBPASSWORD`, `DBHOST`, `DBPORT`, `DBNAME`.
  - RPC: `LOG_LEVEL`, `RPCPORT`.
  - **Redis (REQUIRED)**: `REDIS_URL` (example: `redis://redis:6379/0`) - Required for worker→RPC communication.
  - Validators/LLM: `VALIDATORS_CONFIG_JSON`.
  - **Scaling**:
    - `CONSENSUS_WORKERS` (default: 1) - Number of worker replicas (minimum: 1).
    - `JSONRPC_REPLICAS` (default: 1) - Number of RPC instances (minimum: 1).
  - Workers: `WORKER_ID`, `WORKER_POLL_INTERVAL`, `TRANSACTION_TIMEOUT_MINUTES`.
- Commands (see repo guidelines):
  - Full stack: `cp .env.example .env && docker compose up` (add `-d` for background).
  - Backend services only: `docker compose up jsonrpc webrequest ollama database-migration postgres`.
  - Frontend dev: `cd frontend && npm install && npm run dev`.
  - Python tests: `pytest` (use `-k`/`-m` for filters).

### Extending the Backend (Typical Workflow)
- Add a new RPC:
  1) Implement behavior in `backend/protocol_rpc/endpoints.py` (pure function; accept DI‑supplied args).
  2) Register in `backend/protocol_rpc/rpc_methods.py` with `@rpc.method("your_method")` and declare `Depends(...)` for DI.
  3) If new services are needed, expose them via `dependencies.py` and wire into `app_lifespan` state.
  4) Add tests (PyTest or frontend RPC tests), and update docs if needed.
- Add validator/provider logic:
  - Extend `validators.Manager`/registry; expose admin endpoints as needed in `endpoints.py`.
- Modify consensus behavior:
  - Update `ConsensusAlgorithm` loops and related services; ensure events are emitted and DB consistency maintained.

### Observability & Errors
- Structured events via `MessageHandler` (`LogEvent` types) for RPC and consensus.
- JSON‑RPC error mapping for transport (`ParseError`, `InvalidRequest`), method presence (`MethodNotFound`), and business errors (e.g., invalid address/tx).
- WebSocket: defensively handles invalid JSON and disconnects; DI rejects sockets when broadcast not initialized.

### Key Invariants
- Each RPC invocation gets a fresh SQLAlchemy `Session` (no cross‑request leakage).
- Readonly calls must not mutate state (enforced by readonly GenVM run path).
- Workers and main RPC coordinate via DB rows and event channels; claiming is idempotent and timeouts recover stuck txs.

### File Map (Quick Reference)
- FastAPI & lifecycle: `protocol_rpc/fastapi_server.py`, `protocol_rpc/app_lifespan.py`
- Routing: `protocol_rpc/fastapi_rpc_router.py`, `protocol_rpc/rpc_endpoint_manager.py`, `protocol_rpc/rpc_decorators.py`
- RPC API: `protocol_rpc/rpc_methods.py`, `protocol_rpc/endpoints.py`
- DI: `protocol_rpc/dependencies.py`
- Events/WebSocket: `protocol_rpc/websocket.py`, `protocol_rpc/broadcast.py`, `protocol_rpc/redis_subscriber.py`
- Consensus/Workers: `consensus/base.py`, `consensus/worker.py`, `consensus/worker_service.py`
- Node/VM: `node/base.py`, `node/genvm/base.py`
- DB/Services: `database_handler/models.py`, `session_factory.py`, `transactions_processor.py`, `accounts_manager.py`, `snapshot_manager.py`, `llm_providers.py`

### Troubleshooting
- `/ready` failing: confirm `rpc_router`, DB connectivity, broadcast, validators/consensus started in `app.state`.
- Stuck transactions: check `Transactions` status, worker logs, Redis connectivity, and validator registry health.
- `eth_call` issues: validate calldata encoding, readonly path, and StateProxy access; inspect GenVM logs in `ExecutionResult`.
- Event delivery: ensure Redis subscriber connected; check Redis logs and channel subscriptions.
- **Startup failures**:
  - "REDIS_URL environment variable is required" → Set `REDIS_URL` in `.env` (e.g., `redis://redis:6379/0`).
  - "Failed to connect to Redis" → Verify Redis service is running: `docker compose ps redis` and check health.
  - No transactions processing → Check worker logs: `docker compose logs consensus-worker`.

### Security Notes
- CORS is permissive by default; constrain in production.
- Validate and sanitize new endpoints; prefer DI and service boundaries.
- Redis pub/sub channels are internal; ensure Redis is not exposed publicly.

---
This guide summarizes the backend flow so agents can quickly navigate, extend, and debug the system without re‑deriving the architecture.

