import socket
import typing
import collections.abc
import asyncio
import os
import abc
import json
import time

import aiohttp

from dataclasses import dataclass

from pathlib import Path

from . import host_fns
from . import public_abi

ACCOUNT_ADDR_SIZE = 20
SLOT_ID_SIZE = 32

from .logger import Logger, NoLogger


def _get_timeout_seconds(env_key: str, default: float) -> float:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _http_timeout(
    *,
    total_s: float,
    connect_s: float | None = None,
    sock_read_s: float | None = None,
) -> aiohttp.ClientTimeout:
    """
    Explicit aiohttp timeout to avoid wedging consensus when the local GenVM manager
    accepts a connection but never responds.
    """
    return aiohttp.ClientTimeout(
        total=total_s, connect=connect_s, sock_read=sock_read_s
    )


class HostException(Exception):
    def __init__(self, error_code: host_fns.Errors, message: str = ""):
        if error_code == host_fns.Errors.OK:
            raise ValueError("Error code cannot be OK")
        self.error_code = error_code
        super().__init__(message or f"GenVM error: {error_code}")


class DefaultEthTransactionData(typing.TypedDict):
    value: str


class DefaultTransactionData(typing.TypedDict):
    value: str
    on: str


class DeployDefaultTransactionData(DefaultTransactionData):
    salt_nonce: typing.NotRequired[str]


