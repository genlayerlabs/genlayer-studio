from contextlib import redirect_stdout
from dataclasses import asdict
import io
import json
import base64
from typing import Callable, Optional
import typing
import collections.abc
import os

from backend.domain.types import Validator, Transaction, TransactionType
from backend.protocol_rpc.message_handler.types import LogEvent, EventType, EventScope
import backend.node.genvm.base as genvmbase
import backend.node.genvm.origin.host_fns as genvmconsts
from backend.database_handler.contract_snapshot import ContractSnapshot
from backend.node.types import Receipt, ExecutionMode, Vote, ExecutionResultStatus
from backend.protocol_rpc.message_handler.base import MessageHandler

from .types import Address


class _SnapshotView(genvmbase.StateProxy):
    def __init__(
        self,
        snapshot: ContractSnapshot,
        snapshot_factory: typing.Callable[[str], ContractSnapshot],
        readonly: bool,
    ):
        self.contract_address = Address(snapshot.contract_address)
        self.snapshot = snapshot
        self.snapshot_factory = snapshot_factory
        self.cached = {}
        self.readonly = readonly

    def _get_snapshot(self, addr: Address) -> ContractSnapshot:
        if addr == self.contract_address:
            return self.snapshot
        res = self.cached.get(addr)
        if res is not None:
            return res
        res = self.snapshot_factory(addr.as_hex)
        self.cached[addr] = res
        return res

    def get_code(self, addr: Address) -> bytes:
        return self._get_snapshot(addr).contract_code.encode("utf-8")

    def storage_read(
        self, account: Address, slot: bytes, index: int, le: int, /
    ) -> tuple[bytes, int]:
        snap = self._get_snapshot(account)
        for_acc = snap.encoded_state.setdefault(account.as_b64, {})
        for_slot = for_acc.setdefault(base64.b64encode(slot).decode("ascii"), "")
        data = bytearray(base64.b64decode(for_slot))
        data.extend(b"\x00" * (index + le - len(data)))
        return data[index : index + le]

    def storage_write(
        self,
        account: Address,
        slot: bytes,
        index: int,
        got: collections.abc.Buffer,
        /,
    ) -> None:
        assert account == self.contract_address
        assert not self.readonly
        snap = self._get_snapshot(account)
        for_acc = snap.encoded_state.setdefault(account.as_b64, {})
        slot_id = base64.b64encode(slot).decode("ascii")
        for_slot = for_acc.setdefault(slot_id, "")
        data = bytearray(base64.b64decode(for_slot))
        mem = memoryview(got)
        data.extend(b"\x00" * (index + len(mem) - len(data)))
        data[index : index + len(mem)] = mem
        for_acc[slot_id] = base64.b64encode(data).decode("utf-8")


