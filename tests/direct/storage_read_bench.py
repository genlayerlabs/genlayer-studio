# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *


class ReadBench(gl.Contract):
    value: u256
    items: DynArray[u256]

    def __init__(self, count: int):
        self.value = u256(42)
        for i in range(count):
            self.items.append(u256(i))

    @gl.public.view
    def read_one(self) -> int:
        return int(self.value)

    @gl.public.view
    def read_n(self, n: int) -> int:
        total = u256(0)
        for i in range(int(n)):
            total += self.items[i]
        return int(total)
