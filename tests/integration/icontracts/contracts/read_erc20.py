# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

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
