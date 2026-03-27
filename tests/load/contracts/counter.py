# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *


class Counter(gl.Contract):
    count: bigint

    def __init__(self):
        self.count = bigint(0)

    @gl.public.write
    def increment(self) -> None:
        self.count += bigint(1)

    @gl.public.view
    def get_count(self) -> bigint:
        return self.count
