# backend/workers/zmq_consensus_worker.py
import zmq.asyncio
import asyncio
import logging
import signal
import os
import sys
import json
import time
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

# Import backend modules
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.db_config import get_db_url
from backend.services.state_manager import DistributedStateManager
from backend.node.base import Node
from backend.node.types import ExecutionMode, Receipt, Vote, ExecutionResultStatus
from backend.consensus.vrf import get_validators_for_transaction
from backend.consensus.utils import determine_consensus_from_votes
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.domain.types import Transaction, TransactionType

# Configuration
WORKER_ID = sys.argv[1] if len(sys.argv) > 1 else f"worker-{os.getpid()}"
MAX_GENVM_PER_WORKER = int(os.getenv("MAX_GENVM_PER_WORKER", 5))
WEBDRIVER_HOST = os.getenv("WEBDRIVERHOST", "webdriver")
WEBDRIVER_PORT = os.getenv("WEBDRIVERPORT", "4444")
ZMQ_BROKER_HOST = os.getenv("ZMQ_BROKER_HOST", "zmq-broker")
ZMQ_PULL_URL = f"tcp://{ZMQ_BROKER_HOST}:5558"
ZMQ_CONTROL_URL = f"tcp://{ZMQ_BROKER_HOST}:5560"

# Logging Setup
logging.basicConfig(
    level=logging.INFO, 
    format=f'%(asctime)s - {WORKER_ID} - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@dataclass
class WebDriverConnection:
    """Represents a WebDriver connection"""
    id: str
    url: str
    in_use: bool = False
    last_used: float = 0


class WebDriverPool:
    """Manages a pool of WebDriver connections for GenVM execution"""
    
    def __init__(self, max_size: int):
        self.max_size = max_size
        self.available = asyncio.Queue(maxsize=max_size)
        self.all_connections: List[WebDriverConnection] = []
        self.lock = asyncio.Lock()
        
    async def initialize(self):
        """Create initial WebDriver connections"""
        for i in range(self.max_size):
            conn = WebDriverConnection(
                id=f"{WORKER_ID}-driver-{i}",
                url=f"http://{WEBDRIVER_HOST}:{WEBDRIVER_PORT}"
            )
            self.all_connections.append(conn)
            await self.available.put(conn)
        logger.info(f"Initialized WebDriver pool with {self.max_size} connections")
    
    async def acquire(self) -> WebDriverConnection:
        """Get a WebDriver connection from the pool"""
        conn = await self.available.get()
        conn.in_use = True
        conn.last_used = time.time()
        return conn
    
    async def release(self, conn: WebDriverConnection):
        """Return a WebDriver connection to the pool"""
        conn.in_use = False
        await self.available.put(conn)
    
    async def cleanup(self):
        """Cleanup all connections"""
        logger.info("Cleaning up WebDriver pool")


class ZeroMQConsensusWorker:
    """Worker that processes consensus tasks from ZeroMQ queue"""
    
    def __init__(self, worker_id: str):
        self.worker_id = worker_id
        self.context = zmq.asyncio.Context()
        self.receiver = self.context.socket(zmq.PULL)
        self.feedback = self.context.socket(zmq.DEALER)
        self.feedback.identity = self.worker_id.encode()
        self.active_tasks = set()
        self.shutdown_event = asyncio.Event()
        self.webdriver_pool = WebDriverPool(MAX_GENVM_PER_WORKER)
        self.contract_snapshots: Dict[str, ContractSnapshot] = {}
        
        # Database setup
        self.db_url = get_db_url()
        self.engine = create_engine(
            self.db_url,
            poolclass=NullPool,  # Don't pool connections in worker
            echo=False
        )
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # State manager for distributed caching
        self.state_manager = DistributedStateManager(worker_id=worker_id)
        
        # Message handler for events
        self.msg_handler = MessageHandler()
        
        logger.info(f"Worker {worker_id} initialized")
    
    async def connect_with_retry(self):
        """Connect to broker with exponential backoff."""
        for attempt in range(5):
            try:
                self.receiver.connect(ZMQ_PULL_URL)
                self.feedback.connect(ZMQ_CONTROL_URL)
                logger.info(f"Successfully connected to broker at {ZMQ_BROKER_HOST}")
                return
            except Exception as e:
                wait = 2 ** attempt
                logger.warning(f"Connection failed, retrying in {wait}s: {e}")
                await asyncio.sleep(wait)
        raise Exception("Failed to connect to broker after 5 attempts")
    
    async def run(self):
        """Main worker loop"""
        await self.connect_with_retry()
        await self.webdriver_pool.initialize()
        asyncio.create_task(self._heartbeat_loop())
        
        logger.info(f"Worker {self.worker_id} started processing")
        
        while not self.shutdown_event.is_set():
            # Check if we have capacity for more tasks
            if len(self.active_tasks) >= MAX_GENVM_PER_WORKER:
                await asyncio.sleep(0.1)
                continue
            
            try:
                # Poll for new messages with timeout
                if await self.receiver.poll(timeout=1000):
                    message = await self.receiver.recv_json()
                    task = asyncio.create_task(self._process_transaction(message))
                    self.active_tasks.add(task)
                    task.add_done_callback(self.active_tasks.discard)
            except zmq.error.Again:
                continue
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
    
    async def _process_transaction(self, message: dict):
        """Process transaction with full consensus flow"""
        tx_hash = message['tx_hash']
        contract_address = message['contract_address']
        consensus_mode = message.get('consensus_mode', 'leader')
        
        logger.info(f"Processing {consensus_mode} transaction {tx_hash}")
        
        # Inform broker we're processing
        await self._send_feedback({
            'status': 'PROCESSING',
            'tx_hash': tx_hash,
            'contract_address': contract_address,
            'consensus_mode': consensus_mode
        })
        
        try:
            # Get contract snapshot
            with self.SessionLocal() as session:
                contract_snapshot = self._get_or_create_snapshot(session, contract_address)
            
            # Acquire WebDriver for GenVM execution
            driver_conn = await self.webdriver_pool.acquire()
            
            try:
                if consensus_mode == 'leader':
                    # Execute as leader
                    result = await self._execute_leader_transaction(
                        contract_snapshot, 
                        message['transaction_data'],
                        driver_conn
                    )
                    
                elif consensus_mode == 'validator':
                    # Execute as validator
                    leader_receipt = message.get('leader_receipt')
                    validator_info = message.get('validator_info')
                    
                    result = await self._execute_validator_transaction(
                        contract_snapshot,
                        message['transaction_data'],
                        leader_receipt,
                        validator_info,
                        driver_conn
                    )
                    
                    # Send vote to broker
                    await self._send_feedback({
                        'status': 'CONSENSUS_VOTE',
                        'tx_hash': tx_hash,
                        'validator_id': validator_info.get('id', 'unknown'),
                        'vote': result
                    })
                
                else:
                    result = {'error': f'Unknown consensus mode: {consensus_mode}'}
                
                # Store final result in database
                await self._store_transaction_result(tx_hash, contract_address, result)
                
                await self._send_feedback({
                    'status': 'SUCCESS',
                    'tx_hash': tx_hash,
                    'contract_address': contract_address,
                    'result': result
                })
                
            finally:
                await self.webdriver_pool.release(driver_conn)
                
        except Exception as e:
            logger.error(f"Error processing {tx_hash}: {e}", exc_info=True)
            await self._send_feedback({
                'status': 'FAILED',
                'tx_hash': tx_hash,
                'contract_address': contract_address,
                'transaction_data': message.get('transaction_data'),
                'error': str(e),
                'retryable': self._is_retryable_error(e),
                'retry_count': message.get('retry_count', 0),
                'consensus_mode': consensus_mode
            })
    
    async def _get_or_create_snapshot(self, session: Session, contract_address: str) -> ContractSnapshot:
        """Get or create contract snapshot with distributed caching"""
        try:
            # Try to get from state manager (includes Redis cache)
            snapshot = await self.state_manager.get_contract_snapshot(session, contract_address)
            
            # Keep local reference for this worker
            self.contract_snapshots[contract_address] = snapshot
            return snapshot
        except Exception as e:
            logger.error(f"Error getting contract snapshot: {e}")
            # Fallback to direct creation
            snapshot = ContractSnapshot(session, contract_address)
            self.contract_snapshots[contract_address] = snapshot
            return snapshot
    
    async def _execute_leader_transaction(self, 
                                         contract_snapshot: ContractSnapshot,
                                         tx_data: dict,
                                         driver_conn: WebDriverConnection) -> dict:
        """Execute transaction as leader"""
        try:
            # Create transaction object
            transaction = self._create_transaction_from_data(tx_data)
            
            # Create and execute node
            node = Node(
                contract_snapshot=contract_snapshot,
                validator_mode=ExecutionMode.LEADER,
                leader_receipt=None,
                msg_handler=self.msg_handler,
                validator=None,
                contract_snapshot_factory=lambda addr: self._get_or_create_snapshot(
                    self.SessionLocal(), addr
                ),
                validators_manager=None,
                web_path=driver_conn.url
            )
            
            # Execute transaction
            receipt = await node.exec_transaction(transaction)
            
            return {
                'receipt': receipt.to_dict() if hasattr(receipt, 'to_dict') else str(receipt),
                'mode': 'leader',
                'worker': self.worker_id
            }
            
        except Exception as e:
            logger.error(f"Leader execution failed: {e}")
            raise
    
    async def _execute_validator_transaction(self,
                                            contract_snapshot: ContractSnapshot,
                                            tx_data: dict,
                                            leader_receipt: dict,
                                            validator_info: dict,
                                            driver_conn: WebDriverConnection) -> dict:
        """Execute transaction as validator"""
        try:
            # Create transaction object
            transaction = self._create_transaction_from_data(tx_data)
            
            # Convert leader receipt
            leader_receipt_obj = Receipt(**leader_receipt) if leader_receipt else None
            
            # Create and execute node
            node = Node(
                contract_snapshot=contract_snapshot,
                validator_mode=ExecutionMode.VALIDATOR,
                leader_receipt=leader_receipt_obj,
                msg_handler=self.msg_handler,
                validator=validator_info,
                contract_snapshot_factory=lambda addr: self._get_or_create_snapshot(
                    self.SessionLocal(), addr
                ),
                validators_manager=None,
                web_path=driver_conn.url
            )
            
            # Execute transaction
            vote = await node.exec_transaction(transaction)
            
            return {
                'vote': vote.to_dict() if hasattr(vote, 'to_dict') else str(vote),
                'mode': 'validator',
                'validator_id': validator_info.get('id'),
                'worker': self.worker_id
            }
            
        except Exception as e:
            logger.error(f"Validator execution failed: {e}")
            raise
    
    def _create_transaction_from_data(self, tx_data: dict) -> Transaction:
        """Create Transaction object from dictionary data"""
        return Transaction(
            from_address=tx_data.get('from_address'),
            to_address=tx_data.get('to_address'),
            input_data=tx_data.get('input_data'),
            value=tx_data.get('value', 0),
            type=TransactionType[tx_data.get('type', 'SEND')],
            timestamp=tx_data.get('timestamp', time.time()),
            gaslimit=tx_data.get('gaslimit', 100000000)
        )
    
    async def _store_transaction_result(self, tx_hash: str, contract_address: str, result: dict):
        """Store transaction result in database"""
        try:
            with self.SessionLocal() as session:
                session.execute(
                    text("""
                        INSERT INTO transaction_results 
                        (tx_hash, contract_address, result, processed_at)
                        VALUES (:tx_hash, :contract_address, :result, NOW())
                        ON CONFLICT (tx_hash) DO UPDATE
                        SET result = :result, processed_at = NOW()
                    """),
                    {
                        'tx_hash': tx_hash,
                        'contract_address': contract_address,
                        'result': json.dumps(result)
                    }
                )
                session.commit()
                logger.debug(f"Stored result for transaction {tx_hash}")
        except Exception as e:
            logger.error(f"Failed to store transaction result: {e}")
    
    def _is_retryable_error(self, error: Exception) -> bool:
        """Determine if an error is retryable"""
        # Network errors, timeout errors are retryable
        retryable_errors = (
            ConnectionError,
            TimeoutError,
            zmq.error.Again,
        )
        return isinstance(error, retryable_errors)
    
    async def _heartbeat_loop(self):
        """Send periodic heartbeats to broker"""
        while not self.shutdown_event.is_set():
            try:
                await self._send_feedback({'status': 'HEARTBEAT'})
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error sending heartbeat: {e}")
    
    async def _send_feedback(self, message: dict):
        """Send feedback message to broker"""
        message['worker_id'] = self.worker_id
        message['timestamp'] = time.time()
        try:
            await self.feedback.send_json(message)
        except Exception as e:
            logger.error(f"Failed to send feedback: {e}")
    
    async def shutdown(self):
        """Graceful shutdown"""
        if self.shutdown_event.is_set():
            return
        
        logger.info("Shutdown initiated. Waiting for active tasks to complete...")
        self.shutdown_event.set()
        
        if self.active_tasks:
            await asyncio.gather(*self.active_tasks, return_exceptions=True)
        
        await self.webdriver_pool.cleanup()
        self.context.term()
        logger.info("Worker has shut down gracefully.")


async def main():
    worker = ZeroMQConsensusWorker(WORKER_ID)
    
    # Setup signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(worker.shutdown()))
    
    try:
        await worker.run()
    finally:
        if not worker.context.closed:
            worker.context.term()


if __name__ == "__main__":
    asyncio.run(main())