import abc
import asyncio
import collections.abc
import contextlib
import json
import math
import socket
import time
import types
import typing
import urllib.parse
from dataclasses import dataclass, field

import aiohttp

from . import (
    calldata as gvm_calldata,
)
from . import (
    fees,
    host_fns,
    manager_api,
    public_abi,
)
from .calldata import Address
from .logger import Logger

ACCOUNT_ADDR_SIZE = 20
SLOT_ID_SIZE = 32

ZERO_SLOT = b"\x00" * SLOT_ID_SIZE
"""The root `SlotID`, all zeroes."""

ROOT_OFFSET_MAJOR = 0
"""Offset of the single-octet public-ABI `major` within the root slot."""

UNDEPLOYED_MAJOR = 0
"""
Major to declare when there is no deployed contract to read one from.

Every line released so far has semver major `0`, and the manager matches `0`
against all of them and picks the newest, so this is a de-facto "any line".
A deploy is the honest case for it: the real value comes from the contract
package, which this harness does not parse, and the harness pins its executor
with `unsafe_overrides.reroute_to` regardless. It is also why the manager cannot
yet reject `major == 0`.
"""

# Mirrors the executor's `DebugMode` enum (crates/common/src/debug_mode.rs).
DebugMode = typing.Literal[
    "disabled",
    "safe",
    "safe-unbounded",
    "unsafe",
    "unsafe-tracing",
]

# Default host-provided `node` fee constants (see fees.expr_prelude in
# install/config/genvm.yaml). Values are strings (gas_data is Map<str, str>)
# and are kept minimal/deterministic for tests. `validatorsPerRound` is
# intentionally omitted so the prelude default table is used.
DEFAULT_GAS_DATA: dict[str, str] = {
    "storageUnitPrice": "1",
    "receiptGasPerByte": "1",
    "gasPerChangedSlot": "1",
    "intrinsicGas": "0",
    "bootloaderOverhead": "0",
    "fixedProposeReceiptGas": "0",
    "fixedMessageRevealGas": "0",
    "genPerTimeUnit": "0",
    # 0 = no per-phase timeunit floor, so default-allocation tests are unaffected.
    "minTimeUnitsPerPhase": "0",
    # 0 = no per-round execution-budget floor for balance-funded messages.
    "messageBudgetFloor": "0",
}


class Context(typing.Protocol):
    logger: Logger

    def on_genvm_success(self): ...
    def on_genvm_failure(self): ...

    def add_stat(self, key: str, value: typing.Any, /): ...

    def get_manager_connect_timeout(self) -> float | None:
        return None


def _http_timeout(ctx: Context) -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(connect=ctx.get_manager_connect_timeout())


class HostException(Exception):
    def __init__(self, error_code: host_fns.Errors, message: str = ""):
        if error_code == host_fns.Errors.OK:
            raise ValueError("Error code cannot be OK")
        self.error_code = error_code
        super().__init__(message or f"GenVM error: {error_code}")


@dataclass(frozen=True)
class UnsafeOverrides:
    """Request overrides that reach boundaries production traffic cannot.

    Each member states the `debug_mode` the manager requires before it applies:
    `reroute_to` from `safe`, `initial_recursion` from `unsafe`. With debugging
    disabled none of them take effect.
    """

    reroute_to: str = ""
    initial_recursion: int | None = None

    def as_request_field(self) -> dict[str, typing.Any]:
        return {
            "reroute_to": self.reroute_to,
            "initial_recursion": self.initial_recursion,
        }


class Message(typing.TypedDict):
    contract_address: Address
    sender_address: Address
    origin_address: Address
    signer_address: Address
    chain_id: int
    value: typing.NotRequired[int]
    is_init: bool
    datetime: typing.NotRequired[str]


class FingerprintFrame(typing.TypedDict):
    module_name: str
    func: int


class ResultFingerprint(typing.TypedDict):
    frames: list[FingerprintFrame]
    module_instances: dict[str, typing.Any]


class EthSendInner(typing.TypedDict):
    type: typing.Literal["EthSend"]
    address: Address
    calldata: bytes
    value: int
    message_fee: int
    receipt_fee: int
    fee_params: fees.ExternalMessageParams


class PostMessageInner(typing.TypedDict):
    type: typing.Literal["PostMessage"]
    address: Address
    calldata: gvm_calldata.Decoded
    value: int
    on: typing.Literal["finalized", "accepted"]
    message_fee: int
    receipt_fee: int
    fee_params: fees.InternalMessageParams
    # ABI-encoded allocation subtree carried in the receipt under commitment modes.
    subtree: bytes
    # Chain `useBalance`: fee funded from the emitting contract's balance.
    use_balance: bool


class DeployContractInner(typing.TypedDict):
    type: typing.Literal["DeployContract"]
    calldata: gvm_calldata.Decoded
    code: bytes
    value: int
    on: typing.Literal["finalized", "accepted"]
    salt_nonce: int
    message_fee: int
    receipt_fee: int
    fee_params: fees.InternalMessageParams
    # ABI-encoded allocation subtree carried in the receipt under commitment modes.
    subtree: bytes
    # Chain `useBalance`: fee funded from the emitting contract's balance.
    use_balance: bool


class EmitEventInner(typing.TypedDict):
    type: typing.Literal["EmitEvent"]
    topics: list[bytes]
    blob: dict[str, gvm_calldata.Decoded]
    storage_fee: int


type ResultEmission = typing.Union[
    EthSendInner,
    PostMessageInner,
    DeployContractInner,
    EmitEventInner,
]


