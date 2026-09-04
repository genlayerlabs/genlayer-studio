"""
Message-fee allocation tree types.

Mirrors the executor's `genvm_common::domain::fees` module: the fee parameters
and the nested `MessageAllocationNode` tree that is passed alongside an
execution and matched against emitted messages.
"""

import typing

from .calldata import Address


class InternalMessageParams(typing.TypedDict):
    leader_timeunits_allocation: int
    validator_timeunits_allocation: int
    execution_budget_per_round: int
    # `appealRounds` is not carried here; the chain derives it as
    # `len(rotations) - 1`, so `rotations` must be non-empty.
    rotations: list[int]
    max_price_gen_per_time_unit: int
    storage_fee_max_gas_price: int
    receipt_fee_max_gas_price: int


class ExternalMessageParams(typing.TypedDict):
    gas_limit: int
    max_gas_price: int


# Externally-tagged `MessageAllocationNodeParams` enum: exactly one of the keys.
class _InternalParams(typing.TypedDict):
    Internal: InternalMessageParams


class _ExternalParams(typing.TypedDict):
    External: ExternalMessageParams


MessageAllocationNodeParams = typing.Union[_InternalParams, _ExternalParams]


class MessageAllocationNode(typing.TypedDict):
    recipient: Address | None
    call_key: bytes | None
    budget: int
    # Lifecycle the node matches against (only meaningful for internal messages).
    on: typing.Literal["finalized", "decided"]
    fee_params: MessageAllocationNodeParams
    # Nested allocation subtree; the chain receives this flattened to
    # parent-pointer form.
    children: list["MessageAllocationNode"]


DEFAULT_EXTERNAL_MESSAGE_ALLOC: MessageAllocationNode = {
    "budget": 2**200,
    "recipient": None,
    "call_key": None,
    # Unused for external messages (no acceptance/finalize lifecycle).
    "on": "finalized",
    "fee_params": {
        "External": {
            "gas_limit": 2**200,
            "max_gas_price": 0,
        },
    },
    "children": [],
}

DEFAULT_INTERNAL_DEC_MESSAGE_ALLOC: MessageAllocationNode = {
    "budget": 2**200,
    "recipient": None,
    "call_key": None,
    "on": "decided",
    "fee_params": {
        "Internal": {
            "execution_budget_per_round": 2**10,
            "rotations": [4] * 5,
            "leader_timeunits_allocation": 5,
            "validator_timeunits_allocation": 5,
            "max_price_gen_per_time_unit": 2**200,
            "storage_fee_max_gas_price": 2**200,
            "receipt_fee_max_gas_price": 2**200,
        },
    },
    "children": [],
}

DEFAULT_INTERNAL_FIN_MESSAGE_ALLOC: MessageAllocationNode = {
    "budget": 2**200,
    "recipient": None,
    "call_key": None,
    "on": "finalized",
    "fee_params": {
        "Internal": {
            "execution_budget_per_round": 2**10,
            "rotations": [4] * 5,
            "leader_timeunits_allocation": 5,
            "validator_timeunits_allocation": 5,
            "max_price_gen_per_time_unit": 2**200,
            "storage_fee_max_gas_price": 20,
            "receipt_fee_max_gas_price": 20,
        },
    },
    "children": [],
}
