from datetime import datetime, timezone
import functools
import json
from unittest.mock import Mock, patch, AsyncMock
import pytest
from backend.node.types import (
    ExecutionMode,
    Address,
)
from backend.node.genvm.base import (
    ExecutionResult,
    ExecutionReturn,
)
import backend.node.genvm.base as genvm_base
import backend.node.base as node_base
from backend.node.base import SIMULATOR_CHAIN_ID, Node
from backend.domain.types import Validator, LLMProvider
from backend.database_handler.contract_snapshot import ContractSnapshot


def create_mock_execution_result(processing_time=0, state=None):
    """Helper function to create ExecutionResult with minimal test data."""
    if state is None:
        state = Mock()
        state.snapshot = Mock()
        state.snapshot.states = {
            "accepted": {},
        }

    return ExecutionResult(
        result=ExecutionReturn(ret=b"test"),
        eq_outputs={},
        pending_transactions=[],
        stdout="test",
        stderr="",
        genvm_log=[],
        state=state,
        processing_time=processing_time,
        nondet_disagree=None,
    )


class WithNode:
    def setup(self):
        self.validator = Validator(
            address="0x" + "12" * 20,
            stake=100,
            llmprovider=LLMProvider(
                provider="test",
                model="test-model",
                config={},
                plugin="test-plugin",
                plugin_config={},
            ),
        )

        # Create a mock ContractSnapshot instead of using the real constructor
        self.contract_snapshot = Mock(spec=ContractSnapshot)
        self.contract_snapshot.contract_address = "0x" + "34" * 20
        self.contract_snapshot.balance = 1000
        self.contract_snapshot.states = {}

        self.contract_snapshot_factory = Mock(return_value=self.contract_snapshot)

        self.genvm_manager = Mock(spec=node_base.Manager)
        self.genvm_manager.url = "http://127.0.0.1:3999"

        self.node = Node(
            contract_snapshot=self.contract_snapshot,
            validator_mode=ExecutionMode.LEADER,
            validator=self.validator,
            contract_snapshot_factory=self.contract_snapshot_factory,
            manager=self.genvm_manager,
        )


class TestExecutionTimeEdgeCases(WithNode):
    """Test edge cases for execution time measurement"""

    def setup_method(self):
        WithNode.setup(self)

    @pytest.mark.asyncio
    async def test_zero_execution_time(self):
        """Test handling of zero execution time"""
        mock_state = Mock()

        # Provide enough time.time() values for all calls in run_contract
        # Must patch backend.node.base.time.time since that module imports time directly
        with patch("backend.node.base.time.time", side_effect=[5000.0, 5000.0, 5000.0]):
            with patch(
                "backend.node.genvm.base.run_genvm_host", new_callable=AsyncMock
            ) as mock_run:
                mock_run.return_value = create_mock_execution_result(state=None)

                result = await self.node.get_contract_data(
                    from_address=Address("0x" + "12" * 20).as_hex,
                    calldata=b"instant_call",
                    transaction_datetime=datetime.now(timezone.utc),
                )

                assert result.processing_time == 0

    @pytest.mark.asyncio
    async def test_very_long_execution_time(self):
        """Test handling of very long execution times"""
        long_execution_time = 10.0  # seconds
        # Provide enough time.time() values for all calls in _run_genvm
        # Must patch backend.node.base.time.time since that module imports time directly
        with patch(
            "backend.node.base.time.time",
            side_effect=[
                6000.0,
                6000.0 + long_execution_time,
                6000.0 + long_execution_time,
            ],
        ):
            with patch(
                "backend.node.genvm.base.run_genvm_host", new_callable=AsyncMock
            ) as mock_run:
                mock_run.return_value = create_mock_execution_result()

                result = await self.node.get_contract_data(
                    from_address=Address("0x" + "12" * 20).as_hex,
                    calldata=b"instant_call",
                    transaction_datetime=datetime.now(timezone.utc),
                )

                assert result.processing_time == int(
                    long_execution_time * 1000
                )  # 10000ms
