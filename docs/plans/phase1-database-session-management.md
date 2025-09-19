# Phase 1: Database Session Management - Detailed Implementation Plan

**Date:** 2025-09-15  
**Status:** Ready for Implementation  
**Priority:** HIGH  
**Estimated Duration:** 2-3 days  

## Current State Analysis

### Session Creation Points

1. **FastAPI Server (fastapi_server.py)**
   - Line 121-133: Engine creation with pool configuration
   - Line 131-133: SessionLocal factory creation
   - Line 136-147: `get_db()` dependency function
   - Line 158: Session for initial app state components
   - Line 176: **CRITICAL**: Separate SessionLocal() for validators_manager
   - Line 196-197: `get_session()` function for consensus
   - Line 305: Session creation in RPC handler

2. **Component Initialization Patterns**
   ```python
   # Current problematic patterns:
   
   # Pattern 1: Direct session passing in constructor
   TransactionsProcessor(session)  # Line 164
   AccountsManager(session)        # Line 165
   SnapshotManager(session)        # Line 166
   LLMProviderRegistry(session)    # Line 167
   
   # Pattern 2: Separate session for validators
   validators.Manager(SessionLocal())  # Line 176 - PROBLEM!
   
   # Pattern 3: Session factory for consensus
   def get_session():
       return SessionLocal()  # Line 196-197
   ```

3. **Session Lifecycle Issues**
   - Components hold session references throughout their lifetime
   - No clear transaction boundaries
   - Mixed session scopes (request-scoped vs. application-scoped)
   - Validators manager creates own sessions, breaking transaction isolation

### Database Connection Pool Analysis

**Current Configuration (Line 121-128):**
```python
engine = create_engine(
    db_uri,
    pool_size=20,           # Fixed size, not based on workers
    max_overflow=10,        # Additional connections when needed
    pool_pre_ping=True,     # Health check before use
    pool_recycle=3600,      # Recycle after 1 hour
    pool_timeout=30,        # Wait 30s for connection
)
```

**Problems:**
- Pool size not adjusted for worker count
- No connection limit per worker
- No monitoring of pool usage
- No retry logic for connection failures

## Detailed Implementation Plan

### Step 1: Create Unified Session Management System

#### 1.1 New Session Manager (backend/database_handler/session_factory.py)

