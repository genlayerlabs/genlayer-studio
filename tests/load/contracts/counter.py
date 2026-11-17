# v0.4.0
# { "Depends": "py-genlayer:kzybg95x1gn21dw72m7hyg0maaap04n4nx55j9yt6fkdqxtz6bz0" }
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
