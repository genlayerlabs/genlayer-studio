from datetime import datetime, timezone
from unittest.mock import Mock, patch, AsyncMock
import pytest
from backend.node.types import (
    ExecutionMode,
    Address,
)
from backend.node.genvm.base import (
    ExecutionResult,
    ExecutionReturn,
    GenVMHost,
)
from backend.node.base import Node
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


class TestGenVMHostTimingMeasurement:
    """Test timing measurement in GenVMHost methods"""

    @pytest.mark.asyncio
    async def test_run_contract_measures_execution_time(self):
        """Test that GenVMHost.run_contract() measures execution time"""
        genvm_host = GenVMHost()
        mock_state = Mock()
        mock_execution_time = 0.5  # 500ms

        with patch(
            "backend.node.genvm.base._run_genvm_host", new_callable=AsyncMock
        ) as mock_run:
            mock_execution_result = create_mock_execution_result()
            mock_run.return_value = mock_execution_result

            with patch("time.time", side_effect=[1000.0, 1000.0 + mock_execution_time]):

                result = await genvm_host.run_contract(
                    state=mock_state,
                    from_address=Address("0x" + "12" * 20),
                    contract_address=Address("0x" + "34" * 20),
                    calldata_raw=b"test_calldata",
                    readonly=False,
                    leader_results=None,
                    date=datetime.now(timezone.utc),
                    chain_id=61999,
                    host_data={"test": "data"},
                    config_path=None,
                )

                assert result.processing_time == int(
                    mock_execution_time * 1000
                )  # 500ms

    @pytest.mark.asyncio
    async def test_get_contract_schema_measures_execution_time(self):
        """Test that GenVMHost.get_contract_schema() measures execution time"""
        genvm_host = GenVMHost()
        contract_code = b"def get_schema(): return 'test schema'"
        mock_execution_time = 0.2  # 200ms

        with patch(
            "backend.node.genvm.base._run_genvm_host", new_callable=AsyncMock
        ) as mock_run:
            mock_execution_result = create_mock_execution_result()
            mock_run.return_value = mock_execution_result

            with patch("time.time", side_effect=[2000.0, 2000.0 + mock_execution_time]):
                result = await genvm_host.get_contract_schema(contract_code)
                assert result.processing_time == int(
                    mock_execution_time * 1000
                )  # 200ms


class TestNodeProcessingTimeExtraction:
    """Test Node._run_genvm() processing_time extraction and Receipt creation"""

    def setup_method(self):
        """Set up test fixtures for each test"""
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
        self.contract_snapshot.states = {"accepted": {"slot1": "data1"}}

        self.contract_snapshot_factory = Mock(return_value=self.contract_snapshot)

        self.node = Node(
            contract_snapshot=self.contract_snapshot,
            validator_mode=ExecutionMode.LEADER,
            validator=self.validator,
            contract_snapshot_factory=self.contract_snapshot_factory,
        )

    @pytest.mark.asyncio
    async def test_node_run_genvm_extracts_processing_time(self):
        """Test that Node._run_genvm() extracts processing_time from ExecutionResult"""
        processing_time = 1800  # ms

        mock_execution_result = create_mock_execution_result(
            processing_time=processing_time
        )

        with patch.object(self.node, "_create_genvm") as mock_create_genvm:
            mock_genvm = AsyncMock()
            mock_genvm.run_contract.return_value = mock_execution_result
            mock_create_genvm.return_value = mock_genvm

            receipt = await self.node._run_genvm(
                from_address="0x" + "56" * 20,
                calldata=b"test_calldata",
                readonly=False,
                is_init=False,
                transaction_datetime=datetime.now(timezone.utc),
            )

            assert receipt.processing_time == processing_time

    @pytest.mark.asyncio
    async def test_node_run_genvm_without_processing_time(self):
        """Test that Node._run_genvm() handles ExecutionResult without processing_time"""
        mock_execution_result = create_mock_execution_result()

        with patch.object(self.node, "_create_genvm") as mock_create_genvm:
            mock_genvm = AsyncMock()
            mock_genvm.run_contract.return_value = mock_execution_result
            mock_create_genvm.return_value = mock_genvm

            receipt = await self.node._run_genvm(
                from_address="0x" + "56" * 20,
                calldata=b"test_calldata",
                readonly=False,
                is_init=False,
                transaction_datetime=datetime.now(timezone.utc),
            )
            assert receipt.processing_time == 0


class TestExecutionTimeEdgeCases:
    """Test edge cases for execution time measurement"""

    @pytest.mark.asyncio
    async def test_zero_execution_time(self):
        """Test handling of zero execution time"""
        genvm_host = GenVMHost()
        mock_state = Mock()

        with patch("time.time", side_effect=[5000.0, 5000.0]):
            with patch(
                "backend.node.genvm.base._run_genvm_host", new_callable=AsyncMock
            ) as mock_run:
                mock_run.return_value = create_mock_execution_result(state=mock_state)

                result = await genvm_host.run_contract(
                    state=mock_state,
                    from_address=Address("0x" + "12" * 20),
                    contract_address=Address("0x" + "34" * 20),
                    calldata_raw=b"instant_call",
                    readonly=True,
                    leader_results=None,
                    date=datetime.now(timezone.utc),
                    chain_id=1,
                    host_data={},
                    config_path=None,
                )

                assert result.processing_time == 0

    @pytest.mark.asyncio
    async def test_very_long_execution_time(self):
        """Test handling of very long execution times"""
        genvm_host = GenVMHost()

        long_execution_time = 10.0  # seconds
        with patch("time.time", side_effect=[6000.0, 6000.0 + long_execution_time]):
            with patch(
                "backend.node.genvm.base._run_genvm_host", new_callable=AsyncMock
            ) as mock_run:
                mock_run.return_value = create_mock_execution_result()

                result = await genvm_host.get_contract_schema(b"long_running_contract")

                assert result.processing_time == int(
                    long_execution_time * 1000
                )  # 10000ms