```python
"""
Unified database session management for FastAPI application.
Provides both sync and async session factories with proper lifecycle management.
"""

import os
from typing import AsyncGenerator, Generator, Optional
from contextlib import asynccontextmanager, contextmanager
from sqlalchemy import create_engine, event, pool
from sqlalchemy.orm import Session, sessionmaker, scoped_session
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool, QueuePool
import logging

logger = logging.getLogger(__name__)


class DatabaseSessionManager:
    """
    Centralized database session management with support for:
    - Request-scoped sessions (for API endpoints)
    - Task-scoped sessions (for background tasks)
    - Transaction-scoped sessions (for atomic operations)
    """
    
    def __init__(self, database_url: str, **engine_kwargs):
        """
        Initialize the session manager with optimized pool settings.
        
        Args:
            database_url: PostgreSQL connection string
            **engine_kwargs: Additional engine configuration
        """
        # Calculate pool size based on environment
        self.worker_count = int(os.environ.get("WEB_CONCURRENCY", 1))
        self.pool_size = self._calculate_pool_size()
        self.max_overflow = self._calculate_max_overflow()
        
        # Create sync engine with optimized settings
        self.engine = create_engine(
            database_url,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_pre_ping=True,  # Verify connections before use
            pool_recycle=3600,   # Recycle connections after 1 hour
            pool_timeout=30,     # Wait up to 30s for a connection
            echo_pool=os.environ.get("LOG_LEVEL") == "DEBUG",  # Pool logging in debug
            connect_args={
                "connect_timeout": 10,
                "options": "-c statement_timeout=30000"  # 30s statement timeout
            },
            **engine_kwargs
        )
        
        # Create async engine (for future use)
        async_database_url = database_url.replace("postgresql://", "postgresql+asyncpg://")
        self.async_engine = create_async_engine(
            async_database_url,
            pool_size=self.pool_size,
            max_overflow=self.max_overflow,
            pool_pre_ping=True,
            pool_recycle=3600,
            echo_pool=os.environ.get("LOG_LEVEL") == "DEBUG",
        )
        
        # Session factories
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False  # Prevent lazy loading issues
        )
        
        self.AsyncSessionLocal = async_sessionmaker(
            bind=self.async_engine,
            autocommit=False,
            autoflush=False,
            expire_on_commit=False
        )
        
        # Scoped session for thread-local storage (useful for background tasks)
        self.scoped_session = scoped_session(self.SessionLocal)
        
        # Add event listeners for monitoring
        self._setup_event_listeners()
        
        # Metrics storage
        self.metrics = {
            "connections_created": 0,
            "connections_checked_out": 0,
            "connections_checked_in": 0,
            "connection_errors": 0,
        }
    
    def _calculate_pool_size(self) -> int:
        """Calculate optimal pool size based on worker count."""
        # Formula: (workers * 2) + spare connections
        base_size = self.worker_count * 2
        spare_connections = 4
        return min(base_size + spare_connections, 20)  # Cap at 20
    
    def _calculate_max_overflow(self) -> int:
        """Calculate max overflow based on pool size."""
        return min(self.pool_size // 2, 10)  # Half of pool size, cap at 10
    
    def _setup_event_listeners(self):
        """Setup SQLAlchemy event listeners for monitoring."""
        @event.listens_for(self.engine, "connect")
        def receive_connect(dbapi_conn, connection_record):
            self.metrics["connections_created"] += 1
            logger.debug(f"New connection created. Total: {self.metrics['connections_created']}")
        
        @event.listens_for(self.engine, "checkout")
        def receive_checkout(dbapi_conn, connection_record, connection_proxy):
            self.metrics["connections_checked_out"] += 1
            
        @event.listens_for(self.engine, "checkin")
        def receive_checkin(dbapi_conn, connection_record):
            self.metrics["connections_checked_in"] += 1
    
    # === Sync Session Methods ===
    
    def get_session(self) -> Session:
        """
        Get a new session instance.
        Use this for dependency injection in FastAPI endpoints.
        """
        return self.SessionLocal()
    
    @contextmanager
    def session_scope(self) -> Generator[Session, None, None]:
        """
        Provide a transactional scope with automatic cleanup.
        Commits on success, rolls back on error, always closes.
        """
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
    
    @contextmanager
    def readonly_session_scope(self) -> Generator[Session, None, None]:
        """
        Provide a read-only session scope.
        Automatically rolls back to prevent accidental writes.
        """
        session = self.SessionLocal()
        try:
            yield session
        finally:
            session.rollback()  # Always rollback read-only sessions
            session.close()
    
    # === Async Session Methods ===
    
    async def get_async_session(self) -> AsyncSession:
        """Get a new async session instance."""
        return self.AsyncSessionLocal()
    
    @asynccontextmanager
    async def async_session_scope(self) -> AsyncGenerator[AsyncSession, None]:
        """
        Provide an async transactional scope with automatic cleanup.
        """
        async with self.AsyncSessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()
    
    # === Utility Methods ===
    
    def get_pool_status(self) -> dict:
        """Get current connection pool status for monitoring."""
        pool = self.engine.pool
        return {
            "size": pool.size(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "total": pool.size() + pool.overflow(),
            "max_overflow": self.max_overflow,
            "metrics": self.metrics,
        }
    
    def dispose_all(self):
        """Dispose all connections (useful for testing or shutdown)."""
        self.engine.dispose()
        logger.info("All database connections disposed")
    
    async def dispose_all_async(self):
        """Dispose all async connections."""
        await self.async_engine.dispose()


# Global instance (singleton pattern)
_db_manager: Optional[DatabaseSessionManager] = None


def init_database_manager(database_url: str, **kwargs) -> DatabaseSessionManager:
    """Initialize the global database manager."""
    global _db_manager
    if _db_manager is not None:
        _db_manager.dispose_all()
    _db_manager = DatabaseSessionManager(database_url, **kwargs)
    return _db_manager


def get_database_manager() -> DatabaseSessionManager:
    """Get the global database manager instance."""
    if _db_manager is None:
        raise RuntimeError("Database manager not initialized. Call init_database_manager first.")
    return _db_manager


# === FastAPI Dependency Functions ===

def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency for database sessions.
    Usage: db: Session = Depends(get_db)
    """
    db = get_database_manager().get_session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency for async database sessions.
    Usage: db: AsyncSession = Depends(get_async_db)
    """
    async with get_database_manager().async_session_scope() as session:
        yield session
```

