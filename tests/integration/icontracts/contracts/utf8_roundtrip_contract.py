# v0.1.0
# { "Depends": "py-genlayer:test" }

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
