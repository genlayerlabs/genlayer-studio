from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional, Literal
import base64

import collections.abc

from eth_hash.auto import keccak


class Address:
    SIZE = 20

    __slots__ = ("_as_bytes", "_as_hex")

    _as_bytes: bytes
    _as_hex: str | None

    def __init__(self, val: str | collections.abc.Buffer):
        self._as_hex = None
        if isinstance(val, str):
            if len(val) == 2 + Address.SIZE * 2 and val.startswith("0x"):
                # 0x-prefixed hex string (42 chars)
                val = bytes.fromhex(val[2:])
            elif len(val) == Address.SIZE * 2:
                # Hex string without 0x prefix (40 chars) - try hex first
                try:
                    val = bytes.fromhex(val)
                except ValueError:
                    # Not valid hex, try base64
                    val = base64.b64decode(val)
            elif len(val) > Address.SIZE:
                val = base64.b64decode(val)
        else:
            val = bytes(val)
        if not isinstance(val, bytes) or len(val) != Address.SIZE:
            raise Exception(f"invalid address {val}")
        self._as_bytes = val

    @property
    def as_bytes(self) -> bytes:
        return self._as_bytes

    @property
    def as_hex(self) -> str:
        if self._as_hex is None:
            simple = self._as_bytes.hex()
            low_up = keccak(simple.encode("ascii")).hex()
            res = ["0", "x"]
            for i in range(len(simple)):
                if low_up[i] in ["0", "1", "2", "3", "4", "5", "6", "7"]:
                    res.append(simple[i])
                else:
                    res.append(simple[i].upper())
            self._as_hex = "".join(res)
        return self._as_hex

    @property
    def as_b64(self) -> str:
        return str(base64.b64encode(self.as_bytes), encoding="ascii")

    @property
    def as_int(self) -> int:
        return int.from_bytes(self._as_bytes, "little", signed=False)

    def __hash__(self):
        return hash(self._as_bytes)

    def __lt__(self, r):
        assert isinstance(r, Address)
        return self._as_bytes < r._as_bytes

    def __le__(self, r):
        assert isinstance(r, Address)
        return self._as_bytes <= r._as_bytes

    def __eq__(self, r):
        if not isinstance(r, Address):
            return False
        return self._as_bytes == r._as_bytes

    def __ge__(self, r):
        assert isinstance(r, Address)
        return self._as_bytes >= r._as_bytes

    def __gt__(self, r):
        assert isinstance(r, Address)
        return self._as_bytes > r._as_bytes

    def __repr__(self) -> str:
        return "addr#" + "".join(["{:02x}".format(x) for x in self._as_bytes])


class Vote(Enum):
    NOT_VOTED = "not_voted"
    AGREE = "agree"
    DISAGREE = "disagree"
    TIMEOUT = "timeout"
    DETERMINISTIC_VIOLATION = "deterministic_violation"
    IDLE = "idle"

    @classmethod
    def from_string(cls, value: str) -> "Vote":
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(f"Invalid vote value: {value}")

    def __int__(self) -> int:
        values = {
            Vote.NOT_VOTED: 0,
            Vote.AGREE: 1,
            Vote.DISAGREE: 2,
            Vote.TIMEOUT: 3,
            Vote.DETERMINISTIC_VIOLATION: 4,
            Vote.IDLE: 5,
        }
        return values[self]


class ExecutionMode(Enum):
    LEADER = "leader"
    VALIDATOR = "validator"

    @classmethod
    def from_string(cls, value: str) -> "ExecutionMode":
        try:
            return cls(value.lower())
        except ValueError:
            raise ValueError(f"Invalid execution mode value: {value}")


