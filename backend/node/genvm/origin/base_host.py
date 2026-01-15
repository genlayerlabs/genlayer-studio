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

if True:
    root = Path(__file__).parent
    while root.parent != root:
        mr_root = root.joinpath(".genvm-monorepo-root")
        if mr_root.exists():
            dir_to_add = json.loads(mr_root.read_bytes())["py-std"]
            sys.path.insert(0, str(root.joinpath(*dir_to_add)))
            break
        root = root.parent
    from . import calldata as gvm_calldata


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


def _get_int(env_key: str, default: int) -> int:
    raw = os.getenv(env_key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Callbacks for tracking GenVM Manager failures (set by worker_service)
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
    async def notify_nondet_disagreement(self, call_no: int, /) -> None: ...


async def host_loop(
    handler: IHost, cancellation: asyncio.Event, *, logger: Logger
) -> tuple[public_abi.ResultCode, bytes]:
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
                logger.debug(
                    "handling time",
                    total=total_handling_time,
                    by_method=time_per_method,
                    call_counts=call_counts,
                )
                res = await read_slice()

                await send_all(bytes([0]))

                return public_abi.ResultCode(res[0]), res[1:]
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

    result_kind: public_abi.ResultCode
    result_data: typing.Any
    result_fingerprint: typing.Any
    result_storage_changes: list[tuple[bytes, bytes]]
    result_events: list[list[bytes]]


async def _send_timeout(manager_uri: str, genvm_id: str, logger: Logger):
    try:
        async with aiohttp.request(
            "DELETE",
            f"{manager_uri}/genvm/{genvm_id}?wait_timeout_ms=20",
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
    storage_pages: int = 10_000_000,
    code: bytes | None = None,
    calldata: bytes,
) -> RunHostAndProgramRes:
    if logger is None:
        logger = NoLogger()

    genvm_id_cell: list[str | None] = [None]
    status_cell: list[dict | Exception | None] = [None]
    cancellation_event = asyncio.Event()

    async def wrap_proc():
        try:
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
                        "storage_pages": storage_pages,
                        "code": code,
                        "calldata": calldata,
                    }
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
                    asyncio.ensure_future(wrap_timeout(genvm_id))
        finally:
            logger.debug("proc started", genvm_id=genvm_id_cell[0])

    async def wrap_host():
        r = await host_loop(handler, cancellation_event, logger=logger)
        logger.debug("host loop finished")
        return r

    timeout_fired = asyncio.Event()

    async def wrap_timeout(genvm_id: str):
        if timeout is None:
            return
        await asyncio.sleep(timeout)
        logger.debug("timeout reached", genvm_id=genvm_id)
        timeout_fired.set()
        await _send_timeout(manager_uri, genvm_id, logger)

    poll_status_mutex = asyncio.Lock()

    async def poll_status(genvm_id: str):
        async with poll_status_mutex:
            old_status = status_cell[0]
            if old_status is not None:
                return old_status
            async with aiohttp.request(
                "GET",
                f"{manager_uri}/genvm/{genvm_id}",
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
            status_cell[0] = new_res
            return new_res

    async def prob_died():
        cancellation_future = asyncio.ensure_future(cancellation_event.wait())
        await asyncio.wait(
            [
                asyncio.ensure_future(asyncio.sleep(1)),
                cancellation_future,
            ],
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not cancellation_future.done():
            cancellation_future.cancel()
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
    await asyncio.wait([fut_host, fut_proc, asyncio.ensure_future(prob_died())])

    exceptions: list[Exception] = []
    result_host: tuple[public_abi.ResultCode, bytes] | None = None
    try:
        result_host = fut_host.result()
    except Exception as e:
        if not timeout_fired.is_set():
            exceptions.append(e)
        else:
            logger.warning("host handler failed after timeout", error=e)
    try:
        fut_proc.result()
    except Exception as e:
        exceptions.append(e)

    if len(exceptions) > 0:
        raise Exception(*exceptions) from exceptions[0]

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

        if result_host is None:
            result_kind = public_abi.ResultCode.INTERNAL_ERROR
            result_data = "no_result"
            result_fingerprint = None
            result_storage_changes = []
            result_events = []
        else:
            result_kind = result_host[0]
            decoded = gvm_calldata.decode(result_host[1])
            result_data = decoded.get("data")
            result_fingerprint = decoded.get("fingerprint")
            result_storage_changes = decoded.get("storage_changes", [])
            result_events = decoded.get("events", [])

        return RunHostAndProgramRes(
            stdout=status["stdout"],
            stderr=status["stderr"],
            genvm_log=status.get("genvm_log") or [],
            result_kind=result_kind,
            result_data=result_data,
            result_fingerprint=result_fingerprint,
            result_storage_changes=result_storage_changes,
            result_events=result_events,
        )

    raise Exception("Execution failed")
