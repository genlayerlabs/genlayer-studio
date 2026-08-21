"""Integration test for the studio-only `sim_config.genvm_executor_selector`
deploy parameter.

`genvm_executor_selector` pins a contract to a specific GenVM executor version
directory. It is applied to the deployment execution itself and stored on the
contract row, so every later execution of that contract keeps using that
executor.

The test deploys two contracts that talk to each other:

- contract A: current SDK / current executor (no reroute)
- contract B: legacy SDK (pinned py-genlayer runner), deployed with
  `genvm_executor_selector=v0.2.17` — it cannot run on the current executor at
  all, so every successful step below is evidence that the override was
  applied

Both get each other's address, then each sends a message to the other. The
cross-contract calls happen in *triggered* transactions, which carry no
sim_config of their own — they only work if B's `genvm_executor_selector` was
persisted to the DB at deploy time and reused on later executions.

Run with containers up:
    .venv/bin/pytest tests/integration/test_deploy_reroute_to.py -xvs
"""

import os
import time

import pytest
import requests
from genlayer_py import create_account, create_client, localnet
from genlayer_py.abi import calldata
from genlayer_py.abi.transactions import serialize
from genlayer_py.contracts.actions import (
    _encode_add_transaction_data,
    _prepare_transaction,
)
from genlayer_py.contracts.utils import make_calldata_object
from genlayer_py.types.transactions import TransactionHashVariant
from web3.constants import ADDRESS_ZERO

RPC_URL = "http://localhost:4000/api"

# Executor version directory contract B is pinned to. Must exist under the
# GenVM manager's executors path in the image under test.
LEGACY_EXECUTOR = os.environ.get("TEST_REROUTE_EXECUTOR", "v0.2.17")

# Executor version directory of the *current* line. Pinning a contract to it is
# a no-op for how that contract runs on its own, but it gives the resolve hook
# something to answer with when a caller on another line asks where this
# contract lives.
#
# Hardcoded because the test has no way to ask for it, not because pinning it
# here is desirable. The manager knows the answer --
# `$GENVMROOT/data/manifest.yaml` lists `executor_versions` -- but it serves no
# route that reads the manifest back (`/manifest/reload` only reloads it), and
# `/contract/detect-version` answers with a `specified_major`, which is 0 for
# every 0.x line and so cannot name a directory. Until something exposes it,
# this constant has to be updated whenever the image's current line moves;
# override the env var when it does.
CURRENT_EXECUTOR = os.environ.get("TEST_CURRENT_EXECUTOR", "v0.3.0-rc7")

# Shares the validator registry with the other integration suites, which wipe
# validators while seeding mock responses.
pytestmark = pytest.mark.xdist_group(name="mock_validators")


# Current SDK, current executor.
CONTRACT_A = """# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl
from genlayer.types import *


class RerouteModernPeer(gl.contract.Contract):
    storage: str
    peer: str

    def __init__(self, initial_storage: str):
        self.storage = initial_storage
        self.peer = ""

    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    @gl.public.view
    def get_peer(self) -> str:
        return self.peer

    @gl.public.write
    def set_peer(self, peer: str) -> None:
        self.peer = peer

    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage

    @gl.public.write
    def ping_peer(self, new_storage: str) -> None:
        gl.contract.get_at(Address(self.peer)).emit().update_storage(new_storage)

    @gl.public.view
    def read_peer_storage(self) -> str:
        # Synchronous cross-contract call from the current executor into a
        # contract pinned to the legacy one. The manager runs the callee in a
        # nested executor of its own, which it only knows to do because the
        # host answers `resolve_call_contract_executor` for a callee that
        # carries a `reroute_to`.
        return gl.contract.get_at(Address(self.peer)).view().get_storage()
"""

# Legacy SDK, pinned runner — only runs on the legacy executor.
CONTRACT_B = """# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class RerouteLegacyPeer(gl.Contract):
    storage: str
    peer: str

    def __init__(self, initial_storage: str):
        self.storage = initial_storage
        self.peer = ""

    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    @gl.public.view
    def get_peer(self) -> str:
        return self.peer

    @gl.public.write
    def set_peer(self, peer: str) -> None:
        self.peer = peer

    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage

    @gl.public.write
    def ping_peer(self, new_storage: str) -> None:
        gl.get_contract_at(Address(self.peer)).emit().update_storage(new_storage)

    @gl.public.view
    def read_peer_storage(self) -> str:
        # Synchronous cross-contract call from the legacy executor into a
        # contract running on the current one.
        return gl.get_contract_at(Address(self.peer)).view().get_storage()
"""


