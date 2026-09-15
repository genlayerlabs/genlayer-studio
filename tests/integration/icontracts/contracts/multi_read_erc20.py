# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl
from genlayer.types import *


class multi_read_erc20(gl.contract.Contract):
    balances: gl.storage.TreeMap[Address, gl.storage.TreeMap[Address, u256]]

    def __init__(self):
        pass

    @gl.public.write
    def update_token_balances(
        self, account_address: str, token_contracts: list[str]
    ) -> None:
        for token_contract in token_contracts:
            contract = gl.contract.get_at(Address(token_contract))
            balance = contract.view().get_balance_of(account_address)
            self.balances.get_or_insert_default(Address(account_address))[
                Address(token_contract)
            ] = balance

    @gl.public.view
    def get_balances(self) -> dict[str, dict[str, int]]:
        return {
            k.as_hex: {k.as_hex: v for k, v in v.items()}
            for k, v in self.balances.items()
        }
