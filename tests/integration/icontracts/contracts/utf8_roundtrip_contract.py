# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

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
