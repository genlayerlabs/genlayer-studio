"""Deterministic Studio analogue of Consensus' GhostFactory deployment rules.

Studio does not execute user submissions on an EVM.  Its former fallback for
an unavailable shadow Hardhat node created a random account, which made salted
deployments non-deterministic and let RPC replicas disagree.  This module keeps
the address calculation identical to GhostFactory's CREATE/CREATE2 mechanics
while giving Studio its own configurable virtual protocol deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
import os

import rlp
from eth_utils import is_address, keccak, to_bytes, to_checksum_address


UINT256_MAX = (1 << 256) - 1

# These identify Studio's virtual v0.6 protocol deployment.  Operators that
# intentionally mirror a concrete EVM deployment can override all three values
# with that deployment's GhostFactory, CreationPhase, and bytecodeEVM values.
DEFAULT_GHOST_FACTORY_ADDRESS = "0x130e09c996462963A6398CA04e1011e6ef9d68a6"
DEFAULT_CREATION_PHASE_ADDRESS = "0x6AF00387f985684f159d673b15265E651B9E29f8"
DEFAULT_GHOST_BYTECODE_HASH = (
    "0x4249189346902cb6f0afc196d5719e1b4cbc861a700ae3b0c6dcbe51e5e566de"
)
DEFAULT_GHOST_FACTORY_INITIAL_NONCE = 2


class InvalidGhostFactoryConfiguration(ValueError):
    pass


@dataclass(frozen=True)
class GhostFactoryConfig:
    factory_address: str
    creation_phase_address: str
    bytecode_hash: bytes
    initial_nonce: int

    @classmethod
    def from_env(cls) -> "GhostFactoryConfig":
        factory_address = _configured_address(
            "GENLAYER_STUDIO_GHOST_FACTORY_ADDRESS",
            DEFAULT_GHOST_FACTORY_ADDRESS,
        )
        creation_phase_address = _configured_address(
            "GENLAYER_STUDIO_CREATION_PHASE_ADDRESS",
            DEFAULT_CREATION_PHASE_ADDRESS,
        )
        bytecode_hash = _configured_bytes32(
            "GENLAYER_STUDIO_GHOST_BYTECODE_HASH",
            DEFAULT_GHOST_BYTECODE_HASH,
        )
        initial_nonce = _configured_uint256(
            "GENLAYER_STUDIO_GHOST_FACTORY_INITIAL_NONCE",
            DEFAULT_GHOST_FACTORY_INITIAL_NONCE,
        )
        return cls(
            factory_address=factory_address,
            creation_phase_address=creation_phase_address,
            bytecode_hash=bytecode_hash,
            initial_nonce=initial_nonce,
        )

    def address_for(self, salt_nonce: int, successful_deployments: int) -> str:
        """Return the next GhostFactory address without mutating state.

        ``successful_deployments`` is the number of ghosts already created by
        this virtual factory.  Every successful CREATE and CREATE2 increments
        the EVM account nonce, while a reverted collision does not.
        """

        salt_nonce = _coerce_uint256("salt_nonce", salt_nonce)
        successful_deployments = _coerce_uint256(
            "successful_deployments", successful_deployments
        )
        factory = to_bytes(hexstr=self.factory_address)
        if salt_nonce == 0:
            factory_nonce = self.initial_nonce + successful_deployments
            if factory_nonce > UINT256_MAX:
                raise OverflowError("GhostFactoryNonceOverflow")
            digest = keccak(rlp.encode([factory, factory_nonce]))
        else:
            creation_phase = to_bytes(hexstr=self.creation_phase_address)
            salt = keccak(creation_phase + salt_nonce.to_bytes(32, "big"))
            digest = keccak(b"\xff" + factory + salt + self.bytecode_hash)
        return to_checksum_address("0x" + digest[-20:].hex())


def _configured_address(name: str, default: str) -> str:
    value = os.getenv(name, default)
    if not is_address(value):
        raise InvalidGhostFactoryConfiguration(f"{name} must be an EVM address")
    return to_checksum_address(value)


def _configured_bytes32(name: str, default: str) -> bytes:
    value = os.getenv(name, default)
    try:
        raw = to_bytes(hexstr=value)
    except (TypeError, ValueError) as exc:
        raise InvalidGhostFactoryConfiguration(f"{name} must be bytes32") from exc
    if len(raw) != 32:
        raise InvalidGhostFactoryConfiguration(f"{name} must be bytes32")
    return raw


def _configured_uint256(name: str, default: int) -> int:
    value = os.getenv(name, str(default))
    try:
        return _coerce_uint256(name, int(value, 10))
    except (TypeError, ValueError) as exc:
        raise InvalidGhostFactoryConfiguration(f"{name} must be uint256") from exc


def _coerce_uint256(name: str, value: int) -> int:
    value = int(value)
    if value < 0 or value > UINT256_MAX:
        raise InvalidGhostFactoryConfiguration(f"{name} must be uint256")
    return value
