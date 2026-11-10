"""
Salted child placement and host prediction share GhostFactory's CREATE2 address.
"""

import asyncio
import contextlib
import functools
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from eth_abi import encode
from eth_utils import keccak, to_checksum_address

import backend.node.genvm.base as genvm_base
import backend.node.genvm.origin.base_host as base_host
from backend.consensus.base import _author_message_phase_locally
from backend.domain.types import TransactionType
from backend.node.genvm.base import Context, Host
from backend.node.genvm.error_codes import GenVMInternalError
from backend.node.genvm.origin import host_fns
from backend.node.types import Address
from backend.protocol_rpc.ghost_factory import GhostFactoryConfig

FACTORY = "0x5FbDB2315678afecb367f032d93F642f64180aa3"
CODE_HASH = bytes.fromhex("ab" * 32)
PARENT = "0x1111111111111111111111111111111111111111"
SELECTOR = keccak(text="predictGhostAddress(uint256,address)")[:4]


@pytest.fixture(autouse=True)
def _factory_env(monkeypatch):
    monkeypatch.setenv("GENLAYER_STUDIO_GHOST_FACTORY_ADDRESS", FACTORY)
    monkeypatch.setenv("GENLAYER_STUDIO_GHOST_BYTECODE_HASH", "0x" + CODE_HASH.hex())
    monkeypatch.delenv("GENVMROOT", raising=False)


def _create2(sender: str, salt: int) -> str:
    salt_word = keccak(bytes.fromhex(sender[2:]) + salt.to_bytes(32, "big"))
    digest = keccak(b"\xff" + bytes.fromhex(FACTORY[2:]) + salt_word + CODE_HASH)
    return to_checksum_address(digest[12:])


def _predict_calldata(salt: int, sender: str) -> bytes:
    return SELECTOR + encode(["uint256", "address"], [salt, sender])


async def _external_call(
    address: str, calldata: bytes, *, handler=None, inherit_create2=False
) -> bytes:
    server, client = socket.socketpair()
    server.setblocking(False)
    client.setblocking(False)
    loop = asyncio.get_running_loop()
    kwargs = (
        {}
        if inherit_create2
        else {
            "create2": genvm_base.create2_request_field(GhostFactoryConfig.from_env())
        }
    )
    task = asyncio.create_task(
        base_host.host_loop_on(
            handler or Host.__new__(Host), server, ctx=Context(), **kwargs
        )
    )

    async def read_exact(size):
        data = bytearray()
        while len(data) < size:
            chunk = await loop.sock_recv(client, size - len(data))
            assert chunk
            data.extend(chunk)
        return bytes(data)

    try:
        async with asyncio.timeout(2):
            await loop.sock_sendall(
                client,
                bytes([host_fns.Methods.EXTERNAL_CALL])
                + Address(address).as_bytes
                + len(calldata).to_bytes(4, "little")
                + calldata,
            )
            status = host_fns.Errors((await read_exact(1))[0])
            if status != host_fns.Errors.OK:
                raise base_host.HostException(status)
            size = int.from_bytes(await read_exact(4), "little")
            return await read_exact(size)
    finally:
        client.close()
        task.cancel()
        try:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            server.close()


@pytest.mark.asyncio
async def test_prediction_is_dispatched_to_the_vendored_async_hook():
    handler = Host.__new__(Host)
    handler.predict_ghost_address_hook = AsyncMock(return_value=Address(PARENT))
    handler.external_call = AsyncMock(side_effect=AssertionError("unexpected fallback"))

    assert await _external_call(
        FACTORY, _predict_calldata(42, PARENT), handler=handler
    ) == encode(["address"], [PARENT])
    handler.predict_ghost_address_hook.assert_awaited_once_with(
        genvm_base.create2_request_field(GhostFactoryConfig.from_env()),
        42,
        Address(PARENT),
    )
    handler.external_call.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled", [True, False])
async def test_unhandled_prediction_uses_studios_revert_fallback(disabled):
    handler = Host.__new__(Host)
    handler.predict_ghost_address_hook = (
        None if disabled else AsyncMock(return_value=NotImplemented)
    )
    with pytest.raises(base_host.HostException) as exc:
        await _external_call(FACTORY, _predict_calldata(42, PARENT), handler=handler)
    assert exc.value.error_code == host_fns.Errors.EVM_REVERTED


@pytest.mark.asyncio
@pytest.mark.parametrize("salt", [0, 1, (1 << 256) - 1])
async def test_zero_bytecode_hash_reverts_only_salted_prediction(monkeypatch, salt):
    monkeypatch.setenv("GENLAYER_STUDIO_GHOST_BYTECODE_HASH", "0x" + "00" * 32)
    if salt == 0:
        assert await _external_call(FACTORY, _predict_calldata(salt, PARENT)) == bytes(
            32
        )
    else:
        with pytest.raises(base_host.HostException) as exc:
            await _external_call(FACTORY, _predict_calldata(salt, PARENT))
        assert exc.value.error_code == host_fns.Errors.EVM_REVERTED


