# { "Depends": "py-genlayer:test" }

from genlayer import *


class Other(gl.Contract):
    data: str

    def __init__(self, data: str):
        self.data = data

    @gl.public.view
    def test(self) -> str:
        return self.data
