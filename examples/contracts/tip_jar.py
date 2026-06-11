# v0.2.16
# { "Depends": "py-genlayer:1zr6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh" }

import genlayer as gl
from genlayer import *


class TipJar(gl.contract.Contract):
    owner: Address
    total_tips: u256

    def __init__(self):
        self.owner = gl.message.sender_address
        self.total_tips = u256(0)

    @gl.public.write.payable
    def tip(self) -> None:
        v = gl.message.value
        if v == u256(0):
            raise gl.vm.UserError("send some value")
        self.total_tips = self.total_tips + v

    @gl.public.view
    def get_total_tips(self) -> u256:
        return self.total_tips

    @gl.public.view
    def get_balance(self) -> u256:
        return self.balance

    @gl.public.view
    def get_owner(self) -> str:
        return str(self.owner.as_hex)
