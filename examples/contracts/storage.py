# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *


# contract class
class Storage(gl.Contract):
    storage: str

    # constructor
    def __init__(self, initial_storage: str):
        self.storage = initial_storage

    # read methods must be annotated with view
    @gl.public.view
    def get_storage(self) -> str:
        return self.storage

    # write method
    @gl.public.write
    def update_storage(self, new_storage: str) -> None:
        self.storage = new_storage
