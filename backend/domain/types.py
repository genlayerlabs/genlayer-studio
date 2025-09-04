# Types from our domain
# Trying to follow [hexagonal architecture](https://en.wikipedia.org/wiki/Hexagonal_architecture_(software)) or layered architecture.
# These types should not depend on any other layer.

from dataclasses import dataclass, field
import datetime
import decimal
from enum import Enum, IntEnum
import os
from backend.database_handler.models import TransactionStatus
from backend.database_handler.types import ConsensusData
from backend.database_handler.contract_snapshot import ContractSnapshot


@dataclass
class SimValidatorConfig:
    stake: int
    provider: str
    model: str
    config: dict
    plugin: str
    plugin_config: dict

    @classmethod
    def from_dict(cls, d: dict) -> "SimValidatorConfig":
        return cls(
            stake=d["stake"],
            provider=d["provider"],
            model=d["model"],
            config=d["config"],
            plugin=d["plugin"],
            plugin_config=d["plugin_config"],
        )

    def to_dict(self) -> dict:
        return {
            "stake": self.stake,
            "provider": self.provider,
            "model": self.model,
            "config": self.config,
            "plugin": self.plugin,
            "plugin_config": self.plugin_config,
        }


@dataclass
class SimConfig:
    validators: list[SimValidatorConfig]
    genvm_datetime: str | None = None

    @property
    def genvm_datetime_as_datetime(self) -> datetime.datetime | None:
        if self.genvm_datetime is None:
            return None
        dt = datetime.datetime.fromisoformat(self.genvm_datetime.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)

    @classmethod
    def from_dict(cls, d: dict) -> "SimConfig":
        validators = [
            SimValidatorConfig.from_dict(v) if isinstance(v, dict) else v
            for v in d.get("validators", [])
        ]
        return cls(
            validators=validators,
            genvm_datetime=d.get("genvm_datetime"),
        )

    def to_dict(self) -> dict:
        return {
            "validators": [
                v.to_dict() if hasattr(v, "to_dict") else v for v in self.validators
            ],
            "genvm_datetime": self.genvm_datetime,
        }


@dataclass()
class LLMProvider:
    provider: str
    model: str
    config: dict
    plugin: str
    plugin_config: dict
    id: int | None = None

    def __hash__(self):
        return hash(
            (
                self.id,
                self.provider,
                self.model,
                frozenset(self.config.items()),
                frozenset(self.plugin_config.items()),
            )
        )


@dataclass()
class Validator:
    address: str
    stake: int
    llmprovider: LLMProvider
    id: int | None = None
    private_key: str | None = None

    @staticmethod
    def from_dict(d: dict) -> "Validator":
        ret = Validator.__new__(Validator)

        ret.address = d["address"]
        ret.stake = d["stake"]
        ret.llmprovider = LLMProvider(
            provider=d["provider"],
            config=d["config"],
            model=d["model"],
            plugin=d["plugin"],
            plugin_config=d["plugin_config"],
        )
        ret.id = d.get("id", None)
        ret.private_key = d.get("private_key", None)

        return ret

    def to_dict(self):
        result = {
            "address": self.address,
            "stake": self.stake,
            "provider": self.llmprovider.provider,
            "model": self.llmprovider.model,
            "config": self.llmprovider.config,
            "plugin": self.llmprovider.plugin,
            "plugin_config": self.llmprovider.plugin_config,
            "private_key": self.private_key,
        }

        if self.id:
            result["id"] = self.id

        return result


class TransactionType(IntEnum):
    SEND = 0
    DEPLOY_CONTRACT = 1
    RUN_CONTRACT = 2


