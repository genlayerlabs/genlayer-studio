# v0.3.0
# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }

import genlayer as gl
from genlayer.types import *


class read_erc20(gl.contract.Contract):
    token_contract: Address

    def __init__(self, token_contract: str):
        self.token_contract = Address(token_contract)

    @gl.public.view
    def get_balance_of(self, account_address: str) -> int:
        return (
            gl.contract.get_at(self.token_contract)
            .view()
            .get_balance_of(account_address)
        )
