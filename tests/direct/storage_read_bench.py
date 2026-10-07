# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }
import genlayer as gl
from genlayer.types import *


class ReadBench(gl.contract.Contract):
    value: u256
    items: gl.storage.DynArray[u256]

    def __init__(self, count: int):
        self.value = 42
        for i in range(count):
            self.items.append(i)

    @gl.public.view
    def read_one(self) -> int:
        return int(self.value)

    @gl.public.view
    def read_n(self, n: int) -> int:
        total = 0
        for i in range(int(n)):
            total += self.items[i]
        return int(total)
