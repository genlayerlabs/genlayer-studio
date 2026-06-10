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
import typing
import tempfile
from pathlib import Path
import shutil
import json
import base64
import asyncio
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
from .origin import logger as genvm_logger
from .error_codes import (
    extract_error_code,
    extract_error_code_from_timeout,
    parse_module_error_string,
    parse_ctx_from_module_error_string,
    GenVMInternalError,
)

GENVM_GASLESS_GAS_DATA: dict[str, str] = {
    "storageUnitPrice": "0",
    "receiptGasPerByte": "0",
    "gasPerChangedSlot": "0",
    "intrinsicGas": "0",
    "bootloaderOverhead": "0",
    "fixedProposeReceiptGas": "0",
    "fixedMessageRevealGas": "0",
    "genPerTimeUnit": "0",
}

INTERNAL_MESSAGE_FEE_PARAMS_ABI_TYPE = "(uint256,uint256,uint256,uint256,uint256[])"
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
CALL_KEY_WILDCARD = "0x" + ("0" * 64)


@dataclass(frozen=True)
class GenVMFeeContext:
    bucket_totals: list[int] | None = None
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

    def get_timeout(
        self,
        action: base_host.TimeoutAction,
        type: base_host.TimeoutType,
        /,
    ) -> float | None:
        TA = base_host.TimeoutAction
        TT = base_host.TimeoutType

        if action == TA.GenVMRun:
            total = _get_env_float("GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 10.0)
            if type == TT.TOTAL_S:
                return total
            if type == TT.CONNECT_S:
                return min(5.0, total)
            if type == TT.SOCK_READ_S:
                return total
        elif action == TA.GenVMGet:
            total = _get_env_float("GENVM_MANAGER_STATUS_HTTP_TIMEOUT_SECONDS", 10.0)
            if type == TT.TOTAL_S:
                return total
            if type == TT.CONNECT_S:
                return min(3.0, total)
            if type == TT.SOCK_READ_S:
                return total
        elif action == TA.GenVMDelete:
            if type == TT.TOTAL_S:
                return _get_env_float("GENVM_MANAGER_DELETE_HTTP_TIMEOUT_SECONDS", 3.0)
            if type == TT.CONNECT_S:
                return 1.5
            if type == TT.SOCK_READ_S:
                return 1.5
            if type == TT.DELETE_HTTP_GRACEFUL_TIMEOUT_MS:
                return 20.0
        return None

    def retry_delay(
        self, action: base_host.TimeoutAction, attempt_no: int, /
    ) -> float | None:
        max_retries = _get_int("GENVM_MANAGER_RUN_RETRIES", 3)
        if attempt_no >= max_retries - 1:
            return None
        base_delay = _get_env_float("GENVM_MANAGER_RUN_RETRY_DELAY_SECONDS", 1.0)
        return base_delay * (2**attempt_no)


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
    data_fee_bucket_totals: list[int] | None = None
    data_fees_remaining: list[int] | None = None


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


def _emission_allocation_subtree(emission: dict) -> list[dict]:
    value = _emission_value(emission, "allocationSubtree")
    if isinstance(value, list):
        return value

    subtree = _emission_value(emission, "subtree")
    if subtree is None:
        return []

    raw = _bytes_from_emission_value(subtree)
    if not raw:
        return []

    try:
        decoded = decode([MESSAGE_ALLOCATION_NODE_ABI_TYPE], raw)[0]
    except Exception:
        return []

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
            )
        ],
    )