CLIENT = create_client(chain=localnet, endpoint=RPC_URL)
ACCOUNT = create_account()
CLIENT.local_account = ACCOUNT


def rpc_call(method: str, params):
    response = requests.post(
        RPC_URL,
        json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1},
        timeout=30,
    )
    result = response.json()
    if "error" in result:
        raise AssertionError(f"RPC error on {method}: {result['error']}")
    return result.get("result")


@pytest.fixture(scope="module", autouse=True)
def ensure_validators_exist():
    """Other integration suites delete all validators in teardown."""
    validators = rpc_call("sim_getAllValidators", [])
    if not validators:
        provider = os.environ.get("TEST_PROVIDER", "openai")
        model = os.environ.get("TEST_PROVIDER_MODEL", "gpt-4o")
        rpc_call("sim_createRandomValidators", [5, 8, 12, [provider], [model]])
    yield


def _wait_for_tx(tx_hash: str, timeout: int = 240) -> dict:
    """Wait until the transaction is finalized (triggered messages are only
    emitted on finalization, which is what `emit()` defaults to)."""
    start = time.time()
    last = None
    while time.time() - start < timeout:
        tx = rpc_call("eth_getTransactionByHash", [tx_hash])
        last = tx and tx.get("status")
        if last == "FINALIZED":
            return tx
        if last in ("CANCELED", "UNDETERMINED", "LEADER_TIMEOUT", "VALIDATORS_TIMEOUT"):
            raise AssertionError(f"Transaction {tx_hash} ended as {last}: {tx}")
        time.sleep(2)
    raise TimeoutError(
        f"Transaction {tx_hash} not finalized in {timeout}s (last: {last})"
    )


def _deploy(code: str, args: list, reroute_to: str | None = None) -> str:
    """Deploy a contract, optionally pinning its executor version.

    genlayer-py sends `eth_sendRawTransaction` positionally and has no way to
    attach `sim_config`, so the signed transaction is built here and sent with
    named params instead.
    """
    data = serialize(
        [
            code,
            calldata.encode(make_calldata_object(method=None, args=args, kwargs=None)),
            False,  # leader_only
        ]
    )
    encoded_data = _encode_add_transaction_data(
        self=CLIENT,
        sender_account=ACCOUNT,
        recipient=ADDRESS_ZERO,
        consensus_max_rotations=CLIENT.chain.default_consensus_max_rotations,
        data=data,
    )
    transaction = _prepare_transaction(
        self=CLIENT,
        sender=ACCOUNT.address,
        recipient=CLIENT.chain.consensus_main_contract["address"],
        data=encoded_data,
    )
    signed = ACCOUNT.sign_transaction(transaction)

    params = {"signed_rollup_transaction": CLIENT.w3.to_hex(signed.raw_transaction)}
    if reroute_to is not None:
        params["sim_config"] = {"genvm_executor_selector": reroute_to}

    tx_hash = rpc_call("eth_sendRawTransaction", params)
    tx = _wait_for_tx(tx_hash)
    address = tx.get("contract_address") or tx.get("to_address")
    assert address and address != ADDRESS_ZERO, f"no contract address in {tx}"
    return address


def _write(address: str, method: str, args: list) -> dict:
    tx_hash = CLIENT.write_contract(address=address, function_name=method, args=args)
    return _wait_for_tx(tx_hash)


def _read(address: str, method: str, final: bool = True) -> str:
    return CLIENT.read_contract(
        address=address,
        function_name=method,
        args=[],
        transaction_hash_variant=(
            TransactionHashVariant.LATEST_FINAL
            if final
            else TransactionHashVariant.LATEST_NONFINAL
        ),
    )


def _wait_for_storage(address: str, expected: str, timeout: int = 240) -> None:
    """Triggered transactions are separate transactions; poll until the
    message they carry has been applied."""
    start = time.time()
    last = None
    while time.time() - start < timeout:
        last = _read(address, "get_storage", final=False)
        if last == expected:
            return
        time.sleep(2)
    raise AssertionError(
        f"Contract {address} storage is {last!r}, expected {expected!r}"
    )


