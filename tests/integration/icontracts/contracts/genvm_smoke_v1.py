# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *


class GenVMSmoke(gl.Contract):
    web_data: str
    prompt_result: str

    def __init__(self):
        def fetch_web() -> str:
            return gl.nondet.web.render("https://example.com/", mode="text")

        self.web_data = gl.eq_principle.strict_eq(fetch_web)

        def ask_llm() -> str:
            return gl.nondet.exec_prompt(
                "Respond with exactly the two characters OK and nothing else."
            ).strip()

        self.prompt_result = gl.eq_principle.strict_eq(ask_llm).strip()

        if "example" not in self.web_data.lower():
            raise gl.Rollback("Web smoke check failed")

        if self.prompt_result != "OK":
            raise gl.Rollback("LLM smoke check failed")

    @gl.public.view
    def get_web_data(self) -> str:
        return self.web_data

    @gl.public.view
    def get_prompt_result(self) -> str:
        return self.prompt_result
