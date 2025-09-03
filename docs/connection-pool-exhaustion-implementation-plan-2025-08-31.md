## Database Connection Pool Exhaustion — Root Cause and Implementation Plan

Date: 2025-08-31
Owner: Engineering
Status: Ready to implement

### Summary
- Error observed: SQLAlchemy QueuePool timeouts with defaults (pool_size=5, max_overflow=10)
- Root cause: Two separate SQLAlchemy engines are used. Endpoints use Flask‑SQLAlchemy’s default engine (5/10 pool), while a second Core engine is created with 50/50. The `eth_getBalance` path hits the default 5/10 pool and times out under concurrency.
- Fix: Configure Flask‑SQLAlchemy’s engine pool and remove the duplicate Core engine. Use a single engine everywhere. Optionally reduce Web3 connection fan‑out, add session management helpers, and expose pool metrics.

### Error signature
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00
```

### Where it occurs
Request path for `eth_getBalance` → DB read via AccountsManager using Flask‑SQLAlchemy scoped session.

```12:18:backend/protocol_rpc/endpoints.py
def get_balance(
    accounts_manager: AccountsManager, account_address: str, block_tag: str = "latest"
) -> int:
    if not accounts_manager.is_valid_address(account_address):
        raise InvalidAddressError(
            account_address, f"Invalid address from_address: {account_address}"
        )
    account_balance = accounts_manager.get_account_balance(account_address)
    return account_balance
```

```53:75:backend/database_handler/accounts_manager.py
def get_account(self, account_address: str) -> CurrentState | None:
    account = (
        self.session.query(CurrentState)
        .filter(CurrentState.id == account_address)
        .one_or_none()
    )
    return account
```

The `AccountsManager` instance used by endpoints is created with `sqlalchemy_db.session` (Flask‑SQLAlchemy’s engine):

```68:76:backend/protocol_rpc/server.py
msg_handler = MessageHandler(socketio, config=GlobalConfiguration())
transactions_processor = TransactionsProcessor(sqlalchemy_db.session)
accounts_manager = AccountsManager(sqlalchemy_db.session)
snapshot_manager = SnapshotManager(sqlalchemy_db.session)
```

At the same time a separate Core engine is created with larger pool:

```55:56:backend/protocol_rpc/server.py
engine = create_engine(db_uri, echo=True, pool_size=50, max_overflow=50)
```

Because endpoints use `sqlalchemy_db.session`, they hit the default Flask‑SQLAlchemy pool (5/10), not the 50/50 Core engine. Under concurrency, this exhausts quickly and produces timeouts.

### Implementation plan (Phase 1 — Required)
1) Unify on a single engine (Flask‑SQLAlchemy) and configure its pool

- In `backend/protocol_rpc/server.py`:
  - Remove the standalone Core `create_engine(...)` line.
  - Configure Flask‑SQLAlchemy engine options.
  - After `init_app`, get `engine = sqlalchemy_db.engine` and use it for any explicit `Session(...)` creation.

Edits to apply:

```python
# backend/protocol_rpc/server.py (within create_app)

# DELETE this line:
# engine = create_engine(db_uri, echo=True, pool_size=50, max_overflow=50)

# Flask
app = Flask("jsonrpc_api")
app.config["SQLALCHEMY_DATABASE_URI"] = db_uri
app.config["SQLALCHEMY_ECHO"] = True
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 20,
    "max_overflow": 30,
    "pool_pre_ping": True,
    "pool_recycle": 3600,
    "pool_timeout": 30,
    "echo_pool": True,  # temporary for verifying pool behavior
}
sqlalchemy_db.init_app(app)

# Use the Flask‑SQLAlchemy engine everywhere
with app.app_context():
    engine = sqlalchemy_db.engine

def create_session():
    return Session(engine, expire_on_commit=False)
