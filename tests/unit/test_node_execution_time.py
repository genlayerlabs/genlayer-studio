import base64
from datetime import datetime, timezone
from unittest.mock import Mock, patch, AsyncMock
import pytest
from backend.node.types import (
    Receipt,
    ExecutionMode,
    Vote,
    ExecutionResultStatus,
    PendingTransaction,
    Address,
)
from backend.node.genvm.base import (
    ExecutionResult,
    ExecutionReturn,
    ExecutionError,
    GenVMHost,
    StateProxy,
    ResultCode,
)
from backend.node.base import Node
from backend.domain.types import Validator, LLMProvider
from backend.database_handler.contract_snapshot import ContractSnapshot


class MockStateProxy(StateProxy):
    """Mock implementation of StateProxy for testing"""

    def __init__(self):
        self.storage_data = {}
        self.balances = {}
        # Add mock snapshot attribute for Node tests
        self.snapshot = Mock()
        self.snapshot.states = {"accepted": {"test": "data"}}

    def storage_read(self, account: Address, slot: bytes, index: int, le: int) -> bytes:
        key = (account.as_hex, slot.hex(), index, le)
        return self.storage_data.get(key, b"\x00" * le)

    def storage_write(
        self, account: Address, slot: bytes, index: int, got: bytes
    ) -> None:
        key = (account.as_hex, slot.hex(), index, len(got))
        self.storage_data[key] = got

    def get_balance(self, addr: Address) -> int:
        return self.balances.get(addr.as_hex, 0)


class TestGenVMHostTimingMeasurement:
    """Test timing measurement in GenVMHost methods"""

    @pytest.mark.asyncio
    async def test_run_contract_measures_execution_time(self):
        """Test that GenVMHost.run_contract() measures execution time"""
        genvm_host = GenVMHost()
        mock_state = MockStateProxy()
        mock_execution_time = 0.5  # 500ms

        with patch("backend.node.genvm.base._run_genvm_host") as mock_run:
            mock_execution_result = ExecutionResult(
                result=ExecutionReturn(ret=b"success"),
                eq_outputs={1: b"output1"},
                pending_transactions=[],
                stdout="test output",
                stderr="",
                genvm_log=["log1"],
                state=mock_state,
                processing_time=0,  # Will be overridden by timing measurement
            )

            with patch("time.time", side_effect=[1000.0, 1000.0 + mock_execution_time]):
                mock_run.return_value = mock_execution_result

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

        with patch("backend.node.genvm.base._run_genvm_host") as mock_run:
            mock_state = MockStateProxy()
            mock_execution_result = ExecutionResult(
                result=ExecutionReturn(ret=b"test schema"),
                eq_outputs={},
                pending_transactions=[],
                stdout="schema output",
                stderr="",
                genvm_log=["schema log"],
                state=mock_state,
                processing_time=0,  # Will be overridden by timing measurement
            )

            with patch("time.time", side_effect=[2000.0, 2000.0 + mock_execution_time]):
                mock_run.return_value = mock_execution_result
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

        mock_execution_result = ExecutionResult(
            result=ExecutionReturn(ret=b"node_success"),
            eq_outputs={1: b"node_output1"},
            pending_transactions=[],
            stdout="node output",
            stderr="",
            genvm_log=["node log"],
            state=MockStateProxy(),
            processing_time=processing_time,
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
        mock_execution_result = ExecutionResult(
            result=ExecutionReturn(ret=b"node_success"),
            eq_outputs={1: b"node_output1"},
            pending_transactions=[],
            stdout="node output",
            stderr="",
            genvm_log=["node log"],
            state=MockStateProxy(),
            processing_time=0,  # Default value since processing_time is required
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
            assert receipt.processing_time == 0


class TestExecutionTimeEdgeCases:
    """Test edge cases for execution time measurement"""

    @pytest.mark.asyncio
    async def test_zero_execution_time(self):
        """Test handling of zero execution time"""
        genvm_host = GenVMHost()
        mock_state = MockStateProxy()

        with patch("time.time", side_effect=[5000.0, 5000.0]):
            with patch("backend.node.genvm.base._run_genvm_host") as mock_run:
                mock_run.return_value = ExecutionResult(
                    result=ExecutionReturn(ret=b"instant"),
                    eq_outputs={},
                    pending_transactions=[],
                    stdout="",
                    stderr="",
                    genvm_log=[],
                    state=mock_state,
                    processing_time=0,  # Will be overridden by timing measurement
                )

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
        mock_state = MockStateProxy()

        long_execution_time = 10.0  # seconds
        with patch("time.time", side_effect=[6000.0, 6000.0 + long_execution_time]):
            with patch("backend.node.genvm.base._run_genvm_host") as mock_run:
                mock_run.return_value = ExecutionResult(
                    result=ExecutionReturn(ret=b"long_running"),
                    eq_outputs={},
                    pending_transactions=[],
                    stdout="long output",
                    stderr="",
                    genvm_log=["long log"],
                    state=mock_state,
                    processing_time=0,  # Will be overridden by timing measurement
                )

                result = await genvm_host.get_contract_schema(b"long_running_contract")

                assert result.processing_time == int(
                    long_execution_time * 1000
                )  # 10000ms
