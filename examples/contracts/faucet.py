# v0.4.0
# { "Depends": "py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0" }

import genlayer as gl
from genlayer.types import *


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
        if v == 0:
            raise gl.vm.UserError("send some value")
        _Recipient(Address(recipient)).emit_transfer(value=v)
