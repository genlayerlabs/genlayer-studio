# Backend Architecture Refactoring Plan

**Date:** 2025-09-15  
**Status:** Proposed  
**Author:** System Architecture Analysis  

## Executive Summary

This plan addresses critical issues in the current FastAPI backend architecture that are causing endpoint execution problems. The refactoring focuses on simplifying database session management, streamlining RPC handling, and resolving circular dependencies.

## Current Issues Analysis

### 1. Database Session Management Complexity
- **Problem:** Multiple session creation patterns (SessionLocal, managed_session, get_db)
- **Impact:** Risk of session leaks, connection pool exhaustion, transaction isolation issues
- **Location:** `backend/protocol_rpc/fastapi_server.py:176` (validators_manager session)

### 2. Circular Dependencies in App State
- **Problem:** Components initialized in lifespan depend on each other
- **Impact:** Difficult to test, potential initialization race conditions
- **Location:** `backend/protocol_rpc/fastapi_server.py:150-194`

### 3. Complex RPC Handler Registration
- **Problem:** Three-layer abstraction with partial functions for dependency injection
- **Impact:** Hard to debug, complex parameter filling logic
- **Location:** `endpoints.py` → `fastapi_endpoint_generator.py` → `fastapi_rpc_handler.py`

### 4. WebSocket/MessageHandler Integration
- **Problem:** Tight coupling between MessageHandler and ConnectionManager
- **Impact:** Mixed sync/async operations, unclear separation of concerns
- **Location:** `backend/protocol_rpc/message_handler/fastapi_handler.py`

### 5. Validator Manager Session Handling
- **Problem:** Creates separate SessionLocal() with different lifecycle
- **Impact:** Transaction isolation issues, potential data inconsistencies
- **Location:** `backend/protocol_rpc/fastapi_server.py:176`

## Refactoring Phases

### Phase 1: Database Session Management (Priority: HIGH)

#### 1.1 Create Unified Session Management
```python
# New: backend/database_handler/session_factory.py
class SessionManager:
    def __init__(self, engine):
        self.SessionLocal = sessionmaker(bind=engine)
        self.async_session = async_sessionmaker(engine)
    
    def get_session(self) -> Session:
        """Dependency for FastAPI routes"""
        return Depends(self._get_db)
    
    async def _get_db(self):
        async with self.async_session() as session:
            yield session
```

#### 1.2 Fix Validator Session Handling
- Use dependency injection for validator sessions
- Share session pool with other components
- Implement proper transaction boundaries

**Estimated Effort:** 2-3 days  
**Risk:** Medium - requires testing all database operations

### Phase 2: Simplify RPC Handler (Priority: HIGH)

#### 2.1 Merge RPC Layers
- Combine `fastapi_endpoint_generator.py` and `fastapi_rpc_handler.py`
- Create single `RPCEndpointManager` class
- Remove partial function usage

#### 2.2 Simplify Endpoint Registration
```python
# New approach using decorators
@rpc_method("eth_getBalance")
async def get_balance(
    account_address: str,
    accounts_manager: AccountsManager = Depends(),
    db: Session = Depends(get_db)
):
    return accounts_manager.get_account_balance(account_address)
```

**Estimated Effort:** 3-4 days  
**Risk:** High - affects all RPC endpoints

### Phase 3: Component Initialization (Priority: MEDIUM)

#### 3.1 Implement Dependency Injection Container
```python
# New: backend/core/container.py
class ServiceContainer:
    def __init__(self):
        self._services = {}
        self._factories = {}
    
    def register_factory(self, name: str, factory: Callable):
        self._factories[name] = factory
    
    def get(self, name: str):
        if name not in self._services:
            self._services[name] = self._factories[name]()
        return self._services[name]
```

#### 3.2 Refactor Lifespan Management
- Separate initialization from startup
- Clean shutdown procedures
- Better error handling

**Estimated Effort:** 2-3 days  
**Risk:** Medium - requires careful testing of startup/shutdown

### Phase 4: Event System Refactoring (Priority: LOW)

#### 4.1 Decouple MessageHandler
```python
# New: backend/events/event_bus.py
class EventBus:
    async def emit(self, event: str, data: dict):
        # Decoupled event emission
        pass
    
    async def subscribe(self, event: str, handler: Callable):
        # Event subscription
        pass
```

#### 4.2 Improve WebSocket Handling
- Better connection lifecycle management
- Proper error handling and reconnection
- Improved room management

**Estimated Effort:** 2 days  
**Risk:** Low - can be done incrementally

### Phase 5: Production Optimizations (Priority: MEDIUM)

#### 5.1 Connection Pooling Improvements
```python
# Improved configuration
engine = create_engine(
    db_uri,
    pool_size=20,  # Based on worker count
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_timeout=30,
    connect_args={
        "connect_timeout": 10,
        "options": "-c statement_timeout=30000"
    }
)
```

#### 5.2 Add Monitoring
- Connection pool metrics
- Endpoint performance tracking
- Error rate monitoring

**Estimated Effort:** 1-2 days  
**Risk:** Low - additive changes only

## Implementation Strategy

### Quick Wins (Week 1)
1. Fix validator session handling
2. Add connection pool monitoring
3. Improve error logging

### Core Refactoring (Weeks 2-3)
1. Implement unified session management
2. Simplify RPC handler
3. Refactor component initialization

### Polish & Testing (Week 4)
1. Event system improvements
2. Production optimizations
3. Comprehensive testing

## Success Metrics

1. **Connection Pool Health**
   - No connection leaks
   - Pool utilization < 80%
   - Zero timeout errors

2. **Endpoint Performance**
   - 95th percentile latency < 200ms
   - Error rate < 0.1%
   - Successful request rate > 99.9%

3. **Code Quality**
   - Reduced code complexity by 40%
   - Eliminated circular dependencies
   - Improved test coverage to > 80%

## Rollback Plan

Each phase can be rolled back independently:
1. Keep existing code in parallel during migration
2. Feature flags for new implementations
3. Gradual rollout with monitoring

## Dependencies

- No external library additions required
- Existing FastAPI, SQLAlchemy versions sufficient
- Backward compatibility maintained for API clients

## Risks and Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| Session management bugs | HIGH | MEDIUM | Extensive testing, gradual rollout |
| RPC endpoint breakage | HIGH | LOW | Comprehensive test suite, staging environment |
| Performance regression | MEDIUM | LOW | Performance benchmarks, monitoring |
| Validator synchronization issues | HIGH | MEDIUM | Careful transaction boundary design |

## Conclusion

This refactoring plan addresses the root causes of endpoint execution issues by:
1. Ensuring consistent database session management
2. Simplifying the RPC execution flow
3. Removing complex partial function dependencies
4. Improving error handling and debugging capabilities

The phased approach allows for incremental improvements while maintaining system stability.