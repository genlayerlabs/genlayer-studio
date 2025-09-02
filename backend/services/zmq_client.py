# backend/services/zmq_client.py
import zmq
import zmq.asyncio
import json
import logging
import os
from typing import Optional, Dict, Any
from backend.domain.types import Transaction

logger = logging.getLogger(__name__)

class ZeroMQClient:
    """Client for sending transactions to ZeroMQ broker"""
    
    def __init__(self, broker_host: Optional[str] = None):
        """Initialize ZeroMQ client
        
        Args:
            broker_host: Host address of the ZeroMQ broker. 
                        Defaults to environment variable or 'localhost'
        """
        self.broker_host = broker_host or os.getenv('ZMQ_BROKER_HOST', 'localhost')
        self.context = None
        self.sender = None
        self.connected = False
        self._connect()
    
    def _connect(self):
        """Establish connection to ZeroMQ broker"""
        try:
            self.context = zmq.Context()
            self.sender = self.context.socket(zmq.PUSH)
            self.sender.setsockopt(zmq.LINGER, 1000)  # Wait up to 1 second on close
            self.sender.setsockopt(zmq.SNDHWM, 1000)  # High water mark for sending
            
            url = f"tcp://{self.broker_host}:5557"
            self.sender.connect(url)
            self.connected = True
            logger.info(f"Connected to ZeroMQ broker at {url}")
        except Exception as e:
            logger.error(f"Failed to connect to ZeroMQ broker: {e}")
            self.connected = False
    
    async def queue_transaction(self, transaction: Transaction, consensus_mode: str = 'leader') -> bool:
        """Queue transaction via ZeroMQ
        
        Args:
            transaction: Transaction to queue
            consensus_mode: Mode of consensus ('leader', 'validator', 'rollup')
            
        Returns:
            True if successfully queued, False otherwise
        """
        if not self.connected:
            logger.warning("ZeroMQ client not connected")
            return False
        
        try:
            message = {
                'tx_hash': transaction.hash,
                'contract_address': transaction.contract_address,
                'transaction_data': {
                    'from_address': transaction.from_address,
                    'to_address': transaction.to_address,
                    'input_data': transaction.input_data,
                    'value': transaction.value,
                    'type': transaction.type.value if hasattr(transaction.type, 'value') else str(transaction.type),
                    'timestamp': transaction.timestamp,
                    'gaslimit': getattr(transaction, 'gaslimit', 100000000),
                },
                'consensus_mode': consensus_mode
            }
            
            # Send without blocking
            self.sender.send_json(message, zmq.NOBLOCK)
            logger.debug(f"Queued transaction {transaction.hash} via ZeroMQ")
            return True
            
        except zmq.Again:
            logger.warning(f"ZeroMQ queue full, could not queue transaction {transaction.hash}")
            return False
        except Exception as e:
            logger.error(f"Failed to queue transaction {transaction.hash}: {e}")
            return False
    
    async def queue_validator_task(self, 
                                  transaction: Transaction,
                                  validator_info: Dict[str, Any],
                                  leader_receipt: Dict[str, Any]) -> bool:
        """Queue a validator task for consensus
        
        Args:
            transaction: Transaction to validate
            validator_info: Information about the validator
            leader_receipt: Receipt from leader execution
            
        Returns:
            True if successfully queued, False otherwise
        """
        if not self.connected:
            logger.warning("ZeroMQ client not connected")
            return False
        
        try:
            message = {
                'tx_hash': transaction.hash,
                'contract_address': transaction.contract_address,
                'transaction_data': {
                    'from_address': transaction.from_address,
                    'to_address': transaction.to_address,
                    'input_data': transaction.input_data,
                    'value': transaction.value,
                    'type': transaction.type.value if hasattr(transaction.type, 'value') else str(transaction.type),
                    'timestamp': transaction.timestamp,
                    'gaslimit': getattr(transaction, 'gaslimit', 100000000),
                },
                'consensus_mode': 'validator',
                'validator_info': validator_info,
                'leader_receipt': leader_receipt
            }
            
            self.sender.send_json(message, zmq.NOBLOCK)
            logger.debug(f"Queued validator task for transaction {transaction.hash}")
            return True
            
        except zmq.Again:
            logger.warning(f"ZeroMQ queue full, could not queue validator task")
            return False
        except Exception as e:
            logger.error(f"Failed to queue validator task: {e}")
            return False
    
    def close(self):
        """Close ZeroMQ connection"""
        if self.sender:
            self.sender.close()
        if self.context:
            self.context.term()
        self.connected = False
        logger.info("ZeroMQ client connection closed")
    
    def __del__(self):
        """Cleanup on deletion"""
        self.close()


class AsyncZeroMQClient:
    """Async version of ZeroMQ client for asyncio environments"""
    
    def __init__(self, broker_host: Optional[str] = None):
        """Initialize async ZeroMQ client
        
        Args:
            broker_host: Host address of the ZeroMQ broker
        """
        self.broker_host = broker_host or os.getenv('ZMQ_BROKER_HOST', 'localhost')
        self.context = None
        self.sender = None
        self.connected = False
    
    async def connect(self):
        """Establish async connection to ZeroMQ broker"""
        try:
            self.context = zmq.asyncio.Context()
            self.sender = self.context.socket(zmq.PUSH)
            self.sender.setsockopt(zmq.LINGER, 1000)
            self.sender.setsockopt(zmq.SNDHWM, 1000)
            
            url = f"tcp://{self.broker_host}:5557"
            self.sender.connect(url)
            self.connected = True
            logger.info(f"Async client connected to ZeroMQ broker at {url}")
        except Exception as e:
            logger.error(f"Failed to connect async client to ZeroMQ broker: {e}")
            self.connected = False
    
    async def queue_transaction(self, transaction: Transaction, consensus_mode: str = 'leader') -> bool:
        """Queue transaction via ZeroMQ asynchronously
        
        Args:
            transaction: Transaction to queue
            consensus_mode: Mode of consensus
            
        Returns:
            True if successfully queued, False otherwise
        """
        if not self.connected:
            await self.connect()
            if not self.connected:
                return False
        
        try:
            message = {
                'tx_hash': transaction.hash,
                'contract_address': transaction.contract_address,
                'transaction_data': {
                    'from_address': transaction.from_address,
                    'to_address': transaction.to_address,
                    'input_data': transaction.input_data,
                    'value': transaction.value,
                    'type': transaction.type.value if hasattr(transaction.type, 'value') else str(transaction.type),
                    'timestamp': transaction.timestamp,
                    'gaslimit': getattr(transaction, 'gaslimit', 100000000),
                },
                'consensus_mode': consensus_mode
            }
            
            await self.sender.send_json(message)
            logger.debug(f"Async queued transaction {transaction.hash} via ZeroMQ")
            return True
            
        except Exception as e:
            logger.error(f"Failed to async queue transaction {transaction.hash}: {e}")
            return False
    
    async def close(self):
        """Close async ZeroMQ connection"""
        if self.sender:
            self.sender.close()
        if self.context:
            self.context.term()
        self.connected = False
        logger.info("Async ZeroMQ client connection closed")
    
    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()