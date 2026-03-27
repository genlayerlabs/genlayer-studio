# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class Registry(gl.Contract):
    # Declare persistent storage fields
    contract_addresses: DynArray[str]
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
        contract_address = gl.deploy_contract(
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
