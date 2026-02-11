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
)

import math
import typing
import tempfile
from pathlib import Path
import shutil
import json
import base64
import asyncio
import socket
import logging
import backend.node.genvm.origin.base_host as genvmhost
import collections.abc
import functools
import datetime
import abc
import time
import copy

from backend.node.types import (
    PendingTransaction,
    Address,
)
import backend.node.genvm.origin.calldata as gvm_calldata
from dataclasses import dataclass

from .origin.public_abi import *
from .origin.host_fns import Errors
from .origin import base_host
from .origin import logger as genvm_logger
from .error_codes import (
    extract_error_code,
    extract_error_code_from_timeout,
    parse_module_error_string,
    GenVMInternalError,
)


@dataclass
class ExecutionError:
    message: str
    kind: typing.Literal[ResultCode.USER_ERROR, ResultCode.VM_ERROR]
    error_code: str | None = None  # Standardized error code (e.g., LLM_RATE_LIMITED)
    raw_error: dict | None = None  # Full Lua error structure (causes, fatal, ctx)

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


class Host(genvmhost.IHost):
    """
    Handles all genvm host methods and accumulates results
    """

    _result: ExecutionReturn | ExecutionError | None
    _eq_outputs: dict[int, bytes]
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
        self._eq_outputs = {}
        self._pending_transactions = []
        self._result = None

        self.sock_listener = sock_listen
        self.sock = None
        self._state_proxy = state_proxy
        self.calldata_bytes = calldata_bytes
        self._leader_results = leader_results

    def provide_result(
        self, res: genvmhost.RunHostAndProgramRes, state: StateProxyWritable
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
            error_str = str(res.result_data)

            # Parse the ModuleError to extract details
            error_code, causes, is_fatal = parse_module_error_string(error_str)
            message = (
                f"GenVM internal error: {', '.join(causes)}"
                if causes
                else "GenVM internal error"
            )

            # Increment failure counter to trigger unhealthy status
            from backend.node.genvm.origin.base_host import _on_genvm_failure

            if _on_genvm_failure is not None:
                _on_genvm_failure()

            # Raise exception - worker will release transaction and restart
            raise GenVMInternalError(
                message=message,
                error_code=error_code,
                causes=causes,
                is_fatal=is_fatal,
            )
        else:
            raise Exception(f"invalid result {res.result_kind}")

        apply_storage_changes(res.result_storage_changes, state)

        return ExecutionResult(
            eq_outputs=self._eq_outputs,
            pending_transactions=self._pending_transactions,
            stdout=res.stdout,
            stderr=res.stderr,
            genvm_log=res.genvm_log,
            result=result,
            state=state,
            processing_time=0,
            nondet_disagree=self._nondet_disagreement,
            execution_stats=res.execution_stats,
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
        return self._state_proxy.storage_read(Address(account), slot, index, le)

    async def get_leader_nondet_result(self, call_no: int, /) -> collections.abc.Buffer:
        leader_results = self._leader_results
        if leader_results is None:
            raise genvmhost.HostException(Errors.I_AM_LEADER)
        res = leader_results.get(call_no, None)
        if res is None:
            raise genvmhost.HostException(Errors.ABSENT)
        return res

    async def post_nondet_result(
        self, call_no: int, data: collections.abc.Buffer, /
    ) -> None:
        self._eq_outputs[call_no] = bytes(data)

    async def post_message(
        self, account: bytes, calldata: bytes, data: genvmhost.DefaultTransactionData, /
    ) -> None:
        on = data.get("on", "finalized")
        value = int(data.get("value", "0x0"), 16)
        self._pending_transactions.append(
            PendingTransaction(
                Address(account).as_hex,
                calldata,
                code=None,
                salt_nonce=0,
                value=value,
                on=on,
            )
        )

    async def consume_gas(self, gas: int, /) -> None:
        pass

    async def deploy_contract(
        self,
        calldata: bytes,
        code: bytes,
        data: genvmhost.DeployDefaultTransactionData,
        /,
    ) -> None:
        on = data.get("on", "finalized")
        value = int(data.get("value", "0x0"), 16)
        salt_nonce = int(data.get("salt_nonce", "0x0"), 16)
        self._pending_transactions.append(
            PendingTransaction(
                address="0x",
                calldata=calldata,
                code=code,
                salt_nonce=salt_nonce,
                value=value,
                on=on,
            )
        )

    async def eth_send(
        self,
        account: bytes,
        calldata: bytes,
        data: genvmhost.DefaultEthTransactionData,
        /,
    ) -> None:
        # FIXME(core-team): #748
        assert False

    async def eth_call(self, account: bytes, calldata: bytes, /) -> bytes:
        # FIXME(core-team): #748
        assert False

    async def get_balance(self, account: bytes, /) -> int:
        return self._state_proxy.get_balance(Address(account))

    async def notify_nondet_disagreement(self, call_no: int, /) -> None:
        self._nondet_disagreement = call_no

    async def remaining_fuel_as_gen(self, /) -> int:
        return 2**60


async def _copy_state_proxy(state_proxy) -> StateProxy:
    # snapshot_factory cannot be pickled. Temporarily remove the factory to allow deepcopy
    factory = state_proxy.snapshot_factory
    try:
        state_proxy.snapshot_factory = None
        state_copy = copy.deepcopy(state_proxy)
        state_copy.snapshot_factory = factory
        return state_copy
    finally:
        state_proxy.snapshot_factory = factory


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
    )


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
) -> ExecutionResult:
    if logger is None:
        logger = genvm_logger.NoLogger()
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

            # Create fresh copies of the arguments for each attempt
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

                try:
                    res = await base_host.run_genvm(
                        host,
                        manager_uri=manager_uri,
                        message=message,
                        timeout=timeout,
                        capture_output=capture_output,
                        is_sync=is_sync,
                        host_data=host_data,
                        logger=logger,
                        host=f"unix://{sock_path}",
                        extra_args=extra_args,
                        code=code,
                        calldata=fresh_args.get(
                            "calldata_bytes", host_args.get("calldata_bytes", b"")
                        ),
                    )

                    execution_result = host.provide_result(
                        res,
                        fresh_args.get("state_proxy", host_args.get("state_proxy")),
                    )

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
