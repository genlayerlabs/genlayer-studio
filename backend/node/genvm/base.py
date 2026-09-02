# backend/node/genvm/base.py

__all__ = (
    "run_genvm_host",
    "Host",
    "StateProxy",
    "StateProxyWritable",
    "ExecutionError",
    "ExecutionReturn",
    "ExecutionResult",
    "apply_storage_changes",
    "GenVMInternalError",
    "GenVMFeeContext",
    "Context",
    "set_genvm_callbacks",
)

import math
import os
import re
import typing
import tempfile
from pathlib import Path
import shutil
import json
import base64
import asyncio
import contextlib
import socket
import backend.node.genvm.origin.base_host as genvmhost
import collections.abc
import functools
import abc
import time
import copy

from eth_abi import decode, encode

from backend.node.types import (
    PendingTransaction,
    Address,
)
import backend.node.genvm.origin.calldata as gvm_calldata
from dataclasses import dataclass

from .origin.public_abi import *
from .origin import base_host
from .origin import host_fns

# The wire result code lives in `host_fns`; `public_abi` only carries the
# subset the SDK exposes, so import it last to win over the star import.
from .origin.host_fns import ResultCode
from .origin import logger as genvm_logger
from .origin.leader_public_data import LeaderPublicData
from .error_codes import (
    extract_error_code,
    extract_error_code_from_timeout,
    parse_module_error_string,
    parse_ctx_from_module_error_string,
    GenVMInternalError,
)

GENVM_GASLESS_GAS_DATA: dict[str, str] = {
    **genvmhost.DEFAULT_GAS_DATA,
    "storageUnitPrice": "0",
    "receiptGasPerByte": "0",
    "gasPerChangedSlot": "0",
    "intrinsicGas": "0",
    "bootloaderOverhead": "0",
    "fixedProposeReceiptGas": "0",
    "fixedMessageRevealGas": "0",
    "lockedReceiptGasPrice": "0",
    "genPerTimeUnit": "0",
}

INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = (
    "(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"
)
INTERNAL_MESSAGE_FEE_PARAMS_WITH_CAPS_ABI_TYPE = (
    "(uint256,uint256,uint256,uint256,uint256[],uint256,uint256,uint256)"
)
EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = "(uint256,uint256)"
MESSAGE_ALLOCATION_NODE_ABI_TYPE = (
    "(uint8,bool,uint256,address,bytes32,uint256,bytes)[]"
)
MESSAGE_TYPE_EXTERNAL = 0
MESSAGE_TYPE_INTERNAL = 1
NODE_ROOT_SENTINEL = (1 << 256) - 1
# Consensus reserves keccak256("") as the wildcard. bytes32(0) is the real
# call key for deploys and unnamed calls, so the two must never be aliased.
CALL_KEY_WILDCARD = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


@dataclass(frozen=True)
class GenVMFeeContext:
    bucket_totals: dict[str, int] | None = None
    gas_data: dict[str, str] | None = None
    message_fee_allocation: list[dict] | None = None


@dataclass
class ExecutionError:
    message: str
    kind: typing.Literal[ResultCode.USER_ERROR, ResultCode.VM_ERROR]
    error_code: str | None = None  # Standardized error code (e.g., LLM_RATE_LIMITED)
    raw_error: dict | None = None  # Full Lua error structure (causes, fatal, ctx)
    description: str | None = None

    def __repr__(self):
        data = {"kind": self.kind.name, "message": self.message}
        if self.error_code:
            data["error_code"] = self.error_code
        if self.raw_error:
            data["raw_error"] = self.raw_error
        return json.dumps(data)


@dataclass
class ExecutionReturn:
    ret: bytes

    def __repr__(self):
        return json.dumps(
            {"kind": "return", "data": base64.b64encode(self.ret).decode("ascii")}
        )


def encode_result_to_bytes(result: ExecutionReturn | ExecutionError) -> bytes:
    if isinstance(result, ExecutionReturn):
        return bytes([ResultCode.RETURN]) + result.ret
    if isinstance(result, ExecutionError):
        return bytes([result.kind]) + result.message.encode("utf-8")


