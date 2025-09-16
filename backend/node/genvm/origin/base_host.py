import socket
import typing
import collections.abc
import asyncio
import os
import abc
import json
import logging
import time

from dataclasses import dataclass

from pathlib import Path

# Configure logging for debugging
logger = logging.getLogger(__name__)
DEBUG_GENVM = True
if DEBUG_GENVM:
    logger.setLevel(logging.DEBUG)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(handler)

from .host_fns import *
from .result_codes import *

ACCOUNT_ADDR_SIZE = 20
GENERIC_ADDR_SIZE = 32


class GenVMTimeoutException(Exception):
    "Exception that is raised when time limit is exceeded"


class DefaultEthTransactionData(typing.TypedDict):
    value: str


class DefaultTransactionData(typing.TypedDict):
    value: str
    on: str


class DeployDefaultTransactionData(DefaultTransactionData):
    salt_nonce: typing.NotRequired[str]


class IHost(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    async def loop_enter(self) -> socket.socket: ...

    @abc.abstractmethod
    async def get_calldata(self, /) -> bytes: ...

    @abc.abstractmethod
    async def storage_read(
        self, mode: StorageType, account: bytes, slot: bytes, index: int, le: int, /
    ) -> bytes: ...
    @abc.abstractmethod
    async def storage_write(
        self,
        account: bytes,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None: ...

    @abc.abstractmethod
    async def consume_result(
        self, type: ResultCode, data: collections.abc.Buffer, /
    ) -> None: ...
    @abc.abstractmethod
    def has_result(self) -> bool: ...

    @abc.abstractmethod
    async def get_leader_nondet_result(
        self, call_no: int, /
    ) -> tuple[ResultCode, collections.abc.Buffer] | Errors: ...
    @abc.abstractmethod
    async def post_nondet_result(
        self, call_no: int, type: ResultCode, data: collections.abc.Buffer, /
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


def get_code_slot() -> bytes:
    import hashlib

    code_digest = hashlib.sha3_256(b"\x00" * 32)
    CODE_OFFSET = 1
    code_digest.update(CODE_OFFSET.to_bytes(4, byteorder="little"))
    code_slot = code_digest.digest()
    return code_slot


def save_code_callback[T](
    address: bytes, code: bytes, cb: typing.Callable[[bytes, bytes, int, bytes], T]
) -> tuple[T, T]:
    code_slot = get_code_slot()
    r1 = cb(
        address, code_slot, 0, len(code).to_bytes(4, byteorder="little", signed=False)
    )

    r2 = cb(address, code_slot, 4, code)

    return (r1, r2)


async def save_code_to_host(host: IHost, address: bytes, code: bytes):
    r1, r2 = save_code_callback(address, code, host.storage_write)
    await r1
    await r2


async def host_loop(handler: IHost):
    async_loop = asyncio.get_event_loop()
    if DEBUG_GENVM:
        logger.debug("Starting host_loop")

    sock = await handler.loop_enter()
    if DEBUG_GENVM:
        logger.debug(f"Socket connected: {sock}")

    async def send_all(data: collections.abc.Buffer):
        socket_timeout = float(os.getenv("GENVM_SOCKET_TIMEOUT", "600"))
        try:
            send_coro = async_loop.sock_sendall(sock, data)
            await asyncio.wait_for(send_coro, timeout=socket_timeout)
        except asyncio.TimeoutError:
            error_msg = f"Socket send timeout after {socket_timeout}s"
            if DEBUG_GENVM:
                logger.error(error_msg)
            raise TimeoutError(error_msg)
        except Exception as e:
            if DEBUG_GENVM:
                logger.error(f"Socket send error: {e}")
            raise

    async def read_exact(le: int) -> bytes:
        buf = bytearray([0] * le)
        idx = 0

        # Add socket timeout support
        socket_timeout = float(os.getenv("GENVM_SOCKET_TIMEOUT", "600"))

        while idx < le:
            try:
                # Use asyncio.wait_for to add timeout to socket reads
                read_coro = async_loop.sock_recv_into(sock, memoryview(buf)[idx:le])
                read = await asyncio.wait_for(read_coro, timeout=socket_timeout)

                if read == 0:
                    if DEBUG_GENVM:
                        logger.warning(
                            f"Socket closed while reading (expected {le} bytes, got {idx})"
                        )
                    raise ConnectionResetError(f"Socket closed (read {idx}/{le} bytes)")
                idx += read
            except asyncio.TimeoutError:
                error_msg = f"Socket read timeout after {socket_timeout}s (read {idx}/{le} bytes)"
                if DEBUG_GENVM:
                    logger.error(error_msg)
                raise TimeoutError(error_msg)
            except Exception as e:
                if DEBUG_GENVM:
                    logger.error(f"Socket read error: {e} (read {idx}/{le} bytes)")
                raise

        return bytes(buf)

    async def recv_int(bytes: int = 4) -> int:
        return int.from_bytes(await read_exact(bytes), byteorder="little", signed=False)

    async def send_int(i: int, bytes=4):
        await send_all(int.to_bytes(i, bytes, byteorder="little", signed=False))

    async def read_result() -> tuple[ResultCode, bytes]:
        type = await recv_int(1)
        le = await recv_int()
        data = await read_exact(le)
        return (ResultCode(type), data)

    method_count = 0
    while True:
        method_count += 1
        start_time = time.time() if DEBUG_GENVM else 0

        try:
            meth_id_raw = await recv_int(1)
        except ConnectionResetError:
            if DEBUG_GENVM:
                logger.debug("Connection reset while waiting for method ID")
            raise
        except Exception as e:
            if DEBUG_GENVM:
                logger.error(f"Error receiving method ID: {e}")
            raise

        meth_id = Methods(meth_id_raw)
        if DEBUG_GENVM:
            logger.debug(f"[Request #{method_count}] Received method: {meth_id.name}")

        match meth_id:
            case Methods.GET_CALLDATA:
                cd = await handler.get_calldata()
                await send_all(bytes([Errors.OK]))
                await send_int(len(cd))
                await send_all(cd)
                if DEBUG_GENVM:
                    logger.debug(f"  -> Sent calldata ({len(cd)} bytes)")
            case Methods.STORAGE_READ:
                mode = await read_exact(1)
                mode = StorageType(mode[0])
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                slot = await read_exact(GENERIC_ADDR_SIZE)
                index = await recv_int()
                le = await recv_int()
                res = await handler.storage_read(mode, account, slot, index, le)
                assert len(res) == le
                await send_all(bytes([Errors.OK]))
                await send_all(res)
            case Methods.STORAGE_WRITE:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                slot = await read_exact(GENERIC_ADDR_SIZE)
                index = await recv_int()
                le = await recv_int()
                got = await read_exact(le)
                await handler.storage_write(account, slot, index, got)
                await send_all(bytes([Errors.OK]))
            case Methods.CONSUME_RESULT:
                result = await read_result()
                if DEBUG_GENVM:
                    logger.debug(
                        f"  -> Consuming result: type={result[0]}, data_len={len(result[1])}"
                    )
                await handler.consume_result(*result)
                await send_all(b"\x00")
                if DEBUG_GENVM:
                    logger.debug("Host loop finished - result consumed")
                return
            case Methods.GET_LEADER_NONDET_RESULT:
                call_no = await recv_int()
                data = await handler.get_leader_nondet_result(call_no)
                if isinstance(data, Errors):
                    await send_all(bytes([data]))
                else:
                    await send_all(bytes([Errors.OK]))
                    code, as_bytes = data
                    await send_all(bytes([code]))
                    as_bytes = memoryview(as_bytes)
                    await send_int(len(as_bytes))
                    await send_all(as_bytes)
            case Methods.POST_NONDET_RESULT:
                call_no = await recv_int()
                await handler.post_nondet_result(call_no, *await read_result())
            case Methods.POST_MESSAGE:
                account = await read_exact(ACCOUNT_ADDR_SIZE)

                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                await handler.post_message(account, calldata, message_data)
            case Methods.CONSUME_FUEL:
                gas = await recv_int(8)
                await handler.consume_gas(gas)
            case Methods.DEPLOY_CONTRACT:
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                code_len = await recv_int()
                code = await read_exact(code_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                await handler.deploy_contract(calldata, code, message_data)

            case Methods.ETH_SEND:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                message_data_len = await recv_int()
                message_data_bytes = await read_exact(message_data_len)
                message_data = json.loads(str(message_data_bytes, "utf-8"))

                await handler.eth_send(account, calldata, message_data)
            case Methods.ETH_CALL:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                calldata_len = await recv_int()
                calldata = await read_exact(calldata_len)

                res = await handler.eth_call(account, calldata)
                await send_all(bytes([Errors.OK]))
                await send_int(len(res))
                await send_all(res)
            case Methods.GET_BALANCE:
                account = await read_exact(ACCOUNT_ADDR_SIZE)
                res = await handler.get_balance(account)
                await send_all(bytes([Errors.OK]))
                await send_all(res.to_bytes(32, byteorder="little", signed=False))
            case x:
                error_msg = f"unknown method {x}"
                if DEBUG_GENVM:
                    logger.error(error_msg)
                raise Exception(error_msg)

        if DEBUG_GENVM and start_time:
            elapsed = (time.time() - start_time) * 1000
            logger.debug(f"  -> Method {meth_id.name} completed in {elapsed:.2f}ms")


@dataclass
class RunHostAndProgramRes:
    stdout: str
    stderr: str
    genvm_log: str


async def run_host_and_program(
    handler: IHost,
    program: list[Path | str],
    *,
    env=None,
    cwd: Path | None = None,
    exit_timeout=5.0,  # Increased from 0.05 to 5.0 seconds for proper cleanup
    deadline: float | None = None,
) -> RunHostAndProgramRes:
    if DEBUG_GENVM:
        logger.debug(f"Starting GenVM process with deadline={deadline}s")
        logger.debug(f"Program command: {' '.join(str(p) for p in program)}")

    loop = asyncio.get_running_loop()

    async def connect_reader(fd):
        reader = asyncio.StreamReader(loop=loop)
        reader_proto = asyncio.StreamReaderProtocol(reader)
        transport, _ = await loop.connect_read_pipe(
            lambda: reader_proto, os.fdopen(fd, "rb")
        )
        return reader, transport

    stdout_rfd, stdout_wfd = os.pipe()
    stderr_rfd, stderr_wfd = os.pipe()
    genvm_log_rfd, genvm_log_wfd = os.pipe()
    stdout_reader, stdout_transport = await connect_reader(stdout_rfd)
    stderr_reader, stderr_transport = await connect_reader(stderr_rfd)
    genvm_log_reader, genvm_log_transport = await connect_reader(genvm_log_rfd)

    run_idx = program.index("run")
    program.insert(run_idx, "--log-fd")
    program.insert(run_idx + 1, str(genvm_log_wfd))

    if DEBUG_GENVM:
        logger.debug(f"Creating subprocess with command: {program}")

    try:
        process = await asyncio.create_subprocess_exec(
            *program,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stdout_wfd,
            stderr=stderr_wfd,
            cwd=cwd,
            env=env,
            pass_fds=(genvm_log_wfd,),
        )
        if DEBUG_GENVM:
            logger.debug(f"GenVM process started with PID: {process.pid}")
    except Exception as e:
        if DEBUG_GENVM:
            logger.error(f"Failed to start GenVM process: {e}")
        raise
    os.close(stdout_wfd)
    os.close(stderr_wfd)
    os.close(genvm_log_wfd)
    if process.stdin is not None:
        process.stdin.close()

    async def read_whole(reader, transport, put_to: list[bytes]):
        try:
            while True:
                read = await reader.read(4096)
                if read is None or len(read) == 0:
                    break
                put_to.append(read)
        finally:
            try:
                transport.close()
            except OSError:
                pass
            await asyncio.sleep(0)

    stdout, stderr, genvm_log = [], [], []

    async def wrap_proc():
        await asyncio.gather(
            read_whole(stdout_reader, stdout_transport, stdout),
            read_whole(stderr_reader, stderr_transport, stderr),
            read_whole(genvm_log_reader, genvm_log_transport, genvm_log),
            process.wait(),
        )

    coro_proc = asyncio.ensure_future(wrap_proc())

    async def wrap_host():
        await host_loop(handler)

    coro_loop = asyncio.ensure_future(wrap_host())

    all_proc = [coro_loop, coro_proc]
    deadline_future: None | asyncio.Task[None] = None
    if deadline is not None:
        deadline_future = asyncio.ensure_future(asyncio.sleep(deadline))
        all_proc.append(deadline_future)

    if DEBUG_GENVM:
        logger.debug(f"Waiting for tasks: coro_loop, coro_proc, deadline={deadline}")

    done, _pending = await asyncio.wait(
        all_proc,
        return_when=asyncio.FIRST_COMPLETED,
    )

    if DEBUG_GENVM:
        done_names = []
        if coro_loop in done:
            done_names.append("host_loop")
        if coro_proc in done:
            done_names.append("genvm_process")
        if deadline_future and deadline_future in done:
            done_names.append("deadline")
        logger.debug(f"First completed: {', '.join(done_names)}")

    errors = []

    for x in done:
        try:
            x.result()
        except ConnectionResetError:
            pass
        except Exception as e:
            errors.append(e)

    # coro_loop must finish first if everything succeeded
    if not coro_loop.done() and not handler.has_result() and deadline is None:
        warning_msg = "WARNING: genvm finished first (process terminated before host loop completed)"
        print(warning_msg)
        if DEBUG_GENVM:
            logger.warning(warning_msg)
            logger.debug(
                f"coro_loop.done(): {coro_loop.done()}, handler.has_result(): {handler.has_result()}"
            )
        coro_loop.cancel()

    async def wait_all_timeout():
        timeout = asyncio.ensure_future(asyncio.sleep(exit_timeout))
        all_futs = [timeout, coro_proc]
        if not coro_loop.done():
            all_futs.append(coro_loop)
        done, _pending = await asyncio.wait(
            all_futs,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if coro_loop in done:
            await wait_all_timeout()

    if handler.has_result():
        await wait_all_timeout()

    if not coro_proc.done():
        if DEBUG_GENVM:
            logger.warning(
                f"GenVM process still running after completion, attempting graceful termination (PID: {process.pid})"
            )
        try:
            process.terminate()
        except Exception as e:
            if DEBUG_GENVM:
                logger.error(f"Failed to terminate process: {e}")

        # Wait for graceful termination
        await wait_all_timeout()

        if not coro_proc.done():
            # genvm exit takes too long, forcefully quit it
            if DEBUG_GENVM:
                logger.error(
                    f"GenVM process did not terminate gracefully, forcing kill (PID: {process.pid})"
                )
            try:
                process.kill()
            except Exception as e:
                if DEBUG_GENVM:
                    logger.error(f"Failed to kill process: {e}")

    try:
        await coro_loop
    except ConnectionResetError:
        pass
    except (Exception, asyncio.CancelledError) as e:
        errors.append(e)

    exit_code = await process.wait()
    if DEBUG_GENVM:
        logger.debug(f"GenVM process exited with code: {exit_code}")

    # Check for abnormal exit codes
    if exit_code == -9:
        error_msg = (
            "GenVM process was killed with SIGKILL (exit code -9). This may indicate:"
        )
        error_msg += "\n  - Memory limit exceeded"
        error_msg += "\n  - Process killed by OOM killer"
        error_msg += "\n  - Timeout or resource constraint"
        if DEBUG_GENVM:
            logger.error(error_msg)
        errors.append(Exception(error_msg))
    elif exit_code != 0 and exit_code is not None:
        if DEBUG_GENVM:
            logger.warning(f"GenVM process exited with non-zero code: {exit_code}")

    if not handler.has_result():
        if (
            deadline_future is None
            or deadline_future is not None
            and deadline_future not in done
        ):
            error_msg = "no result provided"
            if DEBUG_GENVM:
                logger.error(f"Error: {error_msg}")
            errors.append(Exception(error_msg))
        else:
            if DEBUG_GENVM:
                logger.warning("Deadline exceeded - setting timeout result")
            await handler.consume_result(ResultCode.VM_ERROR, b"timeout")

    result = RunHostAndProgramRes(
        b"".join(stdout).decode(),
        b"".join(stderr).decode(),
        b"".join(genvm_log).decode(),
    )

    if len(errors) > 0:
        raise Exception(
            *errors,
            {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "genvm_log": result.genvm_log,
            },
        ) from errors[0]

    return result
