# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

import genlayer as gl
from genlayer.types import *


class PayableEscrow(gl.contract.Contract):
    depositor: Address
    deposited: u256

    def __init__(self):
        self.depositor = Address(b"\x00" * 20)
        self.deposited = 0

    @gl.public.write.payable
    def deposit(self) -> None:
        v = gl.message.value
        if v == 0:
            raise gl.vm.UserError("zero value")
        self.depositor = gl.message.sender_address
        self.deposited = self.deposited + v

    @gl.public.write
    def withdraw(self, to: str) -> None:
        if gl.message.sender_address != self.depositor:
            raise gl.vm.UserError("not depositor")
        amount = self.deposited
        if amount == 0:
            raise gl.vm.UserError("nothing to withdraw")
        self.deposited = 0
        gl.contract.get_at(Address(to)).emit_transfer(value=amount)

    @gl.public.view
    def get_deposited(self) -> u256:
        return self.deposited

    @gl.public.view
    def get_balance(self) -> u256:
        return self.balance
