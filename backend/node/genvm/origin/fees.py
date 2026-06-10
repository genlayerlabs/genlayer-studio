"""Message-fee allocation tree types.

Mirrors GenVM's ``tests/runner/origin/fees.py`` for the manager request
schema used by main/rc4+.
"""

import typing

from .calldata import Address


class InternalMessageParams(typing.TypedDict):
    leader_timeunits_allocation: int
    validator_timeunits_allocation: int
    execution_budget_per_round: int
    rotations: list[int]
    max_price_gen_per_time_unit: int
    storage_fee_max_gas_price: int
    receipt_fee_max_gas_price: int


class ExternalMessageParams(typing.TypedDict):
    gas_limit: int
    max_gas_price: int


class _InternalParams(typing.TypedDict):
    Internal: InternalMessageParams


class _ExternalParams(typing.TypedDict):
    External: ExternalMessageParams


MessageAllocationNodeParams = typing.Union[_InternalParams, _ExternalParams]


class MessageAllocationNode(typing.TypedDict):
    recipient: Address | None
    call_key: bytes | None
    budget: int
    on: typing.Literal["finalized", "accepted"]
    fee_params: MessageAllocationNodeParams
    children: list["MessageAllocationNode"]