@pytest.mark.asyncio
async def test_nested_connections_inherit_isolated_run_configuration():
    initial = base_host._HOST_CREATE2.get()

    async def exercise(code_hash):
        create2 = {"deployer": Address(FACTORY), "code_hash": code_hash}
        handler = Host.__new__(Host)
        handler.predict_ghost_address_hook = AsyncMock(return_value=Address(PARENT))
        server, client = socket.socketpair()
        server.setblocking(False)
        client.close()

        async def enter(_cancellation):
            result = await asyncio.create_task(
                _external_call(
                    FACTORY,
                    _predict_calldata(42, PARENT),
                    handler=handler,
                    inherit_create2=True,
                )
            )
            assert result == encode(["address"], [PARENT])
            return server

        handler.loop_enter = enter
        try:
            await base_host.host_loop(
                handler, asyncio.Event(), ctx=Context(), create2=create2
            )
            handler.predict_ghost_address_hook.assert_awaited_once_with(
                create2, 42, Address(PARENT)
            )
            assert base_host._HOST_CREATE2.get() is initial
        finally:
            server.close()

    await asyncio.gather(exercise(CODE_HASH), exercise(bytes([42]) * 32))
    assert base_host._HOST_CREATE2.get() is initial


def test_vendored_default_create2_matches_upstream_preset():
    assert base_host.DEFAULT_CREATE2 == {
        "deployer": Address("0x1429859428C0aBc9C2C47C8Ee9FBaf82cFA0F20f"),
        "code_hash": bytes.fromhex(
            "aa5d6fdc0053688f0cb3b30dbde241e20d9031bf0f5b714582d4e09f3e103ff8"
        ),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("salt", [1, 42, (1 << 256) - 1])
async def test_predict_ghost_address_answers_the_create2_address(salt):
    ret = await _external_call(FACTORY, _predict_calldata(salt, PARENT))

    assert ret == encode(["address"], [_create2(PARENT, salt)])


@pytest.mark.asyncio
async def test_predict_ghost_address_ignores_trailing_calldata():
    ret = await _external_call(FACTORY, _predict_calldata(42, PARENT) + b"\x01")

    assert ret == encode(["address"], [_create2(PARENT, 42)])


@pytest.mark.asyncio
async def test_predict_ghost_address_of_salt_zero_is_the_zero_address():
    ret = await _external_call(FACTORY, _predict_calldata(0, PARENT))

    assert ret == bytes(32)


@pytest.mark.asyncio
async def test_empty_calldata_reaches_the_factory_receive():
    assert await _external_call(FACTORY, b"") == b""


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "calldata",
    [
        pytest.param(keccak(text="isGhost(address)")[:4] + bytes(32), id="selector"),
        pytest.param(SELECTOR[:3], id="short-selector"),
        pytest.param(_predict_calldata(42, PARENT)[:-1], id="short-args"),
        pytest.param(
            SELECTOR + (42).to_bytes(32, "big") + b"\x01" + bytes(11) + bytes(20),
            id="dirty-address",
        ),
    ],
)
async def test_other_factory_calldata_reverts(calldata):
    with pytest.raises(base_host.HostException) as exc:
        await _external_call(FACTORY, calldata)

    assert exc.value.error_code == host_fns.Errors.EVM_REVERTED


@pytest.mark.asyncio
async def test_calls_to_other_addresses_are_forbidden():
    with pytest.raises(base_host.HostException) as exc:
        await _external_call(PARENT, _predict_calldata(42, PARENT))

    assert exc.value.error_code == host_fns.Errors.FORBIDDEN


def test_create2_request_field_names_the_configured_factory():
    field = genvm_base.create2_request_field(GhostFactoryConfig.from_env())

    assert field == {"deployer": Address(FACTORY), "code_hash": CODE_HASH}


class _NoopHost:
    def __init__(self, _sock_listener, **_kwargs):
        self.sock = None

    def bind_context(self, _ctx):
        pass

    async def close_connections(self):
        pass


class _NoopManagerClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


async def _captured_request_extra(monkeypatch) -> dict:
    captured: dict = {}

    async def fake_run_genvm(_handler, **kwargs):
        captured.update(kwargs)
        raise GenVMInternalError("stop", error_code=None, causes=[], is_fatal=False)

    monkeypatch.setattr(base_host, "run_genvm", fake_run_genvm)
    monkeypatch.setattr(base_host, "ManagerClient", _NoopManagerClient)
    with pytest.raises(GenVMInternalError):
        await genvm_base.run_genvm_host(
            functools.partial(
                _NoopHost, state_proxy=None, calldata_bytes=b"", leader_results=None
            ),
            timeout=5,
            is_sync=False,
            message={"is_init": True, "contract_address": Address(PARENT)},
        )
    assert "create2" not in captured
    return captured["request_extra"]


