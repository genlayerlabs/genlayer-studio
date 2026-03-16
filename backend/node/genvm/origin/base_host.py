"""
This module is a part of GenVM source code. When updating

#. Open PR to https://github.com/genlayerlabs/genvm/blob/main/tests/runner/origin/base_host.py
#. Keep interface integration-agnostic: no usage of environment variables, no assumptions
"""

import enum
import socket
import typing
import collections.abc
import asyncio
import os
import sys
import abc
import json
import time

import aiohttp

from dataclasses import dataclass

from pathlib import Path

from .calldata import Address
from . import calldata as gvm_calldata
from . import host_fns
from . import public_abi

ACCOUNT_ADDR_SIZE = 20
SLOT_ID_SIZE = 32

from .logger import Logger


class TimeoutAction(enum.StrEnum):
    VMErrorDescribe = "vm-error/describe"
    GenVMGet = "/genvm/{id}"
    GenVMRun = "/genvm/run"
    GenVMDelete = "DELETE /genvm/{id}"


class TimeoutType(enum.StrEnum):
    TOTAL_S = "HTTP_TIMEOUT_TOTAL_S"
    CONNECT_S = "HTTP_TIMEOUT_CONNECT_S"
    SOCK_READ_S = "HTTP_TIMEOUT_SOCK_READ_S"
    DELETE_HTTP_GRACEFUL_TIMEOUT_MS = "DELETE_HTTP_GRACEFUL_TIMEOUT_MS"


class Context(typing.Protocol):
    logger: Logger

    def on_genvm_success(self): ...
    def on_genvm_failure(self): ...

    def add_stat(self, key: str, value: typing.Any, /): ...

    def get_timeout(self, action: TimeoutAction, type: TimeoutType, /) -> float | None:
        return None

    def retry_delay(self, action: TimeoutAction, attempt_no: int, /) -> float | None:
        """Returns delay before next retry, or None if no retries are left."""
        return None


def _http_timeout(
    ctx: Context,
    action: TimeoutAction,
) -> aiohttp.ClientTimeout:
    """
    Explicit aiohttp timeout to avoid wedging consensus when the local GenVM manager
    accepts a connection but never responds.
    """
    total_s = ctx.get_timeout(action, TimeoutType.TOTAL_S)
    connect_s = ctx.get_timeout(action, TimeoutType.CONNECT_S)
    sock_read_s = ctx.get_timeout(action, TimeoutType.SOCK_READ_S)
    return aiohttp.ClientTimeout(
        total=total_s, connect=connect_s, sock_read=sock_read_s
    )


class HostException(Exception):
    def __init__(self, error_code: host_fns.Errors, message: str = ""):
        if error_code == host_fns.Errors.OK:
            raise ValueError("Error code cannot be OK")
        self.error_code = error_code
        super().__init__(message or f"GenVM error: {error_code}")


class Message(typing.TypedDict):
    contract_address: Address
    sender_address: Address
    origin_address: Address
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


class PostMessageInner(typing.TypedDict):
    type: typing.Literal["PostMessage"]
    address: Address
    calldata: gvm_calldata.Decoded
    value: int
    on: typing.Literal["finalized", "accepted"]


class DeployContractInner(typing.TypedDict):
    type: typing.Literal["DeployContract"]
    calldata: gvm_calldata.Decoded
    code: bytes
    value: int
    on: typing.Literal["finalized", "accepted"]
    salt_nonce: int


class EmitEventInner(typing.TypedDict):
    type: typing.Literal["EmitEvent"]
    topics: list[bytes]
    blob: dict[str, gvm_calldata.Decoded]


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


