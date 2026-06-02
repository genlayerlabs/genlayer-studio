import asyncio
from unittest.mock import MagicMock, patch

import pytest

from backend.protocol_rpc import endpoints
from backend.protocol_rpc.exceptions import JSONRPCError


@pytest.mark.asyncio
async def test_genvm_admission_rejects_when_slots_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_semaphore", asyncio.Semaphore(0))

    with pytest.raises(JSONRPCError) as exc_info:
        async with endpoints._admit_genvm_call("eth_call", "0xabc"):
            pass

    assert exc_info.value.code == -32006
    assert exc_info.value.data["retry_after_seconds"] == 2


@pytest.mark.asyncio
async def test_genvm_admission_releases_slot_after_error(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    monkeypatch.setattr(endpoints, "_genvm_semaphore", semaphore)

    with pytest.raises(RuntimeError):
        async with endpoints._admit_genvm_call("eth_call", "0xabc"):
            raise RuntimeError("boom")

    assert semaphore._value == 1


@pytest.mark.asyncio
async def test_eth_call_rejects_before_db_snapshot_when_genvm_full(monkeypatch):
    monkeypatch.setattr(endpoints, "_GENVM_CONCURRENCY", 1)
    monkeypatch.setattr(endpoints, "_genvm_semaphore", asyncio.Semaphore(0))
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