@pytest.mark.asyncio
async def test_run_request_always_carries_configured_create2(monkeypatch):
    request_extra = await _captured_request_extra(monkeypatch)

    assert request_extra["create2"] == {
        "deployer": Address(FACTORY),
        "code_hash": CODE_HASH,
    }


class _FakeManagerClient:
    def __init__(self):
        self.payloads = []

    async def run(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("stop before manager request")


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [True, False])
async def test_vendored_host_always_sends_create2(monkeypatch, configured):
    host_loop = AsyncMock()
    monkeypatch.setattr(base_host, "host_loop", host_loop)
    create2 = (
        genvm_base.create2_request_field(GhostFactoryConfig.from_env())
        if configured
        else base_host.DEFAULT_CREATE2
    )
    client = _FakeManagerClient()
    with pytest.raises(base_host.ManagerRunNotStarted):
        await base_host.run_genvm(
            Host.__new__(Host),
            manager_client=client,
            ctx=Context(),
            is_sync=False,
            message={"is_init": True, "contract_address": Address(PARENT)},
            host="unix://test",
            calldata=b"",
            bucket_totals=base_host.default_bucket_totals(3),
            request_extra={"create2": create2} if configured else {},
        )

    assert host_loop.call_args.kwargs["create2"] == create2
    assert client.payloads[0]["create2"] == create2


def _salted_child(occurrence: str, salt: int) -> list:
    return [
        "0x0000000000000000000000000000000000000000",
        {},
        TransactionType.DEPLOY_CONTRACT.value,
        0,
        0,
        occurrence,
        {"saltNonce": salt, "data": b"\x01"},
    ]


def _place_children(
    selector: str | None,
    existing: set[str],
    children: list,
    *,
    tx_type: TransactionType = TransactionType.RUN_CONTRACT,
) -> dict:
    processor = SimpleNamespace(
        lock_ghost_factory=lambda: None,
        lock_pending_recipients=lambda _addresses: None,
        get_successful_ghost_creation_count=lambda: 7,
        is_genvm_contract_address=lambda address: str(address).lower() in existing,
        get_pending_transaction_count_for_address=lambda _address: 0,
    )
    context = SimpleNamespace(
        transaction=SimpleNamespace(
            hash="0x" + "11" * 32,
            to_address=PARENT,
            type=tx_type,
            sim_config=None,
            data={},
        ),
        transactions_processor=processor,
        contract_snapshot=SimpleNamespace(genvm_executor_selector=selector),
    )
    return _author_message_phase_locally(context, children, "accepted")


@pytest.mark.parametrize("selector", [None, "v0.2.17", "v0.3.5", "v0.4.0"])
def test_salted_children_always_live_at_the_factory_create2_address(selector):
    receipt = _place_children(
        selector,
        set(),
        [
            _salted_child("0x" + "01" * 32, 42),
            _salted_child("0x" + "02" * 32, 42),
            _salted_child("0x" + "03" * 32, 43),
        ],
    )

    zero = "0x0000000000000000000000000000000000000000"
    assert receipt["recipients"] == [
        _create2(PARENT, 42),
        zero,
        _create2(PARENT, 43),
    ]
    assert receipt["tx_ids_hex"][1] == "0x" + "00" * 32


@pytest.mark.parametrize("selector", [None, "v0.2.17", "v0.3.5", "v0.4.0"])
def test_salted_child_over_an_existing_contract_fails(selector):
    taken = _create2(PARENT, 42)

    receipt = _place_children(
        selector, {taken.lower()}, [_salted_child("0x" + "01" * 32, 42)]
    )

    assert receipt["recipients"] == ["0x0000000000000000000000000000000000000000"]
    assert receipt["tx_ids_hex"] == ["0x" + "00" * 32]


@pytest.mark.parametrize("newest", ["v0.4.1", "v0.3.5"])
@pytest.mark.parametrize(
    "tx_type", [TransactionType.RUN_CONTRACT, TransactionType.DEPLOY_CONTRACT]
)
def test_salted_placement_is_independent_of_installed_lines(
    monkeypatch, tmp_path, newest, tx_type
):
    (tmp_path / "executor" / newest).mkdir(parents=True)
    monkeypatch.setenv("GENVMROOT", str(tmp_path))

    receipt = _place_children(
        None, set(), [_salted_child("0x" + "01" * 32, 42)], tx_type=tx_type
    )

    assert receipt["recipients"] == [_create2(PARENT, 42)]
