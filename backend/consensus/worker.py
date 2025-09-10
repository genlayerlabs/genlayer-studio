# backend/consensus/worker.py

import os
import asyncio
import time
import traceback
import threading
import uuid
from typing import Callable, Optional
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database_handler.models import Transactions, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.accounts_manager import AccountsManager
from backend.domain.types import Transaction
from backend.consensus.base import ConsensusAlgorithm
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.rollup.consensus_service import ConsensusService
import backend.validators as validators


class ConsensusWorker:
    """
    Worker class for distributed consensus processing.
    Each worker claims transactions from the database and processes them independently.
    This reuses the exec_transaction logic from ConsensusAlgorithm.
    """
    
    def __init__(
        self,
        get_session: Callable[[], Session],
        msg_handler: MessageHandler,
        consensus_service: ConsensusService,
        validators_manager: validators.Manager,
        worker_id: str = None,
        poll_interval: int = 5,
        transaction_timeout_minutes: int = 30,
    ):
        """
        Initialize the consensus worker.
        
        Args:
            get_session: Function to get a database session
            msg_handler: Message handler for events
            consensus_service: Consensus service for rollup interaction
            validators_manager: Validators manager
            worker_id: Unique identifier for this worker (auto-generated if not provided)
            poll_interval: Seconds to wait between polls when no work available
            transaction_timeout_minutes: Minutes before a stuck transaction is recovered
        """
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.get_session = get_session
        self.msg_handler = msg_handler
        self.consensus_service = consensus_service
        self.validators_manager = validators_manager
        self.poll_interval = poll_interval
        self.transaction_timeout_minutes = transaction_timeout_minutes
        self.running = True
        
        # Create a ConsensusAlgorithm instance to reuse its exec_transaction method
        self.consensus_algorithm = ConsensusAlgorithm(
            get_session,
            msg_handler,
            consensus_service,
            validators_manager,
        )
        
    async def claim_next_finalization(self, session: Session) -> Optional[dict]:
        """
        Claim the next transaction that needs finalization (appeal window expired).
        
        Returns:
            Transaction data dict if claimed, None otherwise
        """
        # Query for transactions that are ready for finalization
        # They must be in ACCEPTED/UNDETERMINED/TIMEOUT states and appeal window must have passed
        query = text("""
            WITH ready_for_finalization AS (
                SELECT t.*, ROW_NUMBER() OVER (
                    PARTITION BY t.to_address 
                    ORDER BY t.created_at ASC
                ) as rn
                FROM transactions t
                WHERE t.status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
                    AND t.appealed = false
                    AND t.timestamp_awaiting_finalization IS NOT NULL
                    AND (t.blocked_at IS NULL 
                         OR t.blocked_at < NOW() - INTERVAL :timeout)
                    AND NOT EXISTS (
                        -- Ensure no other transaction for same contract is being processed
                        SELECT 1 FROM transactions t2 
                        WHERE t2.to_address = t.to_address 
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - INTERVAL :timeout
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM ready_for_finalization
            WHERE transactions.hash = ready_for_finalization.hash
                AND ready_for_finalization.rn = 1
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at, transactions.timestamp_awaiting_finalization,
                      transactions.appeal_failed
            LIMIT 1;
        """)
        
        result = session.execute(
            query, 
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes"
            }
        ).first()
        
        if result:
            session.commit()
            # Convert result to dict
            return {
                'hash': result.hash,
                'from_address': result.from_address,
                'to_address': result.to_address,
                'data': result.data,
                'value': result.value,
                'type': result.type,
                'nonce': result.nonce,
                'gaslimit': result.gaslimit,
                'r': result.r,
                's': result.s,
                'v': result.v,
                'leader_only': result.leader_only,
                'sim_config': result.sim_config,
                'contract_snapshot': result.contract_snapshot,
                'status': result.status,
                'consensus_data': result.consensus_data,
                'input_data': result.input_data,
                'created_at': result.created_at,
                'timestamp_awaiting_finalization': result.timestamp_awaiting_finalization,
                'appeal_failed': result.appeal_failed,
            }
        
        return None
    
    async def claim_next_appeal(self, session: Session) -> Optional[dict]:
        """
        Claim the next available appealed transaction for processing.
        Uses FOR UPDATE SKIP LOCKED to ensure only one worker claims an appeal.
        
        Returns:
            Transaction data dict if claimed, None otherwise
        """
        # Query to atomically claim an appealed transaction
        query = text("""
            WITH available_appeals AS (
                SELECT t.*, ROW_NUMBER() OVER (
                    PARTITION BY t.to_address 
                    ORDER BY t.created_at ASC
                ) as rn
                FROM transactions t
                WHERE t.appealed = true
                    AND t.status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
                    AND (t.blocked_at IS NULL 
                         OR t.blocked_at < NOW() - INTERVAL :timeout)
                    AND NOT EXISTS (
                        -- Ensure no other appeal for same contract is being processed
                        SELECT 1 FROM transactions t2 
                        WHERE t2.to_address = t.to_address 
                            AND t2.appealed = true
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - INTERVAL :timeout
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM available_appeals
            WHERE transactions.hash = available_appeals.hash
                AND available_appeals.rn = 1
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at, transactions.appealed, transactions.appeal_failed,
                      transactions.timestamp_appeal, transactions.appeal_undetermined,
                      transactions.appeal_leader_timeout, transactions.appeal_validators_timeout
            LIMIT 1;
        """)
        
        result = session.execute(
            query, 
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes"
            }
        ).first()
        
        if result:
            session.commit()
            # Convert result to dict
            return {
                'hash': result.hash,
                'from_address': result.from_address,
                'to_address': result.to_address,
                'data': result.data,
                'value': result.value,
                'type': result.type,
                'nonce': result.nonce,
                'gaslimit': result.gaslimit,
                'r': result.r,
                's': result.s,
                'v': result.v,
                'leader_only': result.leader_only,
                'sim_config': result.sim_config,
                'contract_snapshot': result.contract_snapshot,
                'status': result.status,
                'consensus_data': result.consensus_data,
                'input_data': result.input_data,
                'created_at': result.created_at,
                'appealed': result.appealed,
                'appeal_failed': result.appeal_failed,
                'timestamp_appeal': result.timestamp_appeal,
                'appeal_undetermined': result.appeal_undetermined,
                'appeal_leader_timeout': result.appeal_leader_timeout,
                'appeal_validators_timeout': result.appeal_validators_timeout,
            }
        
        return None
    
    async def claim_next_transaction(self, session: Session) -> Optional[dict]:
        """
        Claim the next available transaction for processing.
        Uses FOR UPDATE SKIP LOCKED to ensure only one worker claims a transaction.
        
        Returns:
            Transaction data dict if claimed, None otherwise
        """
        # Query to atomically claim a transaction
        # Ensures only one transaction per contract is processed at a time
        query = text("""
            WITH available_transactions AS (
                SELECT t.*, ROW_NUMBER() OVER (
                    PARTITION BY t.to_address 
                    ORDER BY t.created_at ASC
                ) as rn
                FROM transactions t
                WHERE t.status IN ('PENDING', 'ACTIVATED')
                    AND (t.blocked_at IS NULL 
                         OR t.blocked_at < NOW() - INTERVAL :timeout)
                    AND NOT EXISTS (
                        -- Ensure no other transaction for same contract is being processed
                        SELECT 1 FROM transactions t2 
                        WHERE t2.to_address = t.to_address 
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - INTERVAL :timeout
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM available_transactions
            WHERE transactions.hash = available_transactions.hash
                AND available_transactions.rn = 1
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at
            LIMIT 1;
        """)
        
        result = session.execute(
            query, 
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes"
            }
        ).first()
        
        if result:
            session.commit()
            # Convert result to dict
            return {
                'hash': result.hash,
                'from_address': result.from_address,
                'to_address': result.to_address,
                'data': result.data,
                'value': result.value,
                'type': result.type,
                'nonce': result.nonce,
                'gaslimit': result.gaslimit,
                'r': result.r,
                's': result.s,
                'v': result.v,
                'leader_only': result.leader_only,
                'sim_config': result.sim_config,
                'contract_snapshot': result.contract_snapshot,
                'status': result.status,
                'consensus_data': result.consensus_data,
                'input_data': result.input_data,
                'created_at': result.created_at,
            }
        
        return None
    
    def release_transaction(self, session: Session, transaction_hash: str):
        """
        Release a transaction by clearing its blocked_at and worker_id.
        
        Args:
            session: Database session
            transaction_hash: Hash of the transaction to release
        """
        update_query = text("""
            UPDATE transactions
            SET blocked_at = NULL,
                worker_id = NULL
            WHERE hash = :hash
        """)
        session.execute(update_query, {"hash": transaction_hash})
        session.commit()
    
    async def recover_stuck_transactions(self, session: Session) -> int:
        """
        Recover transactions that have been stuck for too long.
        Resets them back to PENDING status and clears blocking fields.
        
        Returns:
            Number of transactions recovered
        """
        recovery_query = text("""
            UPDATE transactions
            SET blocked_at = NULL,
                worker_id = NULL,
                status = 'PENDING'
            WHERE blocked_at IS NOT NULL
                AND blocked_at < NOW() - INTERVAL :timeout
                AND status NOT IN ('FINALIZED', 'CANCELED')
            RETURNING hash, status;
        """)
        
        result = session.execute(
            recovery_query,
            {"timeout": f"{self.transaction_timeout_minutes} minutes"}
        )
        
        recovered = result.fetchall()
        if recovered:
            session.commit()
            for row in recovered:
                print(f"[Worker {self.worker_id}] Recovered stuck transaction {row.hash} (was {row.status}, now PENDING)")
        
        return len(recovered)
    
    async def process_transaction(self, transaction_data: dict, session: Session):
        """
        Process a single transaction through consensus.
        Reuses the exec_transaction logic from ConsensusAlgorithm.
        
        Args:
            transaction_data: Transaction data dictionary
            session: Database session
        """
        try:
            # Convert to Transaction domain object
            transaction = Transaction.from_dict(transaction_data)
            
            # Import factories here to avoid circular imports
            from backend.consensus.base import (
                contract_snapshot_factory,
                contract_processor_factory,
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                node_factory,
            )
            
            # Get or create validators snapshot based on sim_config
            virtual_validators = []
            if transaction.sim_config and transaction.sim_config.validators:
                # Handle virtual validators for simulation
                for validator in transaction.sim_config.validators:
                    from backend.node.create_nodes.providers import (
                        get_default_provider_for,
                        validate_provider,
                    )
                    from backend.domain.types import LLMProvider, Validator
                    
                    provider = validator.provider
                    model = validator.model
                    config = validator.config
                    plugin = validator.plugin
                    plugin_config = validator.plugin_config
                    
                    if config is None or plugin is None or plugin_config is None:
                        llm_provider = get_default_provider_for(provider, model)
                    else:
                        llm_provider = LLMProvider(
                            provider=provider,
                            model=model,
                            config=config,
                            plugin=plugin,
                            plugin_config=plugin_config,
                        )
                        validate_provider(llm_provider)
                    
                    account = accounts_manager_factory(session).create_new_account()
                    virtual_validators.append(
                        Validator(
                            address=account.address,
                            private_key=account.key.to_0x_hex(),
                            stake=validator.stake,
                            llmprovider=llm_provider,
                        )
                    )
            
            # Use appropriate validators snapshot
            if virtual_validators:
                async with self.validators_manager.temporal_snapshot(
                    virtual_validators
                ) as validators_snapshot:
                    await self.consensus_algorithm.exec_transaction(
                        transaction,
                        transactions_processor_factory(session),
                        chain_snapshot_factory(session),
                        accounts_manager_factory(session),
                        lambda contract_address: contract_snapshot_factory(
                            contract_address, session, transaction
                        ),
                        contract_processor_factory(session),
                        node_factory,
                        validators_snapshot,
                    )
            else:
                async with self.validators_manager.snapshot() as validators_snapshot:
                    await self.consensus_algorithm.exec_transaction(
                        transaction,
                        transactions_processor_factory(session),
                        chain_snapshot_factory(session),
                        accounts_manager_factory(session),
                        lambda contract_address: contract_snapshot_factory(
                            contract_address, session, transaction
                        ),
                        contract_processor_factory(session),
                        node_factory,
                        validators_snapshot,
                    )
            
            session.commit()
            print(f"[Worker {self.worker_id}] Successfully processed transaction {transaction.hash}")
            
        except Exception as e:
            print(f"[Worker {self.worker_id}] Error processing transaction {transaction_data['hash']}: {e}")
            traceback.print_exc()
            session.rollback()
        finally:
            # Always release the transaction when done
            self.release_transaction(session, transaction_data['hash'])
    
    async def process_finalization(self, finalization_data: dict, session: Session):
        """
        Process a transaction that's ready for finalization.
        
        Args:
            finalization_data: Transaction data dictionary
            session: Database session
        """
        try:
            # Convert to Transaction domain object
            transaction = Transaction.from_dict(finalization_data)
            
            # Check if the appeal window has passed
            from backend.consensus.base import (
                contract_snapshot_factory,
                contract_processor_factory,
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                node_factory,
            )
            
            transactions_processor = transactions_processor_factory(session)
            
            # Check if can finalize (appeal window expired)
            if self.consensus_algorithm.can_finalize_transaction(
                transactions_processor,
                transaction,
                0,  # index in queue (not used in our case)
                [finalization_data]  # mock queue with just this transaction
            ):
                print(f"[Worker {self.worker_id}] Finalizing transaction {transaction.hash}")
                
                await self.consensus_algorithm.process_finalization(
                    transaction,
                    transactions_processor,
                    chain_snapshot_factory(session),
                    accounts_manager_factory(session),
                    lambda contract_address: contract_snapshot_factory(
                        contract_address, session, transaction
                    ),
                    contract_processor_factory(session),
                    node_factory,
                )
                
                session.commit()
                print(f"[Worker {self.worker_id}] Successfully finalized transaction {transaction.hash}")
            else:
                print(f"[Worker {self.worker_id}] Transaction {transaction.hash} not ready for finalization yet")
                
        except Exception as e:
            print(f"[Worker {self.worker_id}] Error processing finalization {finalization_data['hash']}: {e}")
            traceback.print_exc()
            session.rollback()
        finally:
            # Always release the transaction when done
            self.release_transaction(session, finalization_data['hash'])
    
    async def process_appeal(self, appeal_data: dict, session: Session):
        """
        Process an appealed transaction through the appeal logic.
        
        Args:
            appeal_data: Appeal transaction data dictionary
            session: Database session
        """
        try:
            # Convert to Transaction domain object
            transaction = Transaction.from_dict(appeal_data)
            
            print(f"[Worker {self.worker_id}] Processing appeal for transaction {transaction.hash} with status {appeal_data['status']}")
            
            # Import factories
            from backend.consensus.base import (
                contract_snapshot_factory,
                contract_processor_factory,
                chain_snapshot_factory,
                transactions_processor_factory,
                accounts_manager_factory,
                node_factory,
            )
            
            # Process the appeal based on status
            transactions_processor = transactions_processor_factory(session)
            chain_snapshot = chain_snapshot_factory(session)
            accounts_manager = accounts_manager_factory(session)
            
            async with self.validators_manager.snapshot() as validators_snapshot:
                if transaction.status == TransactionStatus.UNDETERMINED:
                    # Leader appeal
                    await self.consensus_algorithm.process_leader_appeal(
                        transaction,
                        transactions_processor,
                        chain_snapshot,
                        accounts_manager,
                        lambda contract_address: contract_snapshot_factory(
                            contract_address, session, transaction
                        ),
                        contract_processor_factory(session),
                        node_factory,
                        validators_snapshot,
                    )
                elif transaction.status == TransactionStatus.LEADER_TIMEOUT:
                    # Leader timeout appeal
                    await self.consensus_algorithm.process_leader_timeout_appeal(
                        transaction,
                        transactions_processor,
                        chain_snapshot,
                        accounts_manager,
                        lambda contract_address: contract_snapshot_factory(
                            contract_address, session, transaction
                        ),
                        contract_processor_factory(session),
                        node_factory,
                        validators_snapshot,
                    )
                else:
                    # Validator appeal (ACCEPTED or VALIDATORS_TIMEOUT)
                    await self.consensus_algorithm.process_validator_appeal(
                        transaction,
                        transactions_processor,
                        chain_snapshot,
                        accounts_manager,
                        lambda contract_address: contract_snapshot_factory(
                            contract_address, session, transaction
                        ),
                        contract_processor_factory(session),
                        node_factory,
                        validators_snapshot,
                    )
            
            session.commit()
            print(f"[Worker {self.worker_id}] Successfully processed appeal for transaction {transaction.hash}")
            
        except Exception as e:
            print(f"[Worker {self.worker_id}] Error processing appeal {appeal_data['hash']}: {e}")
            traceback.print_exc()
            session.rollback()
        finally:
            # Always release the transaction when done
            self.release_transaction(session, appeal_data['hash'])
    
    async def run(self):
        """
        Main worker loop that continuously claims and processes transactions, appeals, and finalizations.
        """
        print(f"[Worker {self.worker_id}] Starting consensus worker")
        
        recovery_counter = 0
        
        while self.running:
            try:
                with self.get_session() as session:
                    # Periodically recover stuck transactions (every 12 iterations = ~60 seconds)
                    recovery_counter += 1
                    if recovery_counter >= 12:
                        recovery_counter = 0
                        recovered = await self.recover_stuck_transactions(session)
                        if recovered > 0:
                            print(f"[Worker {self.worker_id}] Recovered {recovered} stuck transactions")
                    
                    # Priority order: appeals > finalizations > regular transactions
                    
                    # Try to claim an appeal first (highest priority)
                    appeal_data = await self.claim_next_appeal(session)
                    
                    if appeal_data:
                        print(f"[Worker {self.worker_id}] Claimed appeal for transaction {appeal_data['hash']}")
                        # Process in a new session
                        with self.get_session() as process_session:
                            await self.process_appeal(appeal_data, process_session)
                    else:
                        # No appeals, try to claim a finalization
                        finalization_data = await self.claim_next_finalization(session)
                        
                        if finalization_data:
                            print(f"[Worker {self.worker_id}] Claimed finalization for transaction {finalization_data['hash']}")
                            # Process in a new session
                            with self.get_session() as process_session:
                                await self.process_finalization(finalization_data, process_session)
                        else:
                            # No finalizations, try to claim a regular transaction
                            transaction_data = await self.claim_next_transaction(session)
                            
                            if transaction_data:
                                print(f"[Worker {self.worker_id}] Claimed transaction {transaction_data['hash']}")
                                # Process in a new session
                                with self.get_session() as process_session:
                                    await self.process_transaction(transaction_data, process_session)
                            else:
                                # No work available, wait before polling again
                                await asyncio.sleep(self.poll_interval)
                            
            except Exception as e:
                print(f"[Worker {self.worker_id}] Error in main loop: {e}")
                traceback.print_exc()
                await asyncio.sleep(self.poll_interval)
    
    def stop(self):
        """Stop the worker gracefully."""
        print(f"[Worker {self.worker_id}] Stopping consensus worker")
        self.running = False