from contextlib import redirect_stdout
from dataclasses import asdict
import datetime
import functools
import json
import base64
from pathlib import Path
import time
import yaml
import sys
import asyncio
from typing import Callable, Optional
import typing
import collections.abc
import os
import logging

from backend.domain.types import Validator, Transaction, TransactionType
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
import backend.node.genvm.base as genvmbase
import backend.node.genvm.origin.calldata as calldata
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.types import Receipt, ExecutionMode, Vote, ExecutionResultStatus
from backend.protocol_rpc.message_handler.base import IMessageHandler
from .genvm.origin import base_host
from .genvm.origin import logger as genvm_logger
from .genvm.origin import public_abi

from .types import Address


def _ensure_dotenv_loaded_for_chain_id() -> None:
    if os.getenv("HARDHAT_CHAIN_ID") is not None:
        return

    try:
        from dotenv import load_dotenv
    except Exception:
        return

    dotenv_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False)


# region agent log
def _agent_log(hypothesis_id: str, location: str, message: str, data: dict):
    """Best-effort NDJSON log for debug mode; never raises. Avoid secrets."""
    import json as _json
    import os as _os
    import time as _time

    payload = {
        "sessionId": "debug-session",
        "runId": _os.getenv("AGENT_DEBUG_RUN_ID", "pre-fix"),
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(_time.time() * 1000),
    }
    try:
        log_path = _os.getenv("AGENT_DEBUG_LOG_PATH", "/tmp/agent_debug.log")
        with open(log_path, "a") as f:
            f.write(_json.dumps(payload) + "\n")
    except Exception:
        try:
            print("AGENT_DEBUG " + _json.dumps(payload), flush=True)
        except Exception:
            pass


# endregion


def _parse_chain_id() -> int:
    _ensure_dotenv_loaded_for_chain_id()
    raw = os.getenv("HARDHAT_CHAIN_ID", "61127")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"HARDHAT_CHAIN_ID must be decimal digits, got '{raw}'"
        ) from exc


@functools.lru_cache(maxsize=1)
def get_simulator_chain_id() -> int:
    return _parse_chain_id()


def _extract_llm_usage_from_genvm_log(genvm_log: list[dict]) -> list[dict]:
    """
    Extract LLM usage entries from genvm_log.

    Args:
        genvm_log: List of log entries from GenVM execution

    Returns:
        List of usage entries with provider, model, input_tokens, output_tokens
    """
    usage_entries = []
    for entry in genvm_log:
        if not isinstance(entry, dict):
            continue

        # Check for LLM usage log entries
        if entry.get("llm_usage_type") == "llm_usage":
            usage_entries.append(
                {
                    "provider": entry.get("provider", "unknown"),
                    "model": entry.get("model", "unknown"),
                    "input_tokens": entry.get("input_tokens", 0),
                    "output_tokens": entry.get("output_tokens", 0),
                }
            )

    return usage_entries


def _filter_genvm_log_by_level(genvm_log: list[dict]) -> list[dict]:
    """
    Filter genvm_log entries based on configured LOG_LEVEL with a minimum of WARNING.
    Only includes log entries that meet or exceed the effective threshold.
    """
    # Get configured log level from environment
    configured_level = os.getenv("LOG_LEVEL", "INFO").upper()

    # Map string levels to numeric values (matching Python's logging module)
    level_map = {
        "DEBUG": logging.DEBUG,  # 10
        "INFO": logging.INFO,  # 20
        "WARNING": logging.WARNING,  # 30
        "WARN": logging.WARNING,  # 30 (alias)
        "ERROR": logging.ERROR,  # 40
        "CRITICAL": logging.CRITICAL,  # 50
    }

    # Get numeric threshold for configured level (default to INFO if unknown)
    # Enforce minimum WARNING level regardless of configuration
    threshold = max(level_map.get(configured_level, logging.INFO), logging.WARNING)

    # Filter log entries
    filtered_logs = []
    for log_entry in genvm_log:
        if not isinstance(log_entry, dict):
            # Keep non-dict entries as-is
            filtered_logs.append(log_entry)
            continue

        entry_level = log_entry.get("level", "info").upper()
        entry_numeric_level = level_map.get(entry_level, logging.INFO)

        # Include if entry level >= threshold
        if entry_numeric_level >= threshold:
            filtered_logs.append(log_entry)

    return filtered_logs