class IHost(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def loop_enter(self, cancellation: asyncio.Event) -> socket.socket: ...

    @abc.abstractmethod
    async def storage_read(
        self,
        mode: public_abi.StorageType,
        account: bytes,
        slot: bytes,
        index: int,
        le: int,
        /,
    ) -> bytes: ...

    async def resolve_callcontract_executor(
        self,
        contract_address: Address,
        state_mode: public_abi.StorageType,
        advisory_major: int,
        /,
    ) -> bytes | None:
        return None

    @abc.abstractmethod
    async def consume_gas(self, gas: int, /) -> None: ...
    @abc.abstractmethod
    async def eth_call(self, account: bytes, calldata: bytes, /) -> bytes: ...
    @abc.abstractmethod
    async def get_balance(self, account: bytes, /) -> int: ...
    @abc.abstractmethod
    async def remaining_fuel_as_gen(self, /) -> int: ...
    @abc.abstractmethod
    async def notify_nondet_disagreement(self, call_no: int, /) -> None: ...


async def read_contract_major(handler: IHost, message: Message) -> int:
    """
    Reads the public-ABI major the run's contract was deployed against.

    The major is octet `ROOT_OFFSET_MAJOR` of the root slot, written at deploy
    time and read on every load. A deploy has nothing to read, so it declares
    `UNDEPLOYED_MAJOR` instead.

    The read uses the storage view a top-level run itself uses, so a run cannot
    take its major from a state it would not otherwise observe. An address with
    no contract reads back `0`, which is indistinguishable from major `0`; that
    stays a fallback for now.
    """
    if message.get("is_init", False):
        return UNDEPLOYED_MAJOR
    octet = await handler.storage_read(
        public_abi.StorageType.LATEST_NON_FINAL,
        message["contract_address"].as_bytes,
        ZERO_SLOT,
        ROOT_OFFSET_MAJOR,
        1,
    )
    return octet[0]


async def host_loop(
    handler: IHost,
    cancellation: asyncio.Event,
    *,
    ctx: Context,
) -> None:
    """
    Accepts one connection through the handler, then serves it.
    """
    logger = ctx.logger

    logger.trace("entering loop")
    loop_enter_wait_start = time.perf_counter()
    sock = await handler.loop_enter(cancellation)
    host_loop_entered_s = time.perf_counter()
    ctx.add_stat("host_loop_entered_s", host_loop_entered_s)
    ctx.add_stat(
        "host_loop_enter_wait_ms",
        round((host_loop_entered_s - loop_enter_wait_start) * 1000),
    )
    logger.trace("entered loop")

    await host_loop_on(handler, sock, ctx=ctx)


async def host_loop_on(
    handler: IHost,
    sock: socket.socket,
    *,
    ctx: Context,
) -> None:
    """
    Serves an already accepted connection.

    A run that spawns nested executors produces several connections to one
    listener, so the accept step has to be separable from the protocol it feeds.
    """
    async_loop = asyncio.get_event_loop()

    logger = ctx.logger

    accept_time = time.perf_counter()
    first_method_name: str | None = None
    first_method_received_s: float | None = None

    socket_write_buffer = bytearray()

    async def send_all(data: bytes | memoryview):
        socket_write_buffer.extend(data)
        if len(socket_write_buffer) > 4096:
            await flush_socket_buffer()

    async def flush_socket_buffer():
        if len(socket_write_buffer) > 0:
            await async_loop.sock_sendall(sock, socket_write_buffer)
            socket_write_buffer.clear()

    socket_read_buf = bytearray(65536)
    socket_read_buf_view = memoryview(socket_read_buf)
    socket_read_start = 0
    socket_read_end = 0

    async def read_exact(le: int) -> bytes:
        nonlocal socket_read_start, socket_read_end
        out = bytearray(le)
        idx = 0
        while idx < le:
            available = socket_read_end - socket_read_start
            if available == 0:
                socket_read_start = 0
                socket_read_end = await async_loop.sock_recv_into(
                    sock, socket_read_buf_view
                )
                if socket_read_end == 0:
                    raise ConnectionResetError()
                available = socket_read_end
            take = min(available, le - idx)
            out[idx : idx + take] = socket_read_buf[
                socket_read_start : socket_read_start + take
            ]
            idx += take
            socket_read_start += take
        return bytes(out)

    async def recv_int(bytes: int = 4) -> int:
        return int.from_bytes(await read_exact(bytes), byteorder="little", signed=False)

    async def send_int(i: int, bytes=4):
        await send_all(int.to_bytes(i, bytes, byteorder="little", signed=False))

    async def read_slice() -> memoryview:
        le = await recv_int()
        data = await read_exact(le)
        return memoryview(data)

    total_handling_time = 0.0
    time_per_method = {}
    call_counts = {}
    meth_id: host_fns.Methods | None = None

    def emit_host_loop_stats():
        if first_method_name is not None:
            ctx.add_stat("host_first_method", first_method_name)
        ctx.add_stat("host_total_handling_time_ms", round(total_handling_time * 1000))
        ctx.add_stat(
            "host_time_per_method_ms",
            {k: round(v * 1000) for k, v in time_per_method.items()},
        )
        ctx.add_stat("call_counts", call_counts)
        logger.debug(
            "handling time",
            total=total_handling_time,
            by_method=time_per_method,
            call_counts=call_counts,
        )

    handling_start = time.time()
    while True:
        cur_delta = time.time() - handling_start
        if meth_id is not None:
            total_handling_time += cur_delta
            time_per_method[meth_id.name] = (
                time_per_method.get(meth_id.name, 0.0) + cur_delta
            )

        await flush_socket_buffer()

        try:
            meth_id = host_fns.Methods(await recv_int(1))
        except ConnectionResetError:
            emit_host_loop_stats()
            return None
        if first_method_name is None:
            first_method_name = meth_id.name
            first_method_received_s = time.perf_counter()
            ctx.add_stat("host_first_method_received_s", first_method_received_s)
            ctx.add_stat(
                "host_accept_to_first_method_ms",
                round((first_method_received_s - accept_time) * 1000),
            )
        logger.trace("got method", method=meth_id, method_name=meth_id.name)
        call_counts[meth_id.name] = call_counts.get(meth_id.name, 0) + 1

        handling_start = time.time()
        match meth_id:
            case host_fns.Methods.STORAGE_READ:
                mode = await read_exact(1)
                mode = public_abi.StorageType(mode[0])
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                slot = await read_exact(SLOT_ID_SIZE)
                index = await recv_int()
                le = await recv_int()
                try:
                    res = await handler.storage_read(mode, account, slot, index, le)
                    assert len(res) == le
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_all(res)
            case host_fns.Methods.RESOLVE_CALLCONTRACT_EXECUTOR:
                contract_address = Address(await read_exact(ACCOUNT_ADDR_SIZE))
                state_mode = public_abi.StorageType((await read_exact(1))[0])
                advisory_major = await recv_int(1)

                try:
                    res = await handler.resolve_callcontract_executor(
                        contract_address,
                        state_mode,
                        advisory_major,
                    )
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    encoded_res = gvm_calldata.encode(res)
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_int(len(encoded_res))
                    await send_all(encoded_res)
            case host_fns.Methods.CONSUME_RESULT:
                raise Exception(
                    "CONSUME_RESULT is not supported in this host loop implementation, use manager provided one"
                )
            case host_fns.Methods.CONSUME_FUEL:
                gas = await recv_int(32)
                await handler.consume_gas(gas)
            case host_fns.Methods.ETH_CALL:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                try:
                    res = await handler.eth_call(account, calldata)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_int(len(res))
                    await send_all(res)
            case host_fns.Methods.GET_BALANCE:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                try:
                    res = await handler.get_balance(account)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_all(res.to_bytes(32, byteorder="little", signed=False))
            case host_fns.Methods.REMAINING_FUEL_AS_GEN:
                try:
                    res = await handler.remaining_fuel_as_gen()
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_all(res.to_bytes(32, byteorder="little", signed=False))
            case host_fns.Methods.NOTIFY_NONDET_DISAGREEMENT:
                call_no = await recv_int()
                await handler.notify_nondet_disagreement(call_no)
                # No response needed according to the spec
            case x:
                raise Exception(f"unknown method {x}")


class ConsumedResultDecodeError(Exception):
    """`consumed_result` bytes did not parse into a `ConsumedResult` at all.

    Distinct from `ConsumedResult.internal_error(...)`, which is itself a
    valid (if unhappy) result value produced from bytes that *did* parse:
    this means the bytes never parsed, so there is no result to hand back.
    Callers must treat it the same as any other post-terminal failure -- see
    `run_genvm`'s `TerminalResultUnavailable` wrapping -- never as grounds to
    start a new run.
    """


@dataclass
class ConsumedResult:
    """The decoded `consumed_result` blob: a `ResultCode` byte plus calldata."""

    execution_hash: bytes
    result_kind: public_abi.ResultCode
    result_data: gvm_calldata.Decoded
    result_fingerprint: ResultFingerprint | None = None
    result_storage_changes: list[tuple[bytes, bytes]] = field(default_factory=list)
    result_emissions: list[ResultEmission] = field(default_factory=list)
    result_nondet_results: list[bytes] = field(default_factory=list)
    data_fees_remaining: list[int] = field(default_factory=list)

    @classmethod
    def internal_error(cls, message: str) -> "ConsumedResult":
        return cls(
            execution_hash=b"",
            result_kind=public_abi.ResultCode.INTERNAL_ERROR,
            result_data=message,
        )

    @classmethod
    def decode(cls, raw: typing.Any) -> "ConsumedResult":
        if raw is None:
            # The manager never attempted to report a result at all.
            return cls.internal_error("no_result")
        empty = False
        try:
            as_bytes = bytes(raw)
            empty = not as_bytes
            if not empty:
                result_kind = public_abi.ResultCode(as_bytes[0])
                decoded = gvm_calldata.decode(as_bytes[1:])
        except Exception as exc:
            # `raw` wasn't even bytes-shaped, or its ResultCode byte/calldata
            # failed to parse. Either way this is not a result the genvm
            # reported, it's a protocol violation: raise instead of returning
            # an `internal_error(...)` value so it can never be mistaken for a
            # real (if unhappy) execution outcome.
            raise ConsumedResultDecodeError(
                f"malformed consumed_result ({raw!r}): {exc}"
            ) from exc
        if empty:
            # Distinct from `None`: the manager did send a `consumed_result`,
            # but it carries no `ResultCode` byte to read. Still "no usable
            # result data" from the caller's point of view.
            return cls.internal_error("empty_result")
        if not isinstance(decoded, dict):
            return cls.internal_error("result is not a mapping")
        return cls(
            execution_hash=decoded.get("execution_hash", b""),
            result_kind=result_kind,
            result_data=decoded.get("data"),
            result_fingerprint=decoded.get("fingerprint"),
            result_storage_changes=decoded.get("storage_changes", []),
            result_emissions=decoded.get("emissions", []),
            result_nondet_results=decoded.get("nondet_results", []),
            data_fees_remaining=decoded.get("data_fees_remaining", []),
        )


@dataclass
class RunHostAndProgramRes:
    stdout: str
    stderr: str
    genvm_log: list[dict[str, typing.Any]]

    execution_time: float

    execution_hash: bytes

    result_kind: public_abi.ResultCode
    result_data: gvm_calldata.Decoded
    result_fingerprint: ResultFingerprint | None
    result_storage_changes: list[tuple[bytes, bytes]]
    result_emissions: list[ResultEmission]
    result_nondet_results: list[bytes]
    data_fees_remaining: list[int]
    metrics: dict[str, typing.Any] | None = None
    vm_error_description: str | None = None


class Frame(typing.NamedTuple):
    """One manager socket message: a method, a request id and a calldata payload."""

    method: manager_api.Methods
    request_id: int
    payload: typing.Any


class ManagerSocketError(Exception):
    def __init__(self, code: manager_api.Errors, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code.name}: {message}")


_MANAGER_RECONNECT_ATTEMPTS = 3
_MANAGER_RECONNECT_BACKOFF_S = 0.05


class ManagerConnectionLost(Exception):
    """
    The manager socket dropped.

    `generation` is the connection this happened on, so a waiter that wakes up
    after somebody else already reconnected can tell that its loss is stale
    rather than reconnecting a second time.
    """

    def __init__(self, message: str, generation: int = -1):
        super().__init__(message)
        self.generation = generation


class ManagerRunLost(Exception):
    """
    The manager process that owned a run is gone, so the run is unrecoverable.

    Deliberately not a `ManagerConnectionLost`: that one means "reconnect and keep
    waiting", and a waiter that treats this as one waits forever for a run no
    reconnect can bring back.
    """


class ManagerRunNotStarted(Exception):
    """A run never reached execution, so retrying it re-executes nothing.

    Raised for the two ways the manager can refuse before the genvm runs -- a
    rejected RUN request and a `failed_to_start` terminal. Retry policy belongs
    to the caller; `retryable` tells it whether the refusal is transient (the
    manager is still bringing modules up, or the socket bounced) rather than a
    permanent rejection (malformed request, absent runner, bad calldata). This
    is deliberately distinct from a returned `RETURN`/`*_ERROR` result, which
    means the genvm did run and must never be blindly retried.
    """

    def __init__(self, message: str, *, retryable: bool, reason: str):
        self.retryable = retryable
        self.reason = reason
        super().__init__(message)


class TerminalResultUnavailable(Exception):
    """A run reached a terminal `finished` state, but its result could not be
    retrieved or decoded -- artifact transfer failed, or `consumed_result` was
    empty/malformed.

    The genvm already executed exactly once for this `genvm_id`; the caller
    must never treat this the way it treats a plain `Exception` from
    `run_genvm` (which starts a brand new run on retry). Always non-retryable
    for that reason -- there is no `retryable` flag to override.
    """

    def __init__(self, message: str, *, genvm_id: int):
        self.genvm_id = genvm_id
        super().__init__(message)


# Transient manager refusals worth retrying. The manager reports these with the
# generic `Errors.INTERNAL` code (no dedicated variant yet), so the message is
# the only discriminator -- kept here so callers never have to string-match.
_RETRYABLE_RUN_REFUSAL_MARKERS: typing.Final = (
    "modules are required but not running",
    "modules are required but not all are running",
)


def _classify_run_refusal(message: str) -> tuple[bool, str]:
    for marker in _RETRYABLE_RUN_REFUSAL_MARKERS:
        if marker in message:
            return True, "manager_modules_not_running"
    return False, "manager_refused"


@dataclass
class RunState:
    boot_id: int
    genvm_id: int
    host_genvm_id: str | None
    events: asyncio.Queue[dict[str, typing.Any] | BaseException]
    terminal: dict[str, typing.Any] | None = None
    acked: bool = False


class ManagerClient:
    def __init__(
        self,
        manager_uri: str,
        *,
        connect_timeout: float | None = None,
        max_msg_size: int = 64 * 1024 * 1024,
    ):
        self.manager_uri = manager_uri
        self.connect_timeout = connect_timeout
        self.max_msg_size = max_msg_size
        self.boot_id: int | None = None
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._reader_task: asyncio.Task | None = None
        self._request_id = 1
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        # The protocol identifies a run as (boot_id, genvm_id): ids restart at 1
        # with the manager process, so genvm_id alone aliases across a restart.
        self._runs: dict[tuple[int, int], RunState] = {}
        self._orphan_events: dict[tuple[int, int], list[dict[str, typing.Any]]] = {}
        self._connect_lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._reconnect_lock = asyncio.Lock()
        # Bumped on every successful connect, so a disconnect can be attributed
        # to the connection it happened on.
        self._generation = 0

    @property
    def run_states(self) -> dict[tuple[int, int], RunState]:
        return self._runs

    def _key(self, genvm_id: int) -> tuple[int, int]:
        assert self.boot_id is not None, "no hello received yet"
        return (self.boot_id, genvm_id)

    async def __aenter__(self):
        await self._ensure_connected()
        return self

    async def __aexit__(self, *_args):
        await self._close()

    async def disconnect(self) -> None:
        await self._close()

    async def run(self, payload: dict[str, typing.Any]) -> RunState:
        response = await self._request(
            manager_api.Methods.RUN,
            {"run": payload},
            retry_on_disconnect=True,
        )
        genvm_id = int(response["genvm_id"])
        key = self._key(genvm_id)
        state = self._runs.get(key)
        if state is None:
            state = RunState(
                boot_id=key[0],
                genvm_id=genvm_id,
                host_genvm_id=payload.get("host_genvm_id"),
                events=asyncio.Queue(),
            )
            self._runs[key] = state
            for event in self._orphan_events.pop(key, []):
                self._queue_event(state, event)
        return state

    async def attach(self, boot_id: int, genvm_id: int) -> RunState:
        response = await self._request(
            manager_api.Methods.ATTACH,
            {"attach": {"boot_id": boot_id, "genvm_id": genvm_id}},
            retry_on_disconnect=True,
        )
        key = (boot_id, genvm_id)
        state = self._runs.get(key)
        if state is None:
            state = RunState(
                boot_id=boot_id,
                genvm_id=genvm_id,
                host_genvm_id=None,
                events=asyncio.Queue(),
            )
            self._runs[key] = state
        self._queue_event(state, response["snapshot"])
        return state

    def _require_current_generation(
        self, boot_id: int, genvm_id: int, *, op: str
    ) -> None:
        """Refuse to act on a run from a manager generation we have since left.

        `CANCEL`/`ACK`/`GET_ARTIFACT` identify a run to the manager by
        `genvm_id` alone -- the wire protocol has no `boot_id` field for them
        (unlike `ATTACH`, which the manager itself rejects on a mismatch). If
        the manager restarted and reused this `genvm_id` for an unrelated run,
        sending one of these blind would act on that run instead of a stale
        reference to the one this call actually means.
        """
        if self.boot_id is not None and boot_id != self.boot_id:
            raise ManagerRunLost(
                f"refusing to {op} genvm {genvm_id}: it belonged to manager "
                f"boot {boot_id}, but the client is now talking to boot "
                f"{self.boot_id} -- the id may have been reused by the new "
                "process"
            )

    async def cancel(self, boot_id: int, genvm_id: int) -> None:
        self._require_current_generation(boot_id, genvm_id, op="cancel")
        await self._request(
            manager_api.Methods.CANCEL,
            {"cancel": {"genvm_id": genvm_id}},
            retry_on_disconnect=True,
        )

    async def ack(self, boot_id: int, genvm_id: int) -> None:
        self._require_current_generation(boot_id, genvm_id, op="ack")
        await self._request(
            manager_api.Methods.ACK,
            {"ack": {"genvm_id": genvm_id}},
            retry_on_disconnect=True,
        )
        key = (boot_id, genvm_id)
        if state := self._runs.get(key):
            state.acked = True
        self._runs.pop(key, None)
        self._orphan_events.pop(key, None)

    async def get_artifact(self, boot_id: int, genvm_id: int, field: str) -> bytes:
        self._require_current_generation(boot_id, genvm_id, op="get_artifact")
        offset = 0
        out = bytearray()
        while True:
            response = await self._request(
                manager_api.Methods.GET_ARTIFACT,
                {
                    "get_artifact": {
                        "genvm_id": genvm_id,
                        "field": field,
                        "offset": offset,
                        "max_len": 256 * 1024,
                    }
                },
                retry_on_disconnect=True,
            )
            data = bytes(response["data"])
            out.extend(data)
            offset += len(data)
            if offset >= int(response["total_len"]):
                return bytes(out)
            if not data:
                raise ManagerConnectionLost("artifact transfer made no progress")

    async def wait_terminal(self, state: RunState) -> dict[str, typing.Any]:
        if state.terminal is not None:
            return state.terminal
        while True:
            event = await state.events.get()
            if isinstance(event, ManagerConnectionLost):
                await self._reconnect_live_runs(event.generation)
                continue
            if isinstance(event, BaseException):
                raise event
            for variant in ("failed_to_start", "finished"):
                if variant in event:
                    state.terminal = event
                    return event

    async def _request(
        self,
        method: manager_api.Methods,
        payload: typing.Any,
        *,
        retry_on_disconnect: bool,
    ) -> typing.Any:
        last_error: BaseException | None = None
        for attempt in range(_MANAGER_RECONNECT_ATTEMPTS if retry_on_disconnect else 1):
            generation = self._generation
            try:
                return await self._request_once(method, payload)
            except ManagerConnectionLost as exc:
                last_error = exc
                if attempt + 1 < _MANAGER_RECONNECT_ATTEMPTS and retry_on_disconnect:
                    await asyncio.sleep(_MANAGER_RECONNECT_BACKOFF_S * (2**attempt))
                    await self._reconnect_live_runs(generation)
                    continue
                raise
        assert last_error is not None
        raise last_error

    async def _request_once(
        self,
        method: manager_api.Methods,
        payload: typing.Any,
    ) -> typing.Any:
        await self._ensure_connected()
        assert self._ws is not None
        async with self._send_lock:
            request_id = self._request_id
            self._request_id += 1
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future
            body = (
                int(method).to_bytes(2, "big")
                + request_id.to_bytes(8, "big")
                + gvm_calldata.encode(payload)
            )
            try:
                await self._ws.send_bytes(body)
            except (
                BrokenPipeError,
                ConnectionError,
                aiohttp.ClientConnectionError,
            ) as exc:
                self._pending.pop(request_id, None)
                self._mark_disconnected(ManagerConnectionLost(str(exc)))
                raise ManagerConnectionLost(str(exc)) from exc
        try:
            response = await future
        finally:
            self._pending.pop(request_id, None)
        if response.method != method:
            raise ManagerConnectionLost(
                f"manager replied with {response.method} for {method}"
            )
        return response.payload

    async def _ensure_connected(self) -> None:
        if self._ws is not None and not self._ws.closed:
            return
        async with self._connect_lock:
            await self._reconnect_locked(-1)

    async def _connect(self) -> None:
        timeout = aiohttp.ClientTimeout(connect=self.connect_timeout)
        if self.manager_uri.startswith("unix://"):
            connector = aiohttp.UnixConnector(
                path=self.manager_uri.removeprefix("unix://")
            )
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
            ws_url = "http://localhost/ws"
        else:
            self._session = aiohttp.ClientSession(timeout=timeout)
            ws_url = urllib.parse.urljoin(self.manager_uri.rstrip("/") + "/", "ws")
        try:
            self._ws = await self._session.ws_connect(
                ws_url,
                max_msg_size=self.max_msg_size,
            )
            frame = await self._read_frame()
        except BaseException:
            await self._close()
            raise
        if frame.method != manager_api.Methods.HELLO or frame.request_id != 0:
            await self._close()
            raise ManagerConnectionLost("manager did not send hello")
        hello = frame.payload["hello"]
        if hello["protocol_major"] != manager_api.CURRENT_MAJOR:
            await self._close()
            raise ManagerConnectionLost(
                f'unsupported manager socket protocol {hello["protocol_major"]}'
            )
        hello_boot_id = int(hello["boot_id"])
        # Always adopt the generation we are actually talking to. Keeping the old
        # one because a run is still around is what let stale state outlive a
        # manager restart and alias a reused id.
        if self.boot_id is not None and self.boot_id != hello_boot_id:
            self._retire_generation(self.boot_id)
        self.boot_id = hello_boot_id
        self._generation += 1
        self._reader_task = asyncio.create_task(self._reader_loop())

    def _retire_generation(self, boot_id: int) -> None:
        """
        Drops every run belonging to a manager process that is gone.

        Their ids are about to be handed out again by the new process, so a
        waiter must learn its run died with its manager rather than silently
        binding to an unrelated run of the same number.
        """
        for key in [k for k in self._runs if k[0] == boot_id]:
            state = self._runs.pop(key)
            if not state.acked and state.terminal is None:
                state.events.put_nowait(
                    ManagerRunLost(
                        f"manager restarted; run {state.genvm_id} of boot {boot_id} is gone"
                    )
                )
        for key in [k for k in self._orphan_events if k[0] == boot_id]:
            del self._orphan_events[key]

    async def _reader_loop(self) -> None:
        try:
            while True:
                frame = await self._read_frame()
                if frame.request_id == 0:
                    self._handle_notification(frame.method, frame.payload)
                    continue
                future = self._pending.pop(frame.request_id, None)
                if future is None or future.done():
                    continue
                if frame.method == manager_api.Methods.ERROR:
                    code = manager_api.Errors(frame.payload["code"])
                    future.set_exception(
                        ManagerSocketError(code, frame.payload["message"])
                    )
                else:
                    future.set_result(frame)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._mark_disconnected(ManagerConnectionLost(str(exc)))

    async def _read_frame(self) -> Frame:
        assert self._ws is not None
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            body = bytes(msg.data)
        elif msg.type in (
            aiohttp.WSMsgType.CLOSE,
            aiohttp.WSMsgType.CLOSED,
            aiohttp.WSMsgType.ERROR,
        ):
            raise ManagerConnectionLost("manager websocket closed")
        else:
            raise ManagerConnectionLost(f"unexpected websocket message {msg.type}")
        if len(body) < 10:
            raise ManagerConnectionLost("manager sent a short message")
        method = manager_api.Methods(int.from_bytes(body[:2], "big"))
        request_id = int.from_bytes(body[2:10], "big")
        payload = gvm_calldata.decode(body[10:])
        return Frame(method, request_id, payload)

    def _handle_notification(
        self, method: manager_api.Methods, payload: typing.Any
    ) -> None:
        if method != manager_api.Methods.EVENT:
            return
        genvm_id = None
        for event in payload.values():
            if isinstance(event, dict) and "genvm_id" in event:
                genvm_id = int(event["genvm_id"])
                break
        if genvm_id is None:
            return
        # Events arrive on the live connection, so they belong to its generation.
        key = self._key(genvm_id)
        state = self._runs.get(key)
        if state is None:
            self._orphan_events.setdefault(key, []).append(payload)
            return
        self._queue_event(state, payload)

    def _queue_event(self, state: RunState, event: dict[str, typing.Any]) -> None:
        if "failed_to_start" in event or "finished" in event:
            state.terminal = event
        state.events.put_nowait(event)

    def _mark_disconnected(self, exc: ManagerConnectionLost) -> None:
        exc.generation = self._generation
        self._ws = None
        for future in list(self._pending.values()):
            if not future.done():
                future.set_exception(exc)
        for state in self._runs.values():
            if not state.acked and state.terminal is None:
                state.events.put_nowait(exc)

    async def _reconnect_locked(self, generation: int) -> None:
        """
        Rebuilds the connection unless someone already did. Holds `_connect_lock`.

        Every teardown-and-reconnect goes through here, so the two entry points
        (`_ensure_connected` and `_reconnect_live_runs`) cannot run `_close()`
        and `_connect()` against each other and orphan a reader task, a session
        or a socket the other one is installing.

        `generation < 0` means "any live connection will do"; a non-negative one
        names the generation the caller saw die, so a connection newer than it
        is already the replacement and is left alone.
        """
        assert self._connect_lock.locked()
        if self._ws is not None and not self._ws.closed:
            if generation < 0 or self._generation > generation:
                return
        await self._close()
        await self._connect()

    async def _reconnect_live_runs(self, generation: int) -> None:
        """
        Reconnects the connection generation `generation` was lost on.

        Several waiters observe the same disconnect, so whoever gets the lock
        second finds a newer generation already connected and has nothing to do.
        Reconnecting again there would tear down the working socket the first
        waiter just built.
        """
        async with self._reconnect_lock:
            async with self._connect_lock:
                await self._reconnect_locked(generation)
            # Re-attaching runs its own requests, which take `_connect_lock`
            # themselves, so it happens outside of it. `_reconnect_lock` still
            # keeps a second waiter out until this generation is whole again.
            #
            # A restart already retired the previous generation's runs during
            # hello, so whatever is left here belongs to the manager we are now
            # talking to and re-attaches under its own boot id.
            for state in list(self._runs.values()):
                if state.acked:
                    continue
                try:
                    response = await self._request(
                        manager_api.Methods.ATTACH,
                        {
                            "attach": {
                                "boot_id": state.boot_id,
                                "genvm_id": state.genvm_id,
                            }
                        },
                        retry_on_disconnect=False,
                    )
                except ManagerSocketError as exc:
                    state.events.put_nowait(exc)
                else:
                    self._queue_event(state, response["snapshot"])

    async def _close(self) -> None:
        ws = self._ws
        self._ws = None
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(BaseException):
                await self._reader_task
            self._reader_task = None
        if ws is not None:
            with contextlib.suppress(BaseException):
                await ws.close()
        if self._session is not None:
            with contextlib.suppress(BaseException):
                await self._session.close()
            self._session = None


def _duration_string(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    if seconds < 1:
        millis = max(1, math.ceil(seconds * 1000))
        return f"{millis}ms"
    if float(seconds).is_integer():
        return f"{int(seconds)}s"
    return f"{seconds:g}s"


def _decode_genvm_log(data: bytes) -> list[dict[str, typing.Any]]:
    """Decodes the genvm log artifact, which is json lines.

    Split on newline bytes only. `str.splitlines` also breaks on U+2028, U+2029
    and U+0085, none of which json escapes and all of which therefore appear raw
    inside a string value -- splitting there cuts a record in half and raises
    `Unterminated string`. Model output reaches this log, so those codepoints do
    turn up in practice.
    """
    if not data:
        return []
    return [json.loads(line) for line in data.split(b"\n") if line.strip()]


async def run_genvm(
    handler: IHost,
    *,
    timeout: float | None = None,  # noqa: ASYNC109
    manager_uri: str = "http://127.0.0.1:3999",
    manager_client: ManagerClient,
    ctx: Context,
    is_sync: bool,
    debug_mode: DebugMode = "disabled",
    message: Message,
    host_data: str = "",
    gas_data: dict[str, str] | None = None,
    host: str,
    extra_args: collections.abc.Sequence[str] = (),
    # default config fee buckets use bucket_no 0 and 1
    bucket_totals: list[int],
    code: bytes | None = None,
    calldata: bytes,
    leader_nondet_results: list[bytes] | None = None,
    message_fee_allocation: collections.abc.Sequence[fees.MessageAllocationNode] = (),
    unsafe_overrides: UnsafeOverrides | None = None,
    request_extra: collections.abc.Mapping[
        str, gvm_calldata.Encodable
    ] = types.MappingProxyType({}),
    shutdown_early: asyncio.Event | None = None,
    host_hello_data: collections.abc.Sequence[bytes] = (),
    major: int | None = None,
) -> RunHostAndProgramRes:
    logger = ctx.logger

    # `node` fee constants are an ExecutionData field.
    effective_gas_data = DEFAULT_GAS_DATA if gas_data is None else gas_data
    cancellation_event = asyncio.Event()
    host_task = asyncio.create_task(host_loop(handler, cancellation_event, ctx=ctx))

    client = manager_client
    genvm_id: int | None = None
    boot_id: int | None = None
    cancel_task: asyncio.Task | None = None
    terminal_task: asyncio.Task | None = None
    terminal_received = False
    try:
        max_exec_mins = 20
        if timeout is not None:
            max_exec_mins = int(max(max_exec_mins, (timeout * 1.5 + 59) // 60))
        timestamp = message.get("datetime", "2024-11-26T06:42:42.424242Z")
        deadline = _duration_string(timeout)
        host_genvm_id = typing.cast(str | None, request_extra.get("host_genvm_id"))
        if host_genvm_id is None:
            host_genvm_id = f"{time.time_ns()}-{id(asyncio.current_task())}"
        request_payload: dict[str, typing.Any] = {
            "selector": {
                "kind": "major",
                "major": (
                    await read_contract_major(handler, message)
                    if major is None
                    else major
                ),
            },
            "message": message,
            "is_sync": is_sync,
            "debug_mode": debug_mode,
            "host_data": host_data,
            "max_execution_minutes": max_exec_mins,
            "timestamp": timestamp,
            "host": host,
            "extra_args": list(extra_args),
            "code": code,
            "calldata": calldata,
            "leader_nondet_results": leader_nondet_results,
            "bucket_totals": bucket_totals,
            "gas_data": effective_gas_data,
            "message_fee_allocation": list(message_fee_allocation),
            "initial_time_units_allocation": math.ceil(timeout or 10 * 60),
            "unsafe_overrides": (
                unsafe_overrides or UnsafeOverrides()
            ).as_request_field(),
            "host_genvm_id": host_genvm_id,
            "host_hello_data": list(host_hello_data),
            **request_extra,
        }
        if deadline is not None:
            request_payload["deadline"] = deadline

        attempt_start = time.perf_counter()
        try:
            state = await client.run(request_payload)
        except Exception as exc:
            if isinstance(exc, ManagerConnectionLost):
                retryable, reason = True, "manager_connection_lost"
            elif isinstance(exc, ManagerSocketError):
                retryable, reason = _classify_run_refusal(exc.message)
            else:
                retryable, reason = False, "manager_run_error"
            ctx.add_stat(
                "manager_run_attempt_0_error",
                {
                    "attempt": 0,
                    "error_type": type(exc).__name__,
                    "duration_ms": round((time.perf_counter() - attempt_start) * 1000),
                    "retryable": retryable,
                    "reason": reason,
                },
            )
            ctx.on_genvm_failure()
            cancellation_event.set()
            raise ManagerRunNotStarted(
                str(exc), retryable=retryable, reason=reason
            ) from exc
        genvm_id = state.genvm_id
        boot_id = state.boot_id
        ctx.add_stat(
            "manager_run_attempt_success",
            {
                "attempt": 0,
                "duration_ms": round((time.perf_counter() - attempt_start) * 1000),
            },
        )
        logger.debug("genvm manager socket run", genvm_id=genvm_id)
        ctx.on_genvm_success()
        started_at = time.time()

        async def cancel_on_shutdown():
            assert genvm_id is not None
            if shutdown_early is None:
                return
            await shutdown_early.wait()
            logger.debug("shutdown_early event set", genvm_id=genvm_id)
            await client.cancel(boot_id, genvm_id)

        if shutdown_early is not None:
            cancel_task = asyncio.create_task(cancel_on_shutdown())

        terminal_task = asyncio.create_task(client.wait_terminal(state))
        while True:
            done, _pending = await asyncio.wait(
                [terminal_task, host_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if terminal_task in done:
                terminal = terminal_task.result()
                terminal_received = True
                break
            if host_task.done():
                host_exc = host_task.exception()
                if host_exc is not None:
                    await client.cancel(boot_id, genvm_id)
                    cancellation_event.set()
                    raise host_exc
                terminal = await terminal_task
                terminal_received = True
                break

        cancellation_event.set()
        if cancel_task is not None:
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task
        with contextlib.suppress(BaseException):
            await host_task

        if "finished" in terminal:
            status = terminal["finished"]
            sizes = status.get("artifact_sizes") or {}
            try:
                stdout = (
                    (await client.get_artifact(boot_id, genvm_id, "stdout")).decode()
                    if sizes.get("stdout", 0)
                    else ""
                )
                stderr = (
                    (await client.get_artifact(boot_id, genvm_id, "stderr")).decode()
                    if sizes.get("stderr", 0)
                    else ""
                )
                genvm_log = (
                    _decode_genvm_log(
                        await client.get_artifact(boot_id, genvm_id, "genvm_log")
                    )
                    if sizes.get("genvm_log", 0)
                    else []
                )
                consumed = ConsumedResult.decode(status.get("consumed_result"))
            except Exception as exc:
                # The run already executed and finished; a failure retrieving or
                # decoding its result must never look like an attempt that
                # never happened. That distinction is what keeps the outer
                # retry loop (`run_genvm_host`) from starting a second
                # execution for a result that already exists.
                raise TerminalResultUnavailable(
                    f"failed to retrieve/decode result for finished run: {exc}",
                    genvm_id=genvm_id,
                ) from exc
            if (
                status.get("cause") == "deadline"
                and consumed.result_kind != public_abi.ResultCode.RETURN
            ):
                consumed.result_kind = public_abi.ResultCode.VM_ERROR
                consumed.result_data = str(public_abi.VmError.timeout())
        else:
            # A failed_to_start terminal means the genvm was accepted but never
            # ran, so surface it through the same not-started seam as a rejected
            # RUN -- never as an INTERNAL_ERROR result the caller could mistake
            # for a run that executed.
            error = terminal["failed_to_start"]["error"]
            retryable, reason = _classify_run_refusal(error)
            ctx.add_stat(
                "manager_run_start_failed",
                {"reason": reason, "retryable": retryable},
            )
            # Symmetric with the client.run() rejection branch (and with the
            # INTERNAL_ERROR result this used to return): a run that failed to
            # start is a manager failure for health tracking.
            ctx.on_genvm_failure()
            raise ManagerRunNotStarted(error, retryable=retryable, reason=reason)

        vm_error_description: str | None = None
        if consumed.result_kind == public_abi.ResultCode.VM_ERROR and isinstance(
            consumed.result_data, str
        ):
            try:
                async with aiohttp.request(
                    "GET",
                    f"{manager_uri}/vm-error/describe",
                    params={"error": consumed.result_data},
                    timeout=_http_timeout(ctx),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        vm_error_description = body.get("description")
            except Exception as e:
                logger.warning("failed to get vm error description", error=str(e))

        return RunHostAndProgramRes(
            stdout=stdout,
            stderr=stderr,
            genvm_log=genvm_log,
            metrics=status.get("metrics"),
            execution_hash=consumed.execution_hash,
            result_kind=consumed.result_kind,
            result_data=consumed.result_data,
            result_fingerprint=consumed.result_fingerprint,
            result_storage_changes=consumed.result_storage_changes,
            result_emissions=consumed.result_emissions,
            result_nondet_results=consumed.result_nondet_results,
            data_fees_remaining=consumed.data_fees_remaining,
            vm_error_description=vm_error_description,
            execution_time=time.time() - started_at,
        )
    finally:
        if cancel_task is not None and not cancel_task.done():
            cancel_task.cancel()
            with contextlib.suppress(BaseException):
                await cancel_task
        if terminal_task is not None and not terminal_task.done():
            terminal_task.cancel()
            with contextlib.suppress(BaseException):
                await terminal_task
        if genvm_id is not None and terminal_received:
            # ack only releases the manager's retention of a run that already
            # produced its result and artifacts. Letting it fail out of here
            # would turn a finished execution into an attempt error and send
            # the caller back through the retry loop to run the contract again.
            try:
                await client.ack(boot_id, genvm_id)
            except Exception as exc:
                logger.warning(
                    "failed to ack a finished genvm run",
                    genvm_id=genvm_id,
                    error=str(exc),
                )
        elif genvm_id is not None:
            with contextlib.suppress(BaseException):
                await client.cancel(boot_id, genvm_id)
        if not cancellation_event.is_set():
            cancellation_event.set()
        if not host_task.done():
            host_task.cancel()
            with contextlib.suppress(BaseException):
                await host_task
