# v0.1.0
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class Utf8RoundtripContract(gl.Contract):
    value: str

    def __init__(self):
        self.value = "clichéd"

    @gl.public.view
    def get_value(self) -> str:
        return self.value

    @gl.public.view
    def get_enriched_submission(self) -> dict[str, list[dict[str, str]]]:
        return {"analysis": [{"analysis": self.value}]}