def _emission_int(emission: dict, name: str) -> int:
    return int(_emission_value(emission, name) or 0)


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

        apply_storage_changes(res.result_storage_changes, state)

        # Extract pending_transactions from result_emissions
        pending_transactions = []
        for emission in res.result_emissions:
            match emission["type"]:
                case "PostMessage":
                    pending_transactions.append(
                        PendingTransaction(
                            emission["address"].as_hex,
                            gvm_calldata.encode(emission["calldata"]),
                            code=None,
                            salt_nonce=0,
                            value=emission["value"],
                            on=emission["on"],
                            fee_params=_emission_internal_fee_params(emission),
                            declared_budget=_emission_int(emission, "declaredBudget"),
                            call_key=_emission_hex(emission, "callKey"),
                            allocation_subtree=_emission_allocation_subtree(emission),
                        )
                    )
                case "DeployContract":
                    pending_transactions.append(
                        PendingTransaction(
                            address="0x",
                            calldata=gvm_calldata.encode(emission["calldata"]),
                            code=emission["code"],
                            salt_nonce=emission["salt_nonce"],
                            value=emission["value"],
                            on=emission["on"],
                            fee_params=_emission_internal_fee_params(emission),
                            declared_budget=_emission_int(emission, "declaredBudget"),
                            call_key=_emission_hex(emission, "callKey"),
                            allocation_subtree=_emission_allocation_subtree(emission),
                        )
                    )
                case "EthSend":
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

        # Extract eq_outputs from result_nondet_results
        eq_outputs = {i: data for i, data in enumerate(res.result_nondet_results)}

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
            execution_stats=ctx.stats,
            data_fees_remaining=res.data_fees_remaining,
        )

    async def loop_enter(self, cancellation) -> socket.socket:
        async_loop = asyncio.get_event_loop()
        assert self.sock_listener is not None

        interesting = asyncio.ensure_future(async_loop.sock_accept(self.sock_listener))
        canc = asyncio.ensure_future(cancellation.wait())

        done, pending = await asyncio.wait(
            [canc, interesting], return_when=asyncio.FIRST_COMPLETED
        )
        if canc in done:
            raise Exception("Program failed")
        canc.cancel()

        self.sock, _addr = interesting.result()
        self.sock.setblocking(False)
        self.sock_listener.close()
        self.sock_listener = None
        return self.sock

    async def storage_read(
        self, type: StorageType, account: bytes, slot: bytes, index: int, le: int, /
    ) -> bytes:
        assert type != StorageType.LATEST_FINAL
        return await asyncio.to_thread(
            self._state_proxy.storage_read, Address(account), slot, index, le
        )

    async def consume_gas(self, gas: int, /) -> None:
        pass

    async def eth_call(self, account: bytes, calldata: bytes, /) -> bytes:
        # FIXME(core-team): #748
        assert False

    async def get_balance(self, account: bytes, /) -> int:
        return await asyncio.to_thread(self._state_proxy.get_balance, Address(account))

    async def notify_nondet_disagreement(self, call_no: int, /) -> None:
        self._nondet_disagreement = call_no

    async def remaining_fuel_as_gen(self, /) -> int:
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
        data_fees_remaining=[],
    )


def _leader_results_to_list(
    leader_results: dict[int, bytes] | None,
) -> list[bytes] | None:
    """Convert dict[int, bytes] keyed by call_no to ordered list[bytes]."""
    if leader_results is None:
        return None
    if not leader_results:
        return []
    max_key = max(leader_results.keys())
    return [leader_results.get(i, b"") for i in range(max_key + 1)]


async def run_genvm_host(
    host_supplier: typing.Callable[[socket.socket], Host],
    *,
    timeout: float,
    manager_uri: str = "http://127.0.0.1:3999",
    logger: genvm_logger.Logger | None = None,
    is_sync: bool,
    capture_output: bool = True,
    message: typing.Any,
    host_data: str = "",
    extra_args: list[str] = [],
    permissions: str = "rwscn",
    code: bytes | None = None,
    fee_context: GenVMFeeContext | None = None,
) -> ExecutionResult:
    if logger is None:
        logger = genvm_logger.NoLogger()
    ctx = Context(logger=logger)
    fee_context = fee_context or GenVMFeeContext()
    effective_bucket_totals = fee_context.bucket_totals or [
        10_000_000,
        10_000_000,
        10_000_000,
    ]
    effective_gas_data = (
        dict(fee_context.gas_data)
        if fee_context.gas_data
        else dict(GENVM_GASLESS_GAS_DATA)
    )
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

        while True:
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
                sock_listener.listen(1)

                fresh_host_supplier = functools.partial(
                    (
                        host_supplier.func
                        if isinstance(host_supplier, functools.partial)
                        else host_supplier
                    ),
                    **fresh_args,
                )
                host: Host = fresh_host_supplier(sock_listener)

                leader_results = fresh_args.get(
                    "leader_results", host_args.get("leader_results")
                )
                leader_nondet_results = _leader_results_to_list(leader_results)

                try:
                    res = await base_host.run_genvm(
                        host,
                        manager_uri=manager_uri,
                        message=message,
                        timeout=timeout,
                        capture_output=capture_output,
                        is_sync=is_sync,
                        host_data=host_data,
                        ctx=ctx,
                        host=f"unix://{sock_path}",
                        extra_args=extra_args,
                        code=code,
                        bucket_totals=effective_bucket_totals,
                        gas_data=effective_gas_data,
                        message_fee_allocation=fee_context.message_fee_allocation or [],
                        calldata=fresh_args.get(
                            "calldata_bytes", host_args.get("calldata_bytes", b"")
                        ),
                        leader_nondet_results=leader_nondet_results,
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
                except Exception as e:
                    logger.error(
                        f"GenVM execution attempt failed",
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
                    # Sleep for a longer time than the previous attempt to avoid executing it too many times
                    delay = min(base_delay * (2 ** (retry_count - 1)), remaining_time)
                    await asyncio.sleep(delay)

                finally:
                    if host.sock is not None:
                        host.sock.close()
                    sock_path.unlink(missing_ok=True)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
