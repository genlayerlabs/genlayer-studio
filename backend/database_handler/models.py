from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Integer,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    func,
    text,
    ForeignKey,
    LargeBinary,
    Sequence,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    MappedAsDataclass,
    relationship,
)
import datetime
import enum


class TransactionStatus(enum.Enum):
    PENDING = "PENDING"
    ACTIVATED = "ACTIVATED"
    CANCELED = "CANCELED"
    PROPOSING = "PROPOSING"
    COMMITTING = "COMMITTING"
    REVEALING = "REVEALING"
    ACCEPTED = "ACCEPTED"
    FINALIZED = "FINALIZED"
    UNDETERMINED = "UNDETERMINED"
    LEADER_TIMEOUT = "LEADER_TIMEOUT"
    VALIDATORS_TIMEOUT = "VALIDATORS_TIMEOUT"


# We map them to `DataClass`es in order to have better type hints https://docs.sqlalchemy.org/en/20/orm/dataclasses.html#declarative-dataclass-mapping
class Base(MappedAsDataclass, DeclarativeBase):
    pass


class CurrentState(Base):
    __tablename__ = "current_state"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="current_state_pkey"),
        CheckConstraint("balance >= 0", name="check_balance_non_negative"),
    )

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True),
        init=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class Transactions(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("type = ANY (ARRAY[0, 1, 2])", name="transactions_type_check"),
        PrimaryKeyConstraint("hash", name="transactions_pkey"),
        CheckConstraint("value >= 0", name="value_unsigned_int"),
    )

    hash: Mapped[str] = mapped_column(String(66), primary_key=True, unique=True)
    status: Mapped[TransactionStatus] = mapped_column(
        Enum(
            TransactionStatus,
            name="transaction_status",
        ),
        server_default=text("'PENDING'::transaction_status"),
        nullable=False,
    )
    from_address: Mapped[Optional[str]] = mapped_column(String(255))
    to_address: Mapped[Optional[str]] = mapped_column(String(255))
    input_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    data: Mapped[Optional[dict]] = mapped_column(JSONB)
    consensus_data: Mapped[Optional[dict]] = mapped_column(JSONB)
    nonce: Mapped[Optional[int]] = mapped_column(Integer)
    value: Mapped[Optional[int]] = mapped_column(Integer)
    type: Mapped[Optional[int]] = mapped_column(Integer)
    gaslimit: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=func.current_timestamp(), init=False
    )
    leader_only: Mapped[bool] = mapped_column(Boolean)
    r: Mapped[Optional[int]] = mapped_column(Integer)
    s: Mapped[Optional[int]] = mapped_column(Integer)
    v: Mapped[Optional[int]] = mapped_column(Integer)
    appeal_failed: Mapped[Optional[int]] = mapped_column(Integer)
    consensus_history: Mapped[Optional[dict]] = mapped_column(JSONB)
    timestamp_appeal: Mapped[Optional[int]] = mapped_column(BigInteger)
    appeal_processing_time: Mapped[Optional[int]] = mapped_column(Integer)
    contract_snapshot: Mapped[Optional[dict]] = mapped_column(JSONB)
    config_rotation_rounds: Mapped[Optional[int]] = mapped_column(Integer)
    num_of_initial_validators: Mapped[Optional[int]] = mapped_column(Integer)
    last_vote_timestamp: Mapped[Optional[int]] = mapped_column(BigInteger)
    rotation_count: Mapped[Optional[int]] = mapped_column(Integer)
    leader_timeout_validators: Mapped[Optional[list]] = mapped_column(JSONB)
    sim_config: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None
    )

    # Relationship for triggered transactions
    triggered_by_hash: Mapped[Optional[str]] = mapped_column(
        ForeignKey("transactions.hash", name="triggered_by_hash_fkey"),
        init=False,
    )

    triggered_by: Mapped[Optional["Transactions"]] = relationship(
        "Transactions",
        remote_side=[hash],
        foreign_keys=[triggered_by_hash],
        back_populates="triggered_transactions",
        default=None,
    )
    triggered_transactions: Mapped[List["Transactions"]] = relationship(
        "Transactions",
        back_populates="triggered_by",
        init=False,
    )
    appealed: Mapped[bool] = mapped_column(Boolean, default=False)
    appeal_undetermined: Mapped[bool] = mapped_column(Boolean, default=False)
    appeal_leader_timeout: Mapped[bool] = mapped_column(Boolean, default=False)
    appeal_validators_timeout: Mapped[bool] = mapped_column(Boolean, default=False)
    timestamp_awaiting_finalization: Mapped[Optional[int]] = mapped_column(
        BigInteger, default=None
    )
    blocked_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), nullable=True, default=None
    )
    worker_id: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, default=None
    )


class Validators(Base):
    __tablename__ = "validators"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="validators_pkey"),
        CheckConstraint("stake >= 0", name="stake_unsigned_int"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, init=False)
    stake: Mapped[int] = mapped_column(Integer)
    config: Mapped[dict] = mapped_column(JSONB)
    address: Mapped[Optional[str]] = mapped_column(String(255))
    provider: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    plugin: Mapped[str] = mapped_column(String(255))
    plugin_config: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(True), server_default=func.current_timestamp(), init=False
    )
    private_key: Mapped[Optional[str]] = mapped_column(String(255))


class LLMProviderDBModel(Base):
    __tablename__ = "llm_provider"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="llm_provider_pkey"),
        UniqueConstraint(
            "provider", "model", "plugin", name="unique_provider_model_plugin"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, init=False)
    provider: Mapped[str] = mapped_column(String(255))
    model: Mapped[str] = mapped_column(String(255))
    config: Mapped[dict | str] = mapped_column(JSONB)
    plugin: Mapped[str] = mapped_column(String(255), nullable=False)
    plugin_config: Mapped[dict] = mapped_column(JSONB)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=func.current_timestamp(), init=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True),
        init=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="snapshots_pkey"),
        UniqueConstraint("snapshot_id", name="snapshots_snapshot_id_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, init=False)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        Sequence("snapshot_id_seq", start=1, increment=1),
        unique=True,
        nullable=False,
        init=False,
    )  # Incremental identifier
    state_data: Mapped[bytes] = mapped_column(
        LargeBinary
    )  # Stores compressed state data as bytes
    transaction_data: Mapped[bytes] = mapped_column(
        LargeBinary
    )  # Stores compressed transaction data as bytes
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(True), server_default=func.current_timestamp(), init=False
    )
