# v0.3.0
# { "Depends": "py-genlayer:4zpkevgdn5yds0bnh0kdsjg7ak15c4jg3bdh8qk6cd7qj1wwty5g" }
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
