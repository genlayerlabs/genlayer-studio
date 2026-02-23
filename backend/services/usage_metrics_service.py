# backend/services/usage_metrics_service.py

import os
import asyncio
from typing import Optional
from datetime import datetime
import aiohttp
from loguru import logger

from backend.database_handler.types import ConsensusData
from backend.domain.types import Transaction, TransactionType


class UsageMetricsService:
    """
    Service to send transaction metrics to an external API.
    Follows patterns from WorkerMessageHandler for async HTTP operations.

    The service is disabled by default when environment variables are not set.
    All errors are logged but never block transaction processing.
    """

    def __init__(self):
        """Initialize the usage metrics service."""
        self.api_url = os.environ.get("USAGE_METRICS_API_URL", "").rstrip("/")
        self.api_key = os.environ.get("USAGE_METRICS_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None
        self._enabled = bool(self.api_url and self.api_key)

        if self._enabled:
            logger.info(f"UsageMetricsService enabled, sending to {self.api_url}")
        else:
            logger.debug(
                "UsageMetricsService disabled (USAGE_METRICS_API_URL or USAGE_METRICS_API_KEY not set)"
            )

    @property
    def enabled(self) -> bool:
        """Check if the service is enabled."""
        return self._enabled

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send_finalized_transaction_metrics(
        self,
        transaction: Transaction,
        finalization_data: dict,
    ) -> None:
        """
        Send metrics for a finalized transaction to the external API.

        Args:
            transaction: The Transaction domain object
            finalization_data: Raw transaction data dict with timestamps
        """
        if not self._enabled:
            return

        try:
            decision = self._build_decision_payload(transaction, finalization_data)
            await self._send_to_api({"decisions": [decision]})
        except Exception as e:
            # Log error but don't block transaction processing
            logger.error(
                f"Failed to send usage metrics for transaction {transaction.hash}: {e}"
            )

    async def send_system_health_metrics(self, health_cache) -> None:
        """
        Send system health metrics from cache to external API.

        Args:
            health_cache: The HealthCache object with current health data
        """
        if not self._enabled:
            return

        try:
            # Build base payload
            mapped_status = self._map_health_status(health_cache.status)
            system_health = {
                "instanceHealth": mapped_status,
                "genVmStatus": "healthy" if health_cache.genvm_healthy else "down",
                "uptime": health_cache.uptime_percent,
                "activeWorkers": health_cache.services.get("consensus", {}).get(
                    "active_workers", 0
                ),
                "pendingTransactions": health_cache.pending_transactions,
                "decisions": health_cache.total_decisions,
                "users": health_cache.total_users,
                "memoryUsage": health_cache.services.get("memory", {}).get(
                    "percent", 0
                ),
                "cpuUsage": health_cache.services.get("memory", {}).get(
                    "cpu_percent", 0
                ),
            }

            if mapped_status != "healthy":
                system_health["instanceHealthReasons"] = health_cache.issues

            # Add pending contracts breakdown if available
            pending_contracts = getattr(health_cache, "pending_contracts", [])
            if pending_contracts:
                system_health["pendingContracts"] = pending_contracts

            payload = {"systemHealth": system_health}
            await self._send_to_api(payload)
            logger.debug("System health metrics sent successfully")
        except Exception as e:
            logger.error(f"Failed to send system health metrics: {e}")

    def _map_health_status(self, status: str) -> str:
        """Map internal status to API enum."""
        status_map = {
            "healthy": "healthy",
            "degraded": "degraded",
            "unhealthy": "down",
            "error": "down",
            "initializing": "degraded",
        }
        return status_map.get(status, "down")

    def _build_decision_payload(
        self, transaction: Transaction, finalization_data: dict
    ) -> dict:
        """Build the decision payload for the API."""
        # Map transaction type
        tx_type = self._map_transaction_type(transaction.type)

        # Status is always "success" since we only report finalized transactions
        tx_status = "success"

        # Calculate processing time in milliseconds
        processing_time_ms = self._calculate_processing_time_ms(finalization_data)

        # Extract LLM calls from consensus_data
        llm_calls = self._extract_llm_calls(transaction.consensus_data)

        # Build ISO8601 timestamp from created_at
        created_at_iso = self._format_created_at(finalization_data.get("created_at"))

        # Extract execution result from original status, consensus_data, and consensus_history
        # Result can be: success, error, timeout, undetermined
        execution_result = self._extract_execution_result(
            finalization_data, transaction.consensus_data, transaction.consensus_history
        )

        return {
            "externalId": transaction.hash,
            "walletAddress": transaction.from_address
            or "0x0000000000000000000000000000000000000000",
            "contractAddress": transaction.to_address,
            "type": tx_type,
            "status": tx_status,
            "processingTimeMs": processing_time_ms,
            "createdAt": created_at_iso,
            "llmCalls": llm_calls,
            "result": execution_result,
        }

    def _map_transaction_type(self, tx_type: TransactionType) -> str:
        """Map internal TransactionType to API type string."""
        type_map = {
            TransactionType.DEPLOY_CONTRACT: "deploy",
            TransactionType.RUN_CONTRACT: "write",
            TransactionType.UPGRADE_CONTRACT: "upgrade",
        }
        # Handle both enum and int values
        if isinstance(tx_type, int):
            tx_type = TransactionType(tx_type)
        return type_map.get(tx_type, "write")

    def _calculate_processing_time_ms(self, finalization_data: dict) -> int:
        """
        Calculate processing time in milliseconds.

        Processing time = (timestamp_awaiting_finalization - created_at) + appeal_processing_time
        """
        timestamp_awaiting = finalization_data.get("timestamp_awaiting_finalization")
        created_at = finalization_data.get("created_at")
        appeal_processing_time = finalization_data.get("appeal_processing_time", 0) or 0

        if timestamp_awaiting is None or created_at is None:
            return 0

        try:
            # created_at is a datetime, timestamp_awaiting_finalization is epoch seconds
            if isinstance(created_at, datetime):
                created_at_epoch = created_at.timestamp()
            else:
                # Handle string format if needed
                dt = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
                created_at_epoch = dt.timestamp()

            # Calculate time difference in seconds, then convert to ms
            # timestamp_awaiting_finalization is already in seconds
            processing_seconds = float(timestamp_awaiting) - created_at_epoch
            # appeal_processing_time is in seconds, convert to milliseconds
            total_ms = int(processing_seconds * 1000) + int(
                appeal_processing_time * 1000
            )
            return max(0, total_ms)  # Ensure non-negative
        except Exception as e:
            logger.warning(f"Failed to calculate processing time: {e}")
            return 0

    def _extract_execution_result(
        self,
        finalization_data: dict,
        consensus_data: Optional[ConsensusData],
        consensus_history: Optional[dict],
    ) -> str:
        """
        Extract execution result from original status, consensus_data, and consensus_history.

        Returns one of: "success", "error", "timeout", "undetermined"

        Priority:
        1. Original status timeout (LEADER_TIMEOUT, VALIDATORS_TIMEOUT) -> "timeout"
        2. Original status undetermined (UNDETERMINED) -> "undetermined"
        3. consensus_history.consensus_results[-1].consensus_round == "Undetermined" -> "undetermined"
        4. execution_result from consensus_data.leader_receipt[0] -> "success" or "error"
        5. Default -> "success"
        """
        # Check original status from finalization_data for timeout/undetermined
        original_status = finalization_data.get("status")
        if original_status in ("LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"):
            return "timeout"
        if original_status == "UNDETERMINED":
            return "undetermined"

        # Check if consensus was undetermined from consensus_history
        if (
            consensus_history is not None
            and "consensus_results" in consensus_history
            and len(consensus_history["consensus_results"]) > 0
        ):
            last_round = consensus_history["consensus_results"][-1]
            if last_round.get("consensus_round") == "Undetermined":
                return "undetermined"

        # Try to get execution_result from leader receipt
        if consensus_data is not None and consensus_data.leader_receipt:
            first_receipt = consensus_data.leader_receipt[0]
            if first_receipt is not None:
                execution_result = getattr(first_receipt, "execution_result", None)
                if execution_result is not None:
                    # Handle both enum and string values
                    if hasattr(execution_result, "value"):
                        # It's an enum (ExecutionResultStatus)
                        return execution_result.value.lower()
                    else:
                        # It's already a string
                        return str(execution_result).lower()

        # Default to success
        return "success"

    def _extract_llm_calls(self, consensus_data: Optional[ConsensusData]) -> list:
        """
        Extract LLM provider/model info from consensus_data.

        Extracts from:
        - consensus_data.leader_receipt[].node_config.primary_model
        - consensus_data.validators[].node_config.primary_model
        """
        llm_calls = []

        if consensus_data is None:
            return llm_calls

        # Process leader receipts
        if consensus_data.leader_receipt:
            for receipt in consensus_data.leader_receipt:
                llm_call = self._extract_llm_call_from_receipt(receipt)
                if llm_call:
                    llm_calls.append(llm_call)

        # Process validator receipts
        if consensus_data.validators:
            for receipt in consensus_data.validators:
                llm_call = self._extract_llm_call_from_receipt(receipt)
                if llm_call:
                    llm_calls.append(llm_call)

        return llm_calls

    def _extract_llm_call_from_receipt(self, receipt) -> Optional[dict]:
        """Extract LLM info from a single receipt."""
        if receipt is None:
            return None

        node_config = getattr(receipt, "node_config", None)
        if node_config is None or not isinstance(node_config, dict):
            return None

        primary_model = node_config.get("primary_model", {})
        if not primary_model:
            return None

        provider = primary_model.get("provider", "unknown")
        model = primary_model.get("model", "unknown")

        # Skip if both are unknown (no meaningful data)
        if provider == "unknown" and model == "unknown":
            return None

        return {
            "provider": provider,
            "model": model,
            "inputTokens": 0,  # Not tracked yet
            "outputTokens": 0,  # Not tracked yet
            "costUsd": 0,  # Not tracked yet
        }

    def _format_created_at(self, created_at) -> str:
        """Format created_at to ISO8601 string."""
        if created_at is None:
            return datetime.utcnow().isoformat()

        if isinstance(created_at, datetime):
            return created_at.isoformat()

        # If it's already a string, return as-is
        return str(created_at)

    async def _send_to_api(self, payload: dict) -> None:
        """Send payload to the external API."""
        session = await self._get_session()

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        try:
            async with session.post(
                f"{self.api_url}/api/ingest",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as response:
                if response.status != 200:
                    response_text = await response.text()
                    logger.warning(
                        f"Usage metrics API returned status {response.status}: {response_text[:200]}"
                    )
                else:
                    decisions_count = len(payload.get("decisions", []))
                    logger.debug(
                        f"Usage metrics sent successfully for {decisions_count} decision(s)"
                    )
        except asyncio.TimeoutError:
            logger.warning("Timeout sending usage metrics to API")
        except aiohttp.ClientConnectorError:
            # Connection errors are expected when API is unavailable - log at debug level
            logger.debug(
                f"Could not connect to usage metrics API at {self.api_url} - API may be unavailable"
            )
        except aiohttp.ClientError as e:
            # Other client errors (DNS, SSL, etc.) - log at warning level
            logger.warning(f"Client error sending usage metrics to API: {e}")
        except Exception as e:
            logger.error(f"Error sending usage metrics to API: {e}")

    async def close(self):
        """Clean up resources."""
        if self._session:
            await self._session.close()
            self._session = None
