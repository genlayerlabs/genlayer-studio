# v0.3.0
# { "Depends": "py-genlayer:5jycge4q8k23462jtb0b9fyey1s9qz928sz2nbrd9mg4sxqg2qng" }
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
