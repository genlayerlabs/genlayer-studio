# v0.1.0
# { "Depends": "py-genlayer:latest" }

import json

from genlayer import *


class LlmErc20(gl.Contract):
    balances: TreeMap[Address, u256]

    def __init__(self, total_supply: int) -> None:
        self.balances[gl.message.sender_address] = u256(total_supply)

    @gl.public.write
    def transfer(self, amount: int, to_address: str) -> None:
        input = f"""
You keep track of transactions between users and their balance in coins.
The current balance for all users in JSON format is:
{json.dumps(self.get_balances())}
The transaction to compute is: {{
sender: "{gl.message.sender_address.as_hex}",
recipient: "{Address(to_address).as_hex}",
amount: {amount},
}}

"""
        task = """For every transaction, validate that the user sending the Coins has
enough balance. If any transaction is invalid, it shouldn't be processed.
Update the balances based on the valid transactions only.
Given the current balance in JSON format and the transaction provided,
please provide the result of your calculation with the following format:
{{
"transaction_success": bool,          // Whether the transaction was successful
"transaction_error": str,             // Empty if transaction is successful
"updated_balances": object<str, int>  // Updated balances after the transaction
}}

It is mandatory that you respond only using the JSON format above,
nothing else. Don't include any other words or characters,
your output must be only JSON without any formatting prefix or suffix.
This result should be perfectly parsable by a JSON parser without errors.
"""

        criteria = """
The balance of the sender should have decreased by the amount sent.
The balance of the receiver should have increased by the amount sent.
The total sum of all balances should remain the same before and after the transaction"""

        final_result = (
            gl.eq_principle.prompt_non_comparative(
                lambda: input,
                task=task,
                criteria=criteria,
            )
            .replace("```json", "")
            .replace("```", "")
        )
        print("final_result: ", final_result)
        result_json = json.loads(final_result)
        for k, v in result_json["updated_balances"].items():
            self.balances[Address(k)] = v

    @gl.public.view
    def get_balances(self) -> dict[str, int]:
        return {k.as_hex: v for k, v in self.balances.items()}

    @gl.public.view
    def get_balance_of(self, address: str) -> int:
        return self.balances.get(Address(address), 0)
