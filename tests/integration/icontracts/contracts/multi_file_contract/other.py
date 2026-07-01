# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

import genlayer as gl


class Other(gl.contract.Contract):
    data: str

    def __init__(self, data: str):
        self.data = data

    @gl.public.view
    def test(self) -> str:
        return self.data
