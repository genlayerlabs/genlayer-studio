# v0.2.5
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class PayableEscrow(gl.Contract):
    depositor: Address
    deposited: u256

    def __init__(self):
        self.depositor = Address(b"\x00" * 20)
        self.deposited = u256(0)

    @gl.public.write.payable
    def deposit(self) -> None:
        v = gl.message.value
        if v == u256(0):
            raise gl.vm.UserError("zero value")
        self.depositor = gl.message.sender_address
        self.deposited = self.deposited + v

    @gl.public.write
    def withdraw(self, to: str) -> None:
        if gl.message.sender_address != self.depositor:
            raise gl.vm.UserError("not depositor")
        amount = self.deposited
        if amount == u256(0):
            raise gl.vm.UserError("nothing to withdraw")
        self.deposited = u256(0)
        gl.get_contract_at(Address(to)).emit_transfer(value=amount)

    @gl.public.view
    def get_deposited(self) -> u256:
        return self.deposited

    @gl.public.view
    def get_balance(self) -> u256:
        return self.balance
