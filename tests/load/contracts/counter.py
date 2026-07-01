# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }
import genlayer as gl
from genlayer.types import *


class Counter(gl.contract.Contract):
    count: bigint

    def __init__(self):
        self.count = 0

    @gl.public.write
    def increment(self) -> None:
        self.count += 1

    @gl.public.view
    def get_count(self) -> bigint:
        return self.count