async def host_loop(
    handler: IHost,
    cancellation: asyncio.Event,
    *,
    ctx: Context,
) -> None:
    async_loop = asyncio.get_event_loop()

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

    handling_start = time.time()
    while True:
        cur_delta = time.time() - handling_start
        if meth_id is not None:
            total_handling_time += cur_delta
            time_per_method[meth_id.name] = (
                time_per_method.get(meth_id.name, 0.0) + cur_delta
            )

        await flush_socket_buffer()

        meth_id = host_fns.Methods(await recv_int(1))
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
            case host_fns.Methods.CONSUME_RESULT:
                raise Exception(
                    "CONSUME_RESULT is not supported in this host loop implementation, use manager provided one"
                )
            case host_fns.Methods.NOTIFY_FINISHED:
                logger.debug(
                    "handling time",
                    total=total_handling_time,
                    by_method=time_per_method,
                    call_counts=call_counts,
                )
                await send_all(bytes([0]))
                await flush_socket_buffer()

                if first_method_name is not None:
                    ctx.add_stat("host_first_method", first_method_name)
                ctx.add_stat(
                    "host_total_handling_time_ms", round(total_handling_time * 1000)
                )
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
                return None
            case host_fns.Methods.CONSUME_FUEL:
                gas = await recv_int(8)
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
                    res = min(res, 2**53 - 1)
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_all(res.to_bytes(8, byteorder="little", signed=False))
            case host_fns.Methods.NOTIFY_NONDET_DISAGREEMENT:
                call_no = await recv_int()
                await handler.notify_nondet_disagreement(call_no)
                # No response needed according to the spec
            case x:
                raise Exception(f"unknown method {x}")


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
    vm_error_description: str | None = None


async def _send_timeout(
    manager_uri: str,
    genvm_id: str,
    ctx: Context,
):
    try:
        graceful_shutdown_wait_time_ms = ctx.get_timeout(
            TimeoutAction.GenVMDelete, TimeoutType.DELETE_HTTP_GRACEFUL_TIMEOUT_MS
        )
        if graceful_shutdown_wait_time_ms is None:
            graceful_shutdown_wait_time_ms = 20
        else:
            graceful_shutdown_wait_time_ms = int(graceful_shutdown_wait_time_ms)
        async with aiohttp.request(
            "DELETE",
            f"{manager_uri}/genvm/{genvm_id}?wait_timeout_ms={graceful_shutdown_wait_time_ms}",
            timeout=_http_timeout(ctx, TimeoutAction.GenVMDelete),
        ) as resp:
            ctx.add_stat("delete_genvm_status", resp.status)
            if resp.status != 200:
                ctx.add_stat("delete_genvm_failed", True)
                ctx.add_stat("delete_genvm_body", await resp.text())
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        ctx.add_stat("delete_genvm_request_failed", True)
        ctx.add_stat("delete_genvm_request_error", str(exc))


