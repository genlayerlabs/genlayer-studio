# backend/services/zmq_broker.py
import zmq.asyncio
import asyncio
import logging
import time
import json
import os
import sys
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from typing import Dict, Deque, Tuple, Optional, List, Any
from flask import Flask
from flask_socketio import SocketIO
from flask_cors import CORS
import redis
from threading import Thread
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# Add backend modules for ConsensusAlgorithm
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backend.database_handler.db_config import get_db_url
from backend.consensus.base import (
    ConsensusAlgorithm, 
    contract_processor_factory, 
    chain_snapshot_factory,
    transactions_processor_factory,
    accounts_manager_factory,
    contract_snapshot_factory,
    node_factory
)
from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.contract_processor import ContractProcessor
from backend.rollup.consensus_service import ConsensusService
import backend.validators as validators
from backend.domain.types import Transaction as DomainTransaction, TransactionStatus, TransactionType

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
        
        # Database setup for ConsensusAlgorithm
        self.db_url = get_db_url()
        self.engine = create_engine(
            self.db_url,
            poolclass=NullPool,
            echo=False
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # Initialize consensus components (will be fully setup in setup_consensus)
        self.msg_handler = None
        self.consensus_service = None
        self.validators_manager = None
        self.consensus = None
        
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
        self.pending_executions: Dict[str, asyncio.Future] = {}  # task_id -> Future for results
        self.shutdown_event = asyncio.Event()  # For graceful shutdown
        
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
    
    def restore_stuck_transactions(self):
        """Restore transactions that are stuck because of a program crash or shutdown
        
        All stuck transactions are reset to PENDING status, maintaining their
        original created_at ordering to ensure proper transaction sequencing.
        """
        logger.info("Restoring stuck transactions...")
        
        def create_session():
            return Session(self.engine, expire_on_commit=False)
        
        with create_session() as session:
            transactions_processor = transactions_processor_factory(session)
            accounts_mgr = accounts_manager_factory(session)
            
            try:
                # Get ALL stuck transactions ordered by created_at
                # This ensures we process them in the correct order
                from sqlalchemy import or_
                from backend.database_handler.models import Transactions
                
                stuck_transactions = session.query(Transactions).filter(
                    or_(
                        Transactions.status == TransactionStatus.ACTIVATED.value,
                        Transactions.status == TransactionStatus.PROPOSING.value,
                        Transactions.status == TransactionStatus.COMMITTING.value,
                        Transactions.status == TransactionStatus.REVEALING.value,
                    )
                ).order_by(
                    Transactions.created_at.asc()  # Order by creation time globally
                ).all()
                
                if not stuck_transactions:
                    logger.info("No stuck transactions found")
                    return
                
                logger.info(f"Found {len(stuck_transactions)} stuck transactions")
                
                # Log the transactions we're resetting
                for tx in stuck_transactions:
                    status_name = TransactionStatus(tx.status).name
                    logger.info(f"Will reset: {tx.hash[:8]}... status={status_name} created_at={tx.created_at} contract={tx.to_address[:8]}...")
                
            except Exception as e:
                logger.error(f"Failed to find stuck transactions: {e}")
                return
            
            # Reset all stuck transactions back to PENDING
            reset_count = 0
            for tx in stuck_transactions:
                try:
                    # For deploy contracts, ensure account exists
                    if tx.type == 1:  # DEPLOY_CONTRACT
                        contract_proc = contract_processor_factory(session)
                        contract_reset = contract_proc.reset_contract(
                            contract_address=tx.to_address
                        )
                        if not contract_reset:
                            accounts_mgr.create_new_account_with_address(tx.to_address)
                    
                    # Get original status for logging
                    original_status = TransactionStatus(tx.status).name
                    
                    # Reset transaction to PENDING using transactions_processor for consistency
                    logger.info(f"Resetting {tx.hash[:16]}... from {original_status} to PENDING")
                    transactions_processor.update_transaction_status(
                        tx.hash,
                        TransactionStatus.PENDING,
                        update_current_status_changes=True
                    )
                    
                    # Clear all consensus data
                    transactions_processor.set_transaction_contract_snapshot(tx.hash, None)
                    transactions_processor.set_transaction_result(tx.hash, None)
                    transactions_processor.set_transaction_appeal(tx.hash, False)
                    transactions_processor.set_transaction_appeal_failed(tx.hash, 0)
                    transactions_processor.set_transaction_appeal_undetermined(tx.hash, False)
                    transactions_processor.reset_consensus_history(tx.hash)
                    transactions_processor.set_transaction_timestamp_appeal(tx.hash, None)
                    transactions_processor.reset_transaction_appeal_processing_time(tx.hash)
                    
                    reset_count += 1
                    
                except Exception as e:
                    logger.error(f"Failed to reset transaction {tx.hash}: {e}")
                    # Cancel transaction if restoration fails
                    try:
                        transactions_processor.update_transaction_status(
                            tx.hash,
                            TransactionStatus.CANCELED,
                            update_current_status_changes=True
                        )
                        logger.warning(f"Transaction {tx.hash[:16]}... set to CANCELED due to reset failure")
                    except Exception as cancel_error:
                        logger.error(f"Failed to cancel transaction {tx.hash}: {cancel_error}")
            
            session.commit()
            logger.info(f"Stuck transactions restoration completed: {reset_count}/{len(stuck_transactions)} reset to PENDING")
    
    async def populate_queues_with_activated_transactions(self):
        """Add ACTIVATED transactions to the pending queues on startup"""
        logger.info("Populating queues with ACTIVATED transactions...")
        
        def create_session():
            return Session(self.engine, expire_on_commit=False)
        
        with create_session() as session:
            transactions_processor = transactions_processor_factory(session)
            
            # Find ACTIVATED transactions
            from backend.database_handler.models import Transactions
            activated_txs = session.query(Transactions).filter(
                Transactions.status == TransactionStatus.ACTIVATED
            ).all()
            
            for tx_row in activated_txs:
                try:
                    # Parse transaction
                    tx_dict = transactions_processor._parse_transaction_data(tx_row)
                    transaction = DomainTransaction.from_dict(tx_dict)
                    
                    address = transaction.to_address
                    if address:
                        # Initialize queue if not present
                        if address not in self.consensus.pending_queues:
                            self.consensus.pending_queues[address] = asyncio.Queue()
                        if address not in self.consensus.pending_queue_stop_events:
                            self.consensus.pending_queue_stop_events[address] = asyncio.Event()
                        
                        # Add to queue
                        await self.consensus.pending_queues[address].put(transaction)
                        logger.info(f"Added ACTIVATED transaction {transaction.hash} to queue for {address}")
                    
                except Exception as e:
                    logger.error(f"Failed to add activated transaction to queue: {e}")
        
        logger.info("Queue population completed")
    
    async def setup_consensus(self):
        """Initialize ConsensusAlgorithm and related components"""
        try:
            logger.info("Starting consensus setup...")
            
            def create_session():
                return Session(self.engine, expire_on_commit=False)
            
            # Create a minimal message handler that just sends SocketIO events
            # The broker doesn't need GenVM - only workers do
            logger.info("Creating minimal message handler...")
            class MinimalMessageHandler:
                def __init__(self, socketio):
                    self.socketio = socketio
                
                def send_transaction_status_update(self, tx_hash, status, **kwargs):
                    """Send transaction status update via SocketIO"""
                    self.socketio.emit('transaction_status', {
                        'tx_hash': tx_hash,
                        'status': status.name if hasattr(status, 'name') else str(status),
                        **kwargs
                    })
                    logger.debug(f"Sent status update for {tx_hash}: {status}")
                
                def send_event(self, event_name, data):
                    """Send generic event via SocketIO"""
                    self.socketio.emit(event_name, data)
                
                def send_message(self, message, topic=None):
                    """Send message via SocketIO
                    
                    Args:
                        message: The message to send (LogEvent or dict)
                        topic: Optional topic (defaults to 'log_event' for LogEvent)
                    """
                    # Handle LogEvent objects
                    if hasattr(message, '__dict__'):
                        topic = topic or 'log_event'
                        msg_data = message.__dict__ if hasattr(message, '__dict__') else message
                    else:
                        topic = topic or 'message'
                        msg_data = message
                    
                    self.socketio.emit(topic, msg_data)
                    logger.debug(f"Sent message to topic {topic}")
            
            self.msg_handler = MinimalMessageHandler(self.socketio)
            
            # Restore any stuck transactions after msg_handler is initialized
            self.restore_stuck_transactions()
            
            logger.info("Initializing ConsensusService...")
            self.consensus_service = ConsensusService()
            
            # Initialize validators manager
            logger.info("Initializing validators manager...")
            self.validators_manager = validators.Manager(create_session())
            await self.validators_manager.restart()
            logger.info("Validators manager restarted")
            
            # Initialize ConsensusAlgorithm - but we'll modify it to use workers
            logger.info("Creating ConsensusAlgorithm...")
            self.consensus = ConsensusAlgorithm(
                create_session,
                self.msg_handler,
                self.consensus_service,
                self.validators_manager
            )
            
            # Store broker reference in consensus for worker execution
            self.consensus.zmq_broker = self
            
            logger.info("ConsensusAlgorithm initialized successfully in ZeroMQ Broker")
            
            # Populate queues with any ACTIVATED transactions
            await self.populate_queues_with_activated_transactions()
            
        except Exception as e:
            logger.error(f"Failed to setup consensus: {e}", exc_info=True)
            raise
    
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
        # Setup consensus components first
        await self.setup_consensus()
        
        # Restore state from Redis if available
        await self._restore_state()
        
        # Start monitoring task
        asyncio.create_task(self._monitor_worker_health())
        
        # Start state persistence task
        if self.redis_enabled:
            asyncio.create_task(self._persist_state_loop())
        
        # Start consensus polling from database
        asyncio.create_task(self._consensus_poller())
        
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
                tx_hash=message.get('tx_hash'),
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
            
            # ADD: Log every worker interaction
            logger.info(f"[BROKER-FEEDBACK] Received from worker {worker_id}: {status}")
            
            # Update heartbeat
            self.worker_heartbeats[worker_id] = time.time()
            
            # ADD: Log worker count after heartbeat
            logger.info(f"[BROKER-FEEDBACK] Active workers: {len(self.worker_heartbeats)}")
            
            if status == 'EXECUTION_RESULT':
                # Worker is returning execution result
                task_id = message.get('task_id')
                # ADD: Log execution results
                logger.info(f"[BROKER-FEEDBACK] Got execution result from {worker_id} for task {task_id}")
                logger.info(f"[BROKER-FEEDBACK] Result content: {message.get('result')}")
                
                if task_id in self.pending_executions:
                    self.pending_executions[task_id].set_result(message.get('result'))
                    logger.info(f"Received execution result for task {task_id}")
                else:
                    logger.warning(f"Received result for unknown task {task_id}")
            
            elif status == 'PROCESSING':
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
    
    async def _consensus_poller(self):
        """Poll database for pending transactions and run consensus"""
        logger.info("Starting consensus polling loop")
        
        # Wait a bit for consensus to be initialized
        await asyncio.sleep(2)
        
        if not self.consensus:
            logger.error("Consensus not initialized, cannot start poller")
            return
        
        # Create stop events
        import threading
        crawl_stop_event = threading.Event()
        process_stop_event = threading.Event()
        
        # Start both the crawl loop and the processing loop
        crawl_task = asyncio.create_task(self._run_crawl_loop(crawl_stop_event))
        process_task = asyncio.create_task(self._run_process_loop(process_stop_event))
        
        # Wait until shutdown
        await self.shutdown_event.wait()
        
        # Signal stop to both loops
        crawl_stop_event.set()
        process_stop_event.set()
        
        # Wait for tasks to complete
        await asyncio.gather(crawl_task, process_task, return_exceptions=True)
    
    async def _run_crawl_loop(self, stop_event):
        """Run the crawl snapshot loop to pick up pending transactions"""
        try:
            logger.info("Starting consensus crawl snapshot loop...")
            # _crawl_snapshot is an infinite loop, so we just call it once as a task
            await self.consensus._crawl_snapshot(
                chain_snapshot_factory=chain_snapshot_factory,
                transactions_processor_factory=transactions_processor_factory,
                stop_event=stop_event
            )
        except Exception as e:
            logger.error(f"Crawl loop terminated with error: {e}", exc_info=True)
    
    async def _run_process_loop(self, stop_event):
        """Run the process pending transactions loop to execute transactions"""
        try:
            logger.info("Starting transaction processing loop")
            # Log queue status periodically
            async def log_status():
                while not stop_event.is_set():
                    await asyncio.sleep(5)
                    queue_info = {}
                    for addr, queue in self.consensus.pending_queues.items():
                        queue_info[addr] = queue.qsize()
                    if queue_info:
                        logger.info(f"Queue status: {queue_info}")
                        logger.info(f"Tasks running: {self.consensus.pending_queue_task_running}")
            
            # Start status logger
            asyncio.create_task(log_status())
            
            await self.consensus.run_process_pending_transactions_loop(
                chain_snapshot_factory=chain_snapshot_factory,
                transactions_processor_factory=transactions_processor_factory,
                accounts_manager_factory=accounts_manager_factory,
                contract_snapshot_factory=contract_snapshot_factory,
                contract_processor_factory=contract_processor_factory,
                node_factory=None,  # Not used since we execute on workers
                stop_event=stop_event
            )
        except Exception as e:
            logger.error(f"Error in process loop: {e}", exc_info=True)
    
    async def execute_on_worker(self, task_type: str, task_data: dict) -> dict:
        """Send execution task to worker and wait for result"""
        task_id = f"{task_data.get('tx_hash', 'unknown')}_{time.time()}"
        
        logger.info(f"[BROKER-EXEC] execute_on_worker called: type={task_type}, task_id={task_id}")
        logger.info(f"[BROKER-EXEC] Active workers: {list(self.worker_heartbeats.keys())}")
        
        if not self.worker_heartbeats:
            logger.error(f"[BROKER-EXEC] CRITICAL: No workers connected!")
            raise Exception("No workers available")
        
        # Create a future to wait for the result
        result_future = asyncio.Future()
        self.pending_executions[task_id] = result_future
        
        logger.info(f"[BROKER-EXEC] Sending task {task_id} to workers via PUSH socket")
        
        # Send task to worker
        await self.backend.send_json({
            'task_id': task_id,
            'type': task_type,
            **task_data
        })
        
        logger.info(f"[BROKER-EXEC] Task sent, waiting for result...")
        
        # Wait for result with timeout
        try:
            result = await asyncio.wait_for(result_future, timeout=60)
            logger.info(f"[BROKER-EXEC] Got result for {task_id}: success")
            return result
        except asyncio.TimeoutError:
            logger.error(f"[BROKER-EXEC] Task {task_id} timed out after 60s")
            raise Exception(f"Worker execution timed out for task {task_id}")
        finally:
            # Clean up
            if task_id in self.pending_executions:
                del self.pending_executions[task_id]

def run_socketio_server(broker):
    """Run Flask-SocketIO server in a thread"""
    broker.socketio.run(broker.app, host='0.0.0.0', port=5561, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

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