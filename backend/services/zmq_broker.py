# backend/services/zmq_broker.py
import zmq.asyncio
import asyncio
import logging
import time
import json
import os
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Dict, Deque, Tuple, Optional, List
from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS
import redis
from threading import Thread

WORKER_TIMEOUT = 45  # seconds
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
REDIS_PORT = int(os.getenv('REDIS_PORT', 6379))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@dataclass
class Transaction:
    tx_hash: str
    contract_address: str
    transaction_data: dict
    timestamp: float
    retry_count: int = 0
    consensus_mode: str = "leader"  # leader, validator, or rollup
    
    def to_dict(self):
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data):
        return cls(**data)

class ZeroMQBroker:
    def __init__(self, app: Flask = None):
        self.context = zmq.asyncio.Context()
        
        # Flask-SocketIO integration for real-time updates
        if app is None:
            app = Flask(__name__)
            CORS(app)
        self.app = app
        self.socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')
        
        # Socket configuration
        self.frontend = self.context.socket(zmq.PULL)
        self.frontend.bind("tcp://*:5557")  # From jsonrpc service
        
        self.backend = self.context.socket(zmq.PUSH)
        self.backend.bind("tcp://*:5558")   # To consensus workers
        
        self.status_pub = self.context.socket(zmq.PUB)
        self.status_pub.bind("tcp://*:5559") # Status updates
        
        self.control = self.context.socket(zmq.ROUTER)
        self.control.bind("tcp://*:5560")    # Worker feedback
        
        # State management (replaces database queues)
        self.contract_queues: Dict[str, Deque[Transaction]] = defaultdict(deque)
        self.contracts_processing: Dict[str, Tuple[Transaction, str]] = {}
        self.worker_heartbeats: Dict[str, float] = {}
        self.validator_assignments: Dict[str, List[dict]] = {}  # tx_hash -> validators
        self.consensus_votes: Dict[str, List[dict]] = defaultdict(list)  # tx_hash -> votes
        
        # Redis connection for state persistence
        try:
            self.redis_client = redis.Redis(
                host=REDIS_HOST,
                port=REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=5
            )
            self.redis_client.ping()
            self.redis_enabled = True
            logger.info(f"Connected to Redis at {REDIS_HOST}:{REDIS_PORT}")
        except Exception as e:
            logger.warning(f"Redis not available: {e}. Running without persistence.")
            self.redis_enabled = False
            self.redis_client = None
        
        # Setup Flask routes
        self._setup_routes()
        
        logger.info("ZeroMQ Broker started with Flask-SocketIO integration!")
    
    def _setup_routes(self):
        """Setup Flask HTTP endpoints for monitoring"""
        @self.app.route('/health')
        def health():
            return {'status': 'healthy', 'workers': len(self.worker_heartbeats)}
        
        @self.app.route('/metrics')
        def metrics():
            return {
                'queued_transactions': sum(len(q) for q in self.contract_queues.values()),
                'processing_contracts': len(self.contracts_processing),
                'active_workers': len(self.worker_heartbeats),
                'worker_health': {
                    wid: time.time() - last_seen 
                    for wid, last_seen in self.worker_heartbeats.items()
                },
                'queues_by_contract': {
                    k: len(v) for k, v in self.contract_queues.items()
                }
            }
        
        @self.app.route('/queues')
        def queues():
            return {
                'contracts': {
                    addr: {
                        'queued': len(queue),
                        'processing': addr in self.contracts_processing
                    }
                    for addr, queue in self.contract_queues.items()
                }
            }
    
    async def run(self):
        """Main broker event loop"""
        # Restore state from Redis if available
        await self._restore_state()
        
        # Start monitoring task
        asyncio.create_task(self._monitor_worker_health())
        
        # Start state persistence task
        if self.redis_enabled:
            asyncio.create_task(self._persist_state_loop())
        
        poller = zmq.asyncio.Poller()
        poller.register(self.frontend, zmq.POLLIN)
        poller.register(self.control, zmq.POLLIN)
        
        logger.info("Broker event loop started")
        
        while True:
            try:
                events = dict(await poller.poll(timeout=1000))
                
                if self.frontend in events:
                    await self._handle_new_transaction()
                
                if self.control in events:
                    await self._handle_worker_feedback()
            except Exception as e:
                logger.error(f"Error in broker loop: {e}", exc_info=True)
    
    async def _handle_new_transaction(self):
        """Queue transaction for its contract"""
        try:
            message = await self.frontend.recv_json()
            tx = Transaction(
                tx_hash=message['tx_hash'],
                contract_address=message['contract_address'],
                transaction_data=message.get('transaction_data', {}),
                timestamp=time.time(),
                retry_count=message.get('retry_count', 0),
                consensus_mode=message.get('consensus_mode', 'leader')
            )
            
            # Add to per-contract queue (maintains ordering)
            self.contract_queues[tx.contract_address].append(tx)
            logger.info(f"Queued transaction {tx.tx_hash} for contract {tx.contract_address}")
            
            # Emit Socket.IO event
            self.socketio.emit('transaction_queued', {
                'tx_hash': tx.tx_hash,
                'contract_address': tx.contract_address,
                'queue_length': len(self.contract_queues[tx.contract_address])
            })
            
            # Try to dispatch if contract not being processed
            if tx.contract_address not in self.contracts_processing:
                await self._dispatch_next_for_contract(tx.contract_address)
        except Exception as e:
            logger.error(f"Error handling new transaction: {e}", exc_info=True)
    
    async def _dispatch_next_for_contract(self, contract: str):
        """Send next transaction for contract to workers"""
        if contract in self.contracts_processing:
            return  # Already being processed
        
        queue = self.contract_queues.get(contract)
        if not queue:
            return  # No work for this contract
        
        tx = queue.popleft()
        
        try:
            # Send to worker pool (PUSH auto-balances)
            await self.backend.send_json({
                'tx_hash': tx.tx_hash,
                'contract_address': tx.contract_address,
                'transaction_data': tx.transaction_data,
                'retry_count': tx.retry_count,
                'consensus_mode': tx.consensus_mode
            })
            
            # Mark as pending (worker will confirm with PROCESSING)
            self.contracts_processing[contract] = (tx, "pending")
            logger.info(f"Dispatched {tx.tx_hash} for contract {contract}")
            
            # Emit Socket.IO event
            self.socketio.emit('transaction_dispatched', {
                'tx_hash': tx.tx_hash,
                'contract_address': contract
            })
        except Exception as e:
            # On error, put transaction back in queue
            queue.appendleft(tx)
            logger.error(f"Error dispatching transaction: {e}")
    
    async def _handle_worker_feedback(self):
        """Process worker status updates"""
        try:
            worker_id_bytes = await self.control.recv()
            message = await self.control.recv_json()
            
            worker_id = worker_id_bytes.decode()
            status = message['status']
            
            # Update heartbeat
            self.worker_heartbeats[worker_id] = time.time()
            
            if status == 'PROCESSING':
                # Worker confirmed it has the contract
                contract = message['contract_address']
                if contract in self.contracts_processing:
                    tx, _ = self.contracts_processing[contract]
                    self.contracts_processing[contract] = (tx, worker_id)
                    logger.info(f"Worker {worker_id} processing {contract}")
                    
                    # Emit Socket.IO event for frontend
                    self.socketio.emit('transaction_processing', {
                        'tx_hash': tx.tx_hash,
                        'contract_address': contract,
                        'worker_id': worker_id,
                        'status': 'processing'
                    })
            
            elif status in ['SUCCESS', 'FAILED']:
                contract = message['contract_address']
                tx_hash = message['tx_hash']
                
                # Emit Socket.IO event for transaction completion
                self.socketio.emit('transaction_complete', {
                    'tx_hash': tx_hash,
                    'contract_address': contract,
                    'status': status.lower(),
                    'result': message.get('result'),
                    'error': message.get('error')
                })
                
                # Remove from processing
                if contract in self.contracts_processing:
                    del self.contracts_processing[contract]
                
                # Handle result
                if status == 'FAILED' and message.get('retryable'):
                    # Requeue for retry
                    tx = Transaction(
                        tx_hash=tx_hash,
                        contract_address=contract,
                        transaction_data=message.get('transaction_data', {}),
                        timestamp=time.time(),
                        retry_count=message.get('retry_count', 0) + 1,
                        consensus_mode=message.get('consensus_mode', 'leader')
                    )
                    if tx.retry_count < 3:
                        self.contract_queues[contract].append(tx)
                        logger.info(f"Requeued {tx_hash} for retry (attempt {tx.retry_count + 1})")
                    else:
                        logger.error(f"Transaction {tx_hash} failed after 3 attempts")
                
                # Dispatch next transaction for this contract
                await self._dispatch_next_for_contract(contract)
            
            elif status == 'CONSENSUS_VOTE':
                # Handle consensus voting updates
                tx_hash = message['tx_hash']
                validator_id = message['validator_id']
                vote = message['vote']
                
                # Store vote
                self.consensus_votes[tx_hash].append({
                    'validator_id': validator_id,
                    'vote': vote,
                    'timestamp': time.time()
                })
                
                # Emit Socket.IO event for consensus progress
                self.socketio.emit('consensus_vote', {
                    'tx_hash': tx_hash,
                    'validator_id': validator_id,
                    'vote': vote,
                    'timestamp': time.time()
                })
            
            elif status == 'HEARTBEAT':
                logger.debug(f"Heartbeat from worker {worker_id}")
                
        except Exception as e:
            logger.error(f"Error handling worker feedback: {e}", exc_info=True)
    
    async def _monitor_worker_health(self):
        """Detect and recover from dead workers"""
        while True:
            try:
                await asyncio.sleep(15)
                current_time = time.time()
                
                # Find dead workers
                dead_workers = {
                    wid for wid, last_seen in self.worker_heartbeats.items()
                    if current_time - last_seen > WORKER_TIMEOUT
                }
                
                if dead_workers:
                    logger.warning(f"Dead workers detected: {dead_workers}")
                    
                    # Recover contracts from dead workers
                    for contract, (tx, worker_id) in list(self.contracts_processing.items()):
                        if worker_id in dead_workers:
                            # Requeue at front for immediate retry
                            self.contract_queues[contract].appendleft(tx)
                            del self.contracts_processing[contract]
                            logger.info(f"Recovered {tx.tx_hash} from dead worker {worker_id}")
                            
                            # Emit recovery event
                            self.socketio.emit('transaction_recovered', {
                                'tx_hash': tx.tx_hash,
                                'contract_address': contract,
                                'worker_id': worker_id
                            })
                    
                    # Remove dead workers
                    for worker_id in dead_workers:
                        del self.worker_heartbeats[worker_id]
            except Exception as e:
                logger.error(f"Error in health monitoring: {e}", exc_info=True)
    
    async def _persist_state_loop(self):
        """Periodically persist state to Redis"""
        while True:
            try:
                await asyncio.sleep(30)
                await self._persist_state()
            except Exception as e:
                logger.error(f"Error persisting state: {e}", exc_info=True)
    
    async def _persist_state(self):
        """Save broker state to Redis for recovery"""
        if not self.redis_enabled:
            return
        
        try:
            state = {
                'timestamp': time.time(),
                'queues': {
                    contract: [tx.to_dict() for tx in queue]
                    for contract, queue in self.contract_queues.items()
                },
                'processing': {
                    contract: (tx.to_dict(), worker_id)
                    for contract, (tx, worker_id) in self.contracts_processing.items()
                },
                'consensus_votes': dict(self.consensus_votes)
            }
            
            self.redis_client.set(
                'broker_state',
                json.dumps(state),
                ex=300  # Expire after 5 minutes
            )
            logger.debug("State persisted to Redis")
        except Exception as e:
            logger.error(f"Failed to persist state: {e}")
    
    async def _restore_state(self):
        """Restore broker state from Redis on startup"""
        if not self.redis_enabled:
            return
        
        try:
            state_json = self.redis_client.get('broker_state')
            if state_json:
                state = json.loads(state_json)
                
                # Restore queues
                for contract, tx_list in state['queues'].items():
                    for tx_data in tx_list:
                        self.contract_queues[contract].append(
                            Transaction.from_dict(tx_data)
                        )
                
                # Don't restore processing state (workers are gone)
                # But log what was being processed
                for contract, (tx_data, worker_id) in state['processing'].items():
                    tx = Transaction.from_dict(tx_data)
                    # Requeue for processing
                    self.contract_queues[contract].appendleft(tx)
                    logger.info(f"Requeued {tx.tx_hash} from previous session")
                
                # Restore consensus votes
                self.consensus_votes = defaultdict(list, state.get('consensus_votes', {}))
                
                logger.info(f"Restored state: {len(self.contract_queues)} contract queues")
        except Exception as e:
            logger.error(f"Failed to restore state: {e}")

def run_socketio_server(broker):
    """Run Flask-SocketIO server in a thread"""
    broker.socketio.run(broker.app, host='0.0.0.0', port=5561, debug=False, use_reloader=False)

async def main():
    broker = ZeroMQBroker()
    
    # Start Flask-SocketIO in a separate thread (it needs its own event loop)
    import threading
    socketio_thread = threading.Thread(target=run_socketio_server, args=(broker,), daemon=True)
    socketio_thread.start()
    
    # Give Flask-SocketIO time to start
    await asyncio.sleep(2)
    
    # Run ZeroMQ broker in the main asyncio loop
    await broker.run()

if __name__ == "__main__":
    asyncio.run(main())