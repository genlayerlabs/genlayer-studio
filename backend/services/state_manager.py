# backend/services/state_manager.py
import asyncio
import redis
import json
import logging
import os
import time
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
from backend.database_handler.contract_snapshot import ContractSnapshot

logger = logging.getLogger(__name__)

class DistributedStateManager:
    """Manages contract state across distributed workers using Redis for caching and coordination"""
    
    def __init__(self, worker_id: Optional[str] = None):
        """Initialize the distributed state manager
        
        Args:
            worker_id: Unique identifier for this worker
        """
        self.worker_id = worker_id or f"worker-{os.getpid()}"
        self.redis_host = os.getenv('REDIS_HOST', 'localhost')
        self.redis_port = int(os.getenv('REDIS_PORT', 6379))
        self.cache_ttl = int(os.getenv('STATE_CACHE_TTL', 300))  # 5 minutes default
        self.lock_timeout = int(os.getenv('STATE_LOCK_TIMEOUT', 30))  # 30 seconds default
        
        self.redis_client = None
        self.pubsub = None
        self._connect()
    
    def _connect(self):
        """Establish connection to Redis"""
        try:
            self.redis_client = redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True,
                socket_keepalive_options={
                    1: 1,  # TCP_KEEPIDLE
                    2: 3,  # TCP_KEEPINTVL  
                    3: 5   # TCP_KEEPCNT
                }
            )
            
            # Test connection
            self.redis_client.ping()
            
            # Setup pub/sub for invalidation notifications
            self.pubsub = self.redis_client.pubsub()
            self.pubsub.subscribe('contract_invalidation')
            
            logger.info(f"Connected to Redis at {self.redis_host}:{self.redis_port}")
        except Exception as e:
            logger.warning(f"Failed to connect to Redis: {e}. State caching disabled.")
            self.redis_client = None
            self.pubsub = None
    
    async def get_contract_snapshot(self, 
                                   session: Session, 
                                   contract_address: str) -> ContractSnapshot:
        """Get contract snapshot with distributed caching
        
        Args:
            session: Database session
            contract_address: Address of the contract
            
        Returns:
            ContractSnapshot object
        """
        if not self.redis_client:
            # Fallback to direct database access
            return ContractSnapshot(session, contract_address)
        
        try:
            # Try Redis cache first
            cache_key = f"snapshot:{contract_address}"
            cached = self.redis_client.get(cache_key)
            
            if cached:
                # Validate cache version matches DB
                db_version = self._get_db_version(session, contract_address)
                cached_data = json.loads(cached)
                
                if cached_data.get('version') == db_version:
                    logger.debug(f"Cache hit for contract {contract_address}")
                    return self._snapshot_from_cache(session, cached_data)
                else:
                    logger.debug(f"Cache version mismatch for {contract_address}")
            
            # Load from database
            snapshot = ContractSnapshot(session, contract_address)
            
            # Cache for other workers
            cache_data = {
                'version': self._get_db_version(session, contract_address),
                'contract_address': contract_address,
                'state': self._serialize_state(snapshot),
                'code': getattr(snapshot, 'code', None),
                'balance': getattr(snapshot, 'balance', 0),
                'timestamp': time.time()
            }
            
            self.redis_client.setex(
                cache_key,
                self.cache_ttl,
                json.dumps(cache_data)
            )
            
            logger.debug(f"Cached snapshot for contract {contract_address}")
            return snapshot
            
        except Exception as e:
            logger.error(f"Error in get_contract_snapshot: {e}")
            # Fallback to direct database access
            return ContractSnapshot(session, contract_address)
    
    async def invalidate_contract(self, contract_address: str):
        """Invalidate contract cache after state change
        
        Args:
            contract_address: Address of the contract to invalidate
        """
        if not self.redis_client:
            return
        
        try:
            cache_key = f"snapshot:{contract_address}"
            self.redis_client.delete(cache_key)
            
            # Notify other workers via pub/sub
            self.redis_client.publish('contract_invalidation', contract_address)
            
            logger.debug(f"Invalidated cache for contract {contract_address}")
        except Exception as e:
            logger.error(f"Error invalidating contract cache: {e}")
    
    async def acquire_contract_lock(self, 
                                   contract_address: str, 
                                   timeout: Optional[int] = None) -> bool:
        """Acquire distributed lock for contract modification
        
        Args:
            contract_address: Address of the contract to lock
            timeout: Lock timeout in seconds
            
        Returns:
            True if lock acquired, False otherwise
        """
        if not self.redis_client:
            return True  # No Redis, no locking needed
        
        timeout = timeout or self.lock_timeout
        lock_key = f"lock:{contract_address}"
        
        try:
            # Try to acquire lock with timeout
            acquired = self.redis_client.set(
                lock_key, 
                self.worker_id, 
                nx=True,  # Only set if not exists
                ex=timeout  # Expire after timeout
            )
            
            if acquired:
                logger.debug(f"Acquired lock for contract {contract_address}")
            else:
                logger.debug(f"Failed to acquire lock for contract {contract_address}")
            
            return bool(acquired)
            
        except Exception as e:
            logger.error(f"Error acquiring lock: {e}")
            return True  # Allow operation to proceed on Redis error
    
    async def release_contract_lock(self, contract_address: str):
        """Release distributed lock for contract
        
        Args:
            contract_address: Address of the contract to unlock
        """
        if not self.redis_client:
            return
        
        lock_key = f"lock:{contract_address}"
        
        try:
            # Only release if we own the lock
            current_owner = self.redis_client.get(lock_key)
            if current_owner == self.worker_id:
                self.redis_client.delete(lock_key)
                logger.debug(f"Released lock for contract {contract_address}")
        except Exception as e:
            logger.error(f"Error releasing lock: {e}")
    
    async def wait_for_lock(self, 
                           contract_address: str, 
                           max_wait: int = 60) -> bool:
        """Wait for a contract lock to become available
        
        Args:
            contract_address: Address of the contract
            max_wait: Maximum time to wait in seconds
            
        Returns:
            True if lock acquired, False if timeout
        """
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            if await self.acquire_contract_lock(contract_address):
                return True
            await asyncio.sleep(0.1)
        
        return False
    
    def _get_db_version(self, session: Session, contract_address: str) -> Optional[int]:
        """Get current version of contract from database
        
        Args:
            session: Database session
            contract_address: Address of the contract
            
        Returns:
            Version number or None
        """
        try:
            result = session.execute(
                text("""
                    SELECT version 
                    FROM contracts 
                    WHERE address = :address
                """),
                {'address': contract_address}
            ).fetchone()
            
            return result[0] if result else None
        except Exception as e:
            logger.error(f"Error getting contract version: {e}")
            return None
    
    def _serialize_state(self, snapshot: ContractSnapshot) -> Dict[str, Any]:
        """Serialize contract snapshot state for caching
        
        Args:
            snapshot: ContractSnapshot to serialize
            
        Returns:
            Serialized state dictionary
        """
        try:
            # Get state from snapshot
            state = {}
            if hasattr(snapshot, 'state'):
                state = snapshot.state
            elif hasattr(snapshot, 'get_state'):
                state = snapshot.get_state()
            
            # Ensure state is JSON serializable
            return self._make_json_serializable(state)
        except Exception as e:
            logger.error(f"Error serializing snapshot state: {e}")
            return {}
    
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert object to JSON serializable format
        
        Args:
            obj: Object to convert
            
        Returns:
            JSON serializable version
        """
        if isinstance(obj, dict):
            return {k: self._make_json_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, (list, tuple)):
            return [self._make_json_serializable(item) for item in obj]
        elif isinstance(obj, (str, int, float, bool, type(None))):
            return obj
        else:
            return str(obj)
    
    def _snapshot_from_cache(self, session: Session, cached_data: Dict) -> ContractSnapshot:
        """Reconstruct ContractSnapshot from cached data
        
        Args:
            session: Database session
            cached_data: Cached snapshot data
            
        Returns:
            ContractSnapshot object
        """
        # Create snapshot and populate from cache
        snapshot = ContractSnapshot(session, cached_data['contract_address'])
        
        # Restore cached state
        if 'state' in cached_data:
            if hasattr(snapshot, 'state'):
                snapshot.state = cached_data['state']
            elif hasattr(snapshot, 'set_state'):
                snapshot.set_state(cached_data['state'])
        
        # Restore other attributes
        if 'code' in cached_data and hasattr(snapshot, 'code'):
            snapshot.code = cached_data['code']
        if 'balance' in cached_data and hasattr(snapshot, 'balance'):
            snapshot.balance = cached_data['balance']
        
        return snapshot
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics
        
        Returns:
            Dictionary of cache statistics
        """
        if not self.redis_client:
            return {'enabled': False}
        
        try:
            info = self.redis_client.info('stats')
            keys = self.redis_client.keys('snapshot:*')
            locks = self.redis_client.keys('lock:*')
            
            return {
                'enabled': True,
                'connected': True,
                'cached_contracts': len(keys),
                'active_locks': len(locks),
                'hits': info.get('keyspace_hits', 0),
                'misses': info.get('keyspace_misses', 0),
                'hit_rate': (
                    info.get('keyspace_hits', 0) / 
                    (info.get('keyspace_hits', 0) + info.get('keyspace_misses', 1))
                ) * 100
            }
        except Exception as e:
            logger.error(f"Error getting cache stats: {e}")
            return {'enabled': True, 'connected': False}
    
    def close(self):
        """Close Redis connections"""
        if self.pubsub:
            self.pubsub.close()
        if self.redis_client:
            self.redis_client.close()
        logger.info("State manager connections closed")
    
    def __del__(self):
        """Cleanup on deletion"""
        self.close()


# Import asyncio only if needed for async operations
try:
    import asyncio
except ImportError:
    pass