class IHost(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def loop_enter(self, cancellation: asyncio.Event) -> socket.socket: ...

    @abc.abstractmethod
    async def get_calldata(self, /) -> bytes: ...

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
    async def storage_write(
        self,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None: ...

    @abc.abstractmethod
    async def consume_result(
        self, type: public_abi.ResultCode, data: collections.abc.Buffer, /
    ) -> None: ...
    @abc.abstractmethod
    def has_result(self) -> bool: ...

    @abc.abstractmethod
    async def get_leader_nondet_result(
        self, call_no: int, /
    ) -> collections.abc.Buffer: ...
    @abc.abstractmethod
    async def post_nondet_result(
        self, call_no: int, data: collections.abc.Buffer, /
    ) -> None: ...
    @abc.abstractmethod
    async def post_message(
        self, account: bytes, calldata: bytes, data: DefaultTransactionData, /
    ) -> None: ...
    @abc.abstractmethod
    async def deploy_contract(
        self, calldata: bytes, code: bytes, data: DeployDefaultTransactionData, /
    ) -> None: ...
    @abc.abstractmethod
    async def consume_gas(self, gas: int, /) -> None: ...
    @abc.abstractmethod
    async def eth_send(
        self, account: bytes, calldata: bytes, data: DefaultEthTransactionData, /
    ) -> None: ...
    @abc.abstractmethod
    async def eth_call(self, account: bytes, calldata: bytes, /) -> bytes: ...
    @abc.abstractmethod
    async def get_balance(self, account: bytes, /) -> int: ...
    @abc.abstractmethod
    async def remaining_fuel_as_gen(self, /) -> int: ...
    @abc.abstractmethod
    async def post_event(self, topics: list[bytes], blob: bytes, /) -> None: ...
    @abc.abstractmethod
    async def notify_nondet_disagreement(self, call_no: int, /) -> None: ...


import datetime
import base64


async def get_pre_deployment_writes(
    code: bytes, timestamp: datetime.datetime, manager_uri: str
) -> list[tuple[bytes, int, bytes]]:
    async with aiohttp.request(
        "POST",
        headers={
            "Deployment-Timestamp": timestamp.astimezone(datetime.UTC).isoformat(
                timespec="milliseconds"
            ),
        },
        url=f"{manager_uri}/contract/pre-deploy-writes",
        data=code,
        timeout=_http_timeout(
            total_s=_get_timeout_seconds(
                "GENVM_MANAGER_PREDEPLOY_HTTP_TIMEOUT_SECONDS", 10.0
            ),
            connect_s=3.0,
            sock_read_s=_get_timeout_seconds(
                "GENVM_MANAGER_PREDEPLOY_HTTP_TIMEOUT_SECONDS", 10.0
            ),
        ),
    ) as resp:
        if resp.status != 200:
            raise Exception(
                f"pre-deploy-writes failed: {resp.status} {await resp.text()}"
            )
        body = await resp.json()
        ret = []
        for k, v in body["writes"]:
            k = bytes(base64.b64decode(k))
            v = bytes(base64.b64decode(v))
            ret.append((k[:32], int.from_bytes(k[32:], byteorder="little"), v))
        return ret


async def host_loop(handler: IHost, cancellation: asyncio.Event, *, logger: Logger):
    async_loop = asyncio.get_event_loop()

    logger.trace("entering loop")
    sock = await handler.loop_enter(cancellation)
    logger.trace("entered loop")

    async def send_all(data: collections.abc.Buffer):
        await async_loop.sock_sendall(sock, data)

    async def read_exact(le: int) -> bytes:
        buf = bytearray([0] * le)
        idx = 0
        while idx < le:
            read = await async_loop.sock_recv_into(sock, memoryview(buf)[idx:le])
            if read == 0:
                raise ConnectionResetError()
            idx += read
        return bytes(buf)

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
        meth_id = host_fns.Methods(await recv_int(1))
        logger.trace("got method", method=meth_id, method_name=meth_id.name)
        call_counts[meth_id.name] = call_counts.get(meth_id.name, 0) + 1

        handling_start = time.time()
        match meth_id:
            case host_fns.Methods.GET_CALLDATA:
                try:
                    cd = await handler.get_calldata()
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    await send_int(len(cd))
                    await send_all(cd)
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
            case host_fns.Methods.STORAGE_WRITE:
                slot = await read_exact(SLOT_ID_SIZE)
                index = await recv_int()
                le = await recv_int()
                got = await read_exact(le)
                try:
                    await handler.storage_write(slot, index, got)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
            case host_fns.Methods.CONSUME_RESULT:
                logger.debug(
                    "handling time",
                    total=total_handling_time,
                    by_method=time_per_method,
                    call_counts=call_counts,
                )
                res = await read_slice()
                await handler.consume_result(public_abi.ResultCode(res[0]), res[1:])
                await send_all(b"\x00")
                return
            case host_fns.Methods.GET_LEADER_NONDET_RESULT:
                call_no = await recv_int()
                try:
                    data = await handler.get_leader_nondet_result(call_no)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
                    data = memoryview(data)
                    await send_int(len(data))
                    await send_all(data)
            case host_fns.Methods.POST_NONDET_RESULT:
                call_no = await recv_int()
                try:
                    await handler.post_nondet_result(call_no, await read_slice())
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
            case host_fns.Methods.POST_MESSAGE:
                account = await read_exact(ACCOUNT_ADDR_SIZE)

                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                try:
                    await handler.post_message(account, calldata, message_data)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
            case host_fns.Methods.CONSUME_FUEL:
                gas = await recv_int(8)
                await handler.consume_gas(gas)
            case host_fns.Methods.DEPLOY_CONTRACT:
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                code_len = await recv_int()
                code = await read_exact(code_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                try:
                    await handler.deploy_contract(calldata, code, message_data)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))

            case host_fns.Methods.ETH_SEND:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                try:
                    await handler.eth_send(account, calldata, message_data)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
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
            case host_fns.Methods.POST_EVENT:
                topics_len = await recv_int(1)
                topics = []
                for i in range(topics_len):
                    topic = await read_exact(32)
                    topics.append(topic)
                blob = await read_slice()
                try:
                    await handler.post_event(topics, blob)
                except HostException as e:
                    await send_all(bytes([e.error_code]))
                else:
                    await send_all(bytes([host_fns.Errors.OK]))
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


async def _send_timeout(manager_uri: str, genvm_id: str, logger: Logger):
    try:
        async with aiohttp.request(
            "DELETE",
            f"{manager_uri}/genvm/{genvm_id}?wait_timeout_ms=20",
            timeout=_http_timeout(
                total_s=_get_timeout_seconds(
                    "GENVM_MANAGER_DELETE_HTTP_TIMEOUT_SECONDS", 3.0
                ),
                connect_s=1.5,
                sock_read_s=1.5,
            ),
        ) as resp:
            logger.debug("delete /genvm", genvm_id=genvm_id, status=resp.status)
            if resp.status != 200:
                logger.warning(
                    "delete /genvm failed", genvm_id=genvm_id, body=await resp.text()
                )
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            "delete /genvm request failed", genvm_id=genvm_id, error=str(exc)
        )