@dataclass
class Transaction:
    hash: str
    status: TransactionStatus
    type: TransactionType
    from_address: str | None
    to_address: str | None
    input_data: dict | None = None
    data: dict | None = None
    consensus_data: ConsensusData | None = None
    nonce: int | None = None
    value: int | None = None
    gaslimit: int | None = None
    r: int | None = None
    s: int | None = None
    v: int | None = None
    leader_only: bool = (
        False  # Flag to indicate if this transaction should be processed only by the leader. Used for fast and cheap execution of transactions.
    )
    created_at: str | None = None
    appealed: bool = False
    timestamp_awaiting_finalization: int | None = None
    appeal_failed: int = 0
    appeal_undetermined: bool = False
    consensus_history: dict = field(default_factory=dict)
    timestamp_appeal: int | None = None
    appeal_processing_time: int = 0
    contract_snapshot: ContractSnapshot | None = None
    config_rotation_rounds: int | None = int(os.getenv("VITE_MAX_ROTATIONS", 3))
    num_of_initial_validators: int | None = None
    last_vote_timestamp: int | None = None
    rotation_count: int = 0
    appeal_leader_timeout: bool = False
    leader_timeout_validators: list | None = None
    appeal_validators_timeout: bool = False
    sim_config: SimConfig | None = None

    def to_dict(self):
        return {
            "hash": self.hash,
            "status": self.status.value,
            "type": self.type.value,
            "from_address": self.from_address,
            "to_address": self.to_address,
            "input_data": self.input_data,
            "data": self.data,
            "consensus_data": (
                self.consensus_data.to_dict() if self.consensus_data else None
            ),
            "nonce": self.nonce,
            "value": self.value,
            "gaslimit": self.gaslimit,
            "r": self.r,
            "s": self.s,
            "v": self.v,
            "leader_only": self.leader_only,
            "created_at": self.created_at,
            "appealed": self.appealed,
            "timestamp_awaiting_finalization": self.timestamp_awaiting_finalization,
            "appeal_failed": self.appeal_failed,
            "appeal_undetermined": self.appeal_undetermined,
            "consensus_history": self.consensus_history,
            "timestamp_appeal": self.timestamp_appeal,
            "appeal_processing_time": self.appeal_processing_time,
            "contract_snapshot": (
                self.contract_snapshot.to_dict() if self.contract_snapshot else None
            ),
            "config_rotation_rounds": self.config_rotation_rounds,
            "num_of_initial_validators": self.num_of_initial_validators,
            "last_vote_timestamp": self.last_vote_timestamp,
            "rotation_count": self.rotation_count,
            "appeal_leader_timeout": self.appeal_leader_timeout,
            "leader_timeout_validators": self.leader_timeout_validators,
            "appeal_validators_timeout": self.appeal_validators_timeout,
            "sim_config": self.sim_config.to_dict() if self.sim_config else None,
        }

    @classmethod
    def from_dict(cls, input: dict) -> "Transaction":
        return cls(
            hash=input["hash"],
            status=TransactionStatus(input["status"]),
            type=TransactionType(input["type"]),
            from_address=input.get("from_address"),
            to_address=input.get("to_address"),
            input_data=input.get("input_data"),
            data=input.get("data"),
            consensus_data=ConsensusData.from_dict(input.get("consensus_data")),
            nonce=input.get("nonce"),
            value=input.get("value"),
            gaslimit=input.get("gaslimit"),
            r=input.get("r"),
            s=input.get("s"),
            v=input.get("v"),
            leader_only=input.get("leader_only", False),
            created_at=input.get("created_at"),
            appealed=input.get("appealed", False),
            timestamp_awaiting_finalization=input.get(
                "timestamp_awaiting_finalization"
            ),
            appeal_failed=input.get("appeal_failed", 0),
            appeal_undetermined=input.get("appeal_undetermined", False),
            consensus_history=input.get("consensus_history", {}),
            timestamp_appeal=input.get("timestamp_appeal"),
            appeal_processing_time=input.get("appeal_processing_time", 0),
            contract_snapshot=ContractSnapshot.from_dict(
                input.get("contract_snapshot")
            ),
            config_rotation_rounds=input.get("config_rotation_rounds"),
            num_of_initial_validators=input.get("num_of_initial_validators"),
            last_vote_timestamp=input.get("last_vote_timestamp"),
            rotation_count=input.get("rotation_count", 0),
            appeal_leader_timeout=input.get("appeal_leader_timeout", False),
            leader_timeout_validators=input.get("leader_timeout_validators"),
            appeal_validators_timeout=input.get("appeal_validators_timeout", False),
            sim_config=(
                SimConfig.from_dict(input["sim_config"])
                if input.get("sim_config")
                else None
            ),
        )
