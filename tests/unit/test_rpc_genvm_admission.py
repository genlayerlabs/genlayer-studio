import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.node.types import ExecutionResultStatus
from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError


class _AsyncSnapshot:
    def __init__(self, snapshot):
        self.snapshot = snapshot

    async def __aenter__(self):
        return self.snapshot

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_genvm_admission_rejects_when_slots_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", asyncio.Semaphore(0))

    with pytest.raises(JSONRPCError) as exc_info:
        async with endpoints._admit_genvm_call("eth_call", "0xabc"):
            pass

    assert exc_info.value.code == -32006
    assert exc_info.value.data["retry_after_seconds"] == 2


@pytest.mark.asyncio
async def test_genvm_admission_releases_slot_after_error(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)

    with pytest.raises(RuntimeError):
        async with endpoints._admit_genvm_call("eth_call", "0xabc"):
            raise RuntimeError("boom")

    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_eth_call_rejects_before_db_snapshot_when_genvm_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", asyncio.Semaphore(0))
    monkeypatch.setattr(
        endpoints, "handle_consensus_data_call", lambda *args, **kwargs: None
    )

    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True

    params = {
        "to": "0x" + "ab" * 20,
        "from": "0x" + "cd" * 20,
        "data": "0x1234",
    }

    with patch("backend.protocol_rpc.endpoints.ContractSnapshot") as snapshot_cls:
        with pytest.raises(JSONRPCError) as exc_info:
            await endpoints.eth_call(
                session=MagicMock(),
                accounts_manager=accounts_manager,
                msg_handler=MagicMock(),
                transactions_parser=MagicMock(),
                validators_manager=MagicMock(),
                genvm_manager=MagicMock(),
                transactions_processor=MagicMock(),
                params=params,
            )

    assert exc_info.value.code == -32006
    snapshot_cls.assert_not_called()


@pytest.mark.asyncio
async def test_gen_call_rejects_before_validator_snapshot_when_genvm_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", asyncio.Semaphore(0))

    validators_manager = MagicMock()

    with pytest.raises(JSONRPCError) as exc_info:
        await endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=validators_manager,
            genvm_manager=MagicMock(),
            params={"to": "0x" + "ab" * 20},
        )

    assert exc_info.value.code == -32006
    validators_manager.snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_sim_call_rejects_before_validator_snapshot_when_genvm_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", asyncio.Semaphore(0))

    validators_manager = MagicMock()

    with pytest.raises(JSONRPCError) as exc_info:
        await endpoints.sim_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=validators_manager,
            genvm_manager=MagicMock(),
            params={"to": "0x" + "ab" * 20},
        )

    assert exc_info.value.code == -32006
    validators_manager.snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_eth_call_releases_admission_slot_after_success(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)
    monkeypatch.setattr(
        endpoints, "handle_consensus_data_call", lambda *args, **kwargs: None
    )

    accounts_manager = MagicMock()
    accounts_manager.is_valid_address.return_value = True

    decoded_data = MagicMock(calldata=b"\x12\x34")
    transactions_parser = MagicMock()
    transactions_parser.decode_method_call_data.return_value = decoded_data

    validator = MagicMock(address="0xvalidator")
    snapshot = MagicMock(nodes=[MagicMock(validator=validator)])
    validators_manager = MagicMock()
    validators_manager.snapshot.return_value = _AsyncSnapshot(snapshot)

    receipt = MagicMock(
        execution_result=ExecutionResultStatus.SUCCESS,
        result=b"\x00\x12\x34",
    )
    node = MagicMock()
    node.get_contract_data = AsyncMock(return_value=receipt)

    msg_handler = MagicMock()
    msg_handler.with_client_session.return_value = MagicMock()

    params = {
        "to": "0x" + "ab" * 20,
        "from": "0x" + "cd" * 20,
        "data": "0x1234",
    }

    with patch("backend.protocol_rpc.endpoints.ContractSnapshot"):
        with patch("backend.protocol_rpc.endpoints.Node", return_value=node):
            result = await endpoints.eth_call(
                session=MagicMock(),
                accounts_manager=accounts_manager,
                msg_handler=msg_handler,
                transactions_parser=transactions_parser,
                validators_manager=validators_manager,
                genvm_manager=MagicMock(),
                transactions_processor=MagicMock(),
                params=params,
            )

    assert result == "0x1234"
    assert semaphore._value == 1
    node.get_contract_data.assert_awaited_once_with(
        from_address=validator.address,
        calldata=decoded_data.calldata,
    )