def _repr_result_with_capped_data(
    result: genvmbase.ExecutionReturn | genvmbase.ExecutionError, cap: int = 1000
) -> str:
    """
    Return a JSON string representation of result with the 'data' field capped.
    Falls back to the default repr if parsing fails or no 'data' field exists.
    """
    try:
        as_str = f"{result!r}"
        parsed = json.loads(as_str)
        if isinstance(parsed, dict):
            data_value = parsed.get("data")
            if isinstance(data_value, str) and len(data_value) > cap:
                parsed["data"] = data_value[:cap]
                return json.dumps(parsed)
        return as_str
    except Exception:
        return f"{result!r}"


class _SnapshotView(genvmbase.StateProxy):
    def __init__(
        self,
        snapshot: ContractSnapshot,
        snapshot_factory: typing.Callable[[str], ContractSnapshot],
        readonly: bool,
        state_status: str | None = None,
    ):
        self.contract_address = Address(snapshot.contract_address)
        self.snapshot = snapshot
        self.snapshot_factory = snapshot_factory
        self.cached = {}
        self.readonly = readonly
        self.state_status = state_status if state_status else "accepted"

    def _get_snapshot(self, addr: Address) -> ContractSnapshot:
        if addr == self.contract_address:
            return self.snapshot
        res = self.cached.get(addr)
        if res is not None:
            return res
        res = self.snapshot_factory(addr.as_hex)
        self.cached[addr] = res
        return res

    def storage_read(
        self, account: Address, slot: bytes, index: int, le: int, /
    ) -> bytes:
        snap = self._get_snapshot(account)
        slot_id = base64.b64encode(slot).decode("ascii")
        for_slot = snap.states[self.state_status].setdefault(slot_id, "")
        data = bytearray(base64.b64decode(for_slot))
        data.extend(b"\x00" * (index + le - len(data)))
        return data[index : index + le]

    def storage_write(
        self,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        assert not self.readonly
        snap = self._get_snapshot(self.contract_address)
        slot_id = base64.b64encode(slot).decode("ascii")
        for_slot = snap.states[self.state_status].setdefault(slot_id, "")
        data = bytearray(base64.b64decode(for_slot))
        mem = memoryview(got)
        data.extend(b"\x00" * (index + len(mem) - len(data)))
        data[index : index + len(mem)] = mem
        snap.states[self.state_status][slot_id] = base64.b64encode(data).decode("utf-8")

    def get_balance(self, addr: Address) -> int:
        snap = self._get_snapshot(addr)
        # FIXME(core-team): it is not obvious where `value` is added to `self.balance`
        # but return must be increased by it
        return snap.balance


import aiohttp

from .genvm.origin.logger import Logger

from loguru import logger as loguru_logger

Logger.register(type(loguru_logger))


class LLMConfig(typing.TypedDict):
    host: str
    provider: str
    models: dict[str, typing.Any]
    key: str
    enabled: bool


class LLMTestPrompt(typing.TypedDict):
    system_message: str
    user_message: str
    temperature: float
    max_tokens: int
    use_max_completion_tokens: typing.NotRequired[bool]
    images: typing.NotRequired[list]


_MODULE_MAP = {
    "llm": "Llm",
    "web": "Web",
}


class Manager:
    url: str
    llm_config_base: dict[str, typing.Any]
    web_config_base: dict[str, typing.Any]
    logger: Logger
    proc: asyncio.subprocess.Process | None

    async def close(self):
        if self.proc is not None:
            import signal

            self.proc.send_signal(signal.SIGINT)
            await asyncio.wait(
                [
                    asyncio.ensure_future(self.proc.wait()),
                    asyncio.ensure_future(asyncio.sleep(1)),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            if self.proc.returncode is not None:
                self.proc.kill()
                await self.proc.wait()
            self.proc = None

    @staticmethod
    async def create() -> "Manager":
        genvm_root = Path(os.environ["GENVMROOT"])
        _agent_log(
            "H1",
            "backend/node/base.py:Manager.create",
            "creating genvm manager",
            {
                "GENVMROOT": os.getenv("GENVMROOT"),
                "GENVM_TAG": os.getenv("GENVM_TAG"),
                "genvm_root_exists": genvm_root.exists(),
                "genvm_modules_exists": genvm_root.joinpath(
                    "bin", "genvm-modules"
                ).exists(),
                "executor_dir_exists": genvm_root.joinpath(
                    "executor", os.getenv("GENVM_TAG", "")
                ).exists(),
            },
        )

        url = "http://127.0.0.1:3999"

        man = Manager()
        # man.logger = loguru_logger
        man.logger = genvm_logger.StderrLogger(min_level="info")
        man.url = url
        man.llm_config_base = yaml.safe_load(
            genvm_root.joinpath("config", "genvm-module-llm.yaml").read_text()
        )
        man.web_config_base = yaml.safe_load(
            genvm_root.joinpath("config", "genvm-module-web.yaml").read_text()
        )

        debug_enabled = os.getenv("GENVM_WEB_DEBUG") == "1"
        stream_target = sys.stdout if debug_enabled else asyncio.subprocess.DEVNULL

        exe = genvm_root.joinpath("bin", "genvm-modules")
        man.proc = await asyncio.subprocess.create_subprocess_exec(
            exe,
            "manager",
            "--port",
            "3999",
            "--die-with-parent",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=stream_target,
            stderr=stream_target,
        )

        return man

    async def stop_module(self, module_type: typing.Literal["llm", "web"]):

        data = {"module_type": _MODULE_MAP[module_type]}
        async with aiohttp.request(
            "POST", f"{self.url}/module/stop", json=data
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                self.logger.error(
                    f"Failed to stop LLM module", body=body, status=resp.status
                )
            else:
                self.logger.info(f"Stopped LLM module", body=body, status=resp.status)

    async def start_module(
        self,
        module_type: typing.Literal["llm", "web"],
        config: dict[str, typing.Any] | None,
        extra: dict = {},
    ):
        data = {"module_type": _MODULE_MAP[module_type], "config": config, **extra}
        async with aiohttp.request(
            "POST", f"{self.url}/module/start", json=data
        ) as resp:
            body = await resp.json()
            if resp.status != 200:
                self.logger.error(
                    f"Failed to start module",
                    module=module_type,
                    body=body,
                    status=resp.status,
                )
                raise RuntimeError("Failed to start module")

    async def try_llms(
        self, configs: list[LLMConfig], *, prompt: LLMTestPrompt | None
    ) -> list[dict]:
        """
        Executes test prompt against all LLM configs
        """
        if prompt is None:
            prompt = {
                "system_message": "",
                "user_message": "Respond with two letters 'OK' and nothing else",
                "temperature": 0.7,
                "max_tokens": 300,
            }

        data = {
            "configs": configs,
            "test_prompts": [prompt],
        }
        async with aiohttp.request("POST", f"{self.url}/llm/check", json=data) as resp:
            body = await resp.json()
            if resp.status != 200:
                self.logger.error(
                    f"Failed to check llms", body=body, status=resp.status
                )

        body = typing.cast(list[dict], body)
        body.sort(key=lambda x: x["config_index"])

        self.logger.debug("check executed", configs=configs, prompt=prompt, body=body)

        return body


class _StateProxyNone(genvmbase.StateProxy):
    """
    state proxy that always fails and can give code only for address from a constructor
    useful for get_schema
    """

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
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        res = self.data.setdefault(slot, bytearray())
        what = memoryview(got)
        res.extend(b"\x00" * (index + len(what) - len(res)))
        memoryview(res)[index : index + len(what)] = what

    def get_balance(self, addr: Address) -> int:
        return 0


import backend.validators as validators


class Node:
    def __init__(
        self,
        contract_snapshot: ContractSnapshot | None,
        validator_mode: ExecutionMode,
        validator: Validator,
        contract_snapshot_factory: Callable[[str], ContractSnapshot] | None,
        leader_receipt: Optional[Receipt] = None,
        validators_snapshot: validators.Snapshot | None = None,
        timing_callback: Optional[Callable[[str], None]] = None,
        msg_handler: IMessageHandler | None = None,
        *,
        manager: Manager,
        logger: genvm_logger.Logger | None = None,
    ):
        assert manager is not None

        self.contract_snapshot = contract_snapshot
        self.validator_mode = validator_mode
        self.validator = validator
        self.address = validator.address
        self.leader_receipt = leader_receipt
        self.msg_handler = msg_handler
        self.contract_snapshot_factory = contract_snapshot_factory
        self.manager = manager
        self.validators_snapshot = validators_snapshot
        if timing_callback is None:

            def _timing_callback(x: str) -> None:
                pass

            timing_callback = _timing_callback
        self.timing_callback = timing_callback

        if logger is None:
            logger = genvm_logger.StderrLogger()
        self.logger = logger.with_keys({"node_address": self.address})

    async def exec_transaction(self, transaction: Transaction) -> Receipt:
        self.timing_callback("EXEC_START")

        assert transaction.data is not None
        transaction_data = transaction.data
        assert transaction.from_address is not None

        # Override transaction timestamp
        sim_config = transaction.sim_config
        transaction_created_at = transaction.created_at
        if sim_config is not None and sim_config.genvm_datetime is not None:
            transaction_created_at = sim_config.genvm_datetime

        if transaction.type == TransactionType.DEPLOY_CONTRACT:
            self.timing_callback("DEPLOY_START")

            code = base64.b64decode(transaction_data["contract_code"])
            calldata = base64.b64decode(transaction_data["calldata"])

            self.timing_callback("DECODE_COMPLETE")

            receipt = await self.deploy_contract(
                transaction.from_address,
                code,
                calldata,
                transaction.hash,
                transaction_created_at,
            )

            self.timing_callback("DEPLOY_END")
        elif transaction.type == TransactionType.RUN_CONTRACT:
            self.timing_callback("RUN_START")

            calldata = base64.b64decode(transaction_data["calldata"])

            self.timing_callback("DECODE_COMPLETE")

            receipt = await self.run_contract(
                transaction.from_address,
                calldata,
                transaction.hash,
                transaction_created_at,
            )

            self.timing_callback("RUN_END")
        else:
            raise Exception(f"unknown transaction type {transaction.type}")

        self.timing_callback("RECEIPT_CREATED")

        return receipt

    def _create_enhanced_node_config(self, host_data: dict | None) -> dict:
        """
        Create enhanced node_config that includes both primary and fallback provider info.

        Args:
            host_data: The host_data dict containing primary and fallback provider IDs

        Returns:
            Enhanced node_config dict with fallback information
        """
        node_config = self.validator.to_dict()
        enhanced_node_config = {
            "address": node_config["address"],
            "private_key": node_config["private_key"],
            "stake": node_config["stake"],
            "primary_model": {
                k: v
                for k, v in node_config.items()
                if k not in ["address", "private_key", "stake"]
            },
            "secondary_model": None,
        }

        if host_data is None:
            return enhanced_node_config

        fallback_llm_id = host_data.get("fallback_llm_id")
        if fallback_llm_id and self.validators_snapshot:
            fallback_validator = None
            for node in self.validators_snapshot.nodes:
                if f"node-{node.validator.address}" == fallback_llm_id:
                    fallback_validator = node.validator
                    break

            if fallback_validator:
                enhanced_node_config["secondary_model"] = {
                    "provider": fallback_validator.llmprovider.provider,
                    "model": fallback_validator.llmprovider.model,
                    "plugin": fallback_validator.llmprovider.plugin,
                    "plugin_config": fallback_validator.llmprovider.plugin_config,
                    "config": fallback_validator.llmprovider.config,
                }

        return enhanced_node_config

    def _set_vote(self, receipt: Receipt) -> Receipt:
        if (receipt.result[0] == public_abi.ResultCode.VM_ERROR) and (
            receipt.result[1:] == b"timeout"
        ):
            receipt.vote = Vote.TIMEOUT
            return receipt

        leader_receipt = self.leader_receipt
        if (
            leader_receipt.execution_result == receipt.execution_result
            and leader_receipt.result == receipt.result
            and leader_receipt.contract_state == receipt.contract_state
            and leader_receipt.pending_transactions == receipt.pending_transactions
        ):
            if receipt.nondet_disagree is not None:
                receipt.vote = Vote.DISAGREE
            else:
                receipt.vote = Vote.AGREE
        else:
            receipt.vote = Vote.DETERMINISTIC_VIOLATION

        return receipt

    def _date_from_str(
        self, date: str | datetime.datetime | None
    ) -> datetime.datetime | None:
        if date is None:
            return None
        # If already a datetime, ensure it's timezone-aware
        if isinstance(date, datetime.datetime):
            if date.tzinfo is None:
                return date.replace(tzinfo=datetime.UTC)
            return date
        # Otherwise, parse from string; accept ISO-8601 with trailing 'Z'
        date_str = date.replace("Z", "+00:00")
        res = datetime.datetime.fromisoformat(date_str)
        if res.tzinfo is None:
            res = res.replace(tzinfo=datetime.UTC)
        return res

    async def _put_code_to(
        self, to: genvmbase.StateProxy, code: bytes, timestamp: datetime.datetime
    ) -> None:
        from .genvm.origin import base_host

        writes = await base_host.get_pre_deployment_writes(
            code,
            timestamp,
            self.manager.url,
        )
        for slot, off, data in writes:
            to.storage_write(slot, off, data)

    async def deploy_contract(
        self,
        from_address: str,
        code_to_deploy: bytes,
        calldata: bytes,
        transaction_hash: str | None = None,
        transaction_created_at: str | None = None,
    ) -> Receipt:
        assert self.contract_snapshot is not None

        transaction_datetime = self._date_from_str(transaction_created_at)
        if transaction_datetime is None:
            transaction_datetime = datetime.datetime.now()

        def no_factory(*args, **kwargs):
            raise Exception("factory is forbidden for code deployment")

        snapshot_view_for_code = _SnapshotView(
            self.contract_snapshot,
            no_factory,
            False,
            None,
        )

        await self._put_code_to(
            snapshot_view_for_code, code_to_deploy, transaction_datetime
        )

        return await self._run_genvm(
            from_address,
            calldata,
            readonly=False,
            is_init=True,
            transaction_hash=transaction_hash,
            transaction_datetime=transaction_datetime,
        )

    async def run_contract(
        self,
        from_address: str,
        calldata: bytes,
        transaction_hash: str | None = None,
        transaction_created_at: str | None = None,
    ) -> Receipt:
        return await self._run_genvm(
            from_address,
            calldata,
            readonly=False,
            is_init=False,
            transaction_hash=transaction_hash,
            transaction_datetime=self._date_from_str(transaction_created_at),
        )

    async def get_contract_data(
        self,
        from_address: str,
        calldata: bytes,
        state_status: str | None = None,
        transaction_datetime: datetime.datetime | None = None,
    ) -> Receipt:
        return await self._run_genvm(
            from_address,
            calldata,
            readonly=True,
            is_init=False,
            transaction_datetime=(
                transaction_datetime
                if transaction_datetime is not None
                else datetime.datetime.now().astimezone(datetime.UTC)
            ),
            state_status=state_status,
        )

    async def _execution_finished(
        self,
        res: genvmbase.ExecutionResult,
        transaction_hash_str: str | None,
        from_address: str | None,
    ):
        msg_handler = self.msg_handler
        if msg_handler is None:
            return
        is_error = isinstance(res.result, genvmbase.ExecutionError)

        # Filter genvm_log based on configured log level
        filtered_genvm_log = _filter_genvm_log_by_level(res.genvm_log)
        capped_stdout = (
            res.stdout[:500] + res.stdout[-500:]
            if len(res.stdout) > 1000
            else res.stdout
        )

        msg_handler.send_message(
            LogEvent(
                name="execution_finished",
                type=(EventType.INFO if not is_error else EventType.ERROR),
                scope=EventScope.GENVM,
                message="execution finished",
                data={
                    "result": _repr_result_with_capped_data(res.result),
                    "stdout": res.stdout if is_error else capped_stdout,
                    "stderr": res.stderr,
                    "genvm_log": filtered_genvm_log,
                },
                transaction_hash=transaction_hash_str,
                account_address=from_address,
                client_session_id=getattr(msg_handler, "client_session_id", None),
            )
        )

    async def get_contract_schema(self, code: bytes) -> str:
        storage = _StateProxyNone(Address(b"\x00" * 20))

        await self._put_code_to(storage, code, datetime.datetime.now())

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
        writes = await base_host.get_pre_deployment_writes(
            code, datetime.datetime.now(), self.manager.url
        )
        for slot, off, data in writes:
            state_proxy.storage_write(slot, off, data)

        start_time = time.time()
        result = await genvmbase.run_genvm_host(
            functools.partial(
                genvmbase.Host,
                calldata_bytes=calldata.encode(
                    {"method": public_abi.SpecialMethod.GET_SCHEMA.value}
                ),
                state_proxy=state_proxy,
                leader_results=None,
            ),
            message=message,
            permissions="rw",
            extra_args=["--debug-mode"],
            host_data='{"node_address":"0x", "tx_id":"0x"}',
            capture_output=True,
            is_sync=False,
            logger=self.logger,
            timeout=30,
            manager_uri=self.manager.url,
        )
        result.processing_time = int((time.time() - start_time) * 1000)

        await self._execution_finished(result, None, None)

        filtered_genvm_log = _filter_genvm_log_by_level(result.genvm_log)

        err_data = {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "genvm_log": filtered_genvm_log,
            "result": _repr_result_with_capped_data(result.result),
        }
        if not isinstance(result.result, genvmbase.ExecutionReturn):
            raise Exception("execution failed", err_data)
        ret_calldata = result.result.ret
        try:
            schema = calldata.decode(ret_calldata)
        except Exception as e:
            raise Exception(f"abi violation, can't parse calldata #{e}", err_data)
        if not isinstance(schema, str):
            raise Exception(
                f"abi violation, invalid return type #{type(schema)}", err_data
            )
        return schema

    async def _run_genvm(
        self,
        from_address: str,
        calldata: bytes,
        *,
        readonly: bool,
        is_init: bool,
        transaction_hash: str | None = None,
        transaction_datetime: datetime.datetime | None,
        state_status: str | None = None,
        timeout: float = 10 * 60,
    ) -> Receipt:
        self.timing_callback("GENVM_PREPARATION_START")

        leader_res: None | dict[int, bytes]
        if self.leader_receipt is None:
            leader_res = None
        else:
            leader_res = {
                k: base64.b64decode(v)
                for k, v in self.leader_receipt.eq_outputs.items()
            }
        assert self.contract_snapshot is not None
        assert self.contract_snapshot_factory is not None

        self.timing_callback("SNAPSHOT_CREATION_START")

        snapshot_view = _SnapshotView(
            self.contract_snapshot,
            self.contract_snapshot_factory,
            readonly,
            state_status,
        )
        try:
            from backend.node.genvm import get_code_slot

            code_slot_b64 = base64.b64encode(get_code_slot()).decode("ascii")
            accepted_state = snapshot_view.snapshot.states.get("accepted") or {}
            code_entry = accepted_state.get(code_slot_b64)
            code_bytes_len = (
                len(base64.b64decode(code_entry))
                if isinstance(code_entry, str)
                else None
            )
            _agent_log(
                "H2",
                "backend/node/base.py:Node._run_genvm",
                "pre-exec snapshot code slot check",
                {
                    "is_init": bool(is_init),
                    "readonly": bool(readonly),
                    "state_status": state_status,
                    "contract_address": str(self.contract_snapshot.contract_address),
                    "has_code_slot": code_entry is not None,
                    "code_bytes_len": code_bytes_len,
                    "calldata_len": len(calldata) if calldata is not None else None,
                },
            )
        except Exception as _e:
            _agent_log(
                "H2",
                "backend/node/base.py:Node._run_genvm",
                "pre-exec snapshot code slot check failed",
                {"error": str(_e)},
            )

        self.timing_callback("SNAPSHOT_CREATION_END")

        host_data = None
        if self.validators_snapshot is not None:
            for n in self.validators_snapshot.nodes:
                if n.validator.address == self.validator.address:
                    host_data = n.genvm_host_data

        self.timing_callback("GENVM_EXECUTION_START")

        result_exec_code: ExecutionResultStatus

        if host_data is None:
            host_data = {}

        contract_address = Address(self.contract_snapshot.contract_address)

        if "tx_id" not in host_data:
            host_data["tx_id"] = "0x"
        if "node_address" not in host_data:
            host_data["node_address"] = self.address

        logger = self.logger.with_keys({"tx_id": host_data["tx_id"]})

        message = {
            "is_init": is_init,
            "contract_address": contract_address.as_b64,
            "sender_address": Address(from_address).as_b64,
            "origin_address": Address(
                from_address
            ).as_b64,  # FIXME: no origin in simulator #751
            "value": None,
            "chain_id": str(
                get_simulator_chain_id()
            ),  # NOTE: it can overflow u64 so better to wrap it into a string
        }
        if transaction_datetime is not None:
            assert transaction_datetime.tzinfo is not None
            message["datetime"] = transaction_datetime.isoformat()
        perms = "rcn"  # read/call/spawn nondet
        if not readonly:
            perms += "ws"  # write/send

        start_time = time.time()
        result = await genvmbase.run_genvm_host(
            functools.partial(
                genvmbase.Host,
                calldata_bytes=calldata,
                state_proxy=snapshot_view,
                leader_results=leader_res,
            ),
            message=message,
            permissions=perms,
            capture_output=True,
            host_data=json.dumps(host_data),
            extra_args=["--debug-mode"],
            is_sync=False,
            manager_uri=self.manager.url,
            timeout=timeout,
        )
        result.processing_time = int((time.time() - start_time) * 1000)

        await self._execution_finished(result, transaction_hash, from_address)

        self.timing_callback("EXECUTION_FINISHED")

        result_exec_code = (
            ExecutionResultStatus.SUCCESS
            if isinstance(result.result, genvmbase.ExecutionReturn)
            else ExecutionResultStatus.ERROR
        )

        # Extract LLM usage from genvm_log before creating Receipt
        llm_usage = _extract_llm_usage_from_genvm_log(result.genvm_log)

        result = Receipt(
            result=genvmbase.encode_result_to_bytes(result.result),
            gas_used=0,
            eq_outputs={
                k: base64.b64encode(v).decode("ascii")
                for k, v in result.eq_outputs.items()
            },
            pending_transactions=result.pending_transactions,
            vote=None,
            execution_result=result_exec_code,
            contract_state=typing.cast(_SnapshotView, result.state).snapshot.states[
                "accepted"
            ],
            calldata=calldata,
            mode=self.validator_mode,
            node_config=self._create_enhanced_node_config(host_data),
            genvm_result={
                "stdout": result.stdout[:5000],
                "stderr": result.stderr,
            },
            processing_time=result.processing_time,
            nondet_disagree=result.nondet_disagree,
            llm_usage=llm_usage if llm_usage else None,
        )

        if self.validator_mode == ExecutionMode.LEADER:
            return result
        return self._set_vote(result)