class Node:
    def __init__(
        self,
        contract_snapshot: ContractSnapshot | None,
        validator_mode: ExecutionMode,
        validator: Validator,
        contract_snapshot_factory: Callable[[str], ContractSnapshot] | None,
        leader_receipt: Optional[Receipt] = None,
        msg_handler: MessageHandler | None = None,
    ):
        self.contract_snapshot = contract_snapshot
        self.validator_mode = validator_mode
        self.validator = validator
        self.address = validator.address
        self.leader_receipt = leader_receipt
        self.msg_handler = msg_handler
        self.contract_snapshot_factory = contract_snapshot_factory

    def _create_genvm(self) -> genvmbase.IGenVM:
        return genvmbase.GenVMHost()

    async def exec_transaction(self, transaction: Transaction) -> Receipt:
        assert transaction.data is not None
        transaction_data = transaction.data
        assert transaction.from_address is not None
        if transaction.type == TransactionType.DEPLOY_CONTRACT:
            calldata = base64.b64decode(transaction_data["calldata"])
            receipt = await self.deploy_contract(
                transaction.from_address,
                transaction_data["contract_code"],
                calldata,
            )
        elif transaction.type == TransactionType.RUN_CONTRACT:
            calldata = base64.b64decode(transaction_data["calldata"])
            receipt = await self.run_contract(
                transaction.from_address,
                calldata,
            )
        else:
            raise Exception(f"unknown transaction type {transaction.type}")
        return receipt

    def _set_vote(self, receipt: Receipt) -> Receipt:

        def try_decode_error(e: Exception) -> tuple[str, ...]:
            # FIXME(kp2pml30): #622 I am not sure that we should compare args,
            # because if traceback gets there,
            # it will have different ones for validator and nodes
            if len(e.args) != 2:
                return ()
            typ = e.args[0]
            if typ == "rollback":
                return ("rollback", e.args[1])
            if typ == "error":
                return ("error",)
            return ()

        leader_receipt = self.leader_receipt
        if self.validator_mode == ExecutionMode.LEADER:
            receipt.vote = Vote.AGREE
        elif (
            leader_receipt.execution_result == receipt.execution_result
            and (leader_receipt.error is None) == (receipt.error is None)
            and (
                leader_receipt.error is None
                or try_decode_error(leader_receipt.error)
                == try_decode_error(receipt.error)
            )
            and leader_receipt.returned == receipt.returned
            and leader_receipt.contract_state == receipt.contract_state
            and leader_receipt.pending_transactions == receipt.pending_transactions
        ):
            receipt.vote = Vote.AGREE
        else:
            receipt.vote = Vote.DISAGREE

        return receipt

    async def deploy_contract(
        self,
        from_address: str,
        code_to_deploy: str,
        calldata: bytes,
    ) -> Receipt:
        assert self.contract_snapshot is not None
        self.contract_snapshot.contract_code = code_to_deploy
        return await self._run_genvm(
            from_address, calldata, readonly=False, is_init=True
        )

    async def run_contract(self, from_address: str, calldata: bytes) -> Receipt:
        return await self._run_genvm(
            from_address, calldata, readonly=False, is_init=False
        )

    async def get_contract_data(
        self,
        from_address: str,
        calldata: bytes,
    ) -> Receipt:
        return await self._run_genvm(
            from_address, calldata, readonly=True, is_init=False
        )

    async def get_contract_schema(self, code: str) -> str:
        genvm = self._create_genvm()
        return await genvm.get_contract_schema(code.encode("utf-8"))

    async def _run_genvm(
        self,
        from_address: str,
        calldata: bytes,
        *,
        readonly: bool,
        is_init: bool,
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
        config = {
            "modules": [
                {
                    "path": "${genvmRoot}/lib/genvm-modules/",
                    "id": "llm",
                    "config": {
                        "host": f"{os.environ['WEBREQUESTPROTOCOL']}://{os.environ['WEBREQUESTHOST']}:{os.environ['WEBREQUESTPORT']}",
                        "provider": "simulator",
                        "model": json.dumps(self.validator.llmprovider.__dict__),
                    },
                },
                {
                    "path": "${genvmRoot}/lib/genvm-modules/",
                    "id": "web",
                    "config": {
                        "host": f"{os.environ['WEBREQUESTPROTOCOL']}://{os.environ['WEBREQUESTHOST']}:{os.environ['WEBREQUESTSELENIUMPORT']}"
                    },
                },
            ]
        }
        snapshot_view = _SnapshotView(
            self.contract_snapshot, self.contract_snapshot_factory, readonly
        )
        res = await genvm.run_contract(
            snapshot_view,
            contract_address=Address(self.contract_snapshot.contract_address),
            from_address=Address(from_address),
            calldata_raw=calldata,
            is_init=is_init,
            leader_results=leader_res,
            config=json.dumps(config),
        )
        base_receipt = {
            "class_name": "",
            "calldata": calldata,
            "mode": self.validator_mode,
            "node_config": self.validator.to_dict(),
        }
        if self.msg_handler is not None:
            self.msg_handler.send_message(
                LogEvent(
                    name="execution_finished",
                    type=EventType.INFO,
                    scope=EventScope.GENVM,
                    message="execution finished",
                    data={
                        "result": f"{res.result!r}",
                        "stdout": res.stdout,
                        "stderr": res.stderr,
                    },
                )
            )

        returned = None
        error = None
        exec_result_code = ExecutionResultStatus.ERROR

        if isinstance(res.result, genvmbase.ExecutionFail):
            error = Exception("error", repr(res.result))
        elif isinstance(res.result, genvmbase.ExecutionRollback):
            error = Exception("rollback", res.result.message)
        else:
            assert isinstance(res.result, genvmbase.ExecutionReturn)
            returned = res.result.ret
            exec_result_code = ExecutionResultStatus.SUCCESS

        return self._set_vote(
            Receipt(
                returned=returned,
                error=error,
                gas_used=0,
                eq_outputs={
                    k: base64.b64encode(v).decode("ascii")
                    for k, v in res.eq_outputs.items()
                },
                pending_transactions=res.pending_transactions,
                vote=None,
                execution_result=exec_result_code,
                contract_state=self.contract_snapshot.encoded_state,
                **base_receipt,
            )
        )
