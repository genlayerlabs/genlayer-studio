# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }

from genlayer import *


class ErrorWebContractDirect(gl.Contract):
    """
    Copy of tests/integration/icontracts/contracts/error_web_contract.py for direct-mode testing.

    Direct-mode runner extraction requires a concrete runner hash (not "test").
    """

    def __init__(self, testcase: int, url: str):
        if testcase == 1:
            self.test_system_error(url)
        elif testcase == 2:
            self.test_connect_to_url(url)

    def test_system_error(self, url: str):
        result = gl.nondet.web.render(url, mode="text")
        return result

    def test_connect_to_url(self, url: str):
        def get_url_data():
            web_data = gl.nondet.web.render(url, mode="text")
            return web_data

        gl.eq_principle.strict_eq(get_url_data)
