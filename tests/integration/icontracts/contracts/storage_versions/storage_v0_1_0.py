# v0.1.0
# { "Depends": "py-genlayer:15qfivjvy80800rh998pcxmd2m8va1wq2qzqhz850n8ggcr4i9q0" }

from genlayer import *


class Storage(gl.Contract):
    storage: str

    def __init__(self, initial_storage: str):
        self.storage = initial_storage

    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage
