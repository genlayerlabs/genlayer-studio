# backend/node/genvm/base.py

__all__ = (
    "run_genvm_host",
    "Host",
    "StateProxy",
    "ExecutionError",
    "ExecutionReturn",
    "ExecutionResult",
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
import backend.node.genvm.origin.calldata as calldata
from dataclasses import dataclass

from .origin.public_abi import *
from .origin.host_fns import Errors
from .origin import base_host
from .origin import logger as genvm_logger


# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict):
    """Best-effort NDJSON log for debug mode; never raises. Avoid secrets."""
    import json, os, time

    payload = {
        "sessionId": "debug-session",
        "runId": os.getenv("AGENT_DEBUG_RUN_ID", "pre-fix"),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    try:
        with open(
            "/Users/cristiamdasilva/genlayer/genlayer-studio/.cursor/debug.log", "a"
        ) as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        try:
            print("AGENT_DEBUG " + json.dumps(payload), flush=True)
        except Exception:
            pass


# endregion


@dataclass
class ExecutionError:
    message: str
    kind: typing.Literal[ResultCode.USER_ERROR, ResultCode.VM_ERROR]

    def __repr__(self):
        return json.dumps({"kind": self.kind.name, "message": self.message})


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
    def storage_write(
        self,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None: ...
    @abc.abstractmethod
    def get_balance(self, addr: Address) -> int: ...


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
        self, res: genvmhost.RunHostAndProgramRes, state: StateProxy
    ) -> ExecutionResult:
        assert self._result is not None
        return ExecutionResult(
            eq_outputs=self._eq_outputs,
            pending_transactions=self._pending_transactions,
            stdout=res.stdout,
            stderr=res.stderr,
            genvm_log=res.genvm_log,
            result=self._result,
            state=state,
            processing_time=0,
            nondet_disagree=self._nondet_disagreement,
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

    async def get_calldata(self, /) -> bytes:
        return self.calldata_bytes

    def has_result(self) -> bool:
        return self._result is not None

    async def storage_read(
        self, type: StorageType, account: bytes, slot: bytes, index: int, le: int, /
    ) -> bytes:
        assert type != StorageType.LATEST_FINAL
        return self._state_proxy.storage_read(Address(account), slot, index, le)

    async def storage_write(
        self,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        return self._state_proxy.storage_write(slot, index, got)

    async def consume_result(
        self, type: ResultCode, data: collections.abc.Buffer, /
    ) -> None:
        if type == ResultCode.RETURN:
            self._result = ExecutionReturn(ret=bytes(data))
        elif type == ResultCode.USER_ERROR or type == ResultCode.VM_ERROR:
            res = calldata.decode(data)
            _agent_log(
                "H4",
                "backend/node/genvm/base.py:Host.consume_result",
                "genvm result consumed",
                {
                    "result_code": getattr(type, "name", str(type)),
                    "message": res.get("message") if isinstance(res, dict) else None,
                    "res_type": res.__class__.__name__,
                },
            )
            self._result = ExecutionError(res["message"], type)
        elif type == ResultCode.INTERNAL_ERROR:
            raise Exception("GenVM internal error", str(data, encoding="utf-8"))
        else:
            assert False, f"invalid result {type}"

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

    async def post_event(self, topics: list[bytes], blob: bytes, /) -> None:
        raise Exception("not supported in studio")

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
    return ExecutionResult(
        result=ExecutionError(message="timeout", kind=ResultCode.VM_ERROR),
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
                    )

                    execution_result = host.provide_result(
                        res,
                        fresh_args.get("state_proxy", host_args.get("state_proxy")),
                    )

                    execution_result.processing_time = math.ceil(
                        (time.time() - start_time) * 1000
                    )

                    return execution_result
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
