from contextlib import redirect_stdout
from dataclasses import asdict
import datetime
import json
import base64
from typing import Callable, Optional
import typing
import collections.abc
import os
import logging

from backend.domain.types import Validator, Transaction, TransactionType
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
import backend.node.genvm.base as genvmbase
import backend.validators as validators
import backend.node.genvm.origin.calldata as calldata
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.types import Receipt, ExecutionMode, Vote, ExecutionResultStatus
from backend.protocol_rpc.message_handler.base import MessageHandler
from backend.node.genvm.origin.result_codes import ResultCode

from .types import Address


def _parse_chain_id() -> int:
    raw = os.getenv("HARDHAT_CHAIN_ID", "61999")
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(
            f"HARDHAT_CHAIN_ID must be decimal digits, got '{raw}'"
        ) from exc


SIMULATOR_CHAIN_ID: typing.Final[int] = _parse_chain_id()


def _filter_genvm_log_by_level(genvm_log: list[dict]) -> list[dict]:
    """
    Filter genvm_log entries based on configured LOG_LEVEL.
    Only includes log entries that meet or exceed the configured log level.
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
    threshold = level_map.get(configured_level, logging.INFO)

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


class Node:
    def __init__(
        self,
        contract_snapshot: ContractSnapshot | None,
        validator_mode: ExecutionMode,
        validator: Validator,
        contract_snapshot_factory: Callable[[str], ContractSnapshot] | None,
        leader_receipt: Optional[Receipt] = None,
        msg_handler: MessageHandler | None = None,
        validators_snapshot: validators.Snapshot | None = None,
    ):
        self.contract_snapshot = contract_snapshot
        self.validator_mode = validator_mode
        self.validator = validator
        self.address = validator.address
        self.leader_receipt = leader_receipt
        self.msg_handler = msg_handler
        self.contract_snapshot_factory = contract_snapshot_factory
        self.validators_snapshot = validators_snapshot

    def _create_genvm(self) -> genvmbase.IGenVM:
        return genvmbase.GenVMHost()

    async def exec_transaction(self, transaction: Transaction) -> Receipt:
        assert transaction.data is not None
        transaction_data = transaction.data
        assert transaction.from_address is not None

        # Override transaction timestamp
        sim_config = transaction.sim_config
        transaction_created_at = transaction.created_at
        if sim_config is not None and sim_config.genvm_datetime is not None:
            transaction_created_at = sim_config.genvm_datetime

        if transaction.type == TransactionType.DEPLOY_CONTRACT:
            code = base64.b64decode(transaction_data["contract_code"])
            calldata = base64.b64decode(transaction_data["calldata"])
            receipt = await self.deploy_contract(
                transaction.from_address,
                code,
                calldata,
                transaction.hash,
                transaction_created_at,
            )
        elif transaction.type == TransactionType.RUN_CONTRACT:
            calldata = base64.b64decode(transaction_data["calldata"])
            receipt = await self.run_contract(
                transaction.from_address,
                calldata,
                transaction.hash,
                transaction_created_at,
            )
        else:
            raise Exception(f"unknown transaction type {transaction.type}")
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
        if (receipt.result[0] == ResultCode.VM_ERROR) and (
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

    def _date_from_str(self, date: str | None) -> datetime.datetime | None:
        if date is None:
            return None
        # Accept ISO‐8601 strings with a trailing ‘Z’ by normalizing to +00:00
        date_str = date.replace("Z", "+00:00")
        res = datetime.datetime.fromisoformat(date_str)
        if res.tzinfo is None:
            res = res.replace(tzinfo=datetime.UTC)
        return res

    async def deploy_contract(
        self,
        from_address: str,
        code_to_deploy: bytes,
        calldata: bytes,
        transaction_hash: str | None = None,
        transaction_created_at: str | None = None,
    ) -> Receipt:
        assert self.contract_snapshot is not None

        from .genvm.origin import base_host

        def no_factory(*args, **kwargs):
            raise Exception("factory is forbidden for code deployment")

        snapshot_view_for_code = _SnapshotView(
            self.contract_snapshot,
            no_factory,
            False,
            None,
        )

        base_host.save_code_callback(
            code_to_deploy, snapshot_view_for_code.storage_write
        )

        return await self._run_genvm(
            from_address,
            calldata,
            readonly=False,
            is_init=True,
            transaction_hash=transaction_hash,
            transaction_datetime=self._date_from_str(transaction_created_at),
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
        self, res: genvmbase.ExecutionResult, transaction_hash_str: str | None
    ):
        msg_handler = self.msg_handler
        if msg_handler is None:
            return
        is_error = isinstance(res.result, genvmbase.ExecutionError)

        # Filter genvm_log based on configured log level
        filtered_genvm_log = _filter_genvm_log_by_level(res.genvm_log)

        msg_handler.send_message(
            LogEvent(
                name="execution_finished",
                type=(EventType.INFO if not is_error else EventType.ERROR),
                scope=EventScope.GENVM,
                message="execution finished",
                data={
                    "result": f"{res.result!r}",
                    "stdout": res.stdout if is_error else res.stdout[:500],
                    "stderr": res.stderr,
                    "genvm_log": filtered_genvm_log,
                },
                transaction_hash=transaction_hash_str,
            )
        )

    async def get_contract_schema(self, code: bytes) -> str:
        genvm = self._create_genvm()
        res = await genvm.get_contract_schema(code)
        await self._execution_finished(res, None)

        # Filter genvm_log based on configured log level
        filtered_genvm_log = _filter_genvm_log_by_level(res.genvm_log)

        err_data = {
            "stdout": res.stdout,
            "stderr": res.stderr,
            "genvm_log": filtered_genvm_log,
            "result": f"{res.result!r}",
        }
        if not isinstance(res.result, genvmbase.ExecutionReturn):
            raise Exception("execution failed", err_data)
        ret_calldata = res.result.ret
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
    ) -> Receipt:
        genvm = self._create_genvm()
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
        snapshot_view = _SnapshotView(
            self.contract_snapshot,
            self.contract_snapshot_factory,
            readonly,
            state_status,
        )

        config_path = None
        host_data = None
        if self.validators_snapshot is not None:
            config_path = self.validators_snapshot.genvm_config_path
            for n in self.validators_snapshot.nodes:
                if n.validator.address == self.validator.address:
                    host_data = n.genvm_host_data
        result_exec_code: ExecutionResultStatus
        res = await genvm.run_contract(
            snapshot_view,
            contract_address=Address(self.contract_snapshot.contract_address),
            from_address=Address(from_address),
            calldata_raw=calldata,
            is_init=is_init,
            readonly=readonly,
            leader_results=leader_res,
            date=transaction_datetime,
            chain_id=SIMULATOR_CHAIN_ID,
            config_path=config_path,
            host_data=host_data,
        )

        await self._execution_finished(res, transaction_hash)

        result_exec_code = (
            ExecutionResultStatus.SUCCESS
            if isinstance(res.result, genvmbase.ExecutionReturn)
            else ExecutionResultStatus.ERROR
        )

        result = Receipt(
            result=genvmbase.encode_result_to_bytes(res.result),
            gas_used=0,
            eq_outputs={
                k: base64.b64encode(v).decode("ascii")
                for k, v in res.eq_outputs.items()
            },
            pending_transactions=res.pending_transactions,
            vote=None,
            execution_result=result_exec_code,
            contract_state=typing.cast(_SnapshotView, res.state).snapshot.states[
                "accepted"
            ],
            calldata=calldata,
            mode=self.validator_mode,
            node_config=self._create_enhanced_node_config(host_data),
            genvm_result={
                "stdout": res.stdout[:5000],
                "stderr": res.stderr,
            },
            processing_time=res.processing_time,
            nondet_disagree=res.nondet_disagree,
        )

        if self.validator_mode == ExecutionMode.LEADER:
            return result
        return self._set_vote(result)