#### 1.2 Update FastAPI Server Initialization

**File: backend/protocol_rpc/fastapi_server.py**

Replace lines 111-147 with:

```python
from backend.database_handler.session_factory import (
    init_database_manager,
    get_database_manager,
    get_db
)

# Initialize database manager
db_manager = init_database_manager(db_uri)

# Remove old engine and SessionLocal creation
```

Update lifespan function (lines 150-256):

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle."""
    print("Starting up FastAPI application...")
    
    # Get database manager
    db_manager = get_database_manager()
    
    # Initialize database
    Base.metadata.create_all(bind=db_manager.engine)
    
    # Create component factories with dependency injection
    def create_transactions_processor(session: Session) -> TransactionsProcessor:
        return TransactionsProcessor(session)
    
    def create_accounts_manager(session: Session) -> AccountsManager:
        return AccountsManager(session)
    
    def create_snapshot_manager(session: Session) -> SnapshotManager:
        return SnapshotManager(session)
    
    def create_llm_provider_registry(session: Session) -> LLMProviderRegistry:
        return LLMProviderRegistry(session)
    
    # Initialize shared services (that don't need sessions immediately)
    app_state["msg_handler"] = MessageHandler(manager, config=GlobalConfiguration())
    app_state["consensus_service"] = ConsensusService()
    app_state["transactions_parser"] = TransactionParser(app_state["consensus_service"])
    
    # Store factories for lazy initialization
    app_state["db_manager"] = db_manager
    app_state["create_transactions_processor"] = create_transactions_processor
    app_state["create_accounts_manager"] = create_accounts_manager
    app_state["create_snapshot_manager"] = create_snapshot_manager
    app_state["create_llm_provider_registry"] = create_llm_provider_registry
    
    # Initialize validators manager with proper session management
    validators_manager = validators.Manager(db_manager)
    app_state["validators_manager"] = validators_manager
    
    # ... rest of initialization
```

### Step 2: Refactor Component Session Handling

#### 2.1 Update Validators Manager

**File: backend/validators/__init__.py**

Update Manager class to use DatabaseSessionManager:

```python
class Manager:
    def __init__(self, db_manager: DatabaseSessionManager):
        self.db_manager = db_manager
        # Create registry with session scope
        with self.db_manager.session_scope() as session:
            self.registry = ModifiableValidatorsRegistryInterceptor(self, session)
        # ... rest of init
    
    def get_session(self) -> Session:
        """Get a new session for operations."""
        return self.db_manager.get_session()
    
    @contextmanager
    def session_scope(self):
        """Get a session scope for transactions."""
        with self.db_manager.session_scope() as session:
            yield session
```

#### 2.2 Update Component Classes

**Pattern for all database handler classes:**

```python
class ComponentClass:
    def __init__(self, session: Optional[Session] = None):
        """
        Initialize with optional session.
        If no session provided, operations will use their own sessions.
        """
        self._session = session
        self._owns_session = session is None
    
    @property
    def session(self) -> Session:
        """Get the current session or create a new one."""
        if self._session is None:
            self._session = get_database_manager().get_session()
            self._owns_session = True
        return self._session
    
    def close(self):
        """Close the session if we own it."""
        if self._owns_session and self._session:
            self._session.close()
            self._session = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
```

### Step 3: Update RPC Endpoint Handler

#### 3.1 Simplify RPC Request Handling

**File: backend/protocol_rpc/fastapi_server.py**

Update the jsonrpc_endpoint function (lines 279-336):

```python
@app.post("/api")
async def jsonrpc_endpoint(request: Request, db: Session = Depends(get_db)):
    """Main JSON-RPC endpoint with proper session management."""
    try:
        body = await request.json()
        rpc_request = JSONRPCRequest(**body)
        
        # Fast path for healthcheck
        if rpc_request.method == "ping":
            response = JSONRPCResponse(jsonrpc="2.0", result="OK", id=rpc_request.id)
            return JSONResponse(content=response.model_dump(exclude_none=True))
        
        # Get handler
        rpc_handler = app_state.get("rpc_handler")
        if not rpc_handler:
            response = JSONRPCResponse(
                jsonrpc="2.0",
                error={"code": -32603, "message": "RPC handler not initialized"},
                id=rpc_request.id,
            )
            return JSONResponse(content=response.model_dump(exclude_none=True))
        
        # Create components with current session
        app_state_with_session = {
            **app_state,
            "session": db,
            "transactions_processor": app_state["create_transactions_processor"](db),
            "accounts_manager": app_state["create_accounts_manager"](db),
            "snapshot_manager": app_state["create_snapshot_manager"](db),
            "llm_provider_registry": app_state["create_llm_provider_registry"](db),
        }
        
        # Handle request with session-scoped components
        response = await rpc_handler.handle_request(
            rpc_request,
            db,
            app_state_with_session,
        )
        
        return JSONResponse(content=response.model_dump(exclude_none=True))
        
    except json.JSONDecodeError:
        response = JSONRPCResponse(
            jsonrpc="2.0", 
            error={"code": -32700, "message": "Parse error"}, 
            id=None
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))
    except Exception as e:
        response = JSONRPCResponse(
            jsonrpc="2.0",
            error={"code": -32603, "message": str(e)},
            id=body.get("id") if "body" in locals() else None,
        )
        return JSONResponse(content=response.model_dump(exclude_none=True))
```

### Step 4: Update Consensus Algorithm

**File: backend/consensus/base.py**

Replace get_session callback with DatabaseSessionManager:

```python
class ConsensusAlgorithm:
    def __init__(
        self,
        db_manager: DatabaseSessionManager,
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_manager: validators.Manager,
    ):
        self.db_manager = db_manager
        self.msg_handler = msg_handler
        self.consensus_service = consensus_service
        self.validators_manager = validators_manager
        # ... rest of init
    
    async def run_crawl_snapshot_loop(self, stop_event):
        """Use session scope for each iteration."""
        while not stop_event.is_set():
            with self.db_manager.session_scope() as session:
                chain_snapshot = ChainSnapshot(session)
                transactions_processor = TransactionsProcessor(session)
                # ... process transactions
```

### Step 5: Add Monitoring and Health Checks

#### 5.1 Pool Status Endpoint

**File: backend/protocol_rpc/endpoints.py**

Update dev_get_pool_status:

```python
def dev_get_pool_status() -> dict:
    """Get database connection pool status."""
    from backend.database_handler.session_factory import get_database_manager
    from datetime import datetime
    
    db_manager = get_database_manager()
    pool_status = db_manager.get_pool_status()
    
    return {
        "timestamp": datetime.now().isoformat(),
        "pool": pool_status,
        "health": {
            "status": "healthy" if pool_status["checked_out"] < pool_status["size"] else "degraded",
            "utilization": f"{(pool_status['checked_out'] / pool_status['size'] * 100):.1f}%"
        }
    }
```

### Step 6: Migration Strategy

#### 6.1 Gradual Migration Steps

1. **Day 1: Morning**
   - Deploy DatabaseSessionManager
   - Update FastAPI server initialization
   - Test with existing endpoints

2. **Day 1: Afternoon**
   - Update validators manager
   - Update consensus algorithm
   - Run integration tests

3. **Day 2: Morning**
   - Update all database handler components
   - Update RPC endpoint handler
   - Test all RPC methods

4. **Day 2: Afternoon**
   - Add monitoring endpoints
   - Performance testing
   - Load testing

5. **Day 3:**
   - Fix any issues found
   - Documentation updates
   - Deploy to staging

#### 6.2 Rollback Plan

If issues occur, rollback by:
1. Revert to previous commit
2. Keep DatabaseSessionManager but use old SessionLocal pattern
3. Gradually migrate components back

### Step 7: Testing Strategy

#### 7.1 Unit Tests

```python
# tests/unit/test_session_manager.py
import pytest
from backend.database_handler.session_factory import DatabaseSessionManager

def test_session_creation():
    manager = DatabaseSessionManager("postgresql://test@localhost/test")
    with manager.session_scope() as session:
        assert session is not None

def test_pool_metrics():
    manager = DatabaseSessionManager("postgresql://test@localhost/test")
    status = manager.get_pool_status()
    assert "size" in status
    assert "checked_out" in status

@pytest.mark.asyncio
async def test_async_session():
    manager = DatabaseSessionManager("postgresql://test@localhost/test")
    async with manager.async_session_scope() as session:
        assert session is not None
```

#### 7.2 Integration Tests

```python
# tests/integration/test_session_integration.py
def test_concurrent_sessions():
    """Test multiple concurrent sessions don't interfere."""
    # Test implementation

