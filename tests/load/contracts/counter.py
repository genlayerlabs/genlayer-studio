# v0.3.0
# { "Depends": "py-genlayer:babwp9t37jw9g4j2bccgn14g6x7x9s1f7dsmh7emvz735pz4zxa0" }
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
