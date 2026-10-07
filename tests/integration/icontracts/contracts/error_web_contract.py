# v0.3.0
# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }

import genlayer as gl


class ErrorWebContract(gl.contract.Contract):
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

        result = gl.eq_principle.strict_eq(get_url_data)