def test_transaction_isolation():
    """Test transaction isolation between sessions."""
    # Test implementation

def test_session_cleanup_on_error():
    """Test sessions are properly cleaned up on errors."""
    # Test implementation
```

### Step 8: Performance Optimizations

#### 8.1 Connection Pool Tuning

```python
# Environment-based configuration
if os.environ.get("ENVIRONMENT") == "production":
    pool_config = {
        "pool_size": 30,
        "max_overflow": 20,
        "pool_timeout": 60,
        "pool_recycle": 1800,  # 30 minutes
    }
else:
    pool_config = {
        "pool_size": 10,
        "max_overflow": 5,
        "pool_timeout": 30,
        "pool_recycle": 3600,  # 1 hour
    }
```

#### 8.2 Session Caching for Read-Heavy Operations

```python
class CachedSessionScope:
    """Cache session for multiple read operations."""
    def __init__(self, db_manager: DatabaseSessionManager):
        self.db_manager = db_manager
        self._cache = {}
    
    @contextmanager
    def cached_readonly_session(self, cache_key: str):
        if cache_key in self._cache:
            yield self._cache[cache_key]
        else:
            with self.db_manager.readonly_session_scope() as session:
                self._cache[cache_key] = session
                yield session
```

## Success Criteria

1. **No Session Leaks**
   - Monitor for 24 hours, connections should return to pool
   - No gradual increase in connection count

2. **Transaction Isolation**
   - Validators manager uses same transaction boundary as other components
   - No data inconsistencies between components

3. **Performance Metrics**
   - Connection pool utilization < 80%
   - Zero connection timeout errors
   - Endpoint latency unchanged or improved

4. **Code Quality**
   - Single source of truth for session management
   - Clear session lifecycle in all components
   - Simplified dependency injection

## Risk Mitigation

1. **Session Leak Risk**
   - Mitigation: Automatic cleanup in context managers
   - Monitoring: Pool status endpoint

2. **Transaction Deadlock Risk**
   - Mitigation: Consistent transaction ordering
   - Statement timeout configuration

3. **Performance Regression Risk**
   - Mitigation: Load testing before deployment
   - Gradual rollout with monitoring

## Conclusion

This detailed implementation plan for Phase 1 addresses the critical database session management issues:

1. **Unified Session Management**: Single DatabaseSessionManager class handles all session creation
2. **Proper Lifecycle Management**: Clear session scopes with automatic cleanup
3. **Fixed Validator Session Issue**: Validators manager uses shared session pool
4. **Improved Monitoring**: Pool status metrics and health checks
5. **Better Performance**: Optimized pool configuration based on worker count

The implementation can be completed in 2-3 days with minimal risk through gradual migration and comprehensive testing.