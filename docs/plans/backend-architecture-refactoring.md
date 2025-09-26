# Backend RPC Dependency Migration Plan

**Date:** 2025-09-18
**Status:** Ready for Implementation
**Author:** Backend Platform Team

## Goals
- Replace the custom RPC dependency container with FastAPI's native dependency injection.
- Eliminate handcrafted signature inspection and caching in favour of FastAPI-managed lifecycles.
- Preserve existing RPC method behaviour and public JSON-RPC contracts while simplifying the execution path.
- Enable unit/integration tests to use FastAPI's dependency override utilities for fine-grained validation.

## What We Have Today
- `rpc_endpoint_manager.py` introspects function signatures, detects dependency names, and hand-wires objects produced by `dependencies.py` factories.
- `dependencies.py` maintains a manual cache keyed on strings (e.g. `"accounts_manager"`, `"transactions_processor"`).
- RPC functions rely on parameter names matching cache keys; adding a new dependency requires editing both the endpoint and the dependency registry.
- FastAPI only provides the raw request body; everything else is built in our dispatcher.

## Migration Strategy Overview
1. **Adopt FastAPI Dependencies** – Express every shared component as a `Depends` function with the appropriate scope (session, managers, services).
2. **Update RPC Handlers** – Refactor each RPC function signature to accept dependencies via `Depends(...)`, not positional parameters or implicit names.
3. **Reduce Endpoint Manager Responsibilities** – Convert the dispatcher into a thin adapter that: (a) maps JSON-RPC params onto handler arguments; (b) defers all dependency resolution to FastAPI by calling handlers using FastAPI's dependency system (via sub-app or `Depends` injection).
4. **Leverage Dependency Overrides** – Make tests override dependency providers using FastAPI's built-in `app.dependency_overrides` to drop the need for custom caching/binding hacks.
5. **Clean Up Legacy Layers** – Remove the bespoke dependency provider and cached factories once endpoints are converted.

## Phase-by-Phase Plan

### Phase 1 – Inventory & Abstractions (0.5 day)
- Map every dependency exported from `dependencies.py` and the endpoints that consume it.
- Categorise dependencies by scope:
  - *Request-scoped*: database session, per-request managers.
  - *Application-scoped*: message handler, consensus, validators registry, config.
- Document required teardown semantics (e.g. commit/rollback session).

**Deliverables**: Dependency matrix, updated architectural notes, validation that each dependency has an equivalent FastAPI-ready factory.

### Phase 2 – Define FastAPI Dependency Functions (1 day)
- Rebuild `dependencies.py` into a pure FastAPI dependency module:
  - Implement `get_db_session()` as a generator yielding the SQLAlchemy session and handling commit/rollback/close.
  - Introduce thin wrappers (`get_accounts_manager`, `get_transactions_processor`, etc.) that accept dependencies via `Depends`.
  - Replace manual caching with FastAPI's default behaviour (dependencies executed once per request unless marked `use_cache=False`).
- Provide aliases for application-scoped singletons, e.g. `get_message_handler(app_state=Depends(get_app_state))`.

**Deliverables**: New dependency functions returning concrete types, unit tests ensuring correct scoping/cleanup.

### Phase 3 – Reshape RPC Handlers (2 days)
- Convert each function registered in `rpc_methods.py` to accept dependencies explicitly:
  ```python
  @rpc.method("sim_fundAccount")
  async def fund_account(
      account_address: str,
      amount: int,
      accounts_manager: AccountsManager = Depends(get_accounts_manager),
      session: Session = Depends(get_db_session),
  ) -> str:
      ...
  ```
- Remove positional dependency arguments from implementations in `endpoints.py`; update internal calls to use typed parameters.
- Ensure async/sync handlers keep their semantics.

**Deliverables**: Updated endpoints with FastAPI DI, typing passes, pylint/mypy adjustments if required.

### Phase 4 – Simplify the Dispatcher (1 day)
- Replace `RPCEndpointManager` with a minimal adapter that:
  - Accepts a FastAPI `Depends` callable to build a per-request dependency scope (`fastapi.Depends` + `fastapi.params.Depends` utilities or `fastapi.routing.APIRoute.dependant`).
  - Invokes the RPC handler using FastAPI's dependency resolution via `fastapi.routing.run_endpoint_function` or by mounting a hidden APIRouter and calling `app.dependency_overrides`.
  - Handles JSON-RPC error shaping (method not found, invalid params) but stays out of dependency management entirely.
- Delete manual caching, marker classes, and signature-walking logic.

**Deliverables**: Lightweight dispatcher, comprehensive tests covering positional/named JSON-RPC params and FastAPI-managed dependencies.

### Phase 5 – Testing & Overrides (0.5 day)
- Update unit/integration tests to override dependencies using `app.dependency_overrides` or `TestClient` fixtures.
- Backfill tests for session lifecycle (commit/rollback paths).
- Validate that overriding dependencies no longer requires mutating global app state.

**Deliverables**: Adjusted test suites, new fixtures demonstrating dependency overrides, CI green.

### Phase 6 – Cleanup & Documentation (0.5 day)
- Remove obsolete modules (`dependencies` cache helpers, unused managers) once tests pass.
- Update contributor docs explaining how to declare new RPC dependencies using FastAPI patterns.
- Communicate rollout steps to other teams (feature toggles removed, new testing instructions).

**Deliverables**: Clean repo, docs refreshed, release notes summarising the change.

## Automation & Tooling Opportunities
- Use FastAPI’s dependency introspection (`app.router.routes`, `route.dependant`) to automatically register dependencies for RPC handlers, reducing manual lists.
- Generate dependency graphs for debugging via `fastapi.dependencies.utils.get_flat_dependant()` during testing.
- Exploit FastAPI’s `use_cache` flag for dependencies that must run multiple times per request.

## Risk & Mitigation
| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Broken RPC endpoints due to signature drift | HIGH | MEDIUM | Migrate endpoint groups incrementally, add regression tests per group |
| Session lifecycle regressions | HIGH | LOW | Reuse existing integration tests, add explicit session teardown tests |
| Increased latency from dependency overhead | MEDIUM | LOW | Benchmark before/after, leverage dependency caching |
| Coordination with frontend timelines | MEDIUM | LOW | Maintain JSON-RPC schema compatibility, communicate rollout window |

## Success Criteria
1. All RPC handlers rely solely on FastAPI `Depends` for shared services.
2. `rpc_endpoint_manager.py` no longer performs reflection-based dependency resolution.
3. Tests can override any dependency using FastAPI's native override hooks.
4. Production metrics show no regression in RPC latency or error rates for a full 24-hour window.

## Rollout Plan
- Implement phases sequentially; after Phase 3, run full integration suite before touching dispatcher logic.
- Deploy to staging with feature flag to throttle traffic if necessary (rollback is re-importing the old manager module).
- Monitor RPC success/error rates closely during the first production canary.

## Rollback Strategy
- Keep the previous dispatcher and dependency modules in Git history; reintroduce them via revert commit if needed.
- Maintain a deployment artefact for the last pre-migration release so we can redeploy quickly if issues surface.

---
This plan keeps the JSON-RPC surface stable while shifting dependency management to FastAPI’s native mechanisms, reducing bespoke code and aligning the backend with established framework patterns.
