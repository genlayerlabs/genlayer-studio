# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }

import genlayer as gl


class ErrorWebContractDirect(gl.contract.Contract):
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
        def get_url_data():
            return gl.nondet.web.render(url, mode="text")

        gl.eq_principle.strict_eq(get_url_data)

    def test_connect_to_url(self, url: str):
        def get_url_data():
            web_data = gl.nondet.web.render(url, mode="text")
            return web_data

        gl.eq_principle.strict_eq(get_url_data)
