# v0.3.0
# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }

import genlayer as gl
from genlayer.types import *


class TipJar(gl.contract.Contract):
    owner: Address
    total_tips: u256

    def __init__(self):
        self.owner = gl.message.sender_address
        self.total_tips = 0

    @gl.public.write.payable
    def tip(self) -> None:
        v = gl.message.value
        if v == 0:
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
