# v0.3.0
# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }

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