async def _await_first_cancel_others(*it):
    _done, pending = await asyncio.wait(
        [asyncio.ensure_future(x) for x in it],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass


async def run_genvm(
    handler: IHost,
    *,
    timeout: float | None = None,
    manager_uri: str = "http://127.0.0.1:3999",
    ctx: Context,
    is_sync: bool,
    capture_output: bool = True,
    message: Message,
    host_data: str = "",
    host: str,
    extra_args: list[str] = [],
    data_fees_limit: int = 10_000_000,
    storage_page_cost: int = 1,
    receipt_word_cost: int = 1,
    code: bytes | None = None,
    calldata: bytes,
    leader_nondet_results: list[bytes] | None = None,
    request_extra: dict[str, gvm_calldata.Encodable] = {},
) -> RunHostAndProgramRes:
    logger = ctx.logger

    perf_timeline: dict[str, typing.Any] = {
        "run_started_s": time.perf_counter(),
    }
    genvm_id_cell: list[str | None] = [None]
    status_cell: list[dict | Exception | None] = [None]
    timeout_task_cell: list[asyncio.Task | None] = [None]
    cancellation_event = asyncio.Event()

    started_at = [time.time()]

    async def wrap_proc_body(attempt: int):
        max_exec_mins = 20
        if timeout is not None:
            max_exec_mins = int(max(max_exec_mins, (timeout * 1.5 + 59) // 60))

        timestamp = message.get("datetime", "2024-11-26T06:42:42.424242Z")

        async with aiohttp.request(
            "POST",
            f"{manager_uri}/genvm/run",
            data=gvm_calldata.encode(
                {
                    "major": 0,  # FIXME
                    "message": message,
                    "is_sync": is_sync,
                    "capture_output": capture_output,
                    "host_data": host_data,
                    "max_execution_minutes": max_exec_mins,  # this parameter is needed to prevent zombie genvms
                    "timestamp": timestamp,
                    "host": host,
                    "extra_args": extra_args,
                    "code": code,
                    "calldata": calldata,
                    "leader_nondet_results": leader_nondet_results,
                    "data_fees_limit": data_fees_limit,
                    "storage_page_cost": storage_page_cost,
                    "receipt_word_cost": receipt_word_cost,
                    **request_extra,
                }
            ),
            timeout=_http_timeout(ctx, TimeoutAction.GenVMRun),
        ) as resp:
            logger.debug("post /genvm/run", status=resp.status, attempt=attempt)
            data = await resp.json()
            logger.trace("post /genvm/run", body=data)
            if resp.status != 200:
                logger.error(
                    f"genvm manager /genvm/run failed", status=resp.status, body=data
                )
                raise Exception(
                    f"genvm manager /genvm/run failed: {resp.status} {data}"
                )
            else:
                genvm_id = data["id"]
                logger.debug(
                    "genvm manager /genvm", genvm_id=genvm_id, status=resp.status
                )
                genvm_id_cell[0] = genvm_id
                perf_timeline["genvm_id_obtained_s"] = time.perf_counter()
                timeout_task_cell[0] = asyncio.ensure_future(wrap_timeout(genvm_id))
                ctx.on_genvm_success()

    async def wrap_proc():
        attempt = 0
        while True:
            attempt_start = time.perf_counter()
            try:
                await wrap_proc_body(attempt)
                ctx.add_stat(
                    "manager_run_attempt_success",
                    {
                        "attempt": attempt,
                        "duration_ms": round(
                            (time.perf_counter() - attempt_start) * 1000
                        ),
                    },
                )
                break
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                delay = ctx.retry_delay(TimeoutAction.GenVMRun, attempt)
                ctx.add_stat(
                    f"manager_run_attempt_{attempt}_error",
                    {
                        "attempt": attempt,
                        "error_type": type(exc).__name__,
                        "duration_ms": round(
                            (time.perf_counter() - attempt_start) * 1000
                        ),
                        "will_retry": delay is not None,
                    },
                )
                if delay is None:
                    logger.error(
                        "genvm manager request failed after all retries",
                        error=str(exc),
                        attempt=attempt,
                    )
                    ctx.on_genvm_failure()
                    cancellation_event.set()
                    raise
                logger.warning(
                    "genvm manager request failed, retrying",
                    error=str(exc),
                    attempt=attempt,
                    retry_delay_s=delay,
                )
                await asyncio.sleep(delay)
            except Exception:
                ctx.add_stat(
                    f"manager_run_attempt_{attempt}_error",
                    {
                        "attempt": attempt,
                        "outcome": "fatal_error",
                        "duration_ms": round(
                            (time.perf_counter() - attempt_start) * 1000
                        ),
                    },
                )
                raise
            finally:
                if genvm_id_cell[0] is not None:
                    logger.debug("proc started", genvm_id=genvm_id_cell[0])
            attempt += 1
        started_at[0] = time.time()

    async def wrap_host():
        r = await host_loop(handler, cancellation_event, ctx=ctx)
        logger.debug("host loop finished")
        return r

    timeout_fired = asyncio.Event()

    async def wrap_timeout(genvm_id: str):
        if timeout is None:
            return
        await asyncio.sleep(timeout)
        logger.debug("timeout reached", genvm_id=genvm_id)
        timeout_fired.set()
        await _send_timeout(
            manager_uri,
            genvm_id,
            ctx=ctx,
        )

    poll_status_mutex = asyncio.Lock()

    async def poll_status(genvm_id: str):
        async with poll_status_mutex:
            old_status = status_cell[0]
            if old_status is not None:
                return old_status
            try:
                async with aiohttp.request(
                    "GET",
                    f"{manager_uri}/genvm/{genvm_id}",
                    timeout=_http_timeout(ctx, TimeoutAction.GenVMGet),
                ) as resp:
                    logger.debug("get /genvm", genvm_id=genvm_id, status=resp.status)
                    body = await resp.json()
                    logger.trace("get /genvm", genvm_id=genvm_id, body=body)
                    if resp.status != 200:
                        new_res = Exception(
                            f"genvm manager /genvm failed: {resp.status} {body}"
                        )
                    elif body["status"] is None:
                        return None
                    else:
                        new_res = typing.cast(dict, body["status"])
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                new_res = Exception(f"genvm manager /genvm request failed: {exc}")
            status_cell[0] = new_res
            return new_res

    async def prob_died():
        await _await_first_cancel_others(asyncio.sleep(1), cancellation_event.wait())

        genvm_id = genvm_id_cell[0]
        if genvm_id is None:
            return
        status = await poll_status(genvm_id)
        if status is not None and not cancellation_event.is_set():
            logger.error(
                "genvm died without connecting", genvm_id=genvm_id, status=status
            )
            cancellation_event.set()

    fut_host = asyncio.ensure_future(wrap_host())
    fut_proc = asyncio.ensure_future(wrap_proc())
    fut_prob = asyncio.ensure_future(prob_died())

    # Map futures to names for debugging
    task_names = {
        id(fut_host): "host_loop",
        id(fut_proc): "genvm_run",
        id(fut_prob): "prob_died",
    }

    # IMPORTANT: if proc setup fails (e.g., manager accepts TCP but never replies),
    # don't wait forever on host_loop.
    done, pending = await asyncio.wait(
        [fut_host, fut_proc, fut_prob], return_when=asyncio.FIRST_EXCEPTION
    )

    # Log which tasks completed/failed for debugging
    done_names = [task_names.get(id(t), "unknown") for t in done]
    pending_names = [task_names.get(id(t), "unknown") for t in pending]
    logger.debug(
        "asyncio.wait returned",
        done_tasks=done_names,
        pending_tasks=pending_names,
        genvm_id=genvm_id_cell[0],
    )

    # If anything errored, stop the host loop.
    for task in done:
        exc = task.exception()
        if exc is not None:
            task_name = task_names.get(id(task), "unknown")
            logger.error(
                "task raised exception",
                task_name=task_name,
                exception_type=type(exc).__name__,
                exception_msg=str(exc),
                genvm_id=genvm_id_cell[0],
            )
            cancellation_event.set()

    # Cancel any pending tasks to prevent leaks
    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    # Cancel the timeout task if it's still pending
    timeout_task = timeout_task_cell[0]
    if timeout_task is not None and not timeout_task.done():
        timeout_task.cancel()
        try:
            await timeout_task
        except asyncio.CancelledError:
            pass

    # Collect exceptions from all tasks, including CancelledError
    # Note: CancelledError inherits from BaseException, not Exception
    exceptions: list[BaseException] = []
    cancelled_tasks: list[str] = []

    try:
        fut_host.result()
    except asyncio.CancelledError:
        cancelled_tasks.append("host_loop")
    except ConnectionResetError as e:
        if not timeout_fired.is_set():
            logger.warning("connection reset without timeout", error=e)
    except BaseException as e:
        if not timeout_fired.is_set():
            exceptions.append(e)
        else:
            logger.warning("host handler failed after timeout", error=e)

    try:
        fut_proc.result()
    except asyncio.CancelledError:
        cancelled_tasks.append("genvm_run")
    except BaseException as e:
        exceptions.append(e)

    # Log if tasks were cancelled (helps debug root cause)
    if cancelled_tasks:
        logger.debug(
            "tasks were cancelled",
            cancelled_tasks=cancelled_tasks,
            exception_count=len(exceptions),
            genvm_id=genvm_id_cell[0],
        )

    if len(exceptions) > 0:
        # Include cancelled tasks info in the exception message for debugging
        error_details = {
            "exceptions": [f"{type(e).__name__}: {e}" for e in exceptions],
            "cancelled_tasks": cancelled_tasks,
            "genvm_id": genvm_id_cell[0],
        }
        logger.error("genvm execution failed", **error_details)
        raise Exception(f"genvm execution failed: {error_details}") from exceptions[0]

    # If all tasks were cancelled but no exceptions, something went wrong
    if cancelled_tasks and len(exceptions) == 0:
        error_msg = f"all genvm tasks cancelled without error: cancelled={cancelled_tasks}, genvm_id={genvm_id_cell[0]}"
        logger.error(error_msg)
        raise Exception(error_msg)

    genvm_id = genvm_id_cell[0]
    if genvm_id is not None:
        await _send_timeout(
            manager_uri,
            genvm_id,
            ctx=ctx,
        )

        status = await poll_status(genvm_id)
        if status is None:
            exceptions.append(Exception("execution failed: no status"))
        elif isinstance(status, Exception):
            exceptions.append(status)
        if len(exceptions) > 0:
            final_exception = Exception("execution failed", exceptions[1:])
            raise final_exception from exceptions[0]

        # Result was sent to manager via consume_result, get it from status
        consumed_result_raw = (
            status.get("consumed_result") if isinstance(status, dict) else None
        )
        if consumed_result_raw is not None:
            consumed_result_bytes = bytes(consumed_result_raw)
            result_kind = public_abi.ResultCode(consumed_result_bytes[0])
            decoded = gvm_calldata.decode(consumed_result_bytes[1:])
            execution_hash = decoded.get("execution_hash", b"")
            result_data = decoded.get("data")
            result_fingerprint = decoded.get("fingerprint")
            result_storage_changes = decoded.get("storage_changes", [])
            result_emissions = decoded.get("emissions", [])
            nondet_results = decoded.get("nondet_results", [])
        else:
            execution_hash = b""
            result_kind = public_abi.ResultCode.INTERNAL_ERROR
            result_data = "no_result"
            result_fingerprint = None
            result_storage_changes = []
            result_emissions = []
            nondet_results = []

        if timeout_fired.is_set() and result_kind != public_abi.ResultCode.RETURN:
            result_kind = public_abi.ResultCode.VM_ERROR
            result_data = public_abi.VmError.TIMEOUT.value

        vm_error_description: str | None = None
        if result_kind == public_abi.ResultCode.VM_ERROR and isinstance(
            result_data, str
        ):
            try:
                async with aiohttp.request(
                    "GET",
                    f"{manager_uri}/vm-error/describe",
                    params={"error": result_data},
                    timeout=_http_timeout(ctx, TimeoutAction.VMErrorDescribe),
                ) as resp:
                    if resp.status == 200:
                        body = await resp.json()
                        vm_error_description = body.get("description")
            except Exception as e:
                logger.warning("failed to get vm error description", error=str(e))

        return RunHostAndProgramRes(
            stdout=status["stdout"],
            stderr=status["stderr"],
            genvm_log=status.get("genvm_log") or [],
            execution_hash=execution_hash,
            result_kind=result_kind,
            result_data=result_data,
            result_fingerprint=result_fingerprint,
            result_storage_changes=result_storage_changes,
            result_emissions=result_emissions,
            result_nondet_results=nondet_results,
            vm_error_description=vm_error_description,
            execution_time=time.time() - started_at[0],
        )

    raise Exception("Execution failed")
