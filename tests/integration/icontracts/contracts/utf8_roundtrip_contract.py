# v0.3.0
# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }

import genlayer as gl


class Utf8RoundtripContract(gl.contract.Contract):
    value: str

    def __init__(self):
        self.value = "clichéd"

    @gl.public.view
    def get_value(self) -> str:
        return self.value

    @gl.public.view
    def get_enriched_submission(self) -> dict[str, list[dict[str, str]]]:
        return {"analysis": [{"analysis": self.value}]}
