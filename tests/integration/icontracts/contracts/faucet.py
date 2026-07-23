# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

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

    @gl.public.view
    def get_balance(self) -> u256:
        return self.balance
