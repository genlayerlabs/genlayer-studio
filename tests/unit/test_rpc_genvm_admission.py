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
async def test_gen_call_coalesces_identical_read_calls_before_admission(monkeypatch):
    semaphore = asyncio.Semaphore(1)
    started = asyncio.Event()
    release = asyncio.Event()
    execute_calls = 0

    monkeypatch.setattr(endpoints, "_GEN_CALL_SINGLEFLIGHT_ENABLED", True)
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_tasks", {})
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_lock", asyncio.Lock())
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)

    async def fake_execute_call_with_snapshot(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        started.set()
        await release.wait()
        return MagicMock(result=b"\x00\x12\x34")

    monkeypatch.setattr(
        endpoints, "_execute_call_with_snapshot", fake_execute_call_with_snapshot
    )

    params = {
        "type": "read",
        "to": "0x" + "ab" * 20,
        "from": "0x" + "cd" * 20,
        "data": "0x1234",
        "transaction_hash_variant": "latest-nonfinal",
    }

    first = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=params,
        )
    )
    await started.wait()

    second = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=dict(params),
        )
    )

    release.set()
    assert await asyncio.gather(first, second) == ["1234", "1234"]
    assert execute_calls == 1
    assert semaphore._value == 1
    assert endpoints._gen_call_singleflight_tasks == {}


@pytest.mark.asyncio
async def test_gen_call_keeps_different_callers_separate(monkeypatch):
    semaphore = asyncio.Semaphore(2)
    both_started = asyncio.Event()
    release = asyncio.Event()
    execute_calls = 0

    monkeypatch.setattr(endpoints, "_GEN_CALL_SINGLEFLIGHT_ENABLED", True)
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_tasks", {})
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_lock", asyncio.Lock())
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)

    async def fake_execute_call_with_snapshot(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 2:
            both_started.set()
        await release.wait()
        return MagicMock(result=b"\x00\x12\x34")

    monkeypatch.setattr(
        endpoints, "_execute_call_with_snapshot", fake_execute_call_with_snapshot
    )

    base_params = {
        "type": "read",
        "to": "0x" + "ab" * 20,
        "data": "0x1234",
        "transaction_hash_variant": "latest-nonfinal",
    }

    first = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params={**base_params, "from": "0x" + "cd" * 20},
        )
    )
    second = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params={**base_params, "from": "0x" + "ef" * 20},
        )
    )

    await both_started.wait()
    release.set()
    assert await asyncio.gather(first, second) == ["1234", "1234"]
    assert execute_calls == 2
    assert semaphore._value == 2


@pytest.mark.asyncio
async def test_gen_call_does_not_coalesce_write_calls(monkeypatch):
    semaphore = asyncio.Semaphore(2)
    both_started = asyncio.Event()
    release = asyncio.Event()
    execute_calls = 0

    monkeypatch.setattr(endpoints, "_GEN_CALL_SINGLEFLIGHT_ENABLED", True)
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_tasks", {})
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_lock", asyncio.Lock())
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)

    async def fake_execute_call_with_snapshot(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        if execute_calls == 2:
            both_started.set()
        await release.wait()
        return MagicMock(result=b"\x00\x12\x34")

    monkeypatch.setattr(
        endpoints, "_execute_call_with_snapshot", fake_execute_call_with_snapshot
    )

    params = {
        "type": "write",
        "to": "0x" + "ab" * 20,
        "from": "0x" + "cd" * 20,
        "data": "0x1234",
    }

    first = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=params,
        )
    )
    second = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=dict(params),
        )
    )

    await both_started.wait()
    release.set()
    assert await asyncio.gather(first, second) == ["1234", "1234"]
    assert execute_calls == 2
    assert endpoints._gen_call_singleflight_tasks == {}


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


@pytest.mark.asyncio
async def test_gen_call_singleflight_returns_stale_read_after_state_change(monkeypatch):
    """Regression: the gen_call singleflight key is sha256(params) only, with no
    state-version discriminator.

    A slow read R1 of a contract takes its DB snapshot, then a write to that
    contract reaches ACCEPTED, then an identical read R2 arrives. Because the
    coalescing key ignores contract state, R2 joins R1's still-in-flight task
    and receives R1's pre-write result -- even though R2 was issued after the
    write committed. For the 'latest-nonfinal' variant this is a read-your-
    writes violation: R2 must observe the post-write state.
    """
    semaphore = asyncio.Semaphore(2)
    started = asyncio.Event()
    release = asyncio.Event()
    execute_calls = 0

    # Mutable "contract state" observed by an execution when it takes its
    # snapshot (at execution start, before the release barrier).
    current_state = {"value": 0x11}

    monkeypatch.setattr(endpoints, "_GEN_CALL_SINGLEFLIGHT_ENABLED", True)
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_tasks", {})
    monkeypatch.setattr(endpoints, "_gen_call_singleflight_lock", asyncio.Lock())
    monkeypatch.setattr(endpoints, "_genvm_admission_semaphore", semaphore)

    async def fake_execute_call_with_snapshot(*args, **kwargs):
        nonlocal execute_calls
        execute_calls += 1
        captured = current_state["value"]  # snapshot taken at execution start
        started.set()
        await release.wait()
        return MagicMock(result=bytes([0x00, captured]))

    monkeypatch.setattr(
        endpoints, "_execute_call_with_snapshot", fake_execute_call_with_snapshot
    )

    params = {
        "type": "read",
        "to": "0x" + "ab" * 20,
        "from": "0x" + "cd" * 20,
        "data": "0x1234",
        "transaction_hash_variant": "latest-nonfinal",
    }

    first = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=dict(params),
        )
    )
    await started.wait()

    # A write lands and the contract state advances after R1 snapshotted.
    current_state["value"] = 0x22

    second = asyncio.create_task(
        endpoints.gen_call(
            session=MagicMock(),
            accounts_manager=MagicMock(),
            msg_handler=MagicMock(),
            transactions_parser=MagicMock(),
            validators_manager=MagicMock(),
            genvm_manager=MagicMock(),
            params=dict(params),
        )
    )

    release.set()
    first_result, second_result = await asyncio.gather(first, second)

    assert first_result == "11"  # R1 legitimately sees the pre-write state
    # R2 was issued after the write committed and must see the new state.
    assert second_result == "22"
