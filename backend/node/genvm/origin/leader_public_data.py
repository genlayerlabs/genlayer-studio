import collections.abc
from dataclasses import dataclass
from typing import Self

from . import calldata as gvm_calldata


@dataclass
class LeaderPublicData:
    nondet_block_outputs: list[bytes]

    def encode(self) -> bytes:
        return gvm_calldata.encode({"nd_outs": self.nondet_block_outputs})

    @classmethod
    def decode(cls, encoded: collections.abc.Buffer) -> Self:
        decoded = gvm_calldata.decode(bytes(encoded))
        if (
            not isinstance(decoded, dict)
            or decoded.keys() != {"nd_outs"}
            or not isinstance(decoded["nd_outs"], list)
            or any(not isinstance(output, bytes) for output in decoded["nd_outs"])
        ):
            raise ValueError("invalid leader public data")
        return cls(decoded["nd_outs"])


def encode(value: LeaderPublicData) -> bytes:
    return value.encode()


def decode(encoded: collections.abc.Buffer) -> LeaderPublicData:
    return LeaderPublicData.decode(encoded)
