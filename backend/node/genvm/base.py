# backend/node/genvm/base.py

__all__ = ("IGenVM", "GenVMHost")

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
import logging
import os

# Configure logging
logger = logging.getLogger(__name__)
DEBUG_GENVM = True
if DEBUG_GENVM:
    logger.setLevel(logging.DEBUG)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(handler)

from backend.node.types import (
    PendingTransaction,
    Address,
)
import backend.node.genvm.origin.calldata as calldata
from dataclasses import dataclass

from backend.node.genvm.config import get_genvm_path
from .origin.result_codes import *
from .origin.host_fns import Errors


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
        account: Address,
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


# GenVM protocol just in case it is needed for mocks or bringing back the old one
class IGenVM(typing.Protocol):
    async def run_contract(
        self,
        state: StateProxy,
        *,
        from_address: Address,
        contract_address: Address,
        calldata_raw: bytes,
        is_init: bool = False,
        leader_results: None | dict[int, bytes],
        date: datetime.datetime | None,
        chain_id: int,
        host_data: typing.Any,
        readonly: bool,
        config_path: Path | None,
    ) -> ExecutionResult: ...

    async def get_contract_schema(self, contract_code: bytes) -> ExecutionResult: ...


# state proxy that always fails and can give code only for address from a constructor
# useful for get_schema
class _StateProxyNone(StateProxy):
    data: dict[bytes, bytearray]

    def __init__(self, my_address: Address):
        self.my_address = my_address
        self.data = {}

    def storage_read(
        self, account: Address, slot: bytes, index: int, le: int, /
    ) -> bytes:
        assert account == self.my_address
        res = self.data.setdefault(slot, bytearray())
        return res[index : index + le] + b"\x00" * (le - max(0, len(res) - index))

    def storage_write(
        self,
        account: Address,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        assert account == self.my_address
        res = self.data.setdefault(slot, bytearray())
        what = memoryview(got)
        res.extend(b"\x00" * (index + len(what) - len(res)))
        memoryview(res)[index : index + len(what)] = what

    def get_balance(self, addr: Address) -> int:
        return 0


# Actual genvm wrapper that will start process and handle all communication
class GenVMHost(IGenVM):
    async def run_contract(
        self,
        state: StateProxy,
        *,
        from_address: Address,
        contract_address: Address,
        calldata_raw: bytes,
        is_init: bool = False,
        readonly: bool,
        leader_results: None | dict[int, bytes],
        date: datetime.datetime | None,
        chain_id: int,
        host_data: typing.Any,
        config_path: Path | None,
    ) -> ExecutionResult:
        message = {
            "is_init": is_init,
            "contract_address": contract_address.as_b64,
            "sender_address": from_address.as_b64,
            "origin_address": from_address.as_b64,  # FIXME: no origin in simulator #751
            "value": None,
            "chain_id": str(
                chain_id
            ),  # NOTE: it can overflow u64 so better to wrap it into a string
        }
        if date is not None:
            assert date.tzinfo is not None
            message["datetime"] = date.isoformat()
        perms = "rcn"  # read/call/spawn nondet
        if not readonly:
            perms += "ws"  # write/send

        start_time = time.time()
        result = await _run_genvm_host(
            functools.partial(
                _Host,
                calldata_bytes=calldata_raw,
                state_proxy=state,
                leader_results=leader_results,
            ),
            [
                "--message",
                json.dumps(message),
                "--permissions",
                perms,
                "--host-data",
                json.dumps(host_data),
                "--allow-latest",
            ],
            config_path,
        )
        result.processing_time = int((time.time() - start_time) * 1000)
        return result

    async def get_contract_schema(self, contract_code: bytes) -> ExecutionResult:
        NO_ADDR = str(base64.b64encode(b"\x00" * 20), encoding="ascii")
        message = {
            "is_init": False,
            "contract_address": NO_ADDR,
            "sender_address": NO_ADDR,
            "origin_address": NO_ADDR,
            "value": None,
            "chain_id": "0",
        }
        state_proxy = _StateProxyNone(Address(NO_ADDR))
        genvmhost.save_code_callback(
            state_proxy.my_address.as_bytes,
            contract_code,
            lambda addr, *rest: state_proxy.storage_write(Address(addr), *rest),
        )
        # state_proxy.storage_write()
        start_time = time.time()
        result = await _run_genvm_host(
            functools.partial(
                _Host,
                calldata_bytes=calldata.encode({"method": "#get-schema"}),
                state_proxy=state_proxy,
                leader_results=None,
            ),
            ["--message", json.dumps(message), "--permissions", "", "--allow-latest"],
            None,
        )
        result.processing_time = int((time.time() - start_time) * 1000)
        return result


def _decode_genvm_log(log: str) -> list:
    decoded: list = []
    for log_line in log.splitlines():
        try:
            decoded.append(json.loads(log_line))
        except Exception:
            decoded.append(log_line)
    return decoded


# Class that has logic for handling all genvm host methods and accumulating results
class _Host(genvmhost.IHost):
    _result: ExecutionReturn | ExecutionError | None
    _eq_outputs: dict[int, bytes]
    _pending_transactions: list[PendingTransaction]

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

        self.sock_listen = sock_listen
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
            genvm_log=_decode_genvm_log(res.genvm_log),
            result=self._result,
            state=state,
            processing_time=0,
        )

    async def loop_enter(self) -> socket.socket:
        async_loop = asyncio.get_event_loop()
        if DEBUG_GENVM:
            logger.debug("Waiting for GenVM to connect to socket...")

        try:
            # Add timeout for socket accept
            accept_timeout = float(os.getenv("GENVM_ACCEPT_TIMEOUT", "10"))
            accept_coro = async_loop.sock_accept(self.sock_listen)
            self.sock, _addr = await asyncio.wait_for(
                accept_coro, timeout=accept_timeout
            )

            if DEBUG_GENVM:
                logger.debug(f"GenVM connected from {_addr}")

            self.sock.setblocking(False)
            self.sock_listen.close()
            return self.sock
        except asyncio.TimeoutError:
            error_msg = (
                f"Timeout waiting for GenVM to connect (waited {accept_timeout}s)"
            )
            if DEBUG_GENVM:
                logger.error(error_msg)
            raise TimeoutError(error_msg)
        except Exception as e:
            if DEBUG_GENVM:
                logger.error(f"Error accepting GenVM connection: {e}")
            raise

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
        account: bytes,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        return self._state_proxy.storage_write(Address(account), slot, index, got)

    async def consume_result(
        self, type: ResultCode, data: collections.abc.Buffer, /
    ) -> None:
        if DEBUG_GENVM:
            logger.debug(f"Consuming result: type={type}, data_len={len(data)}")

        if type == ResultCode.RETURN:
            self._result = ExecutionReturn(ret=bytes(data))
            if DEBUG_GENVM:
                logger.debug("Result: SUCCESS (RETURN)")
        elif type == ResultCode.USER_ERROR:
            error_msg = str(data, encoding="utf-8")
            self._result = ExecutionError(error_msg, type)
            if DEBUG_GENVM:
                logger.debug(f"Result: USER_ERROR - {error_msg}")
        elif type == ResultCode.VM_ERROR:
            error_msg = str(data, encoding="utf-8")
            self._result = ExecutionError(error_msg, type)
            if DEBUG_GENVM:
                logger.debug(f"Result: VM_ERROR - {error_msg}")
        elif type == ResultCode.INTERNAL_ERROR:
            error_msg = str(data, encoding="utf-8")
            if DEBUG_GENVM:
                logger.error(f"GenVM INTERNAL_ERROR: {error_msg}")
            raise Exception("GenVM internal error", error_msg)
        else:
            assert False, f"invalid result {type}"

    async def get_leader_nondet_result(
        self, call_no: int, /
    ) -> tuple[ResultCode, collections.abc.Buffer] | Errors:
        leader_results = self._leader_results
        if leader_results is None:
            return Errors.I_AM_LEADER
        res = leader_results.get(call_no, None)
        if res is None:
            return Errors.ABSENT
        leader_results_mem = memoryview(res)
        return (ResultCode(leader_results_mem[0]), leader_results_mem[1:])

    async def post_nondet_result(
        self, call_no: int, type: genvmhost.ResultCode, data: collections.abc.Buffer, /
    ) -> None:
        encoded_result = bytearray()
        encoded_result.append(type.value)
        encoded_result.extend(memoryview(data))
        self._eq_outputs[call_no] = bytes(encoded_result)

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
    last_error: str, state_proxy: StateProxy, processing_time: int
) -> ExecutionResult:
    return ExecutionResult(
        result=ExecutionError(message="timeout", kind=ResultCode.VM_ERROR),
        eq_outputs={},
        pending_transactions=[],
        stdout="",
        stderr=last_error,
        genvm_log=[],
        state=state_proxy,
        processing_time=processing_time,
    )


