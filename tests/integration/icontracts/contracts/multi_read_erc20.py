# { "Depends": "py-genlayer:test" }

from genlayer import *


class multi_read_erc20(gl.Contract):
    balances: TreeMap[Address, TreeMap[Address, u256]]

    def __init__(self):
        pass

    @gl.public.write
    def update_token_balances(
        self, account_address: str, token_contracts: list[str]
    ) -> None:
        for token_contract in token_contracts:
            contract = gl.ContractAt(Address(token_contract))
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