```

Notes:
- This eliminates the 5/10 pool by configuring Flask‑SQLAlchemy’s engine directly and ensures both request handlers and background tasks share one pool.
- Keep `teardown_appcontext` logic to commit/rollback/remove the request‑scoped session.

2) Validate runtime uses the configured pool
- With `echo_pool=True`, check logs for pool creation reflecting 20/30 and checked‑out counts during load.
- Ensure there is no remaining `create_engine(...)` call that would create a second engine.

### Implementation plan (Phase 2 — Recommended)
3) Avoid binding long‑lived managers to request‑scoped sessions
- Prefer constructing managers per request with the session supplied by the endpoint, or pass a factory.
- For background/async loops, use `create_session()` to open short‑lived sessions and explicitly close them. A simple context manager helps:

```python
# backend/database_handler/session_manager.py
from contextlib import contextmanager

@contextmanager
def managed_session(open_session):
    session = open_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

4) Reduce Web3 HTTP connection fan‑out (prevents RPC bottlenecks)
- Implement a simple Web3 connection holder to reuse the same HTTP connection in `TransactionsProcessor` and `ConsensusService`:

```python
# backend/rollup/web3_pool.py
import os
from web3 import Web3

class Web3ConnectionPool:
    _web3 = None

    @classmethod
    def get(cls):
        if cls._web3 is None:
            hardhat_url = f"{os.environ.get('HARDHAT_URL')}:{os.environ.get('HARDHAT_PORT')}"
            cls._web3 = Web3(Web3.HTTPProvider(hardhat_url))
        return cls._web3
```

Refactor constructors to use it:

```python
# backend/database_handler/transactions_processor.py
from backend.rollup.web3_pool import Web3ConnectionPool
...
self.web3 = Web3ConnectionPool.get()

# backend/rollup/consensus_service.py
from backend.rollup.web3_pool import Web3ConnectionPool
...
self.web3 = Web3ConnectionPool.get()
```

5) Add a monitoring endpoint for DB pool status (dev only)

```python
# backend/protocol_rpc/endpoints.py (dev helper)
from datetime import datetime
from flask_jsonrpc import JSONRPC

def dev_get_pool_status(jsonrpc: JSONRPC, sqlalchemy_db) -> dict:
    engine = sqlalchemy_db.engine
    return {
        "timestamp": datetime.now().isoformat(),
        "pool": {
            "size": engine.pool.size(),
            "checked_out": engine.pool.checkedout(),
            "overflow": engine.pool.overflow(),
            "max_allowed": engine.pool.size() + engine.pool._max_overflow,
        },
    }
```

6) Hardhat server headroom (optional but helpful for load tests)
- Increase `httpServer.maxConnections` in `hardhat/hardhat.config.js` to avoid RPC back‑pressure during tests.

```javascript
httpServer: {
  maxConnections: 200
}
```

### Test plan
1) Unit/sanity
- Start backend, confirm pool logs show 20/30 and not 5/10.
- Call `eth_getBalance` concurrently (e.g., 50 concurrent for 60s). No pool timeouts.

2) Integration
- Exercise endpoints that use both `sqlalchemy_db.session` and background loops (validators/consensus). Confirm no connection starvation.

3) Monitoring
- Observe `engine.pool.checkedout()`/`overflow()` under load. They should stabilize below limits.

### Rollout
1. Implement Phase 1 changes in `server.py`, deploy to staging.
2. Validate under load; ensure no timeouts and DB metrics look healthy.
3. Implement Phase 2 improvements iteratively (Web3 pooling, session manager, dev monitoring) and verify gains.

### Risks and mitigations
- Risk: lingering code paths still creating a second engine.
  - Mitigation: grep for `create_engine(` and remove/replace with `sqlalchemy_db.engine`.
- Risk: background tasks hold sessions too long.
  - Mitigation: adopt `managed_session` for explicit open/close around work units.
- Risk: Web3 connection contention.
  - Mitigation: singleton Web3 holder and increase Hardhat maxConnections for testing.

### Acceptance criteria
- No `QueuePool limit of size 5 overflow 10` timeouts during 5‑minute load at ≥30 concurrent requests.
- Pool metrics reflect configured limits (20/30) and remain below 80% utilization under expected load.
- Background loops operate normally; no connection leaks observed in Postgres `pg_stat_activity`.