async def _run_genvm_host(
    host_supplier: typing.Callable[[socket.socket], _Host],
    args: list[Path | str],
    config_path: Path | None,
) -> ExecutionResult:
    tmpdir = Path(tempfile.mkdtemp())
    if DEBUG_GENVM:
        logger.debug(f"Created temp directory: {tmpdir}")
        logger.debug(f"Config path: {config_path}")
        logger.debug(f"Args: {args}")

    try:
        # Reduce timeout for faster debugging
        timeout = int(
            os.getenv("GENVM_TIMEOUT", "600")
        )  # Default 60 seconds instead of 600
        base_delay = 5  # seconds
        start_time = time.time()
        retry_count = 0
        last_error = ""

        if DEBUG_GENVM:
            logger.debug(f"Starting GenVM execution with timeout={timeout}s")

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
                if DEBUG_GENVM:
                    logger.error(
                        f"Timeout after {timeout}s and {retry_count} retries. Last error: {last_error}"
                    )
                return _create_timeout_result(
                    last_error,
                    fresh_args.get("state_proxy", host_args.get("state_proxy")),
                    timeout * 1000,
                )

            if DEBUG_GENVM and retry_count > 0:
                logger.warning(
                    f"Retry attempt #{retry_count} (remaining time: {remaining_time:.1f}s)"
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

                # Check if GenVM binary exists
                genvm_path = get_genvm_path()
                if DEBUG_GENVM:
                    logger.debug(f"GenVM binary path: {genvm_path}")
                    if not genvm_path.exists():
                        logger.error(f"GenVM binary not found at: {genvm_path}")

                new_args: list[str | Path] = [
                    genvm_path,
                ]
                if config_path is not None:
                    new_args.extend(["--config", config_path])
                new_args.extend(
                    [
                        "run",
                        "--host",
                        f"unix://{sock_path}",
                        "--print=none",
                    ]
                )

                new_args.extend(args)

                if DEBUG_GENVM:
                    logger.debug(
                        f"Full GenVM command: {' '.join(str(x) for x in new_args)}"
                    )

                fresh_host_supplier = functools.partial(
                    (
                        host_supplier.func
                        if isinstance(host_supplier, functools.partial)
                        else host_supplier
                    ),
                    **fresh_args,
                )
                host: _Host = fresh_host_supplier(sock_listener)

                try:
                    if DEBUG_GENVM:
                        logger.debug(
                            f"Running GenVM with deadline={remaining_time:.1f}s"
                        )

                    run_result = await genvmhost.run_host_and_program(
                        host, new_args, deadline=remaining_time
                    )

                    if DEBUG_GENVM:
                        logger.debug("GenVM execution completed successfully")
                        if run_result.stderr:
                            logger.debug(f"GenVM stderr: {run_result.stderr[:500]}")

                    result = host.provide_result(
                        run_result,
                        state=fresh_args.get(
                            "state_proxy", host_args.get("state_proxy")
                        ),
                    )
                    return result

                except Exception as e:
                    last_error = str(e)

                    if DEBUG_GENVM:
                        logger.error(f"GenVM execution failed: {last_error}")
                        if hasattr(e, "__cause__") and e.__cause__:
                            logger.error(f"Caused by: {e.__cause__}")

                    # Check if llm failed, immediately return timeout error
                    if "fatal: true" in last_error:
                        if DEBUG_GENVM:
                            logger.error(
                                "Fatal LLM error detected - exiting immediately"
                            )
                        return _create_timeout_result(
                            last_error,
                            fresh_args.get("state_proxy", host_args.get("state_proxy")),
                            int((time.time() - start_time) * 1000),
                        )

                    # Check for specific errors that shouldn't be retried
                    if "Can't find executable genvm" in last_error:
                        if DEBUG_GENVM:
                            logger.error(
                                "GenVM binary not found - exiting without retry"
                            )
                        return _create_timeout_result(
                            f"GenVM binary not found: {last_error}",
                            fresh_args.get("state_proxy", host_args.get("state_proxy")),
                            int((time.time() - start_time) * 1000),
                        )

                    retry_count += 1
                    # Sleep for a longer time than the previous attempt to avoid executing it too many times
                    delay = min(base_delay * (2 ** (retry_count - 1)), remaining_time)
                    if DEBUG_GENVM:
                        logger.info(f"Waiting {delay:.1f}s before retry #{retry_count}")
                    await asyncio.sleep(delay)

                finally:
                    if host.sock is not None:
                        host.sock.close()
                    try:
                        sock_path.unlink()
                    except FileNotFoundError:
                        pass
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
