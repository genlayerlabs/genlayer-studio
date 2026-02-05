# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class read_erc20(gl.Contract):
    token_contract: Address

    def __init__(self, token_contract: str):
        self.token_contract = Address(token_contract)

    @gl.public.view
    def get_balance_of(self, account_address: str) -> int:
        return (
            gl.get_contract_at(self.token_contract)
            .view()
            .get_balance_of(account_address)
        )
