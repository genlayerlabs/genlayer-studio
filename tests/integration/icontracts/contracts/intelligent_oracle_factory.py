# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

import genlayer as gl


class Registry(gl.contract.Contract):
    # Declare persistent storage fields
    contract_addresses: gl.storage.DynArray[str]
    intelligent_oracle_code: str

    def __init__(self, intelligent_oracle_code: str):
        self.intelligent_oracle_code = intelligent_oracle_code

    @gl.public.write
    def create_new_prediction_market(
        self,
        prediction_market_id: str,
        title: str,
        description: str,
        potential_outcomes: list[str],
        rules: list[str],
        data_source_domains: list[str],
        resolution_urls: list[str],
        earliest_resolution_date: str,
    ) -> None:
        registered_contracts = len(self.contract_addresses)
        contract_address = gl.contract.deploy(
            code=self.intelligent_oracle_code.encode("utf-8"),
            args=[
                prediction_market_id,
                title,
                description,
                potential_outcomes,
                rules,
                data_source_domains,
                resolution_urls,
                earliest_resolution_date,
            ],
            salt_nonce=registered_contracts + 1,
        )
        print("contract_address", contract_address)
        print("contract_address type", type(contract_address))
        self.contract_addresses.append(contract_address.as_hex)

    @gl.public.view
    def get_contract_addresses(self) -> list[str]:
        return list(self.contract_addresses)
