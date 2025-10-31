# Backend Transaction Hash Mismatch – Architecture Report

## Incident Summary
- `eth_sendRawTransaction` intermittently returns a transaction hash that differs from what the Studio database stores for the same request.
- Logs confirm the RPC handler emits one hash (e.g. `0x3b4d…`) while consensus and persistence routines operate on another (e.g. `0xdd6e…`).
- The divergence only happens when Hardhat is offline, yet it is not caused by the Ethereum layer; it originates in our backend session architecture.

## Root Cause
1. **Process-wide SQLAlchemy Session**
   `TransactionsProcessor`, `AccountsManager`, `SnapshotManager`, and others are instantiated once in `backend/protocol_rpc/server.py` with a single `sqlalchemy_db.session`. Every RPC request reuses that session.

2. **Concurrent Request Collision**
   When two `eth_sendRawTransaction` calls overlap, they share the same session identity map. Request A inserts a new `Transactions` row and sets its `.hash`. Before the session flushes, request B mutates the same identity map; A returns the hash it computed, but by commit time the underlying ORM object reflects B’s update. The database therefore persists B’s hash while A’s caller remembers its own response.

3. **Mixed Session Lifetimes Elsewhere**
   Validators and consensus routines construct independent `SessionLocal()` instances, further blurring transaction boundaries and increasing the chance of cross-talk. There is no clear ownership of connection lifecycle or isolation level.

4. **Opaque Dependency Injection**
   The current RPC registration pipeline (`endpoint_generator → fastapi_endpoint_generator → endpoints`) uses partially applied functions to hide dependencies. It is hard to detect at code review that components are sharing a mutable session, so the race went unnoticed.

## Why Refactor Now
- **Correctness:** As long as the session is shared, we cannot guarantee that the hash returned to clients matches the persisted record. Similar anomalies could appear in balances, validator state, or appeal handling.
- **Scalability:** Under load, shared sessions plus fixed-size connection pools risk deadlocks or starvation. Each refactor plan (see `docs/plans/backend-architecture-refactoring.md` and `docs/plans/phase1-database-session-management.md`) already highlights session management as the highest priority.
- **Maintainability:** The mixture of lifecycle patterns (request-scoped, app-scoped, ad-hoc) makes the codebase difficult to reason about and test. Adding new endpoints compounds the problem.

## Recommended Remediation (Phase 1)
1. **Introduce `DatabaseSessionManager`:** Create a unified session factory that hands out request-scoped sessions (`get_db()` / `session_scope()`) and manages pool settings per worker, as outlined in the Phase 1 plan.
2. **Per-Request Instances:** Instantiate `TransactionsProcessor`, `AccountsManager`, etc., per request using the fresh session instead of storing them globally.
3. **Validator/Consensus Alignment:** Refactor validator managers and background workers to obtain sessions through the same manager so that all writes obey consistent transaction boundaries.
4. **Monitoring & Tests:** Add pool metrics and concurrency tests to detect regressions.

## Outcome Expectation
Implementing Phase 1 eliminates the shared-session race, restores hash consistency, and lays the groundwork for the subsequent architectural cleanup (simplified RPC registration, decoupled messaging). This change directly addresses the customer-visible bug and reduces overall risk in the backend.