async def run_genvm(
    handler: IHost,
    *,
    timeout: float | None = None,
    manager_uri: str = "http://127.0.0.1:3999",
    logger: Logger | None = None,
    is_sync: bool,
    capture_output: bool = True,
    message: typing.Any,
    host_data: str = "",
    host: str,
    extra_args: list[str] = [],
) -> RunHostAndProgramRes:
    if logger is None:
        logger = NoLogger()

    genvm_id_cell: list[str | None] = [None]
    status_cell: list[dict | Exception | None] = [None]
    timeout_task_cell: list[asyncio.Task | None] = [None]
    cancellation_event = asyncio.Event()

    run_http_timeout_s = _get_timeout_seconds(
        "GENVM_MANAGER_RUN_HTTP_TIMEOUT_SECONDS", 30.0
    )
    status_http_timeout_s = _get_timeout_seconds(
        "GENVM_MANAGER_STATUS_HTTP_TIMEOUT_SECONDS", 10.0
    )

    async def wrap_proc():
        try:
            max_exec_mins = 20
            if timeout is not None:
                max_exec_mins = int(max(max_exec_mins, (timeout * 1.5 + 59) // 60))

            timestamp = message.get("datetime", "2024-11-26T06:42:42.424242Z")

            async with aiohttp.request(
                "POST",
                f"{manager_uri}/genvm/run",
                json={
                    "major": 0,  # FIXME
                    "message": message,
                    "is_sync": is_sync,
                    "capture_output": capture_output,
                    "host_data": host_data,
                    "max_execution_minutes": max_exec_mins,  # this parameter is needed to prevent zombie genvms
                    "timestamp": timestamp,
                    "host": host,
                    "extra_args": extra_args,
                },
                timeout=_http_timeout(
                    total_s=run_http_timeout_s,
                    connect_s=min(5.0, run_http_timeout_s),
                    sock_read_s=run_http_timeout_s,
                ),
            ) as resp:
                logger.debug("post /genvm/run", status=resp.status)
                data = await resp.json()
                logger.trace("post /genvm/run", body=data)
                if resp.status != 200:
                    logger.error(
                        f"genvm manager /genvm/run failed",
                        status=resp.status,
                        body=data,
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
                    timeout_task_cell[0] = asyncio.ensure_future(wrap_timeout(genvm_id))
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            # If manager HTTP is wedged, stop the host loop too (otherwise run_genvm hangs).
            logger.error("genvm manager request failed", error=str(exc))
            cancellation_event.set()
            raise
        finally:
            logger.debug("proc started", genvm_id=genvm_id_cell[0])

    async def wrap_host():
        await host_loop(handler, cancellation_event, logger=logger)
        logger.debug("host loop finished")

    async def wrap_timeout(genvm_id: str):
        if timeout is None:
            return
        await asyncio.sleep(timeout)
        logger.debug("timeout reached", genvm_id=genvm_id)
        await _send_timeout(manager_uri, genvm_id, logger)

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
                    timeout=_http_timeout(
                        total_s=status_http_timeout_s,
                        connect_s=min(3.0, status_http_timeout_s),
                        sock_read_s=status_http_timeout_s,
                    ),
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
        sleep_task = asyncio.ensure_future(asyncio.sleep(1))
        wait_task = asyncio.ensure_future(cancellation_event.wait())

        done, pending = await asyncio.wait(
            [sleep_task, wait_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel pending tasks to prevent leaks
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

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
    except BaseException as e:
        exceptions.append(e)

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
        await _send_timeout(manager_uri, genvm_id, logger)

        status = await poll_status(genvm_id)
        if status is None:
            exceptions.append(Exception("execution failed: no status"))
        elif isinstance(status, Exception):
            exceptions.append(status)
        if len(exceptions) > 0:
            final_exception = Exception("execution failed", exceptions[1:])
            raise final_exception from exceptions[0]
        return RunHostAndProgramRes(
            stdout=status["stdout"],
            stderr=status["stderr"],
            genvm_log=status.get("genvm_log") or [],
        )

    raise Exception("Execution failed")