# Interface for accessing the blockchain state, it is needed to not tangle current (awfully unoptimized)
# storage format with the genvm source code
class StateProxy(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def storage_read(
        self, account: Address, slot: bytes, index: int, le: int, /
    ) -> bytes: ...

    @abc.abstractmethod
    def get_balance(self, addr: Address) -> int: ...

    def genvm_executor_selector_for(self, addr: Address) -> str | None:
        """Executor selector this contract is pinned to, or None if unpinned.

        Backs the nested cross-major `resolve_call_contract_executor` hook: the
        genvm asks which executor a call target runs on, and a pinned contract
        answers with its stored `genvm_executor_selector`. Proxies without a
        contract store (e.g. deploy-time `_StateProxyNone`) inherit this None
        default.
        """
        return None


class StateProxyWritable(StateProxy, metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def storage_read(
        self, account: Address, slot: bytes, index: int, le: int, /
    ) -> bytes: ...
    @abc.abstractmethod
    def storage_write(
        self,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None: ...
    @abc.abstractmethod
    def get_balance(self, addr: Address) -> int: ...


EXECUTOR_VERSION_RE: typing.Final = re.compile(r"v?\d+(\.\d+)*(-[0-9A-Za-z.]+)?")
"""
Shape of an exact executor pin.

The manager uses a pin verbatim as the executor directory name, so anything not
shaped like a version must never reach it as a path component. Checked both at
submit time and again where a stored pin is read back.
"""

_CLOSE_CONNECTIONS_TIMEOUT_S: typing.Final = 10.0
"""
Cap on how long `Host.close_connections` waits for a cancelled task to
actually finish.

Cancellation only requests a `CancelledError` at the next await point; a
nested connection task stuck outside the event loop (e.g. in blocking I/O)
would otherwise hang shutdown indefinitely instead of just losing that one
task's cleanup.
"""

EXECUTOR_SELECTOR_REGEX_PREFIX: typing.Final = "re:"
"""Prefix marking a selector as a regex pattern, matching the manager's
`VERSION_REGEX_PREFIX` (genvm-manager crates/modules-interfaces/src/nested.rs)."""


def is_valid_executor_selector(value: str) -> bool:
    """
    Same selector grammar the manager accepts for `reroute_to`: either an exact
    executor version (see `EXECUTOR_VERSION_RE`), or a `re:`-prefixed pattern
    matched by the manager against the directory names in its manifest.

    Both submit-time validation
    (`protocol_rpc.endpoints._validate_genvm_executor_selector`) and
    nested-call resolution (`Host.resolve_call_contract_executor`) must use
    this same grammar so a value that is accepted (or backfilled) on one
    boundary never gets rejected at the other.
    """
    if value.startswith(EXECUTOR_SELECTOR_REGEX_PREFIX):
        pattern = value[len(EXECUTOR_SELECTOR_REGEX_PREFIX) :]
        try:
            re.compile(pattern)
        except re.error:
            return False
        return True
    return bool(EXECUTOR_VERSION_RE.fullmatch(value))


def apply_storage_changes(
    storage_changes: list[tuple[bytes, bytes]], state: StateProxyWritable
) -> None:
    for k, v in storage_changes:
        slot_id = k[:32]
        index = int.from_bytes(k[32:], byteorder="big") * 32
        state.storage_write(slot_id, index, v)


# Callbacks for tracking GenVM Manager failures (moved from base_host)
_on_genvm_success: typing.Callable[[], None] | None = None
_on_genvm_failure: typing.Callable[[], None] | None = None


def set_genvm_callbacks(
    on_success: typing.Callable[[], None] | None = None,
    on_failure: typing.Callable[[], None] | None = None,
):
    """Set callbacks for GenVM Manager success/failure tracking."""
    global _on_genvm_success, _on_genvm_failure
    _on_genvm_success = on_success
    _on_genvm_failure = on_failure


def _get_env_float(env_key: str, default: float) -> float:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


_get_timeout_seconds = _get_env_float


def _get_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


class Context(base_host.Context):
    def __init__(self, logger: genvm_logger.Logger | None = None):
        self.logger: genvm_logger.Logger = logger or genvm_logger.NoLogger()
        self.stats: dict[str, typing.Any] = {}

    def on_genvm_success(self):
        if _on_genvm_success is not None:
            _on_genvm_success()

    def on_genvm_failure(self):
        if _on_genvm_failure is not None:
            _on_genvm_failure()

    def add_stat(self, key: str, value: typing.Any, /):
        self.stats[key] = value

    def get_manager_connect_timeout(self) -> float | None:
        # The manager socket is a local websocket; bound only the connect phase
        # so a dead manager fails fast instead of hanging the run. Mirrors the
        # old GenVMRun connect budget (min(5, run-timeout)).
        total = _get_env_float("GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 10.0)
        return min(5.0, total)


@dataclass
class ExecutionResult:
    result: ExecutionReturn | ExecutionError
    eq_outputs: dict[int, bytes]
    pending_transactions: list[PendingTransaction]
    stdout: str
    stderr: str
    genvm_log: list
    state: StateProxy
    processing_time: int
    nondet_disagree: int | None
    execution_stats: dict | None = None
    data_fee_bucket_totals: dict[str, int] | None = None
    data_fees_remaining: dict[str, int] | None = None


def _emission_value(emission: dict, name: str):
    snake = "".join(f"_{char.lower()}" if char.isupper() else char for char in name)
    return emission.get(name, emission.get(snake))


def _emission_bytes(emission: dict, name: str) -> bytes:
    value = _emission_value(emission, name)
    return _bytes_from_emission_value(value)


def _bytes_from_emission_value(value) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        raw = value.removeprefix("0x")
        try:
            return bytes.fromhex(raw)
        except ValueError:
            return base64.b64decode(value)
    return bytes(value)


def _emission_internal_fee_params(emission: dict) -> bytes:
    value = _emission_value(emission, "feeParams")
    if isinstance(value, dict):
        rotations = [int(rotation) for rotation in value.get("rotations", [])]
        appeal_rounds = max(len(rotations) - 1, 0)
        return encode(
            [INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
            [
                (
                    int(value.get("leader_timeunits_allocation", 0)),
                    int(value.get("validator_timeunits_allocation", 0)),
                    appeal_rounds,
                    int(value.get("execution_budget_per_round", 0)),
                    rotations,
                    int(value.get("max_price_gen_per_time_unit", 0)),
                    int(value.get("storage_fee_max_gas_price", 0)),
                    int(value.get("receipt_fee_max_gas_price", 0)),
                )
            ],
        )
    return _bytes_from_emission_value(value)


def _emission_external_fee_params(emission: dict) -> bytes:
    value = _emission_value(emission, "feeParams")
    if isinstance(value, dict):
        return encode(
            [EXTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
            [
                (
                    int(value.get("gas_limit", 0)),
                    int(value.get("max_gas_price", 0)),
                )
            ],
        )
    return _bytes_from_emission_value(value)


def _emission_allocation_subtree(emission: dict) -> list[dict] | str:
    value = _emission_value(emission, "allocationSubtree")
    if isinstance(value, list):
        return value

    subtree = value if value is not None else _emission_value(emission, "subtree")
    if subtree is None:
        return []

    raw = _bytes_from_emission_value(subtree)
    if not raw:
        return []

    try:
        decoded = decode([MESSAGE_ALLOCATION_NODE_ABI_TYPE], raw)[0]
    except Exception:
        # Consensus hashes the raw SubmittedMessage field even when later
        # inheritance decoding is contained as a restricted child. Preserve
        # those bytes instead of aliasing every malformed payload to empty.
        return "0x" + raw.hex()

    allocation_subtree = []
    for node in decoded:
        message_type = int(node[0])
        fee_params = bytes(node[6])
        if message_type == MESSAGE_TYPE_INTERNAL:
            fee_params = _canonical_internal_fee_params_from_genvm(fee_params)
        allocation_subtree.append(
            {
                "messageType": message_type,
                "onAcceptance": bool(node[1]),
                "parentIndex": int(node[2]),
                "recipient": str(node[3]).lower(),
                "callKey": "0x" + bytes(node[4]).hex(),
                "budget": int(node[5]),
                "feeParams": "0x" + fee_params.hex(),
            }
        )
    return allocation_subtree


def _canonical_internal_fee_params_from_genvm(fee_params: bytes) -> bytes:
    try:
        decoded = decode([INTERNAL_MESSAGE_FEE_PARAMS_WITH_CAPS_ABI_TYPE], fee_params)[
            0
        ]
    except Exception:
        return fee_params
    return encode(
        [INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE],
        [
            (
                int(decoded[0]),
                int(decoded[1]),
                int(decoded[2]),
                int(decoded[3]),
                [int(rotation) for rotation in decoded[4]],
                int(decoded[5]),
                int(decoded[6]),
                int(decoded[7]),
            )
        ],
    )


def _emission_int(emission: dict, name: str) -> int:
    return int(_emission_value(emission, name) or 0)


def _emission_on(emission: dict) -> typing.Literal["accepted", "finalized"]:
    """GenVM calls the pre-finalization lifecycle `decided`; Studio calls it `accepted`.

    Both executor lines normalize to `decided` at the host boundary, so `accepted`
    never arrives on the wire — this maps one way, GenVM → Studio.
    """
    return "finalized" if emission["on"] == "finalized" else "accepted"


def _emission_hex(emission: dict, name: str) -> str:
    value = _emission_value(emission, name)
    if value is None:
        return "0x" + ("0" * 64)
    if isinstance(value, bytes):
        return "0x" + value.hex().rjust(64, "0")[-64:]
    return "0x" + str(value).removeprefix("0x").lower().rjust(64, "0")[-64:]


def _emission_list(emission: dict, name: str) -> list:
    value = _emission_value(emission, name)
    return value if isinstance(value, list) else []


def _extract_llm_token_metrics(
    metrics: dict[str, typing.Any] | None,
) -> dict[str, typing.Any] | None:
    if not isinstance(metrics, dict):
        return None

    llm_metrics = metrics.get("llm")
    if not isinstance(llm_metrics, dict):
        return None

    token_metrics = llm_metrics.get("tokens")
    if not isinstance(token_metrics, dict) or not token_metrics:
        return None

    return token_metrics


def _close_watched(sock: socket.socket) -> None:
    """
    Closes a socket that an asyncio task may still be reading from.

    `Task.cancel` only takes effect on the next loop iteration, so a task blocked
    in `sock_recv` deregisters its reader *after* a synchronous `close` has freed
    the file descriptor -- by which point the number may already belong to
    someone else's socket, whose reader it then silently removes.
    """
    with contextlib.suppress(Exception):
        asyncio.get_event_loop().remove_reader(sock.fileno())
    with contextlib.suppress(OSError):
        sock.close()


class Host(genvmhost.IHost):
    """
    Handles all genvm host methods and accumulates results
    """

    _result: ExecutionReturn | ExecutionError | None
    _pending_transactions: list[PendingTransaction]
    _nondet_disagreement: None | int = None

    def __init__(
        self,
        sock_listen: socket.socket,
        *,
        calldata_bytes: bytes,
        state_proxy: StateProxy,
        leader_results: None | dict[int, bytes],
    ):
        self._pending_transactions = []
        self._result = None

        self.sock_listener = sock_listen
        self.sock = None
        self._state_proxy = state_proxy
        self.calldata_bytes = calldata_bytes
        self._leader_results = leader_results
        # A run that delegates across a major boundary spawns nested executors,
        # and each of them dials the same listener, so the first connection is
        # not necessarily the only one.
        self._ctx: Context | None = None
        self._accept_task: asyncio.Task | None = None
        self._connection_tasks: list[asyncio.Task] = []
        self._accepted_sockets: list[socket.socket] = []

    def bind_context(self, ctx: Context) -> None:
        """
        Gives the host the context its nested connections are served with.

        `loop_enter` is the only seam the host protocol offers and it carries no
        context, so the caller that owns both hands it over before the run.
        """
        self._ctx = ctx

    def provide_result(
        self,
        res: genvmhost.RunHostAndProgramRes,
        state: StateProxyWritable,
        ctx: Context,
    ) -> ExecutionResult:
        # Decode result from RunHostAndProgramRes
        if res.result_kind == ResultCode.RETURN:
            result = ExecutionReturn(gvm_calldata.encode(res.result_data))
        elif (
            res.result_kind == ResultCode.USER_ERROR
            or res.result_kind == ResultCode.VM_ERROR
        ):
            result_decoded = res.result_data
            error_code = None

            if isinstance(result_decoded, dict):
                # Extract standardized error code from Lua error structure
                error_code = extract_error_code(result_decoded, res.stderr)
                # Preserve raw error structure (causes, fatal, ctx) excluding message
                raw_error = {k: v for k, v in result_decoded.items() if k != "message"}

                result = ExecutionError(
                    result_decoded["message"],
                    res.result_kind,
                    error_code=error_code,
                    raw_error=raw_error if raw_error else None,
                    description=res.vm_error_description,
                )
            else:
                # String error - try to extract error code from message
                error_code = extract_error_code(str(result_decoded), res.stderr)
                result = ExecutionError(
                    str(result_decoded),
                    res.result_kind,
                    error_code=error_code,
                )
        elif res.result_kind == ResultCode.INTERNAL_ERROR:
            pass

            error_ctx = None
            error_str = str(res.result_data)

            # Try to extract structured data if result_data is a dict
            if isinstance(res.result_data, dict):
                error_ctx = res.result_data.get("ctx")
                error_code = extract_error_code(res.result_data, res.stderr)
                causes_raw = res.result_data.get("causes", [])
                causes = list(causes_raw) if isinstance(causes_raw, list) else []
                is_fatal = bool(res.result_data.get("fatal", False))
            else:
                # Parse the ModuleError string to extract details
                error_code, causes, is_fatal = parse_module_error_string(error_str)
                # Extract LLM error context (primary_error/fallback_error)
                # from the Rust debug format string
                error_ctx = parse_ctx_from_module_error_string(error_str)

            message = (
                f"GenVM internal error: {', '.join(causes)}"
                if causes
                else "GenVM internal error"
            )

            # Increment failure counter to trigger unhealthy status
            ctx.on_genvm_failure()

            # Raise exception - worker will release transaction and restart
            raise GenVMInternalError(
                message=message,
                error_code=error_code,
                causes=causes,
                is_fatal=is_fatal,
                ctx=error_ctx,
                detail=error_str[:1000],
            )
        else:
            raise Exception(f"invalid result {res.result_kind}")

        # Readonly (view) executions can still report storage changes on GenVM
        # main — e.g. lazy data-structure initialization on first access
        # (genlayer-embeddings VecDB._do_init inside a view knn). Those writes
        # are ephemeral VM-side effects: discard them instead of tripping the
        # storage_write readonly assertion.
        if not getattr(state, "readonly", False):
            apply_storage_changes(res.result_storage_deltas, state)

        # Extract pending_transactions from result_emissions
        pending_transactions = []
        for emission in res.result_emissions:
            match emission["type"]:
                case "InternalMessage":
                    pending_transactions.append(
                        PendingTransaction(
                            emission["address"].as_hex,
                            gvm_calldata.encode(emission["calldata"]),
                            code=None,
                            salt_nonce=0,
                            value=emission["value"],
                            on=_emission_on(emission),
                            fee_params=_emission_internal_fee_params(emission),
                            declared_budget=_emission_int(emission, "declaredBudget"),
                            call_key=_emission_hex(emission, "callKey"),
                            allocation_subtree=_emission_allocation_subtree(emission),
                            use_balance=bool(
                                _emission_value(emission, "useBalance") or False
                            ),
                        )
                    )
                case "InternalDeployMessage":
                    pending_transactions.append(
                        PendingTransaction(
                            address="0x",
                            calldata=gvm_calldata.encode(emission["calldata"]),
                            code=emission["code"],
                            salt_nonce=emission["salt_nonce"],
                            value=emission["value"],
                            on=_emission_on(emission),
                            fee_params=_emission_internal_fee_params(emission),
                            declared_budget=_emission_int(emission, "declaredBudget"),
                            call_key=_emission_hex(emission, "callKey"),
                            allocation_subtree=_emission_allocation_subtree(emission),
                            use_balance=bool(
                                _emission_value(emission, "useBalance") or False
                            ),
                        )
                    )
                case "ExternalMessage":
                    pending_transactions.append(
                        PendingTransaction(
                            address=emission["address"].as_hex,
                            calldata=emission.get("calldata", b""),
                            code=None,
                            salt_nonce=0,
                            value=emission["value"],
                            on="finalized",
                            is_eth_send=True,
                            fee_params=_emission_external_fee_params(emission),
                            declared_budget=_emission_int(emission, "declaredBudget"),
                            call_key=_emission_hex(emission, "callKey"),
                            allocation_subtree=_emission_allocation_subtree(emission),
                            gas_used=_emission_int(emission, "gasUsed"),
                        )
                    )

        eq_outputs = {}
        if res.result_leader_public_data:
            leader_public_data = LeaderPublicData.decode(res.result_leader_public_data)
            eq_outputs = {
                i: data
                for i, data in enumerate(leader_public_data.nondet_block_outputs)
            }

        execution_stats = dict(ctx.stats)
        llm_token_metrics = _extract_llm_token_metrics(res.metrics)
        if llm_token_metrics is not None:
            execution_stats["llm"] = {"tokens": llm_token_metrics}

        return ExecutionResult(
            eq_outputs=eq_outputs,
            pending_transactions=pending_transactions,
            stdout=res.stdout,
            stderr=res.stderr,
            genvm_log=res.genvm_log,
            result=result,
            state=state,
            processing_time=0,
            nondet_disagree=self._nondet_disagreement,
            execution_stats=execution_stats,
            data_fees_remaining=res.data_fees_remaining,
        )

    async def loop_enter(self, cancellation) -> socket.socket:
        sock = await self._accept(cancellation)
        if sock is None:
            raise Exception("Program failed")
        self.sock = sock
        assert self._ctx is not None, "bind_context must run before the genvm connects"
        if self._accept_task is None:
            # Serve every later connection ourselves: accepting stops when the
            # run ends, which is when `run_genvm` sets the cancellation event.
            self._accept_task = asyncio.create_task(
                self._accept_connections(cancellation)
            )
        return sock

    async def _accept(self, cancellation) -> socket.socket | None:
        """
        Accepts one connection, or returns `None` once the run is over.
        """
        async_loop = asyncio.get_event_loop()
        assert self.sock_listener is not None

        accepting = asyncio.ensure_future(async_loop.sock_accept(self.sock_listener))
        canc = asyncio.ensure_future(cancellation.wait())

        accepted: socket.socket | None = None
        try:
            await asyncio.wait([canc, accepting], return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Also runs when this task is cancelled mid-accept, which is the
            # normal way the background acceptor ends. The accept is awaited out
            # even then: until it is gone the event loop still watches the
            # listener, which the caller is about to close.
            canc.cancel()
            if not accepting.done():
                accepting.cancel()
            result = None
            with contextlib.suppress(BaseException):
                result = await accepting
            if result is not None:
                # Recorded here rather than after the `finally`, because this
                # block also runs while a CancelledError is propagating: a
                # connection that arrived just as we were cancelled is still
                # ours to close, and nothing else knows about it.
                accepted, _addr = result
                accepted.setblocking(False)
                self._accepted_sockets.append(accepted)

        return accepted

    async def _accept_connections(self, cancellation) -> None:
        assert self._ctx is not None
        while True:
            sock = await self._accept(cancellation)
            if sock is None:
                return
            self._connection_tasks.append(
                asyncio.create_task(genvmhost.host_loop_on(self, sock, ctx=self._ctx))
            )

    async def close_connections(self) -> None:
        """
        Winds down the nested-connection loops and drops every accepted socket.

        A nested loop that died on its own is reported rather than dropped: the
        run's own result comes from the manager and stays authoritative, but a
        nested executor that lost its host is why it looks the way it does.

        The listener is not closed here: it belongs to whoever created it.

        Every drain below is capped at `_CLOSE_CONNECTIONS_TIMEOUT_S` via
        `asyncio.wait` (not `wait_for`, which -- if the cancelled task keeps
        swallowing `CancelledError` -- keeps re-awaiting it past its own
        timeout instead of returning): a task that never actually stops must
        not be able to hang shutdown forever.
        """
        if self._accept_task is not None:
            task = self._accept_task
            task.cancel()
            done, pending = await asyncio.wait(
                [task], timeout=_CLOSE_CONNECTIONS_TIMEOUT_S
            )
            if pending and self._ctx is not None:
                self._ctx.logger.error(
                    "accept task did not stop within close_connections timeout"
                )
            elif done and not task.cancelled():
                exc = task.exception()
                if exc is not None and self._ctx is not None:
                    self._ctx.logger.error("accept task failed", error=exc)
            self._accept_task = None
        for task in self._connection_tasks:
            if not task.done():
                task.cancel()
        if self._connection_tasks:
            done, pending = await asyncio.wait(
                self._connection_tasks, timeout=_CLOSE_CONNECTIONS_TIMEOUT_S
            )
            if pending and self._ctx is not None:
                self._ctx.logger.error(
                    f"{len(pending)} nested host connection(s) did not stop "
                    "within close_connections timeout"
                )
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc is not None and self._ctx is not None:
                    self._ctx.logger.error("nested host connection failed", error=exc)
        self._connection_tasks.clear()
        for accepted in self._accepted_sockets:
            _close_watched(accepted)
        self._accepted_sockets.clear()
        self.sock = None

    async def storage_read(
        self, type: StorageView, address: bytes, slot: bytes, offset: int, le: int, /
    ) -> bytes:
        assert type != StorageView.LATEST_FINALIZED
        return await asyncio.to_thread(
            self._state_proxy.storage_read, Address(address), slot, offset, le
        )

    async def resolve_call_contract_executor(
        self,
        contract_address: Address,
        state_mode: StorageView,
        advisory_major: int,
        /,
    ) -> bytes | None:
        # Cross-major nested calls: the genvm asks which executor a call target
        # runs on. Answer with the target's own pin (genvm_executor_selector)
        # so a contract deployed against an older line keeps executing there
        # even when called from a newer one. Unpinned targets return None ->
        # the caller's runner keeps advising the major (same-major behavior).
        #
        # The pin names the line outright rather than deriving a major from it:
        # every line released so far is semver major 0, so a major resolves to
        # the newest one whichever line the pin meant.
        reroute = self._state_proxy.genvm_executor_selector_for(contract_address)
        if not reroute:
            return None
        # A pin that is not a version is rejected at submit time, so reaching
        # here means a stored one went bad. Fail this call as a host error:
        # anything else escapes `host_loop_on`, kills the host task and turns a
        # permanently broken callee into retries that only end when the
        # transaction's time budget does.
        if not is_valid_executor_selector(reroute):
            raise base_host.HostException(
                host_fns.Errors.FORBIDDEN,
                f"contract {contract_address.as_hex} is pinned to an unusable executor version: {reroute!r}",
            )
        return gvm_calldata.encode({"kind": "version", "version": reroute})

    async def consume_time_fee_gen_wei(self, time_fee_gen_wei: int, /) -> None:
        pass

    async def external_call(self, address: bytes, calldata: bytes, /) -> bytes:
        # FIXME(core-team): #748
        assert False

    async def get_balance_gen_wei(self, address: bytes, /) -> int:
        return await asyncio.to_thread(self._state_proxy.get_balance, Address(address))

    async def notify_nondet_disagreement(self, call_no: int, /) -> None:
        self._nondet_disagreement = call_no

    async def get_remaining_time_fee_gen_wei(self, /) -> int:
        return 2**60


async def _copy_state_proxy(state_proxy) -> StateProxy:
    # snapshot_factory cannot be pickled. Temporarily remove the factory to allow deepcopy
    factory = state_proxy.snapshot_factory
    shared_decoded_value_cache = getattr(
        state_proxy, "_shared_decoded_value_cache", None
    )
    shared_contract_snapshot_cache = getattr(
        state_proxy, "_shared_contract_snapshot_cache", None
    )
    try:
        state_proxy.snapshot_factory = None
        if hasattr(state_proxy, "_shared_decoded_value_cache"):
            state_proxy._shared_decoded_value_cache = None
        if hasattr(state_proxy, "_shared_contract_snapshot_cache"):
            state_proxy._shared_contract_snapshot_cache = None
        state_copy = copy.deepcopy(state_proxy)
        state_copy.snapshot_factory = factory
        if hasattr(state_copy, "_shared_decoded_value_cache"):
            state_copy._shared_decoded_value_cache = shared_decoded_value_cache
        if hasattr(state_copy, "_shared_contract_snapshot_cache"):
            state_copy._shared_contract_snapshot_cache = shared_contract_snapshot_cache
        return state_copy
    finally:
        state_proxy.snapshot_factory = factory
        if hasattr(state_proxy, "_shared_decoded_value_cache"):
            state_proxy._shared_decoded_value_cache = shared_decoded_value_cache
        if hasattr(state_proxy, "_shared_contract_snapshot_cache"):
            state_proxy._shared_contract_snapshot_cache = shared_contract_snapshot_cache


def _create_timeout_result(
    last_error: Exception | None, state_proxy: StateProxy, processing_time: int
) -> ExecutionResult:
    if last_error is not None:
        import traceback

        error_str = "\n".join(traceback.format_exception(last_error))
    else:
        error_str = ""

    # Extract appropriate error code based on the last error
    error_code = extract_error_code_from_timeout(last_error)

    return ExecutionResult(
        result=ExecutionError(
            message="timeout",
            kind=ResultCode.VM_ERROR,
            error_code=error_code,
        ),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr=error_str,
        genvm_log=[],
        state=state_proxy,
        processing_time=processing_time,
        nondet_disagree=None,
        data_fees_remaining={},
    )


def _encode_leader_public_data(
    leader_results: dict[int, bytes] | None,
) -> bytes | None:
    if leader_results is None:
        return None
    outputs = []
    if leader_results:
        max_key = max(leader_results.keys())
        outputs = [leader_results.get(i, b"") for i in range(max_key + 1)]
    return LeaderPublicData(outputs).encode()


async def run_genvm_host(
    host_supplier: typing.Callable[[socket.socket], Host],
    *,
    timeout: float,  # noqa: ASYNC109 - retry budget spans multiple awaits
    manager_uri: str = "http://127.0.0.1:3999",
    logger: genvm_logger.Logger | None = None,
    is_sync: bool,
    capture_output: bool = True,
    debug_mode: str | None = None,
    message: typing.Any,
    host_data: str = "",
    extra_args: list[str] = [],
    permissions: str = "rwscn",
    code: bytes | None = None,
    fee_context: GenVMFeeContext | None = None,
    genvm_executor_selector: str | None = None,
) -> ExecutionResult:
    if logger is None:
        logger = genvm_logger.NoLogger()
    # base_host.run_genvm no longer derives the level from capture_output, so
    # resolve it here: capture_output implies safe-unbounded (host reads
    # stdout/stderr artifacts), otherwise disabled.
    effective_debug_mode: base_host.DebugMode = debug_mode or (
        "safe-unbounded" if capture_output else "disabled"
    )
    if genvm_executor_selector and effective_debug_mode == "disabled":
        # The manager honors the executor override only under debug_mode >= safe
        # and ignores it silently otherwise, which would run the contract on the
        # manifest-resolved executor instead of the requested one.
        raise ValueError(
            f"genvm_executor_selector={genvm_executor_selector!r} requires "
            "debug_mode >= safe, got 'disabled'"
        )
    ctx = Context(logger=logger)
    fee_context = fee_context or GenVMFeeContext()
    effective_bucket_totals = (
        dict(fee_context.bucket_totals)
        if fee_context.bucket_totals
        else base_host.default_bucket_totals(3)
    )
    effective_gas_data = dict(base_host.DEFAULT_GAS_DATA)
    effective_gas_data.update(GENVM_GASLESS_GAS_DATA)
    if fee_context.gas_data:
        effective_gas_data.update(fee_context.gas_data)
    tmpdir = Path(tempfile.mkdtemp())
    try:
        base_delay = 5  # seconds
        start_time = time.time()
        retry_count = 0
        last_error: Exception | None = None

        # Extract the original arguments from the partial function
        host_args = (
            host_supplier.keywords
            if isinstance(host_supplier, functools.partial)
            else {}
        )
        fresh_args = {}

        # Backoff owed to a failed attempt. It is served after that attempt's
        # listener, sockets and nested connection tasks are gone, so a dead
        # attempt cannot keep serving executors for the length of the sleep.
        retry_delay = 0.0

        while True:
            if retry_delay:
                await asyncio.sleep(retry_delay)
                retry_delay = 0.0

            remaining_time = timeout - (time.time() - start_time)
            if remaining_time <= 0:
                # When the genvm keeps crashing we send a timeout error
                return _create_timeout_result(
                    last_error,
                    fresh_args.get("state_proxy", host_args.get("state_proxy")),
                    int(timeout * 1000),
                )

            # Avoid expensive state deep-copy on the first attempt. We only need
            # a clean copy when retrying after a failed execution attempt.
            if retry_count == 0:
                fresh_args = dict(host_args)
            else:
                fresh_args = {}
                for key, value in host_args.items():
                    if key == "state_proxy" and hasattr(value, "snapshot_factory"):
                        fresh_args[key] = await _copy_state_proxy(value)
                    else:
                        fresh_args[key] = copy.deepcopy(value)

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock_listener:
                sock_listener.setblocking(False)
                sock_path = tmpdir.joinpath(f"sock_{retry_count}")
                sock_listener.bind(str(sock_path))
                # A run that delegates across a major boundary spawns nested
                # executors, and each dials this same listener.
                sock_listener.listen(8)

                fresh_host_supplier = functools.partial(
                    (
                        host_supplier.func
                        if isinstance(host_supplier, functools.partial)
                        else host_supplier
                    ),
                    **fresh_args,
                )
                host: Host = fresh_host_supplier(sock_listener)
                host.bind_context(ctx)

                leader_results = fresh_args.get(
                    "leader_results", host_args.get("leader_results")
                )
                leader_public_data = _encode_leader_public_data(leader_results)

                try:
                    # Fresh manager websocket per attempt: run_genvm never owns
                    # the client's lifecycle, and a retry after a bounce wants a
                    # clean connection rather than a poisoned one.
                    async with base_host.ManagerClient(
                        manager_uri,
                        connect_timeout=ctx.get_manager_connect_timeout(),
                    ) as manager_client:
                        res = await base_host.run_genvm(
                            host,
                            manager_uri=manager_uri,
                            manager_client=manager_client,
                            message=message,
                            timeout=timeout,
                            debug_mode=effective_debug_mode,
                            is_sync=is_sync,
                            host_data=host_data,
                            ctx=ctx,
                            host=f"unix://{sock_path}",
                            extra_args=extra_args,
                            code=code,
                            bucket_totals=effective_bucket_totals,
                            gas_data=effective_gas_data,
                            message_fee_allocation=fee_context.message_fee_allocation
                            or [],
                            calldata=fresh_args.get(
                                "calldata_bytes", host_args.get("calldata_bytes", b"")
                            ),
                            leader_public_data=leader_public_data,
                            unsafe_overrides=base_host.UnsafeOverrides(
                                reroute_to=genvm_executor_selector or ""
                            ),
                            # Ask to be consulted on where a CallContract runs.
                            # Opting out makes the manager answer the resolve
                            # query itself with "stay in-process", which silently
                            # runs a pinned callee's code on the caller's
                            # executor -- the very thing the pin exists to stop.
                            # A nested run inherits this from its parent.
                            request_extra={"hook_cross_contract_calls": True},
                        )

                    execution_result = host.provide_result(
                        res,
                        fresh_args.get("state_proxy", host_args.get("state_proxy")),
                        ctx,
                    )
                    execution_result.data_fee_bucket_totals = effective_bucket_totals

                    execution_result.processing_time = math.ceil(
                        (time.time() - start_time) * 1000
                    )

                    return execution_result
                except GenVMInternalError:
                    # Re-raise GenVMInternalError to propagate to worker for proper handling
                    # (stop worker, release transaction, report unhealthy)
                    raise
                except base_host.TerminalResultUnavailable:
                    # The genvm already executed exactly once and reached a
                    # terminal state; only fetching/decoding its result failed.
                    # Falling into the generic `except Exception` below would
                    # start a brand new run here, executing the contract a
                    # second time for a result that already exists -- so this
                    # propagates as a permanent failure instead of retrying.
                    raise
                except base_host.ManagerRunNotStarted as e:
                    # The genvm never executed. base_host already classified the
                    # refusal, so no transport knowledge or string matching here:
                    # a permanent refusal (bad request/runner) fails fast; a
                    # transient one (manager still starting modules) retries
                    # until the deadline budget runs out (top-of-loop check).
                    if not e.retryable:
                        raise
                    logger.warning(
                        "genvm run not started, retrying",
                        reason=e.reason,
                        retry_count=retry_count,
                    )
                    last_error = e
                    retry_count += 1
                    retry_delay = min(
                        base_delay * (2 ** (retry_count - 1)), remaining_time
                    )
                except Exception as e:
                    logger.error(
                        "GenVM execution attempt failed",
                        error=e,
                        retry_count=retry_count,
                    )
                    last_error = e

                    # Check if llm failed, immediately return timeout error
                    if "fatal: true" in str(last_error):
                        return _create_timeout_result(
                            last_error,
                            fresh_args.get("state_proxy", host_args.get("state_proxy")),
                            int((time.time() - start_time) * 1000),
                        )

                    retry_count += 1
                    # Back off longer than the previous attempt to avoid
                    # executing it too many times.
                    retry_delay = min(
                        base_delay * (2 ** (retry_count - 1)), remaining_time
                    )

                finally:
                    await host.close_connections()
                    sock_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
