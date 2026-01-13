# backend/consensus/worker.py

import os
import asyncio
import time
import traceback
import threading
import uuid
from typing import Callable, Optional, Any
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import text

from backend.database_handler.models import Transactions, TransactionStatus
from backend.database_handler.transactions_processor import TransactionsProcessor
from backend.database_handler.chain_snapshot import ChainSnapshot
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.database_handler.contract_processor import ContractProcessor
from backend.database_handler.accounts_manager import AccountsManager
from backend.database_handler.errors import ContractNotFoundError
from backend.domain.types import Transaction
from backend.consensus.base import ConsensusAlgorithm
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.rollup.consensus_service import ConsensusService
import backend.validators as validators
from loguru import logger
from backend.node.base import Manager as GenVMManager
from backend.services.usage_metrics_service import UsageMetricsService


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
        genvm_manager: GenVMManager,
        worker_id: str = None,
        poll_interval: int = 5,
        transaction_timeout_minutes: int = 20,
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
        self.current_transaction = None  # Track currently processing transaction

        now_monotonic = time.monotonic()
        self._query_log_interval = 60.0  # seconds
        self._query_log_state = {
            "appeal": {
                "label": "Appeal claim",
                "last_log": now_monotonic,
                "polls": 0,
            },
            "finalization": {
                "label": "Finalization claim",
                "last_log": now_monotonic,
                "polls": 0,
            },
            "transaction": {
                "label": "Transaction claim",
                "last_log": now_monotonic,
                "polls": 0,
            },
        }

        # Create a ConsensusAlgorithm instance to reuse its exec_transaction method
        self.consensus_algorithm = ConsensusAlgorithm(
            get_session,
            msg_handler,
            consensus_service,
            validators_manager,
            genvm_manager,
        )

        # Track retry counts for transactions that failed due to no validators
        # Key: transaction_hash, Value: {"count": int, "last_attempt": float}
        self._no_validators_retries: dict[str, dict] = {}
        self._max_no_validators_retries = int(
            os.environ.get("NO_VALIDATORS_MAX_RETRIES", "5")
        )
        self._no_validators_base_backoff = float(
            os.environ.get("NO_VALIDATORS_BASE_BACKOFF_SECONDS", "30")
        )

        # Initialize usage metrics service for reporting transaction metrics
        self.usage_metrics_service = UsageMetricsService()

    async def claim_next_finalization(self, session: Session) -> Optional[dict]:
        """
        Claim the next transaction that needs finalization (appeal window expired).

        Returns:
            Transaction data dict if claimed, None otherwise
        """
        # Query for transactions that are ready for finalization
        # They must be in ACCEPTED/UNDETERMINED/TIMEOUT states and appeal window must have passed
        start_time = time.perf_counter()
        query = text(
            """
            WITH locked_finalizations AS (
                SELECT t.*
                FROM transactions t
                WHERE t.status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
                    AND t.appealed = false
                    AND t.timestamp_awaiting_finalization IS NOT NULL
                    AND (
                        t.leader_only = true
                        OR (
                            EXTRACT(EPOCH FROM NOW()) - t.timestamp_awaiting_finalization - COALESCE(t.appeal_processing_time, 0)
                        ) > :finality_window_seconds * POWER(1 - :appeal_failed_reduction, COALESCE(t.appeal_failed, 0))
                    )
                    AND (t.blocked_at IS NULL
                         OR t.blocked_at < NOW() - CAST(:timeout AS INTERVAL))
                    AND NOT EXISTS (
                        -- Ensure no other transaction for same contract is being processed
                        SELECT 1 FROM transactions t2
                        WHERE t2.to_address = t.to_address
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - CAST(:timeout AS INTERVAL)
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
                FOR UPDATE SKIP LOCKED
            ),
            ready_for_finalization AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY to_address
                    ORDER BY created_at ASC
                ) as rn
                FROM locked_finalizations
            ),
            single_finalization AS (
                -- FIXED: Limit update to exactly ONE transaction
                SELECT *
                FROM ready_for_finalization
                WHERE rn = 1
                ORDER BY created_at ASC
                LIMIT 1
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM single_finalization
            WHERE transactions.hash = single_finalization.hash
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at, transactions.timestamp_awaiting_finalization,
                      transactions.appeal_failed, transactions.blocked_at;
        """
        )

        result = session.execute(
            query,
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes",
                "finality_window_seconds": self.consensus_algorithm.finality_window_time,
                "appeal_failed_reduction": self.consensus_algorithm.finality_window_appeal_failed_reduction,
            },
        ).first()
        duration = time.perf_counter() - start_time
        self._log_query_result("finalization", result, duration)

        if result:
            logger.debug(
                f"[Worker {self.worker_id}] Claimed next finalization result {result.hash}"
            )
            session.commit()
            # Convert result to dict
            return {
                "hash": result.hash,
                "from_address": result.from_address,
                "to_address": result.to_address,
                "data": result.data,
                "value": result.value,
                "type": result.type,
                "nonce": result.nonce,
                "gaslimit": result.gaslimit,
                "r": result.r,
                "s": result.s,
                "v": result.v,
                "leader_only": result.leader_only,
                "sim_config": result.sim_config,
                "contract_snapshot": result.contract_snapshot,
                "status": result.status,
                "consensus_data": result.consensus_data,
                "input_data": result.input_data,
                "created_at": result.created_at,
                "timestamp_awaiting_finalization": result.timestamp_awaiting_finalization,
                "appeal_failed": result.appeal_failed,
                "blocked_at": result.blocked_at,
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
        start_time = time.perf_counter()
        query = text(
            """
            WITH locked_appeals AS (
                SELECT t.*
                FROM transactions t
                WHERE t.appealed = true
                    AND t.status IN ('ACCEPTED', 'UNDETERMINED', 'LEADER_TIMEOUT', 'VALIDATORS_TIMEOUT')
                    AND (t.blocked_at IS NULL
                         OR t.blocked_at < NOW() - CAST(:timeout AS INTERVAL))
                    AND NOT EXISTS (
                        -- Ensure no other appeal for same contract is being processed
                        SELECT 1 FROM transactions t2
                        WHERE t2.to_address = t.to_address
                            AND t2.appealed = true
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - CAST(:timeout AS INTERVAL)
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
                FOR UPDATE SKIP LOCKED
            ),
            available_appeals AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY to_address
                    ORDER BY created_at ASC
                ) as rn
                FROM locked_appeals
            ),
            single_appeal AS (
                -- FIXED: Limit update to exactly ONE transaction
                SELECT *
                FROM available_appeals
                WHERE rn = 1
                ORDER BY created_at ASC
                LIMIT 1
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM single_appeal
            WHERE transactions.hash = single_appeal.hash
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at, transactions.appealed, transactions.appeal_failed,
                      transactions.timestamp_appeal, transactions.appeal_undetermined,
                      transactions.appeal_leader_timeout, transactions.appeal_validators_timeout,
                      transactions.blocked_at;
        """
        )

        result = session.execute(
            query,
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes",
            },
        ).first()
        duration = time.perf_counter() - start_time
        self._log_query_result("appeal", result, duration)

        if result:
            session.commit()
            # Convert result to dict
            return {
                "hash": result.hash,
                "from_address": result.from_address,
                "to_address": result.to_address,
                "data": result.data,
                "value": result.value,
                "type": result.type,
                "nonce": result.nonce,
                "gaslimit": result.gaslimit,
                "r": result.r,
                "s": result.s,
                "v": result.v,
                "leader_only": result.leader_only,
                "sim_config": result.sim_config,
                "contract_snapshot": result.contract_snapshot,
                "status": result.status,
                "consensus_data": result.consensus_data,
                "input_data": result.input_data,
                "created_at": result.created_at,
                "appealed": result.appealed,
                "appeal_failed": result.appeal_failed,
                "timestamp_appeal": result.timestamp_appeal,
                "appeal_undetermined": result.appeal_undetermined,
                "appeal_leader_timeout": result.appeal_leader_timeout,
                "appeal_validators_timeout": result.appeal_validators_timeout,
                "blocked_at": result.blocked_at,
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
        start_time = time.perf_counter()
        query = text(
            """
            WITH candidate_transactions AS (
                SELECT t.*
                FROM transactions t
                WHERE t.status IN ('PENDING', 'ACTIVATED')
                    AND (t.blocked_at IS NULL
                         OR t.blocked_at < NOW() - CAST(:timeout AS INTERVAL))
                    AND NOT EXISTS (
                        -- Ensure no other transaction for same contract is being processed
                        SELECT 1 FROM transactions t2
                        WHERE t2.to_address = t.to_address
                            AND t2.blocked_at IS NOT NULL
                            AND t2.blocked_at > NOW() - CAST(:timeout AS INTERVAL)
                            AND t2.hash != t.hash
                    )
                ORDER BY t.created_at ASC
                FOR UPDATE SKIP LOCKED
            ),
            oldest_per_contract AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY to_address
                    ORDER BY created_at ASC
                ) as rn
                FROM candidate_transactions
            ),
            single_transaction AS (
                -- Select only ONE transaction (oldest across all contracts)
                SELECT *
                FROM oldest_per_contract
                WHERE rn = 1
                ORDER BY created_at ASC
                LIMIT 1
            )
            UPDATE transactions
            SET blocked_at = NOW(),
                worker_id = :worker_id
            FROM single_transaction
            WHERE transactions.hash = single_transaction.hash
            RETURNING transactions.hash, transactions.from_address, transactions.to_address,
                      transactions.data, transactions.value, transactions.type, transactions.nonce,
                      transactions.gaslimit, transactions.r, transactions.s, transactions.v,
                      transactions.leader_only, transactions.sim_config, transactions.contract_snapshot,
                      transactions.status, transactions.consensus_data, transactions.input_data,
                      transactions.created_at, transactions.blocked_at;
        """
        )

        result = session.execute(
            query,
            {
                "worker_id": self.worker_id,
                "timeout": f"{self.transaction_timeout_minutes} minutes",
            },
        ).first()
        duration = time.perf_counter() - start_time
        self._log_query_result("transaction", result, duration)

        if result:
            logger.debug(f"[Worker {self.worker_id}] Claimed transaction {result.hash}")
            session.commit()
            # Convert result to dict
            return {
                "hash": result.hash,
                "from_address": result.from_address,
                "to_address": result.to_address,
                "data": result.data,
                "value": result.value,
                "type": result.type,
                "nonce": result.nonce,
                "gaslimit": result.gaslimit,
                "r": result.r,
                "s": result.s,
                "v": result.v,
                "leader_only": result.leader_only,
                "sim_config": result.sim_config,
                "contract_snapshot": result.contract_snapshot,
                "status": result.status,
                "consensus_data": result.consensus_data,
                "input_data": result.input_data,
                "created_at": result.created_at,
                "blocked_at": result.blocked_at,
            }

        return None

    def release_transaction(self, session: Session, transaction_hash: str):
        """
        Release a transaction by clearing its blocked_at and worker_id.

        Args:
            session: Database session (should be a fresh, valid session)
            transaction_hash: Hash of the transaction to release
        """
        update_query = text(
            """
            UPDATE transactions
            SET blocked_at = NULL,
                worker_id = NULL
            WHERE hash = :hash
              AND worker_id = :worker_id
            RETURNING hash, status, blocked_at, worker_id
        """
        )

        try:

            result = session.execute(
                update_query,
                {"hash": transaction_hash, "worker_id": self.worker_id},
            )
            row = result.first()
            session.commit()

            if row:
                logger.debug(
                    f"[Worker {self.worker_id}] Released transaction {transaction_hash} (status: {row.status})"
                )
            else:
                logger.warning(
                    f"[Worker {self.worker_id}] Transaction {transaction_hash} not found or owned by another worker when releasing"
                )
        except Exception as e:
            logger.error(
                f"[Worker {self.worker_id}] Failed to release transaction {transaction_hash}: {e}",
                exc_info=True,
            )
            try:
                session.rollback()
            except Exception as rollback_error:
                logger.error(
                    f"[Worker {self.worker_id}] Rollback failed while releasing {transaction_hash}: {rollback_error}",
                    exc_info=True,
                )
            raise

    async def recover_stuck_transactions(self, session: Session) -> int:
        """
        Recover transactions that have been stuck for too long.
        Resets them back to PENDING status and clears blocking fields.
        Also recovers orphaned transactions (in processing states but no worker).

        Returns:
            Number of transactions recovered
        """
        recovery_query = text(
            """
            UPDATE transactions
            SET blocked_at = NULL,
                worker_id = NULL,
                consensus_data = NULL,
                consensus_history = NULL,
                status = 'PENDING'
            WHERE (
                -- Case 1: Transactions with expired blocks
                (blocked_at IS NOT NULL
                 AND blocked_at < NOW() - CAST(:timeout AS INTERVAL)
                 AND status NOT IN ('FINALIZED', 'CANCELED'))
                OR
                -- Case 2: Orphaned transactions in processing states with no block
                (blocked_at IS NULL
                 AND status IN ('PROPOSING', 'COMMITTING', 'REVEALING')
                 AND created_at < NOW() - CAST(:orphan_timeout AS INTERVAL))
            )
            RETURNING hash, status;
        """
        )

        result = session.execute(
            recovery_query,
            {
                "timeout": f"{self.transaction_timeout_minutes} minutes",
                "orphan_timeout": "5 minutes",  # Shorter timeout for orphaned transactions
            },
        )
        recovered = result.fetchall()
        if recovered:
            session.commit()
            for row in recovered:
                logger.info(
                    f"[Worker {self.worker_id}] Recovered stuck transaction {row.hash} (was {row.status}, now PENDING)"
                )

        return len(recovered)

    async def process_transaction(self, transaction_data: dict, session: Session):
        """
        Process a single transaction through consensus.
        Reuses the exec_transaction logic from ConsensusAlgorithm.

        Args:
            transaction_data: Transaction data dictionary
            session: Database session
        """
        # Import NoValidatorsAvailableError here to avoid circular imports
        from backend.consensus.base import NoValidatorsAvailableError

        try:
            # Track current transaction for health monitoring
            self.current_transaction = {
                "hash": transaction_data.get("hash"),
                "blocked_at": transaction_data.get("blocked_at"),
            }

            # Handle upgrade transactions specially (no consensus needed)
            from backend.domain.types import TransactionType

            if transaction_data.get("type") == TransactionType.UPGRADE_CONTRACT.value:
                await self._process_upgrade_transaction(transaction_data, session)
                return

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
            logger.info(
                f"[Worker {self.worker_id}] Successfully processed transaction {transaction.hash}"
            )

            # Clean up retry tracking on success
            if transaction.hash in self._no_validators_retries:
                del self._no_validators_retries[transaction.hash]

        except NoValidatorsAvailableError:
            # Handle no-validators case with retry logic and backoff
            logger.warning(
                f"[Worker {self.worker_id}] No validators available for transaction {transaction_data['hash']}"
            )
            await self._handle_no_validators_retry(transaction_data, session)

        except ContractNotFoundError as e:
            # Handle contract not found - mark as ACCEPTED with ERROR execution result
            # This allows the transaction to go through finalization properly
            tx_hash = transaction_data["hash"]
            logger.error(
                f"[Worker {self.worker_id}] Contract not found for transaction {tx_hash}: {e}"
            )
            session.rollback()

            # Import required types for creating error receipt
            import base64
            import time
            from backend.node.types import ExecutionMode, ExecutionResultStatus

            # Create a minimal error receipt for the consensus_data
            error_result = f"Contract {e.address} not found".encode("utf-8")
            error_receipt = {
                "vote": None,
                "execution_result": ExecutionResultStatus.ERROR.value,
                "result": base64.b64encode(error_result).decode("ascii"),
                "calldata": base64.b64encode(b"").decode("ascii"),
                "gas_used": 0,
                "mode": ExecutionMode.LEADER.value,
                "contract_state": {},
                "node_config": {"address": "contract_not_found_handler"},
                "eq_outputs": {},
                "pending_transactions": [],
                "genvm_result": None,
                "processing_time": 0,
            }

            # Mark transaction as ACCEPTED with error consensus_data
            with self.get_session() as error_session:
                tx = error_session.query(Transactions).filter_by(hash=tx_hash).one()
                tx.status = TransactionStatus.ACCEPTED
                tx.timestamp_awaiting_finalization = int(time.time())
                tx.consensus_data = {
                    "votes": {},
                    "leader_receipt": [error_receipt],
                    "validators": [],
                }
                error_session.commit()

                # Send WebSocket notification
                from backend.consensus.base import ConsensusAlgorithm

                await ConsensusAlgorithm.dispatch_transaction_status_update(
                    TransactionsProcessor(error_session),
                    tx_hash,
                    TransactionStatus.ACCEPTED,
                    self.msg_handler,
                )

            logger.info(
                f"[Worker {self.worker_id}] Transaction {tx_hash} marked as ACCEPTED (with ERROR result) due to contract not found"
            )

        except Exception as e:
            logger.exception(
                f"[Worker {self.worker_id}] Error processing transaction {transaction_data['hash']}: {e}"
            )
            session.rollback()
        finally:
            # Clear current transaction tracking
            self.current_transaction = None
            # Always release the transaction when done - use fresh session since current one may be closed
            try:
                with self.get_session() as release_session:
                    self.release_transaction(release_session, transaction_data["hash"])
            except Exception as release_error:
                logger.error(
                    f"[Worker {self.worker_id}] Failed to release transaction {transaction_data['hash']} in finally block: {release_error}",
                    exc_info=True,
                )

    async def _handle_no_validators_retry(
        self, transaction_data: dict, session: Session
    ):
        """
        Handle retry logic when no validators are available.
        Implements exponential backoff and cancels after max retries.

        Args:
            transaction_data: Transaction data dictionary
            session: Database session
        """
        tx_hash = transaction_data["hash"]
        retry_info = self._no_validators_retries.get(
            tx_hash, {"count": 0, "last_attempt": 0}
        )
        retry_info["count"] += 1
        retry_info["last_attempt"] = time.time()
        self._no_validators_retries[tx_hash] = retry_info

        if retry_info["count"] >= self._max_no_validators_retries:
            # Cancel the transaction after max retries
            logger.error(
                f"[Worker {self.worker_id}] Transaction {tx_hash} canceled after "
                f"{retry_info['count']} retries - no validators available"
            )
            tx = session.query(Transactions).filter_by(hash=tx_hash).one()
            tx.status = TransactionStatus.CANCELED
            tx.consensus_data = {
                "error": "no_validators_available",
                "retries": retry_info["count"],
            }
            session.commit()

            # Clean up retry tracking
            del self._no_validators_retries[tx_hash]

            # Send WebSocket notification
            from backend.consensus.base import ConsensusAlgorithm

            await ConsensusAlgorithm.dispatch_transaction_status_update(
                TransactionsProcessor(session),
                tx_hash,
                TransactionStatus.CANCELED,
                self.msg_handler,
            )
        else:
            # Log retry attempt with backoff info
            backoff = self._no_validators_base_backoff * (
                2 ** (retry_info["count"] - 1)
            )
            logger.warning(
                f"[Worker {self.worker_id}] No validators for {tx_hash}, "
                f"retry {retry_info['count']}/{self._max_no_validators_retries}, "
                f"next attempt in {backoff}s"
            )

    async def _process_upgrade_transaction(
        self, transaction_data: dict, session: Session
    ):
        """
        Process a contract upgrade transaction (type=3). No consensus needed.
        Directly updates contract code in database.

        Args:
            transaction_data: Transaction data dictionary containing new_code
            session: Database session
        """
        import base64
        from backend.node.genvm import get_code_slot
        from backend.database_handler.models import CurrentState

        tx_hash = transaction_data["hash"]

        try:
            contract_address = transaction_data["to_address"]
            data = transaction_data.get("data") or {}
            new_code = data.get("new_code")
            if not new_code:
                raise ValueError("Missing new_code in transaction data")
            logger.info(
                f"[Worker {self.worker_id}] Processing upgrade transaction {tx_hash} for contract {contract_address}"
            )

            # Load contract
            contract = (
                session.query(CurrentState).filter_by(id=contract_address).one_or_none()
            )
            if not contract:
                raise ValueError(f"Contract {contract_address} not found")

            # Validate contract has expected state structure
            if (
                not contract.data
                or "state" not in contract.data
                or "accepted" not in contract.data["state"]
                or "finalized" not in contract.data["state"]
            ):
                raise ValueError(
                    f"Contract {contract_address} has invalid state structure"
                )

            # Validate Python syntax before proceeding
            try:
                compile(new_code, "<upgrade>", "exec")
            except SyntaxError as e:
                raise ValueError(f"Invalid Python syntax: {e}") from e

            # Encode code for slot storage: base64(4-byte-len-prefix + code-bytes)
            code_bytes = new_code.encode("utf-8")
            code_len_prefix = len(code_bytes).to_bytes(
                4, byteorder="little", signed=False
            )
            code_slot_value = base64.b64encode(code_len_prefix + code_bytes).decode(
                "ascii"
            )
            code_slot_key = base64.b64encode(get_code_slot()).decode("ascii")

            # Update contract data - update BOTH accepted and finalized state
            # Since upgrade transactions bypass consensus and go directly to FINALIZED,
            # both state trees must be updated for reads to see the new code
            # Note: data["code"] must be base64 encoded (matches deployment format in node/types.py)
            contract.data = {
                "code": base64.b64encode(code_bytes).decode("ascii"),
                "state": {
                    "accepted": {
                        **contract.data["state"]["accepted"],
                        code_slot_key: code_slot_value,
                    },
                    "finalized": {
                        **contract.data["state"]["finalized"],
                        code_slot_key: code_slot_value,
                    },
                },
            }

            # Store success in consensus_data for receipt
            tx = session.query(Transactions).filter_by(hash=tx_hash).one()
            tx.consensus_data = {
                "upgrade_result": "success",
                "contract_address": contract_address,
            }
            session.commit()

            # Mark transaction as finalized and send WebSocket notification
            transactions_processor = TransactionsProcessor(session)
            await ConsensusAlgorithm.dispatch_transaction_status_update(
                transactions_processor,
                tx_hash,
                TransactionStatus.FINALIZED,
                self.msg_handler,
            )

            logger.info(
                f"[Worker {self.worker_id}] Contract {contract_address} upgraded successfully via tx {tx_hash}"
            )

        except Exception as e:
            logger.error(
                f"[Worker {self.worker_id}] Contract upgrade failed for {tx_hash}: {e}",
                exc_info=True,
            )
            session.rollback()

            # Mark transaction as canceled on failure and send WebSocket notification
            try:
                tx = session.query(Transactions).filter_by(hash=tx_hash).one()
                tx.consensus_data = {"upgrade_result": "failed", "error": str(e)}
                session.commit()

                transactions_processor = TransactionsProcessor(session)
                await ConsensusAlgorithm.dispatch_transaction_status_update(
                    transactions_processor,
                    tx_hash,
                    TransactionStatus.CANCELED,
                    self.msg_handler,
                )
            except Exception as update_error:
                logger.error(
                    f"[Worker {self.worker_id}] Failed to update failed upgrade tx status: {update_error}"
                )

        finally:
            # Clear current transaction tracking
            self.current_transaction = None
            # Release the transaction
            try:
                with self.get_session() as release_session:
                    self.release_transaction(release_session, tx_hash)
            except Exception as release_error:
                logger.error(
                    f"[Worker {self.worker_id}] Failed to release upgrade transaction {tx_hash}: {release_error}",
                    exc_info=True,
                )

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
            can_finalize = self.consensus_algorithm.can_finalize_transaction(
                transactions_processor,
                transaction,
                0,  # index in queue (not used in our case)
                [finalization_data],  # mock queue with just this transaction
            )

            if can_finalize:
                logger.info(
                    f"[Worker {self.worker_id}] Finalizing transaction {transaction.hash}"
                )

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
                logger.info(
                    f"[Worker {self.worker_id}] Successfully finalized transaction {transaction.hash}"
                )

                # Send usage metrics to external API (non-blocking)
                await self.usage_metrics_service.send_finalized_transaction_metrics(
                    transaction, finalization_data
                )
            else:
                logger.debug(
                    f"[Worker {self.worker_id}] Transaction {transaction.hash} not ready for finalization yet (appeal window active)"
                )

        except ContractNotFoundError as e:
            # Handle contract not found during finalization - mark as FINALIZED
            # This prevents infinite retry loop for transactions targeting non-existent contracts
            tx_hash = finalization_data["hash"]
            logger.error(
                f"[Worker {self.worker_id}] Contract not found during finalization {tx_hash}: {e}"
            )
            session.rollback()

            # Mark transaction as FINALIZED since it can't be processed further
            with self.get_session() as error_session:
                transactions_processor = TransactionsProcessor(error_session)

                # Send WebSocket notification to mark as FINALIZED
                from backend.consensus.base import ConsensusAlgorithm

                await ConsensusAlgorithm.dispatch_transaction_status_update(
                    transactions_processor,
                    tx_hash,
                    TransactionStatus.FINALIZED,
                    self.msg_handler,
                )

            logger.info(
                f"[Worker {self.worker_id}] Transaction {tx_hash} marked as FINALIZED due to contract not found during finalization"
            )

        except Exception as e:
            logger.exception(
                f"[Worker {self.worker_id}] Error processing finalization {finalization_data['hash']}: {e}"
            )
            session.rollback()
        finally:
            # Always release the transaction when done - use fresh session since current one may be closed
            try:
                with self.get_session() as release_session:
                    self.release_transaction(release_session, finalization_data["hash"])
            except Exception as release_error:
                logger.error(
                    f"[Worker {self.worker_id}] Failed to release finalization {finalization_data['hash']} in finally block: {release_error}",
                    exc_info=True,
                )

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

            logger.info(
                f"[Worker {self.worker_id}] Processing appeal for transaction {transaction.hash} with status {appeal_data['status']}"
            )

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
            logger.info(
                f"[Worker {self.worker_id}] Successfully processed appeal for transaction {transaction.hash}"
            )

        except Exception as e:
            logger.exception(
                f"[Worker {self.worker_id}] Error processing appeal {appeal_data['hash']}: {e}"
            )
            session.rollback()
        finally:
            # Always release the transaction when done - use fresh session since current one may be closed
            try:
                with self.get_session() as release_session:
                    self.release_transaction(release_session, appeal_data["hash"])
            except Exception as release_error:
                logger.error(
                    f"[Worker {self.worker_id}] Failed to release appeal {appeal_data['hash']} in finally block: {release_error}",
                    exc_info=True,
                )

    def _log_query_result(
        self,
        query_name: str,
        result: Optional[Any],
        duration_seconds: float,
    ) -> None:
        """
        Emit a low-frequency log with query duration and outcome.
        Uses DEBUG level to reduce log noise - these are internal polling operations.
        """
        state = self._query_log_state.get(query_name)
        if state is None:
            return

        state["polls"] += 1
        now_monotonic = time.monotonic()
        if now_monotonic - state["last_log"] < self._query_log_interval:
            return

        result_text = "returned a row" if result is not None else "returned no rows"
        logger.debug(
            f"[Worker {self.worker_id}] {state['label']} query {result_text}: {result!r} "
            f"in {duration_seconds:.3f}s (polls since last log: {state['polls']})"
        )
        state["last_log"] = now_monotonic
        state["polls"] = 0

    async def run(self):
        """
        Main worker loop that continuously claims and processes transactions, appeals, and finalizations.
        """
        logger.info(f"[Worker {self.worker_id}] Starting consensus worker")

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
                            logger.info(
                                f"[Worker {self.worker_id}] Recovered {recovered} stuck transactions"
                            )

                    # Priority order: appeals > finalizations > regular transactions

                    # Try to claim an appeal first (highest priority)
                    appeal_data = await self.claim_next_appeal(session)

                    if appeal_data:
                        logger.debug(
                            f"[Worker {self.worker_id}] Claimed appeal for transaction {appeal_data['hash']}"
                        )
                        # Process in a new session
                        with self.get_session() as process_session:
                            await self.process_appeal(appeal_data, process_session)
                        # Continue to next iteration to ensure fresh session state
                        continue
                    else:
                        # No appeals, try to claim a finalization
                        finalization_data = await self.claim_next_finalization(session)

                        if finalization_data:
                            logger.debug(
                                f"[Worker {self.worker_id}] Claimed finalization for transaction {finalization_data['hash']}"
                            )
                            # Process in a new session
                            with self.get_session() as process_session:
                                await self.process_finalization(
                                    finalization_data, process_session
                                )
                            # Continue to next iteration to ensure fresh session state
                            continue
                        else:
                            # No finalizations, try to claim a regular transaction
                            transaction_data = await self.claim_next_transaction(
                                session
                            )

                            if transaction_data:
                                tx_hash = transaction_data["hash"]

                                # Check if transaction is in backoff due to no validators
                                retry_info = self._no_validators_retries.get(tx_hash)
                                if retry_info:
                                    backoff = self._no_validators_base_backoff * (
                                        2 ** (retry_info["count"] - 1)
                                    )
                                    time_since_last = (
                                        time.time() - retry_info["last_attempt"]
                                    )
                                    if time_since_last < backoff:
                                        # Still in backoff period, release and skip
                                        logger.debug(
                                            f"[Worker {self.worker_id}] Transaction {tx_hash} in backoff "
                                            f"({time_since_last:.1f}s < {backoff}s), releasing"
                                        )
                                        self.release_transaction(session, tx_hash)
                                        await asyncio.sleep(1)
                                        continue

                                logger.debug(
                                    f"[Worker {self.worker_id}] Claimed transaction {tx_hash}"
                                )
                                # Process in a new session
                                with self.get_session() as process_session:
                                    await self.process_transaction(
                                        transaction_data, process_session
                                    )
                                # Continue to next iteration to ensure fresh session state
                                continue
                            else:
                                # No work available, wait before polling again
                                await asyncio.sleep(self.poll_interval)

            except Exception as e:
                logger.exception(f"[Worker {self.worker_id}] Error in main loop: {e}")
                await asyncio.sleep(self.poll_interval)

    def stop(self):
        """Stop the worker gracefully."""
        logger.info(f"[Worker {self.worker_id}] Stopping consensus worker")
        self.running = False
