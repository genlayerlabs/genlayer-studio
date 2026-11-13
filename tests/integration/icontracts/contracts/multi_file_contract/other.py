# { "Depends": "py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0" }

import genlayer as gl


class Other(gl.contract.Contract):
    data: str

    def __init__(self, data: str):
        self.data = data

    @gl.public.view
    def test(self) -> str:
        return self.data
