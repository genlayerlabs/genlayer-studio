# v0.3.0
# { "Depends": "py-genlayer:9b8kjyda2ycxyq4ea6g4yfpnydxhd52gqba5rb8dw7krkh5mn9p0" }

import genlayer as gl


# contract class
class Storage(gl.contract.Contract):
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
