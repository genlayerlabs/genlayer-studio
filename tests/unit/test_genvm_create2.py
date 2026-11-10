"""Salted child deploys of v0.4+ executor lines.

Their SDK places a salted child at GhostFactory's CREATE2 address, read from the
run request's `create2` or asked of the factory through `external_call`, so
Studio must answer both the same way and materialize the child there.
"""

import functools
from types import SimpleNamespace

import pytest
from eth_abi import encode
from eth_utils import keccak, to_checksum_address

import backend.node.genvm.base as genvm_base
import backend.node.genvm.executor_selection as executor_selection
import backend.node.genvm.origin.base_host as base_host
from backend.consensus.base import _author_message_phase_locally
from backend.domain.types import TransactionType
from backend.node.genvm.base import Context, Host
from backend.node.genvm.error_codes import GenVMInternalError
from backend.node.genvm.executor_selection import (
    LEGACY_EXECUTOR_SELECTOR,
    uses_create2_child_addresses,
)
from backend.node.genvm.origin import host_fns
from backend.node.types import Address
from backend.protocol_rpc.ghost_factory import (
    GhostFactoryConfig,
    genvm_salted_child_address,
)

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


async def _external_call(address: str, calldata: bytes) -> bytes:
    return await Host.__new__(Host).external_call(Address(address).as_bytes, calldata)


@pytest.mark.asyncio
async def test_predict_ghost_address_answers_the_create2_address():
    ret = await _external_call(FACTORY, _predict_calldata(42, PARENT))

    assert ret == encode(["address"], [_create2(PARENT, 42)])


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
    return captured["request_extra"]


@pytest.mark.asyncio
@pytest.mark.parametrize("speaks", [True, False])
async def test_run_request_carries_create2_only_where_the_manager_speaks_it(
    monkeypatch, speaks
):
    monkeypatch.setattr(executor_selection, "MANAGER_SPEAKS_CREATE2", speaks)

    request_extra = await _captured_request_extra(monkeypatch)

    if speaks:
        assert request_extra["create2"] == {
            "deployer": Address(FACTORY),
            "code_hash": CODE_HASH,
        }
    else:
        assert "create2" not in request_extra


class _FakeManagerClient:
    def __init__(self):
        self.payloads = []

    async def run(self, payload):
        self.payloads.append(payload)
        raise RuntimeError("stop before manager request")


@pytest.mark.asyncio
async def test_vendored_host_sends_studios_create2(monkeypatch):
    """A vendored host that defaults `create2` must not override Studio's."""

    async def fake_host_loop(_handler, cancellation, *, ctx):
        await cancellation.wait()

    monkeypatch.setattr(base_host, "host_loop", fake_host_loop)
    monkeypatch.setattr(executor_selection, "MANAGER_SPEAKS_CREATE2", True)
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
            request_extra=genvm_base._run_request_extra(),
        )

    assert client.payloads[0]["create2"] == {
        "deployer": Address(FACTORY),
        "code_hash": CODE_HASH,
    }


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (LEGACY_EXECUTOR_SELECTOR, False),
        ("v0.2.17", False),
        ("v0.3.5", False),
        ("v0.3.0-rc7", False),
        ("v0.4.0", True),
        ("0.4.1-rc1", True),
        ("v1.0.0", True),
    ],
)
@pytest.mark.parametrize("speaks", [True, False])
def test_exact_pins_name_their_line(monkeypatch, selector, expected, speaks):
    monkeypatch.setattr(executor_selection, "MANAGER_SPEAKS_CREATE2", speaks)

    assert uses_create2_child_addresses(selector) is expected


@pytest.mark.parametrize("selector", [None, "", r"re:^v0\.3\.", "v0"])
@pytest.mark.parametrize("speaks", [True, False])
def test_unresolvable_selectors_follow_the_manager(monkeypatch, selector, speaks):
    monkeypatch.setattr(executor_selection, "MANAGER_SPEAKS_CREATE2", speaks)

    assert uses_create2_child_addresses(selector) is speaks


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (None, True),
        (r"re:^v0\.3\.", False),
        (r"re:^v0\.4\.", True),
        (r"re:v0\.[34]\.", True),
        (LEGACY_EXECUTOR_SELECTOR, False),
    ],
)
def test_regex_and_missing_pins_resolve_against_installed_lines(
    monkeypatch, tmp_path, selector, expected
):
    for name in ("v0.2.17", "v0.3.5", "v0.4.1-rc2", "not-a-version"):
        (tmp_path / "executor" / name).mkdir(parents=True)
    monkeypatch.setenv("GENVMROOT", str(tmp_path))
    monkeypatch.setattr(executor_selection, "MANAGER_SPEAKS_CREATE2", not expected)

    assert uses_create2_child_addresses(selector) is expected


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


def _expected_child(selector: str, salt: int) -> str:
    if selector == "v0.4.0":
        return _create2(PARENT, salt)
    return genvm_salted_child_address(PARENT, salt, 61999)


@pytest.fixture
def _chain_id(monkeypatch):
    monkeypatch.setattr("backend.consensus.base.get_simulator_chain_id", lambda: 61999)


@pytest.mark.usefixtures("_chain_id")
@pytest.mark.parametrize("selector", ["v0.4.0", "v0.3.5"])
def test_salted_children_live_where_the_parents_sdk_put_them(selector):
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
        _expected_child(selector, 42),
        zero,
        _expected_child(selector, 43),
    ]
    assert receipt["tx_ids_hex"][1] == "0x" + "00" * 32


@pytest.mark.usefixtures("_chain_id")
@pytest.mark.parametrize("selector", ["v0.4.0", "v0.3.5"])
def test_salted_child_over_an_existing_contract_fails(selector):
    taken = _expected_child(selector, 42)

    receipt = _place_children(
        selector, {taken.lower()}, [_salted_child("0x" + "01" * 32, 42)]
    )

    assert receipt["recipients"] == ["0x0000000000000000000000000000000000000000"]
    assert receipt["tx_ids_hex"] == ["0x" + "00" * 32]


@pytest.mark.usefixtures("_chain_id")
@pytest.mark.parametrize("newest", ["v0.4.1", "v0.3.5"])
@pytest.mark.parametrize(
    "tx_type", [TransactionType.RUN_CONTRACT, TransactionType.DEPLOY_CONTRACT]
)
def test_unpinned_parent_follows_the_newest_installed_line(
    monkeypatch, tmp_path, newest, tx_type
):
    (tmp_path / "executor" / newest).mkdir(parents=True)
    monkeypatch.setenv("GENVMROOT", str(tmp_path))

    receipt = _place_children(
        None, set(), [_salted_child("0x" + "01" * 32, 42)], tx_type=tx_type
    )

    expected = "v0.4.0" if newest == "v0.4.1" else "v0.3.5"
    assert receipt["recipients"] == [_expected_child(expected, 42)]
