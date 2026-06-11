# v0.2.17
# { "Depends": "py-genlayer:1zr6nqk597d97kg0dyxg0shhrykx5v02zjgnyrajapy4wlqvfvwh" }

import genlayer as gl
from genlayer import *


@gl.evm.contract_interface
class _Recipient:
    class View:
        pass

    class Write:
        pass


class Faucet(gl.contract.Contract):
    def __init__(self):
        pass

    @gl.public.write.payable
    def send(self, recipient: str) -> None:
        v = gl.message.value
        if v == u256(0):
            raise gl.vm.UserError("send some value")
        _Recipient(Address(recipient)).emit_transfer(value=v)
