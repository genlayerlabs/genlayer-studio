# v0.3.0
# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }

import genlayer as gl
from genlayer.message_allocation import InternalAllocation
from genlayer.types import *
from genlayer.vm.public_abi import Permissions

DESCENDANT_BUDGET = 10**21


def _fee_params() -> gl.chain.InternalMessageParams:
    return gl.chain.InternalMessageParams(
        leader_time_units_allocation=100,
        validator_time_units_allocation=200,
        execution_budget_per_round=10**18,
        rotations=[0],
        max_price_gen_per_time_unit=1,
        storage_fee_max_gas_price=10**18,
        receipt_fee_max_gas_price=10**18,
    )


class DescendantGrants(gl.contract.Contract):
    last: str

    def __init__(self):
        self.last = ""
        gl.storage.Root.get().set_permission(
            Permissions.CAN_USE_BALANCE_FOR_MESSAGE_FEES, True
        )

    @gl.public.write.payable
    def fund(self) -> None:
        pass

    @gl.public.write
    def emit_closed(self, relay: str, target: str) -> None:
        grant = gl.contract.UseBalanceParams(_fee_params())
        gl.contract.get_at(Address(relay)).emit(grant).relay(target, "closed")

    @gl.public.write
    def emit_closed_balance_funded(self, relay: str, target: str) -> None:
        grant = gl.contract.UseBalanceParams(_fee_params())
        gl.contract.get_at(Address(relay)).emit(grant).relay_balance_funded(
            target, "closed-balance-funded"
        )

    @gl.public.write
    def emit_open(self, relay: str, target: str) -> None:
        grant = gl.contract.UseBalanceParams(
            _fee_params(),
            descendants=DESCENDANT_BUDGET,
        )
        gl.contract.get_at(Address(relay)).emit(grant).relay(target, "open")

    @gl.public.write
    def emit_pinned(self, relay: str, target: str) -> None:
        allocation = InternalAllocation(
            recipient=Address(target),
            call_key="record",
            fee_params=_fee_params(),
            budget=DESCENDANT_BUDGET,
        )
        grant = gl.contract.UseBalanceParams(
            _fee_params(),
            descendants=[allocation],
        )
        gl.contract.get_at(Address(relay)).emit(grant).relay(target, "pinned")

    @gl.public.write
    def relay(self, target: str, value: str) -> None:
        self.last = value
        gl.contract.get_at(Address(target)).emit().record(value)

    @gl.public.write
    def relay_balance_funded(self, target: str, value: str) -> None:
        self.last = value
        gl.contract.get_at(Address(target)).emit(
            gl.contract.UseBalanceParams(_fee_params())
        ).record(value)

    @gl.public.write
    def record(self, value: str) -> None:
        self.last = value

    @gl.public.view
    def get_last(self) -> str:
        return self.last