@pytest.fixture(scope="module")
def peers() -> tuple[str, str]:
    """Deploy both contracts once and introduce them to each other.

    A runs on the current executor, B on the legacy one — B's deployment
    execution itself already requires the reroute to be honored, and the
    `set_peer` write on B is a plain user transaction with no sim_config, so
    it relies on the value persisted to the contract row.
    """
    address_a = _deploy(CONTRACT_A, args=["a_initial"])
    address_b = _deploy(CONTRACT_B, args=["b_initial"], reroute_to=LEGACY_EXECUTOR)

    assert _read(address_a, "get_storage") == "a_initial"
    # Reading B works only if the stored `reroute_to` is reused after deploy.
    assert _read(address_b, "get_storage") == "b_initial"

    _write(address_a, "set_peer", [address_b])
    _write(address_b, "set_peer", [address_a])
    assert _read(address_a, "get_peer") == address_b
    assert _read(address_b, "get_peer") == address_a

    return address_a, address_b


def test_message_from_current_to_legacy_executor(peers):
    """v0.3 -> v0.2 message: the triggered transaction runs on the legacy
    executor, which it can only pick up from the persisted `reroute_to`."""
    address_a, address_b = peers

    _write(address_a, "ping_peer", ["from_a"])
    _wait_for_storage(address_b, "from_a")


def test_message_from_legacy_to_current_executor(peers):
    """v0.2 -> v0.3 message: the triggered transaction runs on the current
    executor, because the target contract has no override."""
    address_a, address_b = peers

    _write(address_b, "ping_peer", ["from_b"])
    _wait_for_storage(address_a, "from_b")


def test_call_from_current_to_legacy_executor(peers):
    """v0.3 -> v0.2 synchronous call (read): the cross-major path.

    The caller's executor asks the host which executor the callee runs on, the
    host answers with the major derived from the callee's persisted
    `reroute_to`, and the manager runs the callee in a nested v0.2.17 executor
    that dials the same host listener. Without any part of that the call would
    fail the way the reverse direction below does.
    """
    address_a, address_b = peers

    # Read B directly first: earlier tests in this module write to it through
    # messages, so the only stable expectation is "whatever B actually holds".
    expected = _read(address_b, "get_storage")
    peer_storage = _read(address_a, "read_peer_storage", final=False)

    assert peer_storage == expected


@pytest.mark.xfail(
    strict=True,
    reason=(
        "v0.2 -> v0.3 synchronous call to an *unpinned* callee. The limit is "
        "ours, not the legacy executor's: v0.2.17 does issue "
        "RESOLVE_CALLCONTRACT_EXECUTOR (confirmed in the host call counts), but "
        "`Host.resolve_call_contract_executor` answers only for a callee that "
        "carries a `reroute_to` and returns None for anything else. So the "
        "answer here is 'stay in-process', the v0.2.17 executor loads A's v0.3 "
        "code itself, and rejects it with `invalid_contract "
        "absent_runner_comment`. Pinning A makes the same direction work -- see "
        "test_call_from_legacy_to_pinned_current_executor below. Messages cross "
        "either way, being separate transactions run by the target's own "
        "executor."
    ),
)
def test_call_from_legacy_to_current_executor(peers):
    """v0.2 -> v0.3 synchronous call (read), as opposed to a message."""
    _address_a, address_b = peers

    peer_storage = _read(address_b, "read_peer_storage", final=False)

    assert peer_storage is not None
    assert peer_storage != ""


@pytest.fixture(scope="module")
def pinned_peers() -> tuple[str, str]:
    """The same pair, except A is pinned to the current executor explicitly.

    The pin changes nothing about how A runs — it is already on that line. It
    changes what the *host* can say about A: `resolve_call_contract_executor`
    answers from the callee's stored `reroute_to`, so an unpinned A leaves it
    with nothing to answer and the caller keeps the callee in-process.
    """
    address_a = _deploy(CONTRACT_A, args=["a_pinned"], reroute_to=CURRENT_EXECUTOR)
    address_b = _deploy(CONTRACT_B, args=["b_pinned"], reroute_to=LEGACY_EXECUTOR)

    _write(address_a, "set_peer", [address_b])
    _write(address_b, "set_peer", [address_a])

    return address_a, address_b


def test_call_from_legacy_to_pinned_current_executor(pinned_peers):
    """v0.2 -> v0.3 synchronous call, with the callee pinned: this one works.

    Same direction as the xfail above, and the same legacy caller. The only
    difference is that A carries a `reroute_to`, so the host has something to
    answer the caller's resolve query with instead of None, and the manager
    runs A in a nested current-line executor rather than letting v0.2.17 try
    to parse a v0.3 contract.
    """
    _address_a, address_b = pinned_peers

    peer_storage = _read(address_b, "read_peer_storage", final=False)

    assert peer_storage == "a_pinned"