class ExecutionResultStatus(Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"

    @classmethod
    def from_string(cls, value: str) -> "ExecutionResultStatus":
        try:
            return cls(value.upper())
        except ValueError:
            raise ValueError(f"Invalid execution result status value: {value}")


@dataclass
class PendingTransaction:
    address: str  # Address of the contract to call
    calldata: bytes
    code: bytes | None
    salt_nonce: int
    on: Literal["accepted", "finalized"]
    value: int

    def is_deploy(self) -> bool:
        return self.code is not None

    def to_dict(self):
        if self.code is None:
            return {
                "address": self.address,
                "calldata": str(base64.b64encode(self.calldata), encoding="ascii"),
                "on": self.on,
                "value": self.value,
            }
        else:
            return {
                "code": str(base64.b64encode(self.code), encoding="ascii"),
                "calldata": str(base64.b64encode(self.calldata), encoding="ascii"),
                "salt_nonce": self.salt_nonce,
                "on": self.on,
                "value": self.value,
            }

    @classmethod
    def from_dict(cls, input: dict) -> "PendingTransaction":
        if "code" in input:
            return cls(
                address="0x",
                calldata=base64.b64decode(input["calldata"]),
                code=base64.b64decode(input["code"]),
                salt_nonce=input.get("salt_nonce", 0),
                value=input.get("value", 0),
                on=input.get("on", "finalized"),
            )
        else:
            return cls(
                address=input["address"],
                calldata=base64.b64decode(input["calldata"]),
                value=input.get("value", 0),
                code=None,
                salt_nonce=0,
                on=input.get("on", "finalized"),
            )


@dataclass
class Receipt:
    result: bytes
    calldata: bytes
    gas_used: int
    mode: ExecutionMode
    contract_state: dict[str, str]
    node_config: dict
    eq_outputs: dict[int, str]
    execution_result: ExecutionResultStatus
    vote: Optional[Vote] = None
    pending_transactions: Iterable[PendingTransaction] = ()
    genvm_result: dict[str, str] | None = None
    processing_time: Optional[int] = None
    nondet_disagree: int | None = None
    execution_stats: dict | None = None

    def to_dict(self, strip_contract_state: bool = False):
        """Convert Receipt to dict.

        Args:
            strip_contract_state: If True, replaces contract_state with empty dict to save storage.
                                 Contract state is always available from CurrentState table.
        """
        result = base64.b64encode(self.result).decode("ascii")
        calldata = str(base64.b64encode(self.calldata), encoding="ascii")

        return {
            "vote": self.vote.value if self.vote else None,
            "execution_result": self.execution_result.value,
            "result": result,
            "calldata": calldata,
            "gas_used": self.gas_used,
            "mode": self.mode.value,
            "contract_state": {} if strip_contract_state else self.contract_state,
            "node_config": self.node_config,
            "eq_outputs": self.eq_outputs,
            "pending_transactions": [
                pending_transaction.to_dict()
                for pending_transaction in self.pending_transactions
            ],
            "genvm_result": self.genvm_result,
            "processing_time": self.processing_time,
            "nondet_disagree": self.nondet_disagree,
            "execution_stats": self.execution_stats,
        }

    @classmethod
    def from_dict(cls, input: dict) -> Optional["Receipt"]:
        if input:
            return cls(
                vote=Vote.from_string(input.get("vote")) if input.get("vote") else None,
                execution_result=ExecutionResultStatus.from_string(
                    input.get("execution_result")
                ),
                result=base64.b64decode(input.get("result")),
                calldata=base64.b64decode(input.get("calldata")),
                gas_used=input.get("gas_used"),
                mode=ExecutionMode.from_string(input.get("mode")),
                contract_state=input.get("contract_state"),
                node_config=input.get("node_config"),
                eq_outputs={int(k): v for k, v in input.get("eq_outputs", {}).items()},
                pending_transactions=[
                    PendingTransaction.from_dict(pending_transaction)
                    for pending_transaction in input.get("pending_transactions", [])
                ],
                genvm_result=input.get("genvm_result"),
                processing_time=input.get("processing_time"),
                nondet_disagree=input.get("nondet_disagree"),
                execution_stats=input.get("execution_stats"),
            )
        else:
            return None